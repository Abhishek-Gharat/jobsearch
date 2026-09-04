/*
 * Job Control Center - local control + monitoring hub
 *
 * - POST /log            BrowserOS/agent event ingestion (Excel + JSONL + Telegram)
 * - GET  /               Web dashboard
 * - GET  /api/state      Dashboard + agent state JSON
 * - GET  /api/command    Agent polls for pending commands (continue/skip/stop)
 * - POST /api/command    Agent acknowledges a handled command
 * - POST /api/telegram-command  Dashboard buttons enqueue commands
 * - GET  /api/screenshots       List screenshots (newest first)
 * - GET  /healthz        Health check
 *
 * Task endpoints:
 * - POST /task           Create a task {prompt}
 * - GET  /tasks          List active tasks + history
 * - GET  /current        Currently running task
 * - POST /complete       Mark current task completed {reason?}
 * - POST /failed         Mark current task failed {reason}
 * - POST /api/task/:id/cancel   Cancel a task
 *
 * Worker endpoints:
 * - GET  /worker         Worker/Kilo launcher status
 *
 * Telegram commands: /status /continue /skip /stop /today /worker
 *                   /tasks /cancel <id> /clear /history
 * Any non-command message becomes a task (data/tasks.json).
 *
 * Worker: when a task starts, worker.js spawns the Kilo CLI directly
 * (kilo run --auto "<prompt>" --format json) and parses the streamed
 * JSON events. No VS Code bridge, no clipboard/SendKeys.
 *
 * Excel logging: data/applications.xlsx (auto-created, "Applications" + "Tasks" sheets)
 */

const path = require("path");
const fs = require("fs");
const express = require("express");
const ExcelJS = require("exceljs");
require("dotenv").config();

const taskManager = require("./task-manager");
const worker = require("./worker");

const ROOT = __dirname;
const PORT = Number(process.env.PORT || 3000);
const DATA_DIR = process.env.DATA_DIR || path.join(ROOT, "data");
const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR || path.join(ROOT, "screenshots");
const LOG_DIR = process.env.LOG_DIR || path.join(ROOT, "logs");
const EXCEL_FILE = process.env.EXCEL_FILE || path.join(DATA_DIR, "applications.xlsx");
const EVENTS_FILE = path.join(DATA_DIR, "events.jsonl");
const COMMANDS_FILE = path.join(DATA_DIR, "commands.json");
const BOT_TOKEN = process.env.BOT_TOKEN || "";
const CHAT_ID = process.env.CHAT_ID || "";

const EXCEL_COLUMNS = [
  "Date", "Time", "Company", "Role", "Platform", "Status", "Event", "Progress",
  "Posted Date", "Resume Uploaded", "Submitted", "Human Intervention",
  "Failure Reason", "Time Taken", "Screenshot",
];

const TASK_COLUMNS = [
  "Date", "Time", "Task ID", "Prompt", "Status", "Event", "Reason",
];

const JOB_QUEUE_COLUMNS = [
  "Queue ID", "Date Found", "Company", "Role", "Location", "Work Type",
  "Experience Required", "Source", "ATS", "Job URL", "Canonical URL", "Job ID",
  "Posted Date", "Match Score", "Match Reason", "Application Method", "Status",
  "Application Started", "Application Completed", "Resume Uploaded", "Human Required",
  "Human Reason", "Redirected", "Email Required", "Failure Reason", "Notes", "Last Updated"
];

const HUMAN_REQUIRED_COLUMNS = [
  "Queue ID", "Company", "Role", "Job URL", "Platform", "Human Reason",
  "Required Action", "Current Step", "Date", "Time", "Screenshot", "Status", "Notes"
];

const EMAIL_APPLICATIONS_COLUMNS = [
  "Queue ID", "Company", "Role", "Email", "Subject", "Instructions",
  "Job URL", "Resume Required", "Documents Required", "Status", "Notes"
];

const EVENT_EMOJI = {
  "Job Found": "🔎",
  "Form Opened": "📝",
  "Resume Uploaded": "📎",
  "Application Submitted": "✅",
  "Failed": "❌",
  "Waiting for CAPTCHA": "⚠️",
  "Waiting for OTP": "⚠️",
  "Waiting for Missing Info": "⏸",
  "Skipped": "⏭",
  "Agent Started": "🤖",
  "Agent Stopped": "🛑",
  "Resumed": "▶️",
  "Job Search Started": "🧭",
  "Task Created": "➕",
  "Task Started": "▶️",
  "Task Completed": "✅",
  "Task Failed": "❌",
  "Task Cancelled": "⏹",
  "Tasks Cleared": "🧹",
  "Kilo Launch Started": "🚀",
  "Kilo Ready": "⌨️",
  "Kilo Launch Failed": "⚠️",
  "Kilo Running": "🔄",
  "Kilo Run Retrying": "🔁",
};

