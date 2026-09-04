#!/usr/bin/env python3
"""Hot-swap watcher: when current supervisor finishes its in-flight batch
(batch_done event), restart it once so patched code takes over losslessly."""
import json, subprocess, time
from pathlib import Path

BASE = Path(__file__).resolve().parent

def read(p):
    try:
        return Path(p).read_text(encoding="utf-8-sig")
    except Exception:
        return ""

def main():
    lock = BASE / "supervisor.lock"
    pid = json.loads(read(lock) or "{}").get("pid")
    if not pid:
        print("no supervisor running"); return
    start_marker = None
    for ln in reversed(read(BASE/"events.jsonl").splitlines()):
        if '"event": "batch_start"' in ln or '"event":"batch_start"' in ln:
            start_marker = ln; break
    print(f"watching supervisor pid={pid}; swap after next batch_done...")
    while True:
        time.sleep(15)
        try:
            pr = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                capture_output=True, text=True)
            if str(pid) not in pr.stdout:
                print("supervisor already gone"); return
        except Exception:
            pass
        ev = read(BASE/"events.jsonl").splitlines()
        tail = [l for l in ev[-6:] if "batch_done" in l]
        if tail and tail[-1] != start_marker and "batch_start" not in tail[-1]:
            pass
        # trigger: any batch_done AFTER our recorded last batch_start line index
        idx_start = None
        for i, l in enumerate(ev):
            if l == start_marker: idx_start = i
        if idx_start is not None and any("batch_done" in l for l in ev[idx_start+1:]):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            time.sleep(4)
            (BASE/"supervisor.lock").unlink(missing_ok=True)
            subprocess.Popen(['cmd','/c','python supervisor.py --resume >> logs\\console.log 2>&1'],
                             cwd=str(BASE), shell=False,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            print("hot-swapped to patched code between batches")
            return

if __name__ == "__main__":
    main()
