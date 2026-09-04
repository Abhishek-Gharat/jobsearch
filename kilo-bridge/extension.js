/*
 * Kilo Bridge - VS Code extension that lets the Job Control Center
 * trigger Kilo Code tasks through a local HTTP API.
 *
 * Flow: Job Control Center -> POST http://127.0.0.1:3011/run {prompt}
 *   -> VS Code Extension API (focus chat, new task, clipboard, paste,
 *      enter) -> Kilo Code -> BrowserOS MCP -> job automation.
 *
 * No mouse coordinates, no OS-level keyboard automation, no AutoHotkey.
 *
 * Endpoints:
 *   POST /run      {prompt}              -> {ok, reason?}
 *   POST /event    {event, company, ...} -> {ok}  (status mirror)
 *   GET  /status                         -> {ok, kiloDetected, running, lastRun, ...}
 *   GET  /health                         -> {ok}
 */

const vscode = require("vscode");
const http = require("http");
const os = require("os");
const path = require("path");
const fs = require("fs");

const LOG_FILE = path.join(__dirname, "logs", "bridge.log");
const MAX_LOG_BYTES = 1024 * 1024;

const KILO_EXTENSION_ID = "kilocode.kilo-code";
const REQUIRED_KILO_COMMANDS = [
  "kilo-code.SidebarProvider.focus",
  "kilo-code.new.plusButtonClicked",
  "kilo-code.new.focusChatInput",
];

let output = null;
let statusBar = null;
let server = null;
let startedAt = null;

let running = false;
let kiloDetected = false;
let lastRun = null; // { at, ok, reason, prompt }
let lastEvent = null;
let eventCount = 0;
const logRing = []; // last 300 log lines, exposed via GET /debug

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/*
 * Verification: Kilo persists every delivered prompt as a text part in
 * its session DB (~/.local/share/kilo/kilo.db). The WAL file holds the
 * most recent writes in plaintext. Polling for the exact prompt string
 * is the only honest proof that the prompt was actually SENT.
 */
const KILO_DB_DIR = process.env.KILO_DB_DIR || path.join(os.homedir(), ".local", "share", "kilo");
const KILO_DB = path.join(KILO_DB_DIR, "kilo.db");
const KILO_DB_WAL = path.join(KILO_DB_DIR, "kilo.db-wal");

function dbContains(prompt) {
  for (const file of [KILO_DB_WAL, KILO_DB]) {
    try {
      const fd = fs.openSync(file, "r");
      const stat = fs.fstatSync(fd);
      if (stat.size === 0) {
        fs.closeSync(fd);
        continue;
      }
      const size = Math.min(stat.size, 32 * 1024 * 1024); // last 32MB (fresh writes)
      const buf = Buffer.alloc(size);
      fs.readSync(fd, buf, 0, size, stat.size - size);
      fs.closeSync(fd);
      if (buf.includes(prompt)) return true;
    } catch {
      // db not present yet - keep polling
    }
  }
  return false;
}

async function verifyPromptPersisted(prompt, maxMs) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (dbContains(prompt)) return true;
    await sleep(1000);
  }
  return false;
}

function cfg() {
  return vscode.workspace.getConfiguration("kilobridge");
}

function log(line) {
  const ts = new Date().toISOString().replace("T", " ").slice(0, 19);
  const full = `[${ts}] ${line}`;
  if (output) output.appendLine(full);
  logRing.push(full);
  if (logRing.length > 300) logRing.shift();
  console.log(`[kilobridge] ${line}`);
  try {
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    const st = fs.statSync(LOG_FILE);
    if (st.size > MAX_LOG_BYTES) {
      const tmp = LOG_FILE + ".old";
      fs.rmSync(tmp, { force: true });
      fs.renameSync(LOG_FILE, tmp);
    }
    fs.appendFileSync(LOG_FILE, full + "\n", "utf8");
  } catch {
    // file logging is best-effort
  }
}

