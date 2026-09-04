/*
 * Worker: Task Queue -> Kilo CLI (direct spawn, NO VS Code bridge)
 *
 * launchForTask() spawns the Kilo CLI directly:
 *   kilo run --auto "<prompt>" --format json --title job-task-<id>
 * and parses the NDJSON event stream on stdout in real time.
 *
 * Real task states (only "completed" counts as success):
 *   launched   - child process spawned
 *   running    - first step_start event seen
 *   completed  - step_finish with reason=stop AND exit code 0
 *   failed     - error event, non-zero exit, timeout, or no clean stop
 *   aborted    - operator manually completed/failed/cancelled the task
 *
 * Transient "Internal server error" responses from the model provider
 * are retried with exponential backoff (KILO_RUN_RETRIES attempts,
 * starting at KILO_RETRY_BASE_MS).
 *
 * Concurrency: only ONE launch at a time. Each launch gets an identity
 * token (currentRun); a stale close event from a killed child can never
 * touch a newer task's state (isCurrentRun guard).
 *
 * Files:
 *   data/worker.json            worker state (idle/running, taskState, lastLaunch...)
 *   logs/launcher.log           launch audit log (task id, prompt, success, taskState)
 *   logs/worker.log             full worker log (rotated at 5MB)
 *   logs/kilo-stream-<id>-a<n>.jsonl  raw streamed JSON events per attempt
 */

const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn, execFile, exec } = require("child_process");
const readline = require("readline");

const taskManager = require("./task-manager");

const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, "data");
const LOG_DIR = process.env.LOG_DIR || path.join(__dirname, "logs");

const WORKER_FILE = process.env.WORKER_FILE || path.join(DATA_DIR, "worker.json");
const LAUNCHER_LOG = path.join(LOG_DIR, "launcher.log");
const WORKER_LOG = path.join(LOG_DIR, "worker.log");

const KILO_BIN = process.env.KILO_BIN || resolveKiloBin();
const KILO_RUN_TIMEOUT_MS = Number(process.env.KILO_RUN_TIMEOUT_MS || 30 * 60 * 1000);
const KILO_RUN_RETRIES = Number(process.env.KILO_RUN_RETRIES || 3);
const KILO_RETRY_BASE_MS = Number(process.env.KILO_RETRY_BASE_MS || 2000);
const MAX_LOG_BYTES = 5 * 1024 * 1024;

let onEvent = null;
let launching = false;
let currentRun = null; // { taskId, aborted } - identity of the in-flight launch
let currentChild = null;
const launchedTaskIds = new Set();

fs.mkdirSync(LOG_DIR, { recursive: true });

/* ------------------------------------------------------------------ */
/* Kilo binary discovery                                               */
/* ------------------------------------------------------------------ */

function resolveKiloBin() {
  if (process.env.KILO_BIN) return process.env.KILO_BIN;
  const extBases = [
    path.join(os.homedir(), ".vscode", "extensions"),
    path.join(process.env.LOCALAPPDATA || "", "Programs", "Microsoft VS Code", "extensions"),
    path.join(process.env.PROGRAMFILES || "", "Microsoft VS Code", "extensions"),
  ];
  let best = null;
  let bestTime = 0;
  for (const base of extBases) {
    let entries;
    try {
      entries = fs.readdirSync(base);
    } catch {
      continue;
    }
    for (const f of entries.filter((n) => n.startsWith("kilocode.kilo-code-"))) {
      const exe = path.join(base, f, "bin", "kilo.exe");
      try {
        if (fs.existsSync(exe)) {
          const st = fs.statSync(exe);
          if (st.mtimeMs > bestTime) {
            bestTime = st.mtimeMs;
            best = exe;
          }
        }
      } catch {}
    }
  }
  return best;
}

function buildCommand(taskId, prompt) {
  const shown = String(prompt).length > 120 ? String(prompt).slice(0, 120) + "…" : String(prompt);
  return `kilo run --auto "${shown.replace(/"/g, '\\"')}" --format json --title job-task-${taskId}`;
}

