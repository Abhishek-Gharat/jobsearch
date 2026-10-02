#!/usr/bin/env python3
"""
!!! DESTRUCTIVE !!!  _manual_clear_store.py

Wipes JobQueue and all but one Applications row in
    D:\\newjobs\\control-center\\data\\excel-rows.json

This is NOT a read-only check. It sits next to a dozen harmless check_*.py /
show_*.py scripts, which is how it got run by accident and how 106 JobQueue rows
plus 53 Applications rows were destroyed on 2026-09-17.

Recovery from that incident is in D:\\newjobs\\recover_store.py (rebuilds the store
from control-center/data/applications.xlsx and data/events.jsonl).

To avoid a repeat, this script now:
  * is renamed with a leading underscore so it does not sort beside the checks,
  * does NOTHING unless you pass --yes,
  * takes a timestamped backup before it writes.

Usage:
    python _manual_clear_store.py            # dry run: says what it would delete
    python _manual_clear_store.py --yes      # actually clear (writes a .bak first)
"""

import json
import shutil
import sys
import time
from pathlib import Path

STORE = Path(r"D:\newjobs\control-center\data\excel-rows.json")


def field(row: dict, *names: str) -> str:
    """Read a field case-insensitively (Applications rows are Title-Case,
    JobQueue rows are snake_case)."""
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name])
    return ""


def main() -> int:
    if not STORE.exists():
        print(f"missing: {STORE}")
        return 1

    store = json.loads(STORE.read_text(encoding="utf-8"))

    apps = store.get("Applications", [])
    keep = [a for a in apps if field(a, "event", "Event") == "Agent Started"]
    victims = len(apps) - len(keep)
    queued = len(store.get("JobQueue", []))

    print(f"target              : {STORE}")
    print(f"Applications to drop: {victims} (keeping {len(keep)} 'Agent Started' row(s))")
    print(f"JobQueue rows to drop: {queued}")

    if "--yes" not in sys.argv:
        print("\nDRY RUN - nothing was written. Re-run with --yes to actually clear.")
        return 0

    backup = STORE.with_name(f"excel-rows.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(STORE, backup)
    print(f"\nbackup written: {backup.name}")

    store["Applications"] = keep
    store["JobQueue"] = []
    STORE.write_text(json.dumps(store, indent=2), encoding="utf-8")

    print("Cleared Applications and JobQueue")
    print(f"Applications: {len(store['Applications'])}")
    print(f"JobQueue: {len(store['JobQueue'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())