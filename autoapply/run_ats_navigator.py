#!/usr/bin/env python3
"""Spawn ATS navigator worker (playbook-driven interactive discovery)."""
import json, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

def hb_age(prefix):
    hb = BASE / "heartbeat.log"
    try:
        from datetime import datetime as dt
        lines = [l for l in hb.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        for ln in reversed(lines):
            p = ln.split("|", 2)
            if len(p) >= 2 and p[1].startswith(prefix):
                ts = dt.fromisoformat(p[0])
                nowv = dt.now(ts.tzinfo) if ts.tzinfo else dt.now()
                return (nowv - ts).total_seconds()
    except Exception:
        pass
    return float("inf")

def main():
    wid = f"nav{int(time.time())%100000}"
    out_file = BASE / f"ats_nav_discoveries_{wid}.jsonl"
    nav_log = BASE / f"ats_nav_outcomes_{wid}.jsonl"
    done_flag = BASE / f"ats_nav_{wid}.done"

    tpl = (BASE / "ats_nav_prompt.txt").read_text(encoding="utf-8-sig")
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8-sig"))
    grace = max(cfg.get("heartbeat_timeout_sec", 240), 420)
    steady = cfg.get("heartbeat_timeout_sec", 240)
    ceiling = time.time() + 50 * 60

    exe = shutil.which("opencode") or "opencode"
    logs_dir = BASE / "logs"; logs_dir.mkdir(exist_ok=True)
    proc, how = None, "clean"

    for attempt in range(2):
        wid_a = wid if attempt == 0 else f"{wid}_r1"
        tag = f"worker_{wid_a}"
        pf = BASE / f"ats_nav_prompt_{wid_a}.txt"
        pf.write_text(tpl.replace("%%WORKER_ID%%", wid_a)
                         .replace("%%BATCH_FILE%%", str(BASE / "ats_nav_batch.json"))
                         .replace("%%TEMPLATES%%", str(BASE / "ats_navigation_templates.json"))
                         .replace("%%OUT_FILE%%", str(out_file))
                         .replace("%%NAV_LOG%%", str(nav_log))
                         .replace("%%HEARTBEAT%%", str(BASE / "heartbeat.log"))
                         .replace("%%DONE_FLAG%%", str(done_flag)), encoding="utf-8")
        loader = (f"Read the file {pf} and execute it exactly. Instructions are complete; "
                  f"begin immediately with its first step. Do not reply first.")
        argv = [exe, "run", "--model", cfg.get("opencode_model") or "opencode/x-preview-f-free", loader]
        log_file = logs_dir / f"{wid_a}.out.log"
        with open(log_file, "ab") as fh:
            proc = subprocess.Popen(argv, cwd=str(BASE.parent), stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        spawn = time.time(); seen = False; how = "clean"
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            age = hb_age(tag)
            if age != float("inf"):
                seen = True; limit = steady
            else:
                limit = grace; age = time.time() - spawn
            if age > limit:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                how = "no_first_heartbeat" if not seen else "stale"; break
            if time.time() > ceiling:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                how = "ceiling"; break
            time.sleep(10)
        if how == "clean":
            break
        print(f"[attempt {attempt}] ended how={how}", flush=True)
        time.sleep(5)

    print(f"navigator_done how={how} exit={proc.poll()} discoveries={out_file} log={nav_log}")

if __name__ == "__main__":
    sys.exit(main())