for (const dir of [DATA_DIR, SCREENSHOT_DIR, LOG_DIR]) {
  fs.mkdirSync(dir, { recursive: true });
}

/* ------------------------------------------------------------------ */
/* In-memory + JSONL event store                                       */
/* ------------------------------------------------------------------ */

const state = {
  events: [],
  progress: "0/10",
  currentCompany: "-",
  currentRole: "-",
  currentPlatform: "-",
  waiting: false,
  waitingReason: "",
  agentStartedAt: null,
};

function nowParts() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
  };
}

function normalizeEvent(body) {
  const n = nowParts();
  const evt = String(body.event || "Event").trim();
  return {
    date: body.date || n.date,
    time: body.time || n.time,
    company: String(body.company || "").trim() || "-",
    role: String(body.role || "").trim() || "-",
    platform: String(body.platform || "").trim() || "-",
    status: String(body.status || "").trim() || "info",
    event: evt,
    progress: String(body.progress || "").trim() || "",
    postedDate: String(body.postedDate || body.posted_date || "").trim(),
    resumeUploaded: body.resumeUploaded || body.resume_uploaded || "",
    submitted: body.submitted || "",
    humanIntervention: body.humanIntervention || body.human_intervention || "",
    failureReason: body.failureReason || body.failure_reason || "",
    timeTaken: body.timeTaken || body.time_taken || "",
    screenshot: body.screenshot || "",
    raw: body,
  };
}

function appendJsonl(evt) {
  fs.appendFileSync(EVENTS_FILE, JSON.stringify(evt) + "\n", "utf8");
}

function updateState(evt) {
  state.events.push(evt);
  if (state.events.length > 500) state.events.shift();
  if (evt.progress) state.progress = evt.progress;
  if (evt.company && evt.company !== "-") state.currentCompany = evt.company;
  if (evt.role && evt.role !== "-") state.currentRole = evt.role;
  if (evt.platform && evt.platform !== "-") state.currentPlatform = evt.platform;
  if (evt.event === "Agent Started") state.agentStartedAt = `${evt.date} ${evt.time}`;

  const waitingEvents = ["Waiting for CAPTCHA", "Waiting for OTP", "Waiting for Missing Info"];
  if (waitingEvents.includes(evt.event)) {
    state.waiting = true;
    state.waitingReason = evt.event;
  } else if (["Resumed", "Agent Started", "Application Submitted"].includes(evt.event)) {
    state.waiting = false;
    state.waitingReason = "";
  }
}

/* ------------------------------------------------------------------ */
/* Excel logging (rebuild-from-row-store)                              */
/* ------------------------------------------------------------------ */
/* NOTE: ExcelJS loses rows/sheets when a workbook is readFile'd and
   re-saved in the same process (observed on this machine). The server
   therefore NEVER readFile's the workbook after boot. Rows live in an
   in-memory store (persisted to data/excel-rows.json) and each write
   REBUILDS the workbook from scratch and writes it out.                    */

const EXCEL_ROWS_FILE = path.join(DATA_DIR, "excel-rows.json");
const excelStore = { Applications: [], Tasks: [], JobQueue: [], HumanRequired: [], EmailApplications: [] };

function excelStoreLoad() {
  try {
    const raw = JSON.parse(fs.readFileSync(EXCEL_ROWS_FILE, "utf8"));
    if (Array.isArray(raw.Applications)) excelStore.Applications = raw.Applications;
    if (Array.isArray(raw.Tasks)) excelStore.Tasks = raw.Tasks;
    if (Array.isArray(raw.JobQueue)) excelStore.JobQueue = raw.JobQueue;
    if (Array.isArray(raw.HumanRequired)) excelStore.HumanRequired = raw.HumanRequired;
    if (Array.isArray(raw.EmailApplications)) excelStore.EmailApplications = raw.EmailApplications;
  } catch {
    // first run - empty store
  }
}