/* ------------------------------------------------------------------ */
/* State persistence                                                   */
/* ------------------------------------------------------------------ */

function loadWorker() {
  try {
    return JSON.parse(fs.readFileSync(WORKER_FILE, "utf8"));
  } catch {
    return null;
  }
}

let state = Object.assign(
  {
    worker: "idle",
    kiloDetected: false,
    kiloVersion: "",
    launcher: "ready",
    taskState: null,
    lastLaunch: null,
    lastSuccess: null,
    lastReason: "",
    lastResult: "",
    lastCommand: "",
    attempts: 0,
    taskId: null,
  },
  loadWorker() || {}
);

function saveState() {
  fs.writeFileSync(WORKER_FILE, JSON.stringify(state, null, 2), "utf8");
}

function emit(evt) {
  if (onEvent) onEvent(evt);
}

function fmtNow() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/* ------------------------------------------------------------------ */
/* File logging (persisted + rotated)                                  */
/* ------------------------------------------------------------------ */

function rotateIfNeeded(file) {
  try {
    const st = fs.statSync(file);
    if (st.size > MAX_LOG_BYTES) {
      const old = file + ".old";
      fs.rmSync(old, { force: true });
      fs.renameSync(file, old);
    }
  } catch {
    // first write or missing - nothing to rotate
  }
}

function log(line) {
  const full = `[${fmtNow()}] ${line}`;
  console.log(full);
  try {
    rotateIfNeeded(WORKER_LOG);
    fs.appendFileSync(WORKER_LOG, full + "\n", "utf8");
  } catch {
    // best effort
  }
}

function appendLauncherLog(entry) {
  const line = [
    entry.launchTime,
    `task=${entry.taskId}`,
    `prompt="${String(entry.prompt).replace(/"/g, '""')}"`,
    `kilo=${entry.kiloDetected ? "yes" : "no"}`,
    `success=${entry.success ? 1 : 0}`,
    `state=${entry.taskState}`,
    `attempts=${entry.attempts || 1}`,
    entry.reason ? `reason=${entry.reason}` : "reason=",
  ].join(" | ");
  try {
    rotateIfNeeded(LAUNCHER_LOG);
    fs.appendFileSync(LAUNCHER_LOG, line + "\n", "utf8");
  } catch {
    // best effort
  }
}

function streamLogFile(taskId, attempt) {
  return path.join(LOG_DIR, `kilo-stream-${taskId}-a${attempt}.jsonl`);
}

function writeStreamLine(taskId, attempt, line) {
  try {
    fs.appendFileSync(streamLogFile(taskId, attempt), line + "\n", "utf8");
  } catch {
    // best effort
  }
}

/* ------------------------------------------------------------------ */
/* Kilo CLI interaction                                                */
/* ------------------------------------------------------------------ */

/* Probe: verify the kilo CLI binary exists and responds. */
function probe() {
  return new Promise((resolve) => {
    if (!KILO_BIN) {
      state.kiloDetected = false;
      state.launcher = "error";
      state.lastReason = "kilo CLI not found (set KILO_BIN or install Kilo Code)";
      saveState();
      log(`[worker] probe: ${state.lastReason}`);
      return resolve({ ok: false, reason: state.lastReason });
    }
    execKilo(["--version"], (err, stdout) => {
      if (err) {
        state.kiloDetected = false;
        state.launcher = "error";
        state.lastReason = `kilo CLI check failed: ${err.message}`;
        saveState();
        log(`[worker] probe failed: ${state.lastReason}`);
        return resolve({ ok: false, reason: state.lastReason });
      }
      state.kiloDetected = true;
      state.launcher = "ready";
      state.kiloVersion = String(stdout || "").trim().split("\n")[0] || "unknown";
      saveState();
      log(`[worker] probe: kilo CLI ${state.kiloVersion} at ${KILO_BIN}`);
      resolve({ ok: true, kiloDetected: true, kilo: state.kiloVersion });
    });
  });
}

