#!/usr/bin/env python3
"""
One-off repair for control-center/data/excel-rows.json.

The store was zeroed out (Applications -> 0, JobQueue -> 0). Both are
restorable without loss:

  JobQueue     <- control-center/data/applications.xlsx ("Job Queue" sheet, 106 rows)
  Applications <- control-center/data/events.jsonl     (canonical event log; /log
                  appends to events.jsonl on every write, so nothing is lost)
  Tasks        <- kept from the existing store (untouched by the damage)

Sheet routing is exact: every event in events.jsonl carries its original POST
body in "raw", whose "sheet" field records which sheet the server wrote to.
Events with no sheet (or sheet == "Applications") are the Applications rows.

Readers that must keep working:
  control-center/server.js excelStoreLoad() -> Applications/Tasks/JobQueue/...
  show_keys.py / check_apps.py              -> Applications rows use Title-Case keys
  show_jobqueue.py / check_jobs.py          -> JobQueue rows use snake_case keys

Usage:  python recover_store.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import openpyxl

DATA = Path(r"D:\newjobs\control-center\data")
STORE = DATA / "excel-rows.json"
XLSX = DATA / "applications.xlsx"
EVENTS = DATA / "events.jsonl"
BACKUP = DATA / "excel-rows.json.damaged.bak"

# Key names used by server.js logJobQueueToExcel() (snake_case, sheet order).
JOB_QUEUE_KEYS = [
    "queue_id", "date_found", "company", "role", "location", "work_type",
    "experience_required", "source", "ats", "job_url", "canonical_url", "job_id",
    "posted_date", "match_score", "match_reason", "application_method", "status",
    "application_started", "application_completed", "resume_uploaded",
    "human_required", "human_reason", "redirected", "email_required",
    "failure_reason", "notes", "last_updated",
]


def load_events() -> list[dict]:
    """events.jsonl is UTF-8 with a BOM on line 1; one line is unparseable."""
    events, skipped = [], 0
    with open(EVENTS, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    if skipped:
        print(f"  note: skipped {skipped} unparseable line(s) in events.jsonl")
    return events


def build_applications(events: list[dict]) -> list[dict]:
    """Rebuild Applications exactly as server.js logToExcel() wrote it.

    Same field mapping as control-center/scripts/rebuild-excel.js, but keyed
    with the Title-Case column names the store actually used.
    """
    rows = []
    for e in events:
        if e.get("taskId") is not None:          # Tasks rows, not Applications
            continue
        raw = e.get("raw") or {}
        if str(raw.get("sheet") or "Applications") != "Applications":
            continue                             # went to JobQueue / HumanRequired / ...
        rows.append({
            "Date": e.get("date", ""),
            "Time": e.get("time", ""),
            "Company": e.get("company", ""),
            "Role": e.get("role", ""),
            "Platform": e.get("platform", ""),
            "Status": e.get("status", ""),
            "Event": e.get("event", ""),
            "Progress": e.get("progress", ""),
            "Posted Date": e.get("postedDate") or "",
            "Resume Uploaded": str(e.get("resumeUploaded") or ""),
            "Submitted": str(e.get("submitted") or ""),
            "Human Intervention": str(e.get("humanIntervention") or ""),
            "Failure Reason": e.get("failureReason") or "",
            "Time Taken": e.get("timeTaken") or "",
            "Screenshot": e.get("screenshot") or "",
        })
    return rows


def build_job_queue() -> list[dict]:
    """Restore the 106 Job Queue rows from the workbook's 'Job Queue' sheet."""
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    try:
        ws = wb["Job Queue"]
        it = ws.iter_rows(values_only=True)
        header = next(it)
        if len(header) != len(JOB_QUEUE_KEYS):
            print(f"  warn: sheet has {len(header)} cols, expected {len(JOB_QUEUE_KEYS)} "
                  f"(positional mapping used)")
        rows = []
        for raw in it:
            if raw is None or all(v in (None, "") for v in raw):
                continue
            rows.append({
                key: ("" if i >= len(raw) or raw[i] is None else str(raw[i]))
                for i, key in enumerate(JOB_QUEUE_KEYS)
            })
        return rows
    finally:
        wb.close()


def main() -> int:
    dry = "--dry-run" in sys.argv

    if not XLSX.exists():
        print(f"FATAL: recovery source missing: {XLSX}")
        return 2

    store = {}
    if STORE.exists():
        try:
            store = json.loads(STORE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  warn: current store unreadable ({exc}); treating as empty")

    tasks = store.get("Tasks", []) if isinstance(store, dict) else []

    print("Sources:")
    print(f"  {XLSX.name}   -> 'Job Queue' sheet")
    print(f"  {EVENTS.name} -> canonical event log")
    print(f"  {STORE.name}  -> current Tasks ({len(tasks)} rows, preserved)")

    restored = {
        "Applications": build_applications(load_events()),
        "Tasks": tasks,
        "JobQueue": build_job_queue(),
        "HumanRequired": [],
        "EmailApplications": [],
    }

    print("\nRebuilt store:")
    for key, val in restored.items():
        print(f"  {key:<18} {len(val):>4}")

    before = store.get("Applications", []) if isinstance(store, dict) else []
    before_jq = store.get("JobQueue", []) if isinstance(store, dict) else []
    print(f"\n  Applications  {len(before)} -> {len(restored['Applications'])}")
    print(f"  JobQueue      {len(before_jq)} -> {len(restored['JobQueue'])}")

    if dry:
        print("\n--dry-run: nothing written.")
        return 0

    if STORE.exists() and not BACKUP.exists():
        shutil.copy2(STORE, BACKUP)
        print(f"\nBackup of the damaged file: {BACKUP}")

    tmp = STORE.with_name(STORE.name + ".tmp")
    tmp.write_text(json.dumps(restored, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STORE)
    print(f"Wrote {STORE}  ({STORE.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