function excelStoreSave() {
  try {
    fs.writeFileSync(EXCEL_ROWS_FILE, JSON.stringify(excelStore), "utf8");
  } catch (err) {
    console.error(`[excel] row store save failed: ${err.message}`);
  }
}

/* One-time migration from a legacy applications.xlsx (before the store
   exists). Runs at boot, only reads, never writes the legacy file.      */
async function excelMigrateLegacy() {
  if (excelStore.Applications.length || excelStore.Tasks.length) return;
  if (!fs.existsSync(EXCEL_FILE)) return;
  try {
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.readFile(EXCEL_FILE);
    for (const name of ["Applications", "Tasks"]) {
      const ws = wb.getWorksheet(name);
      if (!ws || ws.rowCount <= 1) continue;
      const headers = [];
      for (let c = 1; c <= ws.getRow(1).actualCellCount; c++) headers.push(String(ws.getRow(1).getCell(c).value));
      const rows = [];
      for (let r = 2; r <= ws.rowCount; r++) {
        const row = {};
        for (let c = 0; c < headers.length; c++) row[headers[c]] = ws.getRow(r).getCell(c + 1).value;
        rows.push(row);
      }
      excelStore[name] = rows;
    }
    excelStoreSave();
    console.log(`[excel] migrated legacy file: Applications=${excelStore.Applications.length}, Tasks=${excelStore.Tasks.length}`);
  } catch (err) {
    console.error(`[excel] legacy migration failed: ${err.message}`);
  }
}

function buildWorkbook() {
  const wb = new ExcelJS.Workbook();
  const apps = wb.addWorksheet("Applications");
  apps.columns = EXCEL_COLUMNS.map((header) => ({ header, key: header.toLowerCase().replace(/\s+/g, "_"), width: 22 }));
  apps.getRow(1).font = { bold: true };
  for (const row of excelStore.Applications) {
    apps.addRow(row).commit();
  }
  const tasks = wb.addWorksheet("Tasks");
  tasks.columns = TASK_COLUMNS.map((header) => ({ header, key: header.toLowerCase().replace(/\s+/g, "_"), width: 22 }));
  tasks.getRow(1).font = { bold: true };
  for (const row of excelStore.Tasks) {
    tasks.addRow(row).commit();
  }
  const jobQueue = wb.addWorksheet("Job Queue");
  jobQueue.columns = JOB_QUEUE_COLUMNS.map((header) => ({ header, key: header.toLowerCase().replace(/\s+/g, "_"), width: 22 }));
  jobQueue.getRow(1).font = { bold: true };
  for (const row of excelStore.JobQueue) {
    jobQueue.addRow(row).commit();
  }
  const humanRequired = wb.addWorksheet("Human Required");
  humanRequired.columns = HUMAN_REQUIRED_COLUMNS.map((header) => ({ header, key: header.toLowerCase().replace(/\s+/g, "_"), width: 22 }));
  humanRequired.getRow(1).font = { bold: true };
  for (const row of excelStore.HumanRequired) {
    humanRequired.addRow(row).commit();
  }
  const emailApplications = wb.addWorksheet("Email Applications");
  emailApplications.columns = EMAIL_APPLICATIONS_COLUMNS.map((header) => ({ header, key: header.toLowerCase().replace(/\s+/g, "_"), width: 22 }));
  emailApplications.getRow(1).font = { bold: true };
  for (const row of excelStore.EmailApplications) {
    emailApplications.addRow(row).commit();
  }
  return wb;
}

function excelAppend(sheet, row) {
  excelStore[sheet].push(row);
  excelStoreSave();
  return excelWorkbookWrite();
}

function excelWorkbookWrite() {
  const wb = buildWorkbook();
  return wb.xlsx.writeFile(EXCEL_FILE);
}

let excelWriteChain = Promise.resolve();

function logToExcel(evt) {
  excelWriteChain = excelWriteChain
    .then(() =>
      excelAppend("Applications", {
        date: evt.date,
        time: evt.time,
        company: evt.company,
        role: evt.role,
        platform: evt.platform,
        status: evt.status,
        event: evt.event,
        progress: evt.progress,
        posted_date: evt.postedDate,
        resume_uploaded: String(evt.resumeUploaded),
        submitted: String(evt.submitted),
        human_intervention: String(evt.humanIntervention),
        failure_reason: evt.failureReason,
        time_taken: evt.timeTaken,
        screenshot: evt.screenshot,
      })
    )
    .catch((err) => console.error(`[excel] write failed: ${err.message}`));
  return excelWriteChain;
}

