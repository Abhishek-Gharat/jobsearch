/*
 * End-to-end test for Job Control Center.
 * Runs the server on a test port with temp data, then verifies:
 *  - /healthz, GET / (dashboard), POST /log (valid + invalid)
 *  - /api/state aggregation
 *  - /api/command queue (continue/skip/stop)
 *  - /api/telegram-command
 *  - Excel file created with the logged row
 *  - Screenshot listing
 *  - Task queue + worker -> Kilo CLI (fake kilo binary emitting scripted
 *    JSON events; no real model calls)
 */

const path = require("path");
const fs = require("fs");
const os = require("os");
const { execSync } = require("child_process");
const ExcelJS = require("exceljs");

const TEST_PORT = 3199;
const TEST_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "jcc-test-"));
const TEST_SHOTS = path.join(TEST_DATA, "screenshots");
const FAKE_KILO = path.join(__dirname, "test", "fake-kilo.cmd");
const FAKE_KILO_STATE = path.join(TEST_DATA, "fake-kilo-invocations.txt");
fs.mkdirSync(TEST_SHOTS, { recursive: true });

process.env.PORT = String(TEST_PORT);
process.env.DATA_DIR = TEST_DATA;
process.env.SCREENSHOT_DIR = TEST_SHOTS;
process.env.LOG_DIR = path.join(TEST_DATA, "logs");
process.env.BOT_TOKEN = "";
process.env.CHAT_ID = "6574417193";
process.env.TASK_WATCH_INTERVAL_MS = "300";
process.env.KILO_BIN = FAKE_KILO;
process.env.KILO_RETRY_BASE_MS = "150";
process.env.KILO_RUN_RETRIES = "3";
process.env.FAKE_KILO_SCENARIO = "hang";
process.env.FAKE_KILO_STATE = FAKE_KILO_STATE;

const { start, worker } = require("./server");
const BASE = `http://127.0.0.1:${TEST_PORT}`;

let passed = 0;
let failed = 0;
const results = [];

function check(name, cond, extra) {
  if (cond) { passed++; results.push(`PASS ${name}`); }
  else { failed++; results.push(`FAIL ${name}${extra ? " | " + extra : ""}`); }
}

async function req(method, url, body) {
  const res = await fetch(BASE + url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}
  return { status: res.status, json, text };
}

async function waitFor(fn, attempts, intervalMs, label) {
  for (let i = 0; i < attempts; i++) {
    const out = await fn();
    if (out) return out;
    await new Promise((res) => setTimeout(res, intervalMs));
  }
  return null;
}

function readWorkerFile() {
  return JSON.parse(fs.readFileSync(path.join(TEST_DATA, "worker.json"), "utf8"));
}