/* Spawn the kilo CLI. .cmd/.bat shims need cmd.exe on Windows. */
function spawnKilo(args) {
  const opts = {
    windowsHide: true,
    cwd: __dirname,
    env: { ...process.env, FORCE_COLOR: "0", NO_COLOR: "1" },
  };
  if (/\.(cmd|bat)$/i.test(KILO_BIN)) {
    return spawn("cmd.exe", ["/d", "/s", "/c", KILO_BIN, ...args], opts);
  }
  return spawn(KILO_BIN, args, opts);
}

function execKilo(args, cb) {
  if (/\.(cmd|bat)$/i.test(KILO_BIN)) {
    return execFile("cmd.exe", ["/d", "/s", "/c", KILO_BIN, ...args], { timeout: 10000, windowsHide: true }, cb);
  }
  return execFile(KILO_BIN, args, { timeout: 10000, windowsHide: true }, cb);
}

/* Run one `kilo run` invocation, streaming-parse stdout JSON events. */
function runOnce(taskId, prompt, attempt, run) {
  return new Promise((resolve) => {
    const args = ["run", "--auto", prompt, "--format", "json", "--title", `job-task-${taskId}`];
    log(`[worker] spawning: ${KILO_BIN} ${args.map((a) => (a === prompt ? `"${prompt}"` : a)).join(" ")}`);
    const child = spawnKilo(args);
    currentChild = child;
    log(`[worker] spawned kilo pid=${child.pid}`);
    child.once("close", () => {
      if (currentChild === child) currentChild = null;
    });

    let seenStepStart = false;
    let seenStepFinishStop = false;
    let resultText = "";
    let errorText = "";
    let settled = false;

    const settle = (fn) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn();
    };

    const timer = setTimeout(() => {
      if (run.aborted) {
        settle(() => resolve({ ok: false, aborted: true, retriable: false, reason: "aborted by operator" }));
        return;
      }
      log(`[worker] task ${taskId} timed out after ${KILO_RUN_TIMEOUT_MS}ms - killing kilo`);
      killTree(child);
      settle(() =>
        resolve({ ok: false, aborted: false, retriable: false, reason: `kilo run timed out after ${Math.round(KILO_RUN_TIMEOUT_MS / 1000)}s` })
      );
    }, KILO_RUN_TIMEOUT_MS);

    const rl = readline.createInterface({ input: child.stdout });
    rl.on("line", (line) => {
      const trimmed = String(line).trim();
      if (!trimmed) return;
      writeStreamLine(taskId, attempt, trimmed);
      log(`[stream:${taskId}] ${trimmed}`);
      let evt = null;
      try {
        evt = JSON.parse(trimmed);
      } catch {
        return;
      }
      if (evt.type === "step_start") {
        if (!seenStepStart) {
          seenStepStart = true;
          if (currentRun === run && state.taskId === taskId && state.taskState !== "running") {
            state.taskState = "running";
            saveState();
          }
          emit({ status: "running", event: "Kilo Running", taskId, prompt });
        }
      } else if (evt.type === "text" && evt.part && evt.part.type === "text" && evt.part.text) {
        resultText += evt.part.text;
      } else if (evt.type === "step_finish" && evt.part && evt.part.reason === "stop") {
        seenStepFinishStop = true;
      } else if (evt.type === "error") {
        const d = evt.error || {};
        const msg = d.message || (d.data && d.data.message) || JSON.stringify(d);
        errorText += (errorText ? " | " : "") + String(msg);
      }
    });

    child.stderr.on("data", (d) => {
      const s = String(d).trim();
      if (!s) return;
      log(`[stderr:${taskId}] ${s}`);
      errorText += (errorText ? " | " : "") + s;
    });

    child.on("error", (err) => {
      settle(() => resolve({ ok: false, aborted: run.aborted, retriable: false, reason: `spawn failed: ${err.message}` }));
    });

    child.on("close", (code) => {
      settle(() => {
        if (run.aborted) return resolve({ ok: false, aborted: true, retriable: false, reason: "aborted by operator" });
        const completed = seenStepFinishStop && code === 0 && !errorText;
        if (completed) return resolve({ ok: true, aborted: false, result: resultText.trim() });
        const reason =
          errorText ||
          (code !== 0 ? `kilo exited with code ${code}` : "") ||
          "no step_finish reason=stop seen";
        resolve({
          ok: false,
          aborted: false,
          retriable: /internal server error/i.test(errorText),
          reason: String(reason).slice(0, 800),
        });
      });
    });
  });
}

