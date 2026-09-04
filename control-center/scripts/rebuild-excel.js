/*
 * Rebuild the Excel row store (data/excel-rows.json) from the canonical
 * event log (data/events.jsonl). Deletes nothing - the .xlsx is rebuilt
 * from the store on the next write; remove it manually to force a fresh
 * file:  del data\applications.xlsx
 *
 * Usage: node scripts\rebuild-excel.js
 */

const path = require("path");
const fs = require("fs");

const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, "..", "data");
const EVENTS_FILE = path.join(DATA_DIR, "events.jsonl");
const EXCEL_ROWS_FILE = path.join(DATA_DIR, "excel-rows.json");

const EXCEL_COLUMNS = [
  "Date", "Time", "Company", "Role", "Platform", "Status", "Event", "Progress",
  "Posted Date", "Resume Uploaded", "Submitted", "Human Intervention",
  "Failure Reason", "Time Taken", "Screenshot",
];
const TASK_COLUMNS = ["Date", "Time", "Task ID", "Prompt", "Status", "Event", "Reason"];
const EXCEL_KEYS = EXCEL_COLUMNS.map((h) => h.toLowerCase().replace(/\s+/g, "_"));
const TASK_KEYS = TASK_COLUMNS.map((h) => h.toLowerCase().replace(/\s+/g, "_"));

const store = { Applications: [], Tasks: [] };

for (const line of fs.readFileSync(EVENTS_FILE, "utf8").split("\n")) {
  if (!line.trim()) continue;
  let evt;
  try {
    evt = JSON.parse(line);
  } catch {
    continue;
  }
  const row = {
    date: evt.date,
    time: evt.time,
    company: evt.company,
    role: evt.role,
    platform: evt.platform,
    status: evt.status,
    event: evt.event,
    progress: evt.progress,
    posted_date: evt.postedDate || "",
    resume_uploaded: String(evt.resumeUploaded || ""),
    submitted: String(evt.submitted || ""),
    human_intervention: String(evt.humanIntervention || ""),
    failure_reason: evt.failureReason || "",
    time_taken: evt.timeTaken || "",
    screenshot: evt.screenshot || "",
  };
  if (evt.taskId != null) {
    store.Tasks.push({
      date: evt.date,
      time: evt.time,
      task_id: evt.taskId,
      prompt: evt.prompt || "",
      status: evt.status,
      event: evt.event,
      reason: evt.failureReason || evt.reason || "",
    });
  } else {
    store.Applications.push(row);
  }
}

fs.writeFileSync(EXCEL_ROWS_FILE, JSON.stringify(store, null, 2), "utf8");
console.log(`excel-rows.json written: Applications=${store.Applications.length}, Tasks=${store.Tasks.length}`);
console.log(`(delete data\\applications.xlsx, then the next event write regenerates it from this store)`);