async function updateStatusBar() {
  if (!statusBar) return;
  const port = cfg().get("port", 3011);
  const base = `$(server-process) Kilo Bridge :${port}`;
  if (server === null) {
    statusBar.text = base + " — stopped";
    statusBar.tooltip = "Kilo Bridge server is not running";
    statusBar.show();
    return;
  }
  const parts = [base];
  if (kiloDetected) parts.push("$(check) Kilo");
  else parts.push("$(error) Kilo not found");
  if (running) parts.push("$(sync~spin) running");
  if (lastEvent && cfg().get("eventMirror", true)) {
    parts.push(lastEvent.length > 40 ? lastEvent.slice(0, 40) + "…" : lastEvent);
  }
  statusBar.text = parts.join("  ");
  statusBar.tooltip = `Kilo Bridge\nKilo Code: ${kiloDetected ? "available" : "NOT detected"}\nLast run: ${lastRun ? `${lastRun.at} ok=${lastRun.ok}` : "never"}\nEvents mirrored: ${eventCount}`;
  statusBar.show();
}

/* ------------------------------------------------------------------ */
/* Kilo availability                                                   */
/* ------------------------------------------------------------------ */

async function refreshKiloAvailability() {
  try {
    const cmds = await vscode.commands.getCommands(true);
    const missing = REQUIRED_KILO_COMMANDS.filter((c) => !cmds.includes(c));
    kiloDetected = missing.length === 0 && !!vscode.extensions.getExtension(KILO_EXTENSION_ID);
    if (missing.length) log(`Kilo commands missing: ${missing.join(", ")}`);
    return kiloDetected;
  } catch (err) {
    log(`kilo availability check failed: ${err.message}`);
    kiloDetected = false;
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* Task execution                                                      */
/* ------------------------------------------------------------------ */

async function execCommand(id, arg) {
  try {
    const out = await vscode.commands.executeCommand(id, arg);
    if (out === false) {
      // VS Code commands often RESOLVE false instead of throwing when the
      // context is wrong (e.g. editor actions with no editor open). Treat
      // that as a real failure, not a success.
      return { ok: false, error: "command resolved false (no-op in this context)" };
    }
    return { ok: true, out };
  } catch (err) {
    return { ok: false, error: err && err.message ? err.message : String(err) };
  }
}

/* Get registered command ids matching a filter (for diagnostics). */
async function listCommands(filter) {
  try {
    const cmds = await vscode.commands.getCommands(true);
    const f = String(filter || "").toLowerCase();
    return cmds.filter((c) => !f || c.toLowerCase().includes(f));
  } catch (err) {
    return [`error: ${err.message}`];
  }
}

/*
 * Last-resort send: deliver a REAL Ctrl+V / Enter keypress through the
 * Windows SendKeys API into THIS VS Code window (the one hosting the
 * extension), found by walking the process parent chain up to the
 * owning Code.exe. Falls back to title matching. No screen coordinates.
 * Enabled via kilobridge.sendMode = "sendkeys".
 */
function findOwnWindowPid() {
  try {
    let pid = process.ppid;
    for (let i = 0; i < 5 && pid; i++) {
      const child = require("child_process");
      const out = child.execSync(
        `powershell -NoProfile -NonInteractive -Command "(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).ProcessName"`,
        { timeout: 5000, windowsHide: true, encoding: "utf8" }
      );
      const name = String(out || "").trim();
      if (/^Code$/i.test(name)) return pid;
      const parent = child.execSync(
        `powershell -NoProfile -NonInteractive -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=${pid}').ParentProcessId"`,
        { timeout: 5000, windowsHide: true, encoding: "utf8" }
      );
      const ppid = Number(String(parent || "").trim());
      if (!ppid || ppid === pid) break;
      pid = ppid;
    }
  } catch {}
  return null;
}

function sendKeys(keys) {
  return new Promise((resolve) => {
    try {
      const { exec } = require("child_process");
      const ownPid = findOwnWindowPid();
      const titleFallback = ownPid
        ? ""
        : "$win = Get-Process | Where-Object { $_.MainWindowTitle -like '*Extension Development Host*' -or $_.MainWindowTitle -like '*Visual Studio Code*' } | Select-Object -First 1;";
      const winRef = ownPid ? `$win = Get-Process -Id ${ownPid};` : titleFallback;
      const script = [
        "$ErrorActionPreference='Stop';",
        "$sh = New-Object -ComObject WScript.Shell;",
        winRef,
        "if (-not $win) { 'NO_VSCODE_WINDOW'; exit 1 };",
        "if (-not $sh.AppActivate($win.Id)) { 'FOCUS_FAILED'; exit 2 };",
        "Start-Sleep -Milliseconds 400;",
        `$sh.SendKeys('${keys}');`,
        "'SENT'",
      ].join(" ");
      exec(
        `powershell -NoProfile -NonInteractive -Command "${script}"`,
        { timeout: 10000, windowsHide: true },
        (err, stdout) => {
          if (err) return resolve({ ok: false, error: String(err.message || err) });
          const out = String(stdout || "").trim();
          if (out === "SENT") return resolve({ ok: true });
          resolve({ ok: false, error: out || "unknown sendKeys result" });
        }
      );
    } catch (err) {
      resolve({ ok: false, error: String(err.message || err) });
    }
  });
}

async function runTask(prompt) {
  if (running) return { ok: false, reason: "another task launch is already in progress" };

  running = true;
  const at = new Date().toISOString();
  try {
    log(`runTask: prompt="${prompt.length > 120 ? prompt.slice(0, 120) + "…" : prompt}"`);

    const deliver = async () => {
      const ok = await refreshKiloAvailability();
      if (!ok) return { ok: false, reason: "Kilo Code is not available in this VS Code window (extension missing or disabled)" };

      const focusCmd = cfg().get("focusViewCommand", "kilo-code.SidebarProvider.focus");
      const newTaskCmd = cfg().get("newTaskCommand", "kilo-code.new.plusButtonClicked");
      const focusInputCmd = cfg().get("focusInputCommand", "kilo-code.new.focusChatInput");
      const sendMode = cfg().get("sendMode", "sendkeys"); // "sendkeys" | "commands"

      const steps = [
        ["reveal chat", focusCmd],
        ["start new task", newTaskCmd],
      ];
      for (const [name, cmd] of steps) {
        const r = await execCommand(cmd);
        log(`  step "${name}" (${cmd}) -> ${r.ok ? "ok" : "FAILED: " + r.error}`);
        if (!r.ok) {
          return { ok: false, reason: `could not ${name}: ${r.error}` };
        }
      }
      await sleep(cfg().get("settleMs", 2000)); // let the webview render the new-task view

      const focusTextarea = async () => {
        for (let i = 0; i < 3; i++) {
          const r = await execCommand(focusInputCmd);
          if (r.ok) return r;
          await sleep(600);
        }
        return { ok: false, error: "focus command failed 3 times" };
      };
      const focus = await focusTextarea();
      log(`  step "focus chat input" (${focusInputCmd}) -> ${focus.ok ? "ok" : "FAILED: " + focus.error}`);
      if (!focus.ok) return { ok: false, reason: `could not focus Kilo chat input: ${focus.error}` };

      try {
        await vscode.env.clipboard.writeText(prompt);
        log("  clipboard written");
      } catch (err) {
        return { ok: false, reason: `clipboard write failed: ${err.message}` };
      }

      await sleep(300);
      await execCommand(focusInputCmd); // re-focus AFTER clipboard, before window activation

      if (sendMode === "commands") {
        const pasteCmds = cfg().get("pasteCommands", ["editor.action.clipboardPasteAction", "webview.paste"]);
        let paste = null;
        for (const cmd of pasteCmds) {
          const cmds = await vscode.commands.getCommands(true);
          if (!cmds.includes(cmd)) continue;
          paste = await execCommand(cmd);
          log(`  paste (${cmd}) -> ${paste.ok ? "ok" : "FAILED: " + paste.error}`);
          if (paste.ok) break;
        }
        if (!paste || !paste.ok) {
          return { ok: false, reason: `paste into Kilo chat failed (tried ${pasteCmds.join(", ")}) - switch kilobridge.sendMode to "sendkeys"` };
        }
        await sleep(300);
        const send = await execCommand("type", { text: "\n" });
        log(`  send (type \\n) -> ${send.ok ? "ok" : "FAILED: " + send.error}`);
        if (!send.ok) {
          return { ok: false, reason: `prompt pasted but send (Enter) failed - switch kilobridge.sendMode to "sendkeys"` };
        }
      } else {
        log("  sendMode=sendkeys: sending Ctrl+V then Enter");
        const pv = await sendKeys("^v");
        log(`  sendKeys ^v -> ${pv.ok ? "ok" : "FAILED: " + pv.error}`);
        if (!pv.ok) return { ok: false, reason: `keyboard paste failed: ${pv.error}` };
        await sleep(800);
        const en = await sendKeys("{ENTER}");
        log(`  sendKeys {ENTER} -> ${en.ok ? "ok" : "FAILED: " + en.error}`);
        if (!en.ok) return { ok: false, reason: `prompt pasted but Enter send failed: ${en.error}` };
      }

      // HONEST VERIFICATION: the prompt must appear in Kilo's session DB.
      const maxMs = Number(cfg().get("verifyMs", 15000));
      const verified = await verifyPromptPersisted(prompt, maxMs);
      log(`  verification (prompt in kilo.db) -> ${verified ? "CONFIRMED" : "NOT FOUND"}`);
      if (!verified) return { ok: false, reason: `prompt was pasted but not confirmed in Kilo (webview busy?) after ${maxMs}ms` };
      return { ok: true, verified: true };
    };

    let result = await deliver();
    if (!result.ok) {
      log("  retrying delivery once");
      await sleep(800);
      result = await deliver();
    }

    if (!result.ok) {
      lastRun = { at, ok: false, reason: result.reason, prompt };
      log(`runTask FAILED: ${result.reason}`);
      return { ok: false, reason: result.reason };
    }

    lastRun = { at, ok: true, reason: "", prompt, verified: true };
    log("runTask OK - prompt confirmed in Kilo (session db)");
    return { ok: true, verified: true };
  } finally {
    running = false;
    updateStatusBar();
  }
}

/* ------------------------------------------------------------------ */
/* HTTP server                                                         */
/* ------------------------------------------------------------------ */

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => {
      data += chunk;
      if (data.length > 1024 * 1024) req.destroy();
    });
    req.on("end", () => resolve(data));
    req.on("error", () => resolve(""));
  });
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) });
  res.end(body);
}