function logTaskToExcel(evt) {
  excelWriteChain = excelWriteChain
    .then(() =>
      excelAppend("Tasks", {
        date: evt.date,
        time: evt.time,
        task_id: evt.taskId,
        prompt: evt.prompt,
        status: evt.status,
        event: evt.event,
        reason: evt.reason || evt.failureReason || "",
      })
    )
    .catch((err) => console.error(`[excel] task write failed: ${err.message}`));
  return excelWriteChain;
}

function logJobQueueToExcel(row) {
  excelWriteChain = excelWriteChain
    .then(() => excelAppend("JobQueue", row))
    .catch((err) => console.error(`[excel] job queue write failed: ${err.message}`));
  return excelWriteChain;
}

function logHumanRequiredToExcel(row) {
  excelWriteChain = excelWriteChain
    .then(() => excelAppend("HumanRequired", row))
    .catch((err) => console.error(`[excel] human required write failed: ${err.message}`));
  return excelWriteChain;
}

function logEmailApplicationToExcel(row) {
  excelWriteChain = excelWriteChain
    .then(() => excelAppend("EmailApplications", row))
    .catch((err) => console.error(`[excel] email application write failed: ${err.message}`));
  return excelWriteChain;
}

/* ------------------------------------------------------------------ */
/* Task event pipeline (Excel Tasks sheet + JSONL + state + Telegram)   */
/* ------------------------------------------------------------------ */

function taskTgText(evt) {
  const emoji = EVENT_EMOJI[evt.event] || "•";
  let line = `${emoji} ${evt.event} #${evt.taskId}`;
  if (evt.prompt) line += `\n📝 ${evt.prompt}`;
  line += `\n📊 Status: ${evt.status}`;
  if (evt.reason) line += `\n⚠️ ${evt.reason}`;
  return line;
}

function logTaskEvent(evt) {
  const n = nowParts();
  const e = {
    date: n.date,
    time: n.time,
    company: "-",
    role: "-",
    platform: "Tasks",
    status: evt.status,
    event: evt.event,
    progress: "",
    postedDate: "",
    resumeUploaded: "",
    submitted: "",
    humanIntervention: "",
    failureReason: evt.reason || "",
    timeTaken: "",
    screenshot: "",
    taskId: evt.taskId,
    prompt: evt.prompt,
    raw: evt,
  };
  appendJsonl(e);
  updateState(e);
  logTaskToExcel(e);
  sendTelegram(taskTgText(e));
  if (evt.event === "Task Started") {
    worker.launchForTask({ id: evt.taskId, prompt: evt.prompt });
  }
}

taskManager.setNotifier(logTaskEvent);
worker.setOnEvent(logTaskEvent);

/* ------------------------------------------------------------------ */
/* Telegram                                                             */
/* ------------------------------------------------------------------ */

let bot = null;
let telegramReady = false;

function tgText(evt) {
  const emoji = EVENT_EMOJI[evt.event] || "•";
  let line = `${emoji} ${evt.event}`;
  if (evt.company !== "-" && evt.role !== "-") line += `\n🏢 ${evt.company} — ${evt.role}`;
  else if (evt.company !== "-") line += `\n🏢 ${evt.company}`;
  if (evt.platform && evt.platform !== "-") line += `\n📌 Platform: ${evt.platform}`;
  if (evt.status) line += `\n📊 Status: ${evt.status}`;
  if (evt.progress) line += `\n🛣 Progress: ${evt.progress}`;
  if (evt.failureReason) line += `\n⚠️ Reason: ${evt.failureReason}`;
  return line;
}

function sendTelegram(text) {
  if (!telegramReady || !bot) {
    console.log(`[telegram] skipped (token not configured): ${text.split("\n")[0]}`);
    return Promise.resolve(false);
  }
  if (!CHAT_ID) {
    console.log("[telegram] skipped (CHAT_ID not set)");
    return Promise.resolve(false);
  }
  return bot
    .sendMessage(CHAT_ID, text)
    .then((sent) => {
      console.log(`[telegram] delivered message_id=${sent && sent.message_id} first_line="${String(text).split("\n")[0]}"`);
      return true;
    })
    .catch((err) => {
      console.error(`[telegram] send failed: ${err.message}`);
      return false;
    });
}

