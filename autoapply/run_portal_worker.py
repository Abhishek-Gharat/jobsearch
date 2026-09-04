#!/usr/bin/env python3
"""Spawn Naukri/Indeed portal worker with watchdog-lite."""
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
    portal = sys.argv[1] if len(sys.argv) > 1 else "naukri"
    assert portal in ("naukri", "indeed")
    tpl = (BASE / f"{portal}_prompt.txt").read_text(encoding="utf-8-sig")
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8-sig"))
    wid = f"{portal[:2]}{int(time.time())%100000}"
    out_file = BASE / f"{portal}_results_raw.jsonl"
    done_flag = BASE / f"{portal}_worker.done"

    exe = shutil.which("opencode") or "opencode"
    proc, how = None, "clean"
    for attempt in range(2):
        wid_a = wid if attempt == 0 else f"{wid}r"
        tag = f"worker_{wid_a}"
        pf = BASE / f"{portal}_prompt_{wid_a}.txt"
        pf.write_text(tpl.replace("%%WORKER_ID%%", wid_a)
                          .replace("%%OUT_FILE%%", str(out_file))
                          .replace("%%HEARTBEAT%%", str(BASE / "heartbeat.log"))
                          .replace("%%DONE_FLAG%%", str(done_flag))
                          .replace("%%STATE%%", str(BASE))
                          .replace("%%RESUME%%", str(BASE.parent / "Resume.pdf")),
                      encoding="utf-8")
        loader = (f"Read the file {pf} and execute it exactly. Begin immediately; "
                  f"do not reply first.")
        argv = [exe, "run", "--model", cfg.get("opencode_model") or "opencode/x-preview-f-free", loader]
        logs_dir = BASE / "logs"; logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / f"{wid_a}.out.log"
        with open(log_file, "ab") as fh:
            proc = subprocess.Popen(argv, cwd=str(BASE.parent), stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        spawn = time.time(); seen = False; how = "clean"
        ceiling = time.time() + 55 * 60
        while True:
            if proc.poll() is not None:
                break
            if done_flag.exists():
                break
            age = hb_age(tag)
            if age != float("inf"):
                seen = True; limit = cfg.get("heartbeat_timeout_sec", 240)
            else:
                limit = max(420, cfg.get("first_heartbeat_grace_sec", 420)); age = time.time() - spawn
            if age > limit:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                how = "no_first_heartbeat" if not seen else "stale"; break
            if time.time() > ceiling:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                how = "ceiling"; break
            time.sleep(10)
        if how == "clean" or done_flag.exists():
            break
        print(f"[attempt {attempt}] ended how={how}", flush=True)
        time.sleep(5)
    print(f"{portal}_worker_done how={how} results={out_file}")

if __name__ == "__main__":
    sys.exit(main())