function handleEvent(body) {
  try {
    const evt = body && body.event ? body.event : "Event";
    const company = body && body.company && body.company !== "-" ? body.company : "";
    const taskId = body && body.taskId ? `#${body.taskId} ` : "";
    const extra = [];
    if (body && body.role && body.role !== "-") extra.push(`role=${body.role}`);
    if (body && body.platform && body.platform !== "-") extra.push(`platform=${body.platform}`);
    if (body && body.status) extra.push(`status=${body.status}`);
    if (body && body.progress) extra.push(`progress=${body.progress}`);
    if (body && body.failureReason) extra.push(`reason=${body.failureReason}`);
    lastEvent = `${taskId}${evt}${company ? " · " + company : ""}${extra.length ? " · " + extra.join(" · ") : ""}`;
    eventCount++;
    log(`event: ${lastEvent}`);
    updateStatusBar();
  } catch (err) {
    log(`event handling failed: ${err.message}`);
  }
}

function startServer() {
  const host = cfg().get("host", "127.0.0.1");
  const port = Number(cfg().get("port", 3011));

  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://${req.headers.host || "127.0.0.1"}`);
    const route = url.pathname;
    log(`${req.method} ${route}`);

    try {
      if (req.method === "POST" && route === "/run") {
        const raw = await readBody(req);
        let body = {};
        try {
          body = JSON.parse(raw || "{}");
        } catch {
          return sendJson(res, 400, { ok: false, reason: "invalid JSON body" });
        }
        const prompt = String(body.prompt || "").trim();
        const maxLen = Number(cfg().get("maxPromptLength", 20000));
        if (!prompt) return sendJson(res, 400, { ok: false, reason: "prompt is required" });
        if (prompt.length > maxLen) return sendJson(res, 400, { ok: false, reason: `prompt too long (max ${maxLen} chars)` });

        // retry the launch once on failure (transient focus/command issues)
        let result = await runTask(prompt);
        if (!result.ok) {
          await sleep(600);
          log("retrying runTask once");
          result = await runTask(prompt);
        }
        return sendJson(res, result.ok ? 200 : 502, result);
      }

      if (req.method === "POST" && route === "/event") {
        const raw = await readBody(req);
        let body = {};
        try {
          body = JSON.parse(raw || "{}");
        } catch {}
        if (cfg().get("eventMirror", true)) handleEvent(body);
        return sendJson(res, 200, { ok: true });
      }

      if (req.method === "GET" && route === "/commands") {
        const q = url.searchParams.get("q") || "";
        return sendJson(res, 200, { ok: true, count: (await listCommands(q)).length, commands: await listCommands(q) });
      }

      if (req.method === "GET" && route === "/debug") {
        return sendJson(res, 200, { ok: true, log: logRing.slice(-100) });
      }

      if (req.method === "GET" && route === "/healthz") {
        return sendJson(res, 200, { ok: true, kilo: kiloDetected, bridge: true });
      }

      if (req.method === "GET" && (route === "/status" || route === "/health")) {
        return sendJson(res, 200, {
          ok: true,
          service: "kilo-bridge",
          kiloDetected,
          running,
          lastRun,
          lastEvent,
          eventCount,
          uptimeSec: startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0,
          version: "1.0.0",
        });
      }

      return sendJson(res, 404, { ok: false, reason: `no route ${req.method} ${route}` });
    } catch (err) {
      log(`request error: ${err.message}`);
      return sendJson(res, 500, { ok: false, reason: err.message });
    }
  });

  server.on("error", (err) => {
    if (err.code === "EADDRINUSE") {
      // Single-instance: another window already owns the bridge port.
      log(`ERROR: port ${port} already in use - another Kilo Bridge instance is running. This instance will NOT serve HTTP (single-instance mode).`);
      server = null;
      updateStatusBar();
    } else {
      log(`server error: ${err.message}`);
    }
  });

  server.listen(port, host, () => {
    log(`Kilo Bridge listening on http://${host}:${port}`);
    updateStatusBar();
  });
}