function killTree(child) {
  const pid = child.pid;
  if (!pid) return;
  if (process.platform === "win32") {
    exec(`taskkill /PID ${pid} /T /F`, { windowsHide: true }, () => {});
  } else {
    try {
      child.kill();
    } catch {}
  }
}

/* Abort the in-flight kilo process (operator manually finished the task).
   Finalizes the aborted state SYNCHRONOUSLY (same tick as the /complete
   handler, before any new launch can start), and releases the launcher
   so the next task can start while the dying child's close event is
   still settling. */
function killRunning() {
  const run = currentRun;
  if (!run) return { ok: false, reason: "nothing running" };
  run.aborted = true;
  log("[worker] operator action - aborting in-flight kilo run");
  if (currentChild) killTree(currentChild);
  finalizeAborted(run, run.launchTime, state.attempts);
  return { ok: true };
}

/* ------------------------------------------------------------------ */
/* Launch flow                                                         */
/* ------------------------------------------------------------------ */

function launchForTask({ id, prompt }) {
  const taskId = Number(id);
  if (launching) return { ok: false, reason: "launcher already in progress" };
  if (launchedTaskIds.has(taskId)) return { ok: false, reason: "task already launched" };
  if (!KILO_BIN) {
    const reason = "kilo CLI not found (set KILO_BIN or install Kilo Code)";
    state.launcher = "error";
    state.lastReason = reason;
    saveState();
    return { ok: false, reason };
  }

  launchedTaskIds.add(taskId);
  launching = true;
  const run = { taskId, prompt, launchTime: fmtNow(), aborted: false };
  currentRun = run;
  const launchTime = run.launchTime;
  state.worker = "running";
  state.taskId = taskId;
  state.launcher = "launching";
  state.taskState = "launched";
  state.attempts = 0;
  state.lastCommand = buildCommand(taskId, prompt);
  state.lastReason = "";
  saveState();
  emit({ status: "info", event: "Kilo Launch Started", taskId, prompt, reason: state.lastCommand });

  runWithRetry(run, prompt, launchTime);
  return { ok: true, taskId };
}

async function runWithRetry(run, prompt, launchTime) {
  const taskId = run.taskId;
  let lastErr = "";
  let attempts = 0;
  for (let attempt = 1; attempt <= KILO_RUN_RETRIES; attempt++) {
    attempts = attempt;
    state.attempts = attempt;
    saveState();
    const r = await runOnce(taskId, prompt, attempt, run);
    if (r.aborted) return finishAborted(run, prompt, launchTime, attempts);
    if (r.ok) return finishOk(run, prompt, launchTime, attempts, r);
    lastErr = r.reason;
    if (!r.retriable) break;
    if (attempt >= KILO_RUN_RETRIES) break;
    const delay = KILO_RETRY_BASE_MS * Math.pow(2, attempt - 1);
    log(`[worker] task ${taskId} attempt ${attempt}/${KILO_RUN_RETRIES} failed with transient provider error, retrying in ${delay}ms`);
    emit({
      status: "warning",
      event: "Kilo Run Retrying",
      taskId,
      prompt,
      reason: `attempt ${attempt} failed: ${lastErr} — retrying in ${Math.round(delay / 1000)}s`,
    });
    await new Promise((res) => setTimeout(res, delay));
    if (run.aborted) return finishAborted(run, prompt, launchTime, attempts);
  }
  return finishFail(run, prompt, launchTime, attempts, lastErr);
}