function initTelegram() {
  if (!BOT_TOKEN) {
    console.log("[telegram] BOT_TOKEN not set — Telegram features disabled. Add it to .env and restart.");
    return;
  }
  const TelegramBot = require("node-telegram-bot-api").TelegramBot;
  bot = new TelegramBot(BOT_TOKEN, { polling: true });
  telegramReady = true;
  console.log("[telegram] bot polling started");

  bot.on("error", (err) => {
    console.error(`[telegram] bot error: ${err.message}`);
    if (/401|Unauthorized/.test(err.message)) {
      telegramReady = false;
      console.error("[telegram] invalid token — Telegram features disabled. Fix BOT_TOKEN in .env.");
    }
  });

  bot.onText(/\/status/, (msg) => {
    const lines = [
      "📋 JOB CONTROL CENTER STATUS",
      `🛣 Progress: ${state.progress}`,
      `🏢 Company: ${state.currentCompany}`,
      `💼 Role: ${state.currentRole}`,
      `📌 Platform: ${state.currentPlatform}`,
      `⏳ Waiting: ${state.waiting ? state.waitingReason : "No"}`,
      `📅 Applied today: ${todayCount()}`,
      `❌ Failed total: ${failedCount()}`,
    ];
    const recent = state.events.slice(-3);
    if (recent.length) {
      lines.push("🕘 Recent:");
      for (const e of recent) lines.push(`  ${EVENT_EMOJI[e.event] || "•"} ${e.event} — ${e.company}`);
    }
    bot.sendMessage(msg.chat.id, lines.join("\n")).catch(() => {});
  });

  bot.onText(/\/continue/, (msg) => {
    enqueueCommand({ type: "continue", at: new Date().toISOString(), source: "telegram" });
    bot
      .sendMessage(msg.chat.id, "▶️ Continue signal sent to the agent. It will resume work.")
      .catch(() => {});
  });

  bot.onText(/\/skip/, (msg) => {
    enqueueCommand({ type: "skip", at: new Date().toISOString(), source: "telegram" });
    bot
      .sendMessage(msg.chat.id, "⏭ Skip signal sent. Agent will skip the current job.")
      .catch(() => {});
  });

  bot.onText(/\/stop/, (msg) => {
    enqueueCommand({ type: "stop", at: new Date().toISOString(), source: "telegram" });
    bot
      .sendMessage(msg.chat.id, "🛑 Stop signal sent. Agent should stop after the current step.")
      .catch(() => {});
  });

  bot.onText(/\/today/, (msg) => {
    const rows = todaySubmissions();
    if (!rows.length) {
      bot.sendMessage(msg.chat.id, "📅 No applications submitted today yet.").catch(() => {});
      return;
    }
    const lines = [`📅 Today (${todaySubmissions().length} submitted):`];
    for (const r of rows) {
      lines.push(`  ✅ ${r.time} ${r.company} — ${r.role} (${r.platform})`);
    }
    bot.sendMessage(msg.chat.id, lines.join("\n")).catch(() => {});
  });

  bot.onText(/\/tasks/, (msg) => {
    const tasks = taskManager.activeList();
    const current = taskManager.getCurrent();
    const lines = ["🗂 TASK QUEUE"];
    if (current) lines.push(`🟢 RUNNING #${current.id}: ${current.prompt}`);
    const pending = tasks.filter((t) => t.status === "pending");
    if (!current && !pending.length) {
      bot.sendMessage(msg.chat.id, "🗂 No tasks in queue. Send any message to create a task.").catch(() => {});
      return;
    }
    if (pending.length) {
      lines.push(`🟡 PENDING (${pending.length}):`);
      for (const t of pending) lines.push(`  #${t.id} ${t.prompt}`);
    }
    bot.sendMessage(msg.chat.id, lines.join("\n")).catch(() => {});
  });

  bot.onText(/\/cancel\s+(\d+)/, (msg, match) => {
    const id = match[1];
    const res = taskManager.cancelTask(id);
    bot
      .sendMessage(msg.chat.id, res.ok ? `⏹ Task #${id} cancelled.` : `❌ ${res.error}`)
      .catch(() => {});
  });

  bot.onText(/\/clear/, (msg) => {
    const res = taskManager.clearTasks();
    bot
      .sendMessage(msg.chat.id, `🧹 Cleared. ${res.archived} task(s) archived to history.`)
      .catch(() => {});
  });

  bot.onText(/\/history/, (msg) => {
    const items = taskManager.historyList(10);
    if (!items.length) {
      bot.sendMessage(msg.chat.id, "📜 No finished tasks yet.").catch(() => {});
      return;
    }
    const lines = ["📜 RECENT TASKS:"];
    for (const t of items) {
      const icon = t.status === "completed" ? "✅" : t.status === "failed" ? "❌" : "⏹";
      lines.push(`  ${icon} #${t.id} ${t.prompt} — ${t.status}`);
    }
    bot.sendMessage(msg.chat.id, lines.join("\n")).catch(() => {});
  });

  bot.onText(/\/worker/, (msg) => {
    const w = worker.getState();
    const running = !!taskManager.getCurrent() || w.worker === "running";
    const lines = [
      "🤖 WORKER STATUS",
      `Worker: ${running ? "Running" : "Idle"}`,
      `Kilo: ${w.kiloDetected ? "Connected" : "Not detected"}`,
      `Transport: Kilo CLI (direct spawn)`,
      `Launcher: ${w.launcher === "ready" ? "Ready" : w.launcher === "launching" ? "Launching…" : "Error"}`,
      `Task #${w.taskId || "-"} state: ${w.taskState || "-"}`,
      `Last Launch: ${w.lastLaunch ? hhmm12(w.lastLaunch) : "never"}`,
    ];
    if (w.lastReason) lines.push(`Note: ${w.lastReason}`);
    bot.sendMessage(msg.chat.id, lines.join("\n")).catch(() => {});
  });

  bot.on("message", (msg) => {
    if (!msg.text) return;
    const isCommand = msg.text.trim().startsWith("/");
    if (isCommand) {
      const known = ["/status", "/continue", "/skip", "/stop", "/today", "/tasks", "/cancel", "/clear", "/history", "/worker", "/start"];
      const cmd = msg.text.trim().split(" ")[0];
      if (!known.includes(cmd)) {
        bot
          .sendMessage(
            msg.chat.id,
            "Commands: /status, /continue, /skip, /stop, /today, /worker, /tasks, /cancel <id>, /clear, /history, /start\n\nAny other message becomes a task."
          )
          .catch(() => {});
      }
      return;
    }
    const res = taskManager.createTask(msg.text);
    if (res.ok) {
      bot
        .sendMessage(msg.chat.id, `➕ Task #${res.task.id} created: ${res.task.prompt}\nStatus: pending`)
        .catch(() => {});
    }
  });

  sendTelegram("🤖 Job Control Center is online.\nAgent can start logging events now.").then((ok) => {
    console.log(ok ? "[telegram] startup message delivered" : "[telegram] startup message not delivered (no token/chat)");
  });
}

