#!/usr/bin/env python3
"""
queue_store.py — safe access layer for the master job queue (D:\\newjobs\\excel-rows.json).

WHY THIS EXISTS
---------------
The master queue kept getting corrupted. It is written two ways today, both unsafe:

  bos.py save_queue()            open(QUEUE, "w") then json.dump(...)  <-- truncates the
                                 file the moment it opens, so any crash mid-dump leaves a
                                 half-written array (the observed symptom: file ends with
                                 "  }," and has no closing "]"), and json.load() in
                                 load_queue() then raises forever after.
  starterprompt.md sec.15        the discovery agent writes the queue directly with a file
                                 tool, with no validation and no backup.

Consumers worked around it with copy-pasted "tolerant reader" salvage hacks
(whatsapp_outreach.py, recruiter_outreach.py) that repair in memory only, so the
broken file stayed broken on disk.

This module is the single write path. It guarantees:
  1. Atomic writes  — temp file in the same directory + os.replace, so a crash
     leaves the previous good file untouched.
  2. Validated writes — refuses to persist a structure it cannot parse back, or a
     row missing required identity fields / using an unknown status.
  3. A rolling backup — excel-rows.json.bak written before each successful save.
  4. Tolerant reads — load() salvages a truncated array (in memory) so readers keep
     working, and repair() can rewrite the file properly on disk.

CLI
---
  python queue_store.py check     # validate the queue, report problems
  python queue_store.py count     # status breakdown
  python queue_store.py repair    # rewrite a truncated queue as valid JSON (backs up)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "excel-rows.json"
BACKUP = ROOT / "excel-rows.json.bak"

# Terminal + in-flight states allowed by exicutionrules.md sec.14
VALID_STATUSES = {
    "UNPROCESSED", "PENDING", "READY", "IN_PROGRESS",
    "SUBMITTED", "SKIPPED", "PENDING_HUMAN", "FAILED", "NOT_PROCESSED",
}

# Fields starterprompt.md sec.14 requires on every accepted job
REQUIRED_FIELDS = ("queueId", "company", "role", "jobUrl", "status")


def _encoding_for(path: Path) -> str:
    """Preserve the file's existing BOM convention (the queue has historically had one)."""
    try:
        with open(path, "rb") as fh:
            return "utf-8-sig" if fh.read(3) == b"\xef\xbb\xbf" else "utf-8"
    except FileNotFoundError:
        return "utf-8-sig"


def _salvage(text: str) -> list | None:
    """Parse a truncated JSON array by trimming the dangling element and closing it."""
    stripped = text.rstrip()
    if stripped.endswith(","):
        candidates = [stripped[:-1] + "\n]"]
    else:
        # cut back to the last complete top-level element, then close the array
        cut = stripped.rfind("\n  },")
        tail = stripped.rfind("\n  }")
        if cut != -1:
            candidates = [stripped[:cut] + "\n]"]
        elif tail != -1:
            candidates = [stripped[:tail + len("\n  }")] + "\n]"]
        else:
            candidates = [stripped + "\n]"]
        candidates.insert(0, _drop_last_element(stripped))
    for cand in candidates:
        try:
            rows = json.loads(cand)
        except Exception:
            continue
        if isinstance(rows, dict):
            rows = rows.get("jobs", rows.get("rows", []))
        if isinstance(rows, list):
            return rows
    return None


def _drop_last_element(text: str) -> str:
    """Remove the final (possibly half-written) object and close the array."""
    end = text.rfind("\n  }")
    if end == -1:
        return text + "\n]"
    return text[:end] + "\n  }\n]"


def load(path: Path | str = QUEUE, quiet: bool = True) -> list[dict]:
    """Read the queue. Never writes. Returns [] if unusable."""
    path = Path(path)
    try:
        with open(path, encoding=_encoding_for(path)) as fh:
            text = fh.read()
    except FileNotFoundError:
        if not quiet:
            print(f"queue_store: {path} not found")
        return []

    try:
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = rows.get("jobs", rows.get("rows", []))
        return rows if isinstance(rows, list) else []
    except json.JSONDecodeError as exc:
        salvaged = _salvage(text)
        if salvaged is None:
            if not quiet:
                print(f"queue_store: {path} unparseable and unsalvageable: {exc}")
            return []
        if not quiet:
            print(f"queue_store: WARNING {path} is corrupt ({exc}); "
                  f"salvaged {len(salvaged)} row(s) in memory. "
                  f"Run: python queue_store.py repair")
        return salvaged


