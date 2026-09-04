#!/usr/bin/env python3
"""Spawn one opencode browser-discovery worker with watchdog-lite."""
import json, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
BATCH = BASE / "browser_discovery_batch.json"
PROMPT_TPL = BASE / "browser_discovery_prompt.txt"

def now(): return datetime.now().isoformat(timespec="seconds")

def hb_age(prefix):
    hb = BASE / "heartbeat.log"
    try:
        lines = [l for l in hb.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        from datetime import datetime as dt
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
    batch = json.loads(BATCH.read_text(encoding="utf-8-sig"))
    wid = f"bd{int(time.time())%100000}"
    tag = f"worker_{wid}"
    out_file = BASE / f"browser_discoveries_{wid}.jsonl"
    done_flag = BASE / f"browser_discovery_{wid}.done"
    prompt_file = BASE / f"browser_discovery_prompt_{wid}.txt"
    log_file = BASE / "logs" / f"{wid}.out.log"
    logs_dir = BASE / "logs"; logs_dir.mkdir(exist_ok=True)

    tpl = PROMPT_TPL.read_text(encoding="utf-8-sig")
    prompt = (tpl.replace("%%WORKER_ID%%", wid)
                 .replace("%%BATCH_FILE%%", str(BATCH))
                 .replace("%%OUT_FILE%%", str(out_file))
                 .replace("%%HEARTBEAT%%", str(BASE / "heartbeat.log"))
                 .replace("%%DONE_FLAG%%", str(done_flag)))
    prompt_file.write_text(prompt, encoding="utf-8")

    loader = (f"Read the file {prompt_file} and execute it exactly. Its instructions "
              f"are complete; begin immediately with its first step. Do not reply first.")
    exe = shutil.which("opencode") or "opencode"
    argv = [exe, "run", "--model", "opencode/x-preview-f-free", loader]

    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8-sig"))
    grace = max(cfg.get("heartbeat_timeout_sec", 240), 420)
    steady = cfg.get("heartbeat_timeout_sec", 240)
    ceiling = time.time() + 45 * 60

    for attempt in range(2):
        wid_a = wid if attempt == 0 else f"{wid}_r1"
        tag_a = f"worker_{wid_a}"
        # regenerate prompt per attempt so id matches watchdog tag
        pf = BASE / f"browser_discovery_prompt_{wid_a}.txt"
        pf.write_text(prompt.replace(wid, wid_a), encoding="utf-8")
        loader2 = (f"Read the file {pf} and execute it exactly. Its instructions are "
                   f"complete; begin immediately. Do not reply first.")
        argv = [exe, "run", "--model", "opencode/x-preview-f-free", loader2]
        with open(log_file, "ab") as fh:
            proc = subprocess.Popen(argv, cwd=str(BASE.parent), stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        spawn = time.time()
        seen = False
        how = "clean"
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            age = hb_age(tag_a)
            if age != float("inf"):
                seen = True
                limit = steady
            else:
                limit = grace
                age = time.time() - spawn
            if age > limit:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
                how = "no_first_heartbeat" if not seen else "stale"
                break
            if time.time() > ceiling:
                kill_tree = True
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
                how = "ceiling"
                break
            time.sleep(10)
        if how == "clean":
            break
        print(f"[attempt {attempt}] ended how={how}", flush=True)
        time.sleep(5)

    print(f"worker_done how={how} exit={proc.poll()} results={out_file}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