/* ------------------------------------------------------------------ */
/* Command queue (Telegram/dashboard -> agent)                          */
/* ------------------------------------------------------------------ */

function readCommands() {
  try {
    return JSON.parse(fs.readFileSync(COMMANDS_FILE, "utf8"));
  } catch {
    return [];
  }
}

function writeCommands(list) {
  fs.writeFileSync(COMMANDS_FILE, JSON.stringify(list, null, 2), "utf8");
}

function enqueueCommand(cmd) {
  const list = readCommands();
  list.push(cmd);
  writeCommands(list);
  return cmd;
}

/* ------------------------------------------------------------------ */
/* Aggregations                                                         */
/* ------------------------------------------------------------------ */

function today() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function hhmm12(stamp) {
  // "2026-08-17 20:42:11" -> "08:42 PM"
  const m = String(stamp).match(/(\d{2}):(\d{2})/);
  if (!m) return stamp;
  let h = Number(m[1]);
  const suffix = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${String(h).padStart(2, "0")}:${m[2]} ${suffix}`;
}

function todayCount() {
  return state.events.filter((e) => e.event === "Application Submitted" && e.date === today()).length;
}

function failedCount() {
  return state.events.filter((e) => e.event === "Failed" || e.status === "failed").length;
}

function todaySubmissions() {
  return state.events
    .filter((e) => e.event === "Application Submitted" && e.date === today())
    .map((e) => ({ time: e.time, company: e.company, role: e.role, platform: e.platform }));
}

function listScreenshots() {
  try {
    return fs
      .readdirSync(SCREENSHOT_DIR)
      .filter((f) => /\.(png|jpe?g|webp)$/i.test(f))
      .map((f) => {
        const st = fs.statSync(path.join(SCREENSHOT_DIR, f));
        return { file: f, mtime: st.mtimeMs, size: st.size };
      })
      .sort((a, b) => b.mtime - a.mtime);
  } catch {
    return [];
  }
}

/* ------------------------------------------------------------------ */
/* HTTP app                                                             */
/* ------------------------------------------------------------------ */

const app = express();
app.use(express.json({ limit: "2mb" }));
app.use("/screenshots", express.static(SCREENSHOT_DIR));
app.use(express.static(path.join(ROOT, "public")));

app.get("/healthz", (req, res) => {
  res.json({ status: "ok", telegram: telegramReady, waiting: state.waiting });
});

app.post("/log", (req, res) => {
  const body = req.body || {};
  if (!body.event && !body.company) {
    return res.status(400).json({ ok: false, error: "Missing required fields: event or company" });
  }
  const evt = normalizeEvent(body);
  appendJsonl(evt);
  updateState(evt);
  
  const sheet = body.sheet || "Applications";
  if (sheet === "Tasks") {
    logTaskToExcel(evt);
  } else if (sheet === "JobQueue") {
    logJobQueueToExcel({
      queue_id: body.queueId || "",
      date_found: body.dateFound || evt.date,
      company: evt.company,
      role: evt.role,
      location: body.location || "",
      work_type: body.workType || "",
      experience_required: body.experienceRequired || "",
      source: body.source || "",
      ats: body.ats || "",
      job_url: body.jobUrl || "",
      canonical_url: body.canonicalUrl || "",
      job_id: body.jobId || "",
      posted_date: body.postedDate || evt.postedDate || "",
      match_score: body.matchScore || "",
      match_reason: body.matchReason || "",
      application_method: body.applicationMethod || "",
      status: evt.status || "DISCOVERED",
      application_started: body.applicationStarted || "",
      application_completed: body.applicationCompleted || "",
      resume_uploaded: String(evt.resumeUploaded || body.resumeUploaded || ""),
      human_required: body.humanRequired || "",
      human_reason: body.humanReason || "",
      redirected: body.redirected || "",
      email_required: body.emailRequired || "",
      failure_reason: evt.failureReason || body.failureReason || "",
      notes: body.notes || "",
      last_updated: `${evt.date} ${evt.time}`,
    });
  } else if (sheet === "HumanRequired") {
    logHumanRequiredToExcel({
      queue_id: body.queueId || "",
      company: evt.company,
      role: evt.role,
      job_url: body.jobUrl || "",
      platform: evt.platform,
      human_reason: body.humanReason || evt.failureReason || "",
      required_action: body.requiredAction || "",
      current_step: body.currentStep || "",
      date: evt.date,
      time: evt.time,
      screenshot: evt.screenshot || "",
      status: body.status || "PENDING_HUMAN",
      notes: body.notes || "",
    });
  } else if (sheet === "EmailApplications") {
    logEmailApplicationToExcel({
      queue_id: body.queueId || "",
      company: evt.company,
      role: evt.role,
      email: body.email || "",
      subject: body.subject || "",
      instructions: body.instructions || "",
      job_url: body.jobUrl || "",
      resume_required: body.resumeRequired ? "Yes" : "No",
      documents_required: body.documentsRequired || "",
      status: body.status || "EMAIL_PENDING",
      notes: body.notes || "",
    });
  } else {
    logToExcel(evt);
  }
  sendTelegram(tgText(evt));
  res.json({ ok: true, logged: true, event: evt.event, ts: `${evt.date} ${evt.time}` });
});

app.get("/api/state", (req, res) => {
  res.json({
    progress: state.progress,
    currentCompany: state.currentCompany,
    currentRole: state.currentRole,
    currentPlatform: state.currentPlatform,
    waiting: state.waiting,
    waitingReason: state.waitingReason,
    agentStartedAt: state.agentStartedAt,
    todaySubmissions: todaySubmissions(),
    todayCount: todayCount(),
    failedCount: failedCount(),
    totalEvents: state.events.length,
    recentEvents: state.events.slice(-15).reverse(),
    screenshots: listScreenshots(),
    commands: readCommands(),
    telegramReady,
    tasks: taskManager.activeList(),
    currentTask: taskManager.getCurrent(),
    taskHistory: taskManager.historyList(10),
    worker: worker.getState(),
  });
});

app.get("/api/command", (req, res) => {
  const list = readCommands();
  if (!list.length) return res.json({ command: null });
  const cmd = list.shift();
  writeCommands(list);
  res.json({ command: cmd });
});

app.post("/api/command", (req, res) => {
  const body = req.body || {};
  const list = readCommands().filter((c) => !body.id || c.id !== body.id);
  writeCommands(list);
  res.json({ ok: true });
});

app.post("/api/telegram-command", (req, res) => {
  const { type } = req.body || {};
  if (!["continue", "skip", "stop"].includes(type)) {
    return res.status(400).json({ ok: false, error: "type must be continue|skip|stop" });
  }
  enqueueCommand({ type, at: new Date().toISOString(), source: "dashboard" });
  sendTelegram(`🎛 Command "${type}" sent from dashboard.`);
  res.json({ ok: true });
});

app.get("/api/screenshots", (req, res) => {
  res.json({ screenshots: listScreenshots() });
});

/* Clear the enqueued command backlog (dashboard/Telegram signals that the
   agent never consumed, e.g. while no agent was polling /api/command). */
app.post("/api/commands/clear", (req, res) => {
  writeCommands([]);
  res.json({ ok: true, cleared: true });
});

/* ------------------------------------------------------------------ */
/* Task API                                                            */
/* ------------------------------------------------------------------ */

app.post("/task", (req, res) => {
  const { prompt } = req.body || {};
  const result = taskManager.createTask(prompt);
  if (!result.ok) return res.status(400).json(result);
  res.json(result);
});

app.get("/tasks", (req, res) => {
  res.json({ tasks: taskManager.activeList(), history: taskManager.historyList(10) });
});

app.get("/current", (req, res) => {
  res.json({ task: taskManager.getCurrent() });
});

app.post("/complete", (req, res) => {
  const { reason } = req.body || {};
  const result = taskManager.completeCurrent(reason);
  if (!result.ok) return res.status(400).json(result);
  worker.killRunning();
  res.json(result);
});

app.post("/failed", (req, res) => {
  const { reason } = req.body || {};
  const result = taskManager.failCurrent(reason);
  if (!result.ok) return res.status(400).json(result);
  worker.killRunning();
  res.json(result);
});

app.post("/api/task/:id/cancel", (req, res) => {
  const result = taskManager.cancelTask(req.params.id);
  if (!result.ok) return res.status(400).json(result);
  worker.killRunning();
  res.json(result);
});

app.get("/worker", (req, res) => {
  const w = worker.getState();
  const running = !!taskManager.getCurrent() || w.worker === "running";
  res.json({
    worker: running ? "running" : "idle",
    kiloDetected: !!w.kiloDetected,
    launcher: w.launcher,
    lastLaunch: w.lastLaunch,
    lastSuccess: w.lastSuccess,
    lastReason: w.lastReason,
    lastResult: w.lastResult,
    taskId: w.taskId,
    taskState: w.taskState,
    attempts: w.attempts,
    transport: w.transport || "kilo-cli",
    lastCommand: w.lastCommand,
  });
});

/* ------------------------------------------------------------------ */
/* Startup                                                              */
/* ------------------------------------------------------------------ */

function start() {
  return new Promise((resolve) => {
    const server = app.listen(PORT, async () => {
      console.log(`[server] Job Control Center running on http://127.0.0.1:${PORT}`);
      console.log(`[server] Excel log: ${EXCEL_FILE}`);
      console.log(`[server] Screenshots: ${SCREENSHOT_DIR}`);
      excelStoreLoad();
      await excelMigrateLegacy();
      taskManager.startWatch();
      initTelegram();
      worker.probe().then((p) => {
        console.log(
          p.ok
            ? `[worker] kilo detection probe: ${p.kiloDetected ? `Kilo CLI ${p.kilo} found` : "Kilo CLI not found"}`
            : `[worker] probe failed: ${p.reason}`
        );
      });
      console.log(`[worker] Kilo CLI: ${worker.KILO_BIN || "(not found)"}`);
      server.on("close", () => taskManager.stopWatch());
      resolve(server);
    });
  });
}

if (require.main === module) {
  start();
}

module.exports = { app, start, state, sendTelegram, enqueueCommand, logToExcel, logTaskToExcel, normalizeEvent, taskManager, worker };