def validate(rows: list[dict]) -> list[str]:
    """Return a list of human-readable problems. Empty list == safe to save."""
    problems: list[str] = []
    if not isinstance(rows, list):
        return [f"queue root must be a JSON array, got {type(rows).__name__}"]

    seen: dict[str, int] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"row {i}: not an object ({type(row).__name__})")
            continue
        for field in REQUIRED_FIELDS:
            if not str(row.get(field) or "").strip():
                problems.append(f"row {i} ({row.get('queueId') or '?'}): missing {field}")
        status = str(row.get("status") or "").strip().upper()
        if status and status not in VALID_STATUSES:
            problems.append(f"row {i} ({row.get('queueId')}): unknown status {status!r}")
        qid = str(row.get("queueId") or "").strip()
        if qid:
            if qid in seen:
                problems.append(f"row {i}: duplicate queueId {qid} (also row {seen[qid]})")
            else:
                seen[qid] = i
    return problems


def save(rows: list[dict], path: Path | str = QUEUE, backup: bool = True,
         allow_shrink: bool = False) -> bool:
    """Validate, then atomically write. Returns True on success.

    Raises ValueError if `rows` fails validation - a corrupt structure is never
    persisted. On any other failure the previous file is left untouched.

    allow_shrink: a queue that mysteriously loses rows is this project's dominant
    failure mode, so shrinking is refused unless explicitly requested.
    """
    problems = validate(rows)
    if problems:
        raise ValueError("refusing to save invalid queue:\n  - " + "\n  - ".join(problems[:20]))

    path = Path(path)
    encoding = _encoding_for(path)

    if not allow_shrink and path.exists():
        existing_count = None
        try:
            existing = json.loads(path.read_text(encoding=encoding))
            if isinstance(existing, list):
                existing_count = len(existing)
        except json.JSONDecodeError:
            existing_count = None          # corrupt on disk: a rewrite can only improve it
        if existing_count is not None and len(rows) < existing_count:
            raise ValueError(
                f"refusing to shrink the queue from {existing_count} to {len(rows)} rows; "
                f"pass allow_shrink=True if that is intentional"
            )

    tmp = path.with_name(path.name + ".tmp")

    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())

        if backup and path.exists():
            shutil.copy2(path, BACKUP if path == QUEUE else path.with_suffix(".json.bak"))

        os.replace(tmp, path)          # atomic on Windows and POSIX
    except BaseException:
        # Never leave a partial temp file behind for the next run to trip over.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return True


def repair(path: Path | str = QUEUE) -> int:
    """Rewrite a truncated/corrupt queue as valid JSON, keeping the salvaged rows.

    The original bytes are preserved as excel-rows.json.corrupt.bak so nothing
    found during salvage is lost. Returns the number of rows written.
    """
    path = Path(path)
    encoding = _encoding_for(path)
    raw = path.read_text(encoding=encoding)

    try:
        json.loads(raw)
        rows = load(path)
        print(f"repair: {path.name} already parses ({len(rows)} rows). Rewriting canonically.")
    except json.JSONDecodeError:
        rows = load(path)
        corrupt = path.with_name(path.name + ".corrupt.bak")
        shutil.copy2(path, corrupt)
        print(f"repair: {path.name} was corrupt. Original bytes saved to {corrupt.name}")

    save(rows, path, backup=False)
    print(f"repair: wrote {len(rows)} row(s) to {path.name}")
    return len(rows)


def status_counts(rows: list[dict]) -> Counter:
    return Counter(str(r.get("status") or "UNKNOWN") for r in rows)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"

    if cmd == "repair":
        repair()
        return 0

    rows = load(QUEUE, quiet=False)

    if cmd == "check":
        problems = validate(rows)
        print(f"queue: {QUEUE}")
        print(f"rows:  {len(rows)}")
        if problems:
            print(f"INVALID: {len(problems)} problem(s)")
            for p in problems[:40]:
                print("  -", p)
            return 1
        print("VALID")
        for status, n in status_counts(rows).most_common():
            print(f"  {n:>4}  {status}")
        return 0

    if cmd == "count":
        for status, n in status_counts(rows).most_common():
            print(f"{status:<16} {n}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