/* Only the CURRENT run may mutate shared worker state, mark the task
   finished, or release the launcher. A stale close event from a
   previously killed child must never touch a newer task's state. */
function isCurrentRun(run) {
  return currentRun === run && state.taskId === run.taskId;
}

function finishOk(run, prompt, launchTime, attempts, r) {
  const taskId = run.taskId;
  const result = r.result || "(no text output)";
  log(`[worker] task ${taskId} COMPLETED after ${attempts} attempt(s)`);
  const current = isCurrentRun(run);
  if (current) {
    state.launcher = "ready";
    state.kiloDetected = true;
    state.lastLaunch = launchTime;
    state.lastSuccess = true;
    state.lastReason = "";
    state.lastResult = result.slice(0, 2000);
    state.taskState = "completed";
    state.attempts = attempts;
    state.worker = "idle";
    saveState();
    currentRun = null;
    launching = false;
  }
  appendLauncherLog({ taskId, prompt, launchTime, kiloDetected: true, success: true, taskState: "completed", attempts, reason: "" });
  emit({ status: "info", event: "Kilo Ready", taskId, prompt, reason: "" });
  const cur = taskManager.getCurrent();
  if (cur && cur.id === taskId) taskManager.completeCurrent(`Attempts: ${attempts}\nResult: ${result.slice(0, 1500)}`);
  if (current) log(`[worker] task ${taskId} done, worker idle`);
}

function finishFail(run, prompt, launchTime, attempts, reason) {
  const taskId = run.taskId;
  log(`[worker] task ${taskId} FAILED after ${attempts} attempt(s): ${reason}`);
  const current = isCurrentRun(run);
  if (current) {
    state.launcher = "ready";
    state.lastLaunch = launchTime;
    state.lastSuccess = false;
    state.lastReason = reason;
    state.lastResult = "";
    state.taskState = "failed";
    state.attempts = attempts;
    state.worker = "idle";
    saveState();
    currentRun = null;
    launching = false;
  }
  appendLauncherLog({ taskId, prompt, launchTime, kiloDetected: true, success: false, taskState: "failed", attempts, reason });
  emit({ status: "failed", event: "Kilo Launch Failed", taskId, prompt, reason });
  const cur = taskManager.getCurrent();
  if (cur && cur.id === taskId) taskManager.failCurrent(reason);
  if (current) log(`[worker] task ${taskId} failed, worker idle`);
}

function finishAborted(run, prompt, launchTime, attempts) {
  if (!isCurrentRun(run)) return; // stale close event - killRunning already finalized, or a newer task owns the state
  finalizeAborted(run, launchTime, attempts);
}

/* Write the aborted state + audit entry. Only the current run may do so. */
function finalizeAborted(run, launchTime, attempts) {
  if (!isCurrentRun(run)) return;
  const taskId = run.taskId;
  log(`[worker] task ${taskId} aborted by operator`);
  state.launcher = "ready";
  state.lastLaunch = launchTime;
  state.lastSuccess = false;
  state.lastReason = "aborted by operator (manual complete/fail/cancel)";
  state.lastResult = "";
  state.taskState = "aborted";
  state.attempts = attempts;
  state.worker = "idle";
  saveState();
  currentRun = null;
  launching = false;
  appendLauncherLog({ taskId, prompt: run.prompt, launchTime, kiloDetected: true, success: false, taskState: "aborted", attempts, reason: "aborted by operator" });
  log(`[worker] task ${taskId} aborted, worker idle`);
}

function getState() {
  return {
    ...state,
    transport: "kilo-cli",
    bridge: "removed",
    command: state.lastCommand || (state.taskId ? buildCommand(state.taskId, "") : ""),
  };
}

function setOnEvent(fn) {
  onEvent = fn;
}

module.exports = { getState, launchForTask, probe, killRunning, setOnEvent, KILO_BIN, buildCommand };