function readEventsJsonl() {
  return fs
    .readFileSync(path.join(TEST_DATA, "events.jsonl"), "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l));
}

async function main() {
  const server = await start();
  try {
    // 1. healthz
    let r = await req("GET", "/healthz");
    check("GET /healthz -> 200", r.status === 200, `status=${r.status}`);
    check("healthz reports status ok", r.json && r.json.status === "ok");

    // 2. dashboard
    r = await req("GET", "/");
    check("GET / -> 200 dashboard html", r.status === 200 && r.text.includes("Job Control Center"), `status=${r.status}`);

    // 3. invalid log
    r = await req("POST", "/log", {});
    check("POST /log empty -> 400", r.status === 400, `status=${r.status}`);

    // 4. valid log (job found)
    r = await req("POST", "/log", {
      company: "TestCorp", role: "Frontend Developer", platform: "Greenhouse",
      status: "found", event: "Job Found", progress: "1/10", postedDate: "2026-08-16",
    });
    check("POST /log job found -> 200 ok", r.status === 200 && r.json && r.json.ok === true, JSON.stringify(r.json));
    check("POST /log echoes event", r.json && r.json.event === "Job Found");

    // 5. log submitted event
    r = await req("POST", "/log", {
      company: "TestCorp", role: "Frontend Developer", platform: "Greenhouse",
      status: "Applied", event: "Application Submitted", progress: "1/10",
      resumeUploaded: true, submitted: true,
    });
    check("POST /log submitted -> 200", r.status === 200 && r.json.ok === true);

    // 6. waiting event
    r = await req("POST", "/log", {
      company: "TestCorp2", role: "React Developer", platform: "Lever",
      status: "waiting", event: "Waiting for CAPTCHA", progress: "1/10",
    });
    check("POST /log captcha -> 200", r.status === 200 && r.json.ok === true);

    // 7. state aggregation
    r = await req("GET", "/api/state");
    check("GET /api/state -> 200", r.status === 200);
    check("state progress = 1/10", r.json.progress === "1/10", r.json.progress);
    check("state current company = TestCorp2", r.json.currentCompany === "TestCorp2", r.json.currentCompany);
    check("state waiting = true", r.json.waiting === true && r.json.waitingReason === "Waiting for CAPTCHA");
    check("state todayCount = 1", r.json.todayCount === 1, String(r.json.todayCount));
    check("state failedCount = 0", r.json.failedCount === 0);

    // 8. command queue
    r = await req("GET", "/api/command");
    check("no command initially", r.json && r.json.command === null);
    r = await req("POST", "/api/telegram-command", { type: "continue" });
    check("enqueue continue -> 200", r.status === 200 && r.json.ok === true);
    r = await req("POST", "/api/telegram-command", { type: "skip" });
    check("enqueue skip -> 200", r.status === 200 && r.json.ok === true);
    r = await req("POST", "/api/telegram-command", { type: "bad" });
    check("enqueue bad type -> 400", r.status === 400, `status=${r.status}`);
    r = await req("GET", "/api/command");
    check("pop continue first", r.json && r.json.command && r.json.command.type === "continue", JSON.stringify(r.json));
    r = await req("GET", "/api/command");
    check("pop skip second", r.json && r.json.command && r.json.command.type === "skip");
    r = await req("GET", "/api/command");
    check("queue drained", r.json && r.json.command === null);

    // 9. screenshots
    fs.writeFileSync(path.join(TEST_SHOTS, "test-shot.png"), Buffer.from("fake png"));
    r = await req("GET", "/api/screenshots");
    check("screenshots listed", r.json && r.json.screenshots.length === 1, JSON.stringify(r.json));

    // 10. Excel file verification
    const excelPath = path.join(TEST_DATA, "applications.xlsx");
    let rowCount = 0;
    let sheet = null;
    for (let attempt = 0; attempt < 20; attempt++) {
      await new Promise((res) => setTimeout(res, 300));
      if (fs.existsSync(excelPath)) {
        const wb = new ExcelJS.Workbook();
        await wb.xlsx.readFile(excelPath);
        sheet = wb.getWorksheet("Applications");
        rowCount = sheet ? sheet.rowCount : 0;
        if (rowCount === 4) break;
      }
    }
    check("excel file exists", fs.existsSync(excelPath), excelPath);
    if (fs.existsSync(excelPath)) {
      check("excel sheet exists", !!sheet);
      check("excel header row correct", !!sheet && sheet.getRow(1).getCell(1).value === "Date");
      check("excel has 3 data rows", rowCount === 4, `rowCount=${rowCount}`);
      const row2 = sheet.getRow(2);
      check("excel company logged", row2.getCell(3).value === "TestCorp", String(row2.getCell(3).value));
      check("excel event logged", row2.getCell(7).value === "Job Found", String(row2.getCell(7).value));
    }

    // ---- TASK SYSTEM (fake kilo "hang" scenario: keeps tasks running until
    // the operator manually completes/fails/cancels them) ----

    // 11. create tasks
    r = await req("POST", "/task", { prompt: "Find 5 React jobs" });
    check("POST /task -> 200", r.status === 200 && r.json.ok === true && r.json.task.id === 1, JSON.stringify(r.json));
    r = await req("POST", "/task", { prompt: "Apply to 3 Mumbai roles" });
    check("POST /task second -> id 2", r.status === 200 && r.json.ok === true && r.json.task.id === 2);
    r = await req("POST", "/task", {});
    check("POST /task empty prompt -> 400", r.status === 400, `status=${r.status}`);

    // 12. task list
    r = await req("GET", "/tasks");
    check("GET /tasks -> 2 active", r.json && r.json.tasks.length === 2, JSON.stringify(r.json.tasks));

    // 13. watcher picks oldest task as current and worker launches it
    let current = await waitFor(async () => {
      const rr = await req("GET", "/current");
      return rr.json && rr.json.task ? rr.json.task : null;
    }, 25, 100);
    check("watcher picked task 1 as current", current && current.id === 1 && current.status === "running", JSON.stringify(current));
    r = await req("GET", "/tasks");
    const runningCount = r.json.tasks.filter((t) => t.status === "running").length;
    check("only ONE task running at a time", runningCount === 1, `running=${runningCount}`);
    check("task 2 still pending", r.json.tasks.some((t) => t.id === 2 && t.status === "pending"));
    const launchedWk = await waitFor(async () => {
      const rr = await req("GET", "/worker");
      return rr.json && rr.json.taskId === 1 && rr.json.taskState === "running" ? rr.json : null;
    }, 30, 100);
    check("worker taskState=running after step_start", !!launchedWk, JSON.stringify(launchedWk));
    check("worker transport is kilo-cli", launchedWk && launchedWk.transport === "kilo-cli", launchedWk && launchedWk.transport);
    check("worker lastCommand recorded", launchedWk && /kilo run --auto/.test(launchedWk.lastCommand), launchedWk && launchedWk.lastCommand);

    // 14. complete current -> worker aborts in-flight kilo, watcher moves to next
    r = await req("POST", "/complete", { reason: "done" });
    check("POST /complete -> ok", r.status === 200 && r.json.ok === true && r.json.task.status === "completed");
    r = await req("GET", "/current");
    check("current cleared after complete", r.json.task === null);
    const abortedWk = await waitFor(async () => {
      const rr = await req("GET", "/worker");
      return rr.json && rr.json.worker === "idle" && rr.json.taskState === "aborted" ? rr.json : null;
    }, 30, 100);
    // TODO(abort-timing): worker idle + taskState=aborted after manual complete
    // Known non-blocking issue: on Windows, child.kill() sends SIGTERM which Node
    // ignores; killTree() falls back to taskkill /F after a short grace period.
    // The close event can arrive after the manual /complete has already returned,
    // so finishAborted() runs after the test has already checked the state.
    // Core flow (task -> kilo run -> completed/failed) works. Fix later by
    // wiring currentChild/abort directly into task-manager or awaiting kill.
    check("worker idle + taskState=aborted after manual complete", !!abortedWk, JSON.stringify(abortedWk));

    current = await waitFor(async () => {
      const rr = await req("GET", "/current");
      return rr.json && rr.json.task ? rr.json.task : null;
    }, 25, 100);
    check("watcher picked task 2 after complete", current && current.id === 2, JSON.stringify(current));

    // 15. fail current
    r = await req("POST", "/failed", { reason: "company rejected" });
    check("POST /failed -> ok", r.status === 200 && r.json.ok === true && r.json.task.status === "failed");
    r = await req("POST", "/failed");
    check("POST /failed with no current -> 400", r.status === 400, `status=${r.status}`);
    r = await req("POST", "/complete");
    check("POST /complete with no current -> 400", r.status === 400, `status=${r.status}`);

    // 16. cancel task
    r = await req("POST", "/task", { prompt: "Cancel me" });
    const cancelId = r.json.task.id;
    r = await req("POST", `/api/task/${cancelId}/cancel`);
    check("POST cancel -> ok", r.status === 200 && r.json.ok === true && r.json.task.status === "cancelled");
    r = await req("POST", `/api/task/${cancelId}/cancel`);
    check("cancel already finished -> 400", r.status === 400);
    r = await req("POST", "/api/task/999/cancel");
    check("cancel missing task -> 400", r.status === 400);

    // 17. history
    r = await req("GET", "/tasks");
    const hist = r.json.history || [];
    check("history contains completed #1", hist.some((t) => t.id === 1 && t.status === "completed"));
    check("history contains failed #2", hist.some((t) => t.id === 2 && t.status === "failed"));
    check("history contains cancelled #3", hist.some((t) => t.id === cancelId && t.status === "cancelled"));

    // 18. /api/state includes task data
    r = await req("GET", "/api/state");
    check("state has currentTask + tasks", "currentTask" in r.json && Array.isArray(r.json.tasks) && "taskHistory" in r.json);

    // 19. Excel Tasks sheet
    let taskRows = 0;
    let taskSheet = null;
    for (let attempt = 0; attempt < 20; attempt++) {
      await new Promise((res) => setTimeout(res, 300));
      const wb = new ExcelJS.Workbook();
      await wb.xlsx.readFile(excelPath);
      taskSheet = wb.getWorksheet("Tasks");
      taskRows = taskSheet ? taskSheet.rowCount : 0;
      if (taskRows >= 9) break;
    }
    check("excel Tasks sheet exists", !!taskSheet);
    check("excel Tasks has logged rows", taskRows >= 8, `rows=${taskRows}`);
    if (taskSheet) {
      const events = [];
      for (let i = 2; i <= taskSheet.rowCount; i++) events.push(taskSheet.getRow(i).getCell(6).value);
      check("Tasks sheet has Task Started", events.includes("Task Started"), events.join(","));
      check("Tasks sheet has Task Completed", events.includes("Task Completed"));
      check("Tasks sheet has Task Failed", events.includes("Task Failed"));
      check("Tasks sheet has Task Cancelled", events.includes("Task Cancelled"));
    }

    // ---- WORKER / KILO CLI (fake kilo binary) ----

    // 20. worker endpoint shape + kilo detected via fake CLI probe
    r = await req("GET", "/worker");
    check("GET /worker -> 200", r.status === 200, `status=${r.status}`);
    check(
      "worker endpoint shape",
      r.json && "worker" in r.json && "kiloDetected" in r.json && "launcher" in r.json && "lastLaunch" in r.json &&
        "taskState" in r.json && "transport" in r.json && "lastCommand" in r.json,
      JSON.stringify(r.json)
    );
    check("kilo detected via CLI probe", r.json.kiloDetected === true, String(r.json.kiloDetected));

    // 21. SUCCESS scenario: task auto-completes with real state, only on step_finish reason=stop
    process.env.FAKE_KILO_SCENARIO = "success";
    r = await req("POST", "/task", { prompt: "Kilo pipeline test" });
    const kiloTaskId = r.json.task.id;

    const cur = await waitFor(async () => {
      const rr = await req("GET", "/current");
      return rr.json && rr.json.task && rr.json.task.id === kiloTaskId ? rr.json.task : null;
    }, 25, 100);
    check("watcher picked kilo task", cur && cur.id === kiloTaskId && cur.status === "running", JSON.stringify(cur));

    const okWk = await waitFor(async () => {
      const rr = await req("GET", "/worker");
      const w = rr.json;
      if (w && w.taskId === kiloTaskId && w.taskState === "completed") return w;
      return null;
    }, 60, 100);
    check("taskState=completed on step_finish reason=stop", !!okWk, JSON.stringify(okWk));
    check("lastSuccess=true only on real completion", okWk && okWk.lastSuccess === true, String(okWk && okWk.lastSuccess));
    check("worker idle after auto-complete", okWk && okWk.worker === "idle", okWk && okWk.worker);
    check("launcher ready after run", okWk && okWk.launcher === "ready", okWk && okWk.launcher);
    check("lastResult captured from streamed text", okWk && /Senior React Developer at Pune Corp/.test(okWk.lastResult), okWk && okWk.lastResult);

    const taskList = await req("GET", "/tasks");
    const doneTask = taskList.json.history.find((t) => t.id === kiloTaskId);
    check("task auto-completed in tasks.json", doneTask && doneTask.status === "completed", JSON.stringify(doneTask));

    const okEvents = readEventsJsonl();
    check("events.jsonl has Task Completed for the task", okEvents.some((e) => e.taskId === kiloTaskId && e.event === "Task Completed"), "missing");
    check("events.jsonl has Kilo Running (step_start seen)", okEvents.some((e) => e.taskId === kiloTaskId && e.event === "Kilo Running"), "missing");
    check("events.jsonl Task Completed carries result", (() => {
      const e = okEvents.find((x) => x.taskId === kiloTaskId && x.event === "Task Completed");
      return e && /Senior React Developer at Pune Corp/.test(e.raw.reason);
    })());

    const okWorkerFile = readWorkerFile();
    check("worker.json taskState=completed", okWorkerFile.taskState === "completed", JSON.stringify(okWorkerFile));
    check("worker.json lastSuccess=true", okWorkerFile.lastSuccess === true);

    // 22. duplicate launch prevention
    const dedupe = worker.launchForTask({ id: kiloTaskId, prompt: "Kilo pipeline test" });
    check("duplicate launch prevented", dedupe.ok === false && /already launched/.test(dedupe.reason), JSON.stringify(dedupe));

    // 23. dashboard contains Worker panel
    r = await req("GET", "/");
    check("dashboard has Worker panel", r.text.includes("Worker / Kilo bridge") && r.text.includes("wkWorker"), `len=${r.text.length}`);

    // 24. launcher.log audit entry (persisted)
    const launcherLog = path.join(TEST_DATA, "logs", "launcher.log");
    check("launcher.log created", fs.existsSync(launcherLog));
    if (fs.existsSync(launcherLog)) {
      const logText = fs.readFileSync(launcherLog, "utf8");
      check("launcher.log has task id + prompt", logText.includes(`task=${kiloTaskId}`) && logText.includes("Kilo pipeline test"), logText.split("\n").pop());
      check("launcher.log success=1 + state=completed", new RegExp(`task=${kiloTaskId}[^\\n]*success=1[^\\n]*state=completed`).test(logText));
    }

    // 25. ERROR scenario: failure recorded as failure (not success)
    process.env.FAKE_KILO_SCENARIO = "error";
    r = await req("POST", "/task", { prompt: "Error scenario test" });
    const errTaskId = r.json.task.id;
    const errWk = await waitFor(async () => {
      const rr = await req("GET", "/worker");
      const w = rr.json;
      if (w && w.taskId === errTaskId && w.taskState === "failed") return w;
      return null;
    }, 60, 100);
    check("taskState=failed on error event", !!errWk, JSON.stringify(errWk));
    check("lastSuccess=false when task failed", errWk && errWk.lastSuccess === false, String(errWk && errWk.lastSuccess));
    check("failure reason surfaced", errWk && /Model provider exploded/.test(errWk.lastReason), errWk && errWk.lastReason);
    check("worker idle after failure", errWk && errWk.worker === "idle", errWk && errWk.worker);
    const errHistory = await req("GET", "/tasks");
    const failedTask = errHistory.json.history.find((t) => t.id === errTaskId);
    check("task marked failed in tasks.json", failedTask && failedTask.status === "failed", JSON.stringify(failedTask));
    const errEvents = readEventsJsonl();
    check("events.jsonl has Task Failed with reason", errEvents.some((e) => e.taskId === errTaskId && e.event === "Task Failed" && /Model provider exploded/.test(e.raw.reason)), "missing");
    if (fs.existsSync(launcherLog)) {
      const logText = fs.readFileSync(launcherLog, "utf8");
      check("launcher.log success=0 for failed task", new RegExp(`task=${errTaskId}[^\\n]*success=0`).test(logText));
    }

    // 26. RETRY scenario: transient "Internal server error" retried with backoff
    fs.writeFileSync(FAKE_KILO_STATE, "0");
    process.env.FAKE_KILO_SCENARIO = "retry";
    r = await req("POST", "/task", { prompt: "Retry scenario test" });
    const retryTaskId = r.json.task.id;
    const retryWk = await waitFor(async () => {
      const rr = await req("GET", "/worker");
      const w = rr.json;
      if (w && w.taskId === retryTaskId && w.taskState === "completed") return w;
      return null;
    }, 60, 100);
    check("retry task eventually completed", !!retryWk, JSON.stringify(retryWk));
    check("retry recorded attempts=2", retryWk && retryWk.attempts === 2, String(retryWk && retryWk.attempts));
    const workerLog = path.join(TEST_DATA, "logs", "worker.log");
    check("worker.log exists (persisted)", fs.existsSync(workerLog));
    if (fs.existsSync(workerLog)) {
      const wt = fs.readFileSync(workerLog, "utf8");
      check("worker.log logs retry attempt", /retrying in \d+ms/.test(wt), "no retry line");
      check("worker.log has streamed JSON events", /\[stream:\d+\] \{"type":"step_start"/.test(wt), "no stream lines");
    }
    const retryEvents = readEventsJsonl();
    check("events.jsonl has Kilo Run Retrying event", retryEvents.some((e) => e.taskId === retryTaskId && e.event === "Kilo Run Retrying"), "missing");
    if (fs.existsSync(launcherLog)) {
      const logText = fs.readFileSync(launcherLog, "utf8");
      check("launcher.log retry task success=1 + attempts=2", new RegExp(`task=${retryTaskId}[^\\n]*success=1[^\\n]*attempts=2`).test(logText));
    }

    // 27. NO-STOP scenario: exit 0 WITHOUT step_finish reason=stop is NOT success
    process.env.FAKE_KILO_SCENARIO = "nostop";
    r = await req("POST", "/task", { prompt: "No-stop scenario test" });
    const nsTaskId = r.json.task.id;
    const nsWk = await waitFor(async () => {
      const rr = await req("GET", "/worker");
      const w = rr.json;
      if (w && w.taskId === nsTaskId && (w.taskState === "failed" || w.taskState === "completed")) return w;
      return null;
    }, 60, 100);
    check("exit 0 without reason=stop -> taskState=failed", !!nsWk && nsWk.taskState === "failed", JSON.stringify(nsWk));
    check("lastSuccess=false for no-stop run", nsWk && nsWk.lastSuccess === false);

    // 28. probe reports kilo found via CLI
    const probe = await worker.probe();
    check("probe ok with fake kilo CLI", probe.ok === true && probe.kiloDetected === true, JSON.stringify(probe));
  } catch (err) {
    failed++;
    results.push(`FAIL uncaught: ${err.stack || err.message}`);
  } finally {
    server.close();
    // kill any lingering fake kilo "hang" children
    try {
      execSync(`wmic process where "commandline like '%fake-kilo%' and name='node.exe'" call terminate`, { windowsHide: true });
    } catch {}
    fs.rmSync(TEST_DATA, { recursive: true, force: true });
    console.log(results.join("\n"));
    console.log(`\n${passed} passed, ${failed} failed`);
    process.exit(failed ? 1 : 0);
  }
}

main();