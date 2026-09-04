/*
 * Task manager for Job Control Center.
 *
 * Files:
 *   data/tasks.json         active task list (pending / running / cancelled)
 *   data/current-task.json  the task currently being executed (or null)
 *   data/history.json       archived tasks (moved here by /clear)
 *
 * Rules:
 *   - only ONE task runs at a time (watcher guard + in-memory lock)
 *   - watcher picks the OLDEST pending task
 *   - every state change notifies the injected handler (Excel + Telegram + dashboard)
 */

const path = require("path");
const fs = require("fs");

const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, "data");
const TASKS_FILE = path.join(DATA_DIR, "tasks.json");
const CURRENT_FILE = path.join(DATA_DIR, "current-task.json");
const HISTORY_FILE = path.join(DATA_DIR, "history.json");

const WATCH_INTERVAL = Number(process.env.TASK_WATCH_INTERVAL_MS || 5000);

let notify = null;
let watcherTimer = null;
let inMemoryLock = false;

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(file, data) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2), "utf8");
}

function loadTasks() {
  return readJson(TASKS_FILE, []);
}

function saveTasks(tasks) {
  writeJson(TASKS_FILE, tasks);
}

function loadHistory() {
  return readJson(HISTORY_FILE, []);
}

function saveHistory(history) {
  writeJson(HISTORY_FILE, history);
}

function loadCurrent() {
  return readJson(CURRENT_FILE, null);
}

function saveCurrent(task) {
  writeJson(CURRENT_FILE, task);
}

function nextId(tasks) {
  return tasks.reduce((max, t) => Math.max(max, t.id), 0) + 1;
}

function emit(evt) {
  if (notify) notify(evt);
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

function createTask(prompt) {
  const text = String(prompt || "").trim();
  if (!text) return { ok: false, error: "prompt is required" };
  const tasks = loadTasks();
  const task = {
    id: nextId(tasks),
    prompt: text,
    status: "pending",
    createdAt: new Date().toISOString(),
  };
  tasks.push(task);
  saveTasks(tasks);
  emit({ status: "pending", event: "Task Created", taskId: task.id, prompt: task.prompt });
  return { ok: true, task };
}

function activeList() {
  return loadTasks().filter((t) => t.status === "pending" || t.status === "running");
}

function getCurrent() {
  return loadCurrent();
}

function historyList(limit = 10) {
  const tasks = loadTasks().filter((t) => ["completed", "failed", "cancelled"].includes(t.status));
  const archived = loadHistory();
  return [...archived.slice().reverse(), ...tasks.reverse()].slice(0, limit);
}

function startNext() {
  if (inMemoryLock) return { ok: false, reason: "lock held" };
  const current = loadCurrent();
  if (current) return { ok: false, reason: `task ${current.id} already running` };

  const tasks = loadTasks();
  const pending = tasks
    .filter((t) => t.status === "pending")
    .sort((a, b) => a.id - b.id);
  if (!pending.length) return { ok: false, reason: "no pending tasks" };

  const task = pending[0];
  inMemoryLock = true;
  try {
    task.status = "running";
    task.startedAt = new Date().toISOString();
    saveTasks(tasks);
    saveCurrent({ id: task.id, prompt: task.prompt, status: "running", startedAt: task.startedAt });
    emit({ status: "running", event: "Task Started", taskId: task.id, prompt: task.prompt });
    return { ok: true, task };
  } finally {
    inMemoryLock = false;
  }
}

function finishCurrent(status, reason) {
  const current = loadCurrent();
  if (!current) return { ok: false, error: "no task currently running" };

  const tasks = loadTasks();
  const task = tasks.find((t) => t.id === current.id);
  if (task) {
    task.status = status;
    task.finishedAt = new Date().toISOString();
    if (reason) task.reason = String(reason).trim();
    saveTasks(tasks);
  }
  saveCurrent(null);
  const finished = task || { id: current.id, prompt: current.prompt, status, finishedAt: new Date().toISOString(), reason };
  emit({
    status,
    event: status === "completed" ? "Task Completed" : "Task Failed",
    taskId: finished.id,
    prompt: finished.prompt,
    reason: finished.reason || "",
  });
  return { ok: true, task: finished };
}

function completeCurrent(reason) {
  return finishCurrent("completed", reason);
}

function failCurrent(reason) {
  return finishCurrent("failed", reason);
}

function cancelTask(id) {
  const numId = Number(id);
  const tasks = loadTasks();
  const task = tasks.find((t) => t.id === numId);
  if (!task) return { ok: false, error: `task ${id} not found` };
  if (["completed", "failed", "cancelled"].includes(task.status)) return { ok: false, error: "task already finished" };

  task.status = "cancelled";
  task.finishedAt = new Date().toISOString();
  saveTasks(tasks);

  const current = loadCurrent();
  if (current && current.id === numId) saveCurrent(null);

  emit({ status: "cancelled", event: "Task Cancelled", taskId: task.id, prompt: task.prompt });
  return { ok: true, task };
}

function clearTasks() {
  const tasks = loadTasks();
  const current = loadCurrent();
  const toArchive = tasks.filter((t) => !(current && t.id === current.id));
  const keep = tasks.filter((t) => current && t.id === current.id);

  if (toArchive.length) {
    const history = loadHistory();
    const stamped = toArchive.map((t) => ({ ...t, archivedAt: new Date().toISOString() }));
    history.push(...stamped);
    saveHistory(history);
  }
  saveTasks(keep);

  emit({ status: "info", event: "Tasks Cleared", taskId: "-", prompt: `${toArchive.length} tasks archived` });
  return { ok: true, archived: toArchive.length };
}

/* ------------------------------------------------------------------ */
/* Watcher                                                             */
/* ------------------------------------------------------------------ */

function tick() {
  const current = loadCurrent();
  if (current) return; // one task at a time
  startNext();
}

function startWatch() {
  if (watcherTimer) return;
  watcherTimer = setInterval(tick, WATCH_INTERVAL);
  tick();
  console.log(`[tasks] watcher started (every ${WATCH_INTERVAL}ms)`);
}

function stopWatch() {
  if (watcherTimer) {
    clearInterval(watcherTimer);
    watcherTimer = null;
  }
}

function setNotifier(fn) {
  notify = fn;
}

module.exports = {
  setNotifier,
  createTask,
  activeList,
  getCurrent,
  historyList,
  startNext,
  completeCurrent,
  failCurrent,
  cancelTask,
  clearTasks,
  startWatch,
  stopWatch,
};