/* ------------------------------------------------------------------ */
/* Activation                                                          */
/* ------------------------------------------------------------------ */

function activate(context) {
  startedAt = Date.now();
  output = vscode.window.createOutputChannel("Kilo Bridge");
  log("Kilo Bridge activating…");

  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  context.subscriptions.push(statusBar);

  context.subscriptions.push(
    vscode.commands.registerCommand("kilobridge.runTask", async (promptArg) => {
      let prompt = String(promptArg || "").trim();
      if (!prompt) {
        prompt = await vscode.window.showInputBox({
          prompt: "Prompt to send to Kilo",
          placeHolder: "Find 5 React jobs in Pune posted this week.",
          ignoreFocusOut: true,
        });
        if (!prompt) return;
      }
      const result = await runTask(prompt);
      vscode.window.showInformationMessage(
        result.ok ? "Kilo Bridge: task sent to Kilo" : `Kilo Bridge: ${result.reason}`
      );
      return result;
    }),
    vscode.commands.registerCommand("kilobridge.showLog", () => {
      output.show();
    })
  );

  refreshKiloAvailability().then(() => {
    startServer();
    updateStatusBar();
  });

  log("Kilo Bridge active");
}

function deactivate() {
  if (server) {
    server.close();
    server = null;
    log("Kilo Bridge server closed");
  }
}

module.exports = { activate, deactivate };