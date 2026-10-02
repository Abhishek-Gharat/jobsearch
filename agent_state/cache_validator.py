#!/usr/bin/env python3
"""
cache_validator.py — File modification and hash caching utility.
Implements Rule 6: Before rereading a large local file, check whether modification
timestamp or file hash changed. If unchanged, reuse existing summary/data.
"""

import hashlib
import json
import os
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent
CACHE_FILE = STATE_DIR / "cache_meta.json"

def get_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def is_file_changed(path_str: str) -> bool:
    p = Path(path_str)
    if not p.exists():
        return False
    
    mtime = p.stat().st_mtime
    meta = {}
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
            
    key = str(p.resolve())
    if key in meta:
        old_mtime = meta[key].get("mtime")
        if old_mtime == mtime:
            return False
            
    # Mtime differed or not found, check SHA-256
    curr_hash = get_file_hash(p)
    if key in meta and meta[key].get("hash") == curr_hash:
        meta[key]["mtime"] = mtime
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return False

    # Updated
    meta[key] = {
        "mtime": mtime,
        "hash": curr_hash,
        "size_bytes": p.stat().st_size
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return True

if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "candidate_core.json")
    print(f"Checking {target}: changed = {is_file_changed(target)}")
