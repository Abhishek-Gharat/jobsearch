#!/usr/bin/env python3
"""
!!! DESTRUCTIVE !!!  _manual_cleanup.py

Dedupes and rewrites D:\\newjobs\\control-center\\data\\excel-rows.json
  * drops Applications rows whose company == "Test"
  * keeps only the first row per (company, role) in Applications
  * keeps only the first row per (company, role) in JobQueue

This is NOT a read-only check. It sits next to a dozen harmless check_*.py /
show_*.py scripts, which is how it got run by accident on 2026-09-17.

Note: JobQueue legitimately holds one row per STATUS TRANSITION
(Q001 appears 3x as DISCOVERED -> FAILED -> SKIPPED), so the dedupe below is a
lossy operation by design. Confirm you mean it before passing --yes.

Usage:
    python _manual_cleanup.py           # dry run: reports what would be removed
    python _manual_cleanup.py --yes     # actually rewrite (writes a .bak first)
"""

import json
import shutil
import sys
import time
from pathlib import Path

STORE = Path(r"D:\newjobs\control-center\data\excel-rows.json")


def field(row: dict, *names: str) -> str:
    """Read a field case-insensitively.

    Applications rows carry Title-Case keys ('Company', 'Event'); JobQueue rows
    carry snake_case keys ('company', 'queue_id'). Both shapes live in this one
    file, so a plain row.get("company") silently returns None for Applications -
    which made the original dedupe collapse all 53 rows into a single bogus row.
    """
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name])
    return ""


def dedupe(rows: list) -> list:
    seen, out = set(), []
    for r in rows:
        key = (field(r, "company", "Company"), field(r, "role", "Role"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def main() -> int:
    if not STORE.exists():
        print(f"missing: {STORE}")
        return 1

    store = json.loads(STORE.read_text(encoding="utf-8"))

    apps = store.get("Applications", [])
    jq = store.get("JobQueue", [])

    new_apps = [a for a in apps if field(a, "company", "Company") != "Test"]
    removed_test = len(apps) - len(new_apps)
    new_apps = dedupe(new_apps)
    new_jq = dedupe(jq)

    print(f"target                    : {STORE}")
    print(f"'Test' Applications dropped: {removed_test}")
    print(f"Applications  {len(apps)} -> {len(new_apps)}")
    print(f"JobQueue      {len(jq)} -> {len(new_jq)}")

    if "--yes" not in sys.argv:
        print("\nDRY RUN - nothing was written. Re-run with --yes to actually rewrite.")
        return 0

    backup = STORE.with_name(f"excel-rows.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(STORE, backup)
    print(f"\nbackup written: {backup.name}")

    store["Applications"] = new_apps
    store["JobQueue"] = new_jq
    STORE.write_text(json.dumps(store, indent=2), encoding="utf-8")

    print(f"Cleaned Applications: {len(new_apps)}")
    print(f"Cleaned JobQueue: {len(new_jq)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())