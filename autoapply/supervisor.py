#!/usr/bin/env python3
"""
AutoApply Supervisor v2 - overnight fault-tolerant orchestrator (exactly-N capped).

Layer model:
  L0  start.bat / resume.bat   -> launches this process DETACHED
  L1  supervisor.py            -> stdlib-only watchdog. Never touches the browser.
                                   Dynamic portal-sized batches, heartbeat watchdog,
                                   60s-flush checkpointing, live dashboard, final report.
  L2  `opencode run <prompt>`  -> disposable agent session per batch driving
                                   BrowserOS MCP + OX Alpha vision.

Every processed job MUST end in exactly one terminal state:
    submitted | skipped | failed | failed_unconfirmed | review_required
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DRYRUN = False
RUN_START_TS = None

# ---------------------------------------------------------------- states

STATUS_PENDING = "pending"
STATUS_INPROG = "in_progress"
STATUS_SUBMITTED = "submitted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_UNCONFIRMED = "failed_unconfirmed"
STATUS_REVIEW = "review_required"
PROVIDER_RANK = {"greenhouse":0,"ashby":1,"lever":2,"smartrecruiters":3,"workable":4,
                  "recruiterflow":5,"zohorecruit":6,"default":7}

TERMINAL = {STATUS_SUBMITTED, STATUS_SKIPPED, STATUS_FAILED, STATUS_UNCONFIRMED, STATUS_REVIEW}
STATUS_ALIASES = {"applied": STATUS_SUBMITTED, "needs_human": STATUS_REVIEW}

RESULT_TO_STATUS = {
    "submitted": STATUS_SUBMITTED,
    "applied": STATUS_SUBMITTED,                      # legacy worker vocab
    "skipped": STATUS_SKIPPED,
    "failed": STATUS_FAILED,
    "failed_unconfirmed": STATUS_UNCONFIRMED,
    "unconfirmed": STATUS_UNCONFIRMED,
    "needs_human": STATUS_REVIEW,
    "review_required": STATUS_REVIEW,
}

DEFAULTS = {
    "max_jobs_per_run": 200,
    "poll_interval_sec": 10,
    "heartbeat_timeout_sec": 120,
    "first_heartbeat_grace_sec": 300,
    "job_budget_sec": 150,
    "batch_base_timeout_sec": 600,
    "batch_timeout_per_job_sec": 240,
    "max_retries_per_batch": 2,
    "max_job_attempts": 2,
    "opencode_cmd": "opencode",
    "opencode_model": "",
    "opencode_run_extra_args": [],
    "keep_system_awake": True,
    "excluded_portal_classes": ["linkedin"],
    "batch_size_by_portal": {
        "linkedin": 8, "greenhouse": 12, "lever": 12, "ashby": 12,
        "workday": 5, "taleo": 5, "smartrecruiters": 5, "workable": 5,
        "icims": 5, "recruiterflow": 5, "zoho": 5, "default": 5,
    },
    "pause_between_batches_sec": {"default": 45, "linkedin": 120},
}


# ---------------------------------------------------------------- paths

def p(name: str) -> Path:
    return (BASE / "_dryrun" / name) if DRYRUN else (BASE / name)


def cfg_path() -> Path:
    return BASE / "_dryrun_config.json" if DRYRUN else BASE / "config.json"


def jobs_file() -> Path: return p("jobs.json")
def checkpoint_file() -> Path: return p("checkpoint.json")
def failed_file() -> Path: return p("failed_jobs.json")
def review_file() -> Path: return p("review_queue.json")
def heartbeat_file() -> Path: return p("heartbeat.log")
def events_file() -> Path: return p("events.jsonl")
def lock_file() -> Path: return p("supervisor.lock")
def live_status_file() -> Path: return p("status_live.json")
def batches_dir() -> Path: return p("batches")
def results_dir() -> Path: return p("results")


def ensure_dirs():
    for d in (batches_dir(), results_dir(), p("logs")):
        d.mkdir(parents=True, exist_ok=True)
    if not heartbeat_file().exists():
        heartbeat_file().write_text(f"{now()}|supervisor|init\n", encoding="utf-8")
    for f, init in ((review_file(), {"updated_at": None, "items": []}),
                    (failed_file(), {"updated_at": None, "items": []})):
        if not f.exists():
            save_json(f, init)
    if DRYRUN:
        src, dst = BASE / "jobs.json", p("jobs.json")
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


# ---------------------------------------------------------------- utils

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except Exception as exc:
        log_event("state_corrupt", note=f"{path.name}: {exc}; default substituted")
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def touch_heartbeat(source: str, msg: str = "") -> None:
    with open(heartbeat_file(), "a", encoding="utf-8") as fh:
        fh.write(f"{now()}|{source}|{msg}\n")


def heartbeat_age_of(prefix: str) -> float:
    try:
        lines = [ln for ln in heartbeat_file().read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
        for ln in reversed(lines):
            parts = ln.split("|", 2)
            if len(parts) >= 2 and parts[1].startswith(prefix):
                ts = datetime.fromisoformat(parts[0])
                # worker lines carry TZ offset (PowerShell -Format o); supervisor
                # lines are naive local. Normalize before subtracting.
                if ts.tzinfo is not None:
                    nowv = datetime.now(timezone.utc).astimezone(ts.tzinfo)
                else:
                    nowv = datetime.now()
                return (nowv - ts).total_seconds()
    except Exception:
        pass
    return float("inf")


def log_event(kind: str, job_id: str | None = None, note: str = "", **extra) -> None:
    rec = {"ts": now(), "event": kind}
    if job_id:
        rec["job_id"] = job_id
    if note:
        rec["note"] = str(note)[:500]
    rec.update(extra)
    with open(events_file(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if h:
            k32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def keep_system_awake(on: bool) -> None:
    """Prevent OS sleep while supervisor lives; display may still sleep."""
    try:
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


# ---------------------------------------------------------------- lock

class Lock:
    def __init__(self):
        self.path = lock_file()

    def acquire(self) -> bool:
        if self.path.exists():
            old = load_json(self.path, {}).get("pid", -1)
            if pid_alive(old):
                print(f"[lock] supervisor already running (pid {old}). Exiting.", flush=True)
                return False
            log_event("restart", note=f"stale lock removed (dead pid {old})")
        save_json(self.path, {"pid": os.getpid(), "started_at": now()})
        return True

    def release(self):
        try:
            if self.path.exists() and load_json(self.path, {}).get("pid") == os.getpid():
                self.path.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- state ops

def load_jobs() -> dict:
    data = load_json(jobs_file(), {"updated_at": None, "jobs": []})
    if isinstance(data, list):
        data = {"updated_at": None, "jobs": data}
    migrated = False
    for j in data.setdefault("jobs", []):
        s = j.get("status")
        if s in STATUS_ALIASES:
            j["status"] = STATUS_ALIASES[s]
            migrated = True
    if migrated:
        save_jobs(data)
        log_event("state_migrated", note="legacy statuses mapped to v2 vocabulary")
    return data


def save_jobs(data: dict) -> None:
    data["updated_at"] = now()
    save_json(jobs_file(), data)


def stats(jobs: dict) -> dict:
    order = [STATUS_PENDING, STATUS_INPROG, STATUS_SUBMITTED, STATUS_SKIPPED,
             STATUS_FAILED, STATUS_UNCONFIRMED, STATUS_REVIEW]
    c = {s: 0 for s in order}
    for j in jobs["jobs"]:
        s = j.get("status", STATUS_PENDING)
        c[s] = c.get(s, 0) + 1
    return c


def register_failed(job: dict, reason: str) -> None:
    reg = load_json(failed_file(), {"updated_at": None, "items": []})
    reg.setdefault("items", [])
    reg["items"].append({
        **{k: job.get(k) for k in ("id", "company", "title", "url", "portal")},
        "final_state": job.get("status"), "reason": reason,
        "attempts": job.get("attempts"), "ts": now(),
    })
    reg["items"] = reg["items"][-1000:]
    reg["updated_at"] = now()
    save_json(failed_file(), reg)


def register_review(job: dict, reason: str) -> None:
    reg = load_json(review_file(), {"updated_at": None, "items": []})
    reg.setdefault("items", [])
    if not any(it.get("id") == job.get("id") for it in reg["items"]):
        reg["items"].append({
            **{k: job.get(k) for k in ("id", "company", "title", "url", "portal")},
            "reason": reason, "attempts": job.get("attempts"),
            "queued_at": now(),
        })
    reg["updated_at"] = now()
    save_json(review_file(), reg)


def requeue_in_progress(jobs: dict, cfg: dict) -> None:
    dirty = False
    for job in jobs["jobs"]:
        if job.get("status") == STATUS_INPROG:
            job["attempts"] = job.get("attempts", 0) + 1
            if job["attempts"] >= cfg["max_job_attempts"]:
                job["status"] = STATUS_FAILED
                job["error"] = "worker_died_mid_job"
                register_failed(job, "worker_died_mid_job")
            else:
                job["status"] = STATUS_PENDING
                job["error"] = "requeued_after_crash"
            job["updated_at"] = now()
            dirty = True
    if dirty:
        save_jobs(jobs)
        log_event("crash_recovery", note="orphan in_progress jobs requeued on boot")


# ---------------------------------------------------------------- checkpoint v2

def refresh_checkpoint(jobs: dict | None = None, *, batch_id: str | None = None,
                       worker_id: str | None = None, current_job: dict | None = None,
                       phase: str = "idle", history_entry: dict | None = None,
                       restart_count: int | None = None) -> dict:
    cp = load_json(checkpoint_file(), {})
    jobs = jobs if jobs is not None else load_jobs()
    cp.update({
        "version": 2,
        "phase": phase,
        "updated_at": now(),
        "stats": stats(jobs),
    })
    if batch_id is not None:
        cp["current_batch_id"] = batch_id
    if worker_id is not None:
        cp["worker_id"] = worker_id
    if current_job is not None:
        cp["current_job"] = current_job          # {"id","url","step"}
    if restart_count is not None:
        cp["restart_count"] = restart_count
    if RUN_START_TS:
        cp["run_started_at"] = RUN_START_TS
    if history_entry:
        cp.setdefault("history", []).append(history_entry)
        cp["history"] = cp["history"][-100:]
    save_json(checkpoint_file(), cp)
    write_live_status(cp, jobs)
    return cp


def write_live_status(cp: dict, jobs: dict) -> None:
    hist = [h for h in cp.get("history", []) if h.get("took_sec")]
    rate = None
    if hist:
        n_jobs = sum(sum(h.get("tally", {}).values()) for h in hist)
        n_sec = sum(h["took_sec"] for h in hist)
        if n_jobs > 0:
            rate = n_sec / n_jobs
    pend = cp.get("stats", {}).get(STATUS_PENDING, 0)
    eta_min = round((pend * rate) / 60, 1) if rate and pend else (0 if pend == 0 else None)
    hb_age = heartbeat_age_of("worker")
    snap = {
        "updated_at": now(),
        "phase": cp.get("phase", "idle"),
        "counts": cp.get("stats", {}),
        "review_required": cp.get("stats", {}).get(STATUS_REVIEW, 0),
        "current_batch": cp.get("current_batch_id"),
        "current_job": cp.get("current_job"),
        "worker_id": cp.get("worker_id"),
        "eta_minutes": eta_min,
        "watchdog": {"state": "watching" if cp.get("phase") == "batch_active" else "idle",
                     "heartbeat_age_sec": round(hb_age) if hb_age != float("inf") else None,
                     "heartbeat_timeout_sec": DEFAULTS["heartbeat_timeout_sec"],
                     "restart_count": cp.get("restart_count", 0),
                     "last_restart_reason": cp.get("last_restart_reason")},
        "mcp_status": cp.get("mcp_status", "unknown"),
        "run_started_at": cp.get("run_started_at"),
    }
    save_json(live_status_file(), snap)


# ---------------------------------------------------------------- portal classes

def portal_class(portal: str, table: dict) -> tuple[str, int]:
    pl = (portal or "").lower()
    best, size = "default", table.get("default", 5)
    for key, val in table.items():
        if key != "default" and key in pl and len(key) > len(best):
            best, size = key, val
    return best, size


def pause_for(cls: str, table: dict) -> int:
    return int(table.get(cls, table.get("default", 45)))


# ---------------------------------------------------------------- worker spawn

def _extract_profile_facts() -> str:
    import re
    try:
        txt = (ROOT / "Job_Profile.TEMPLATE.md").read_text(encoding="utf-8-sig")
    except Exception:
        return "(profile file unreadable - rely on tracker fill rules)"
    wanted = ["Full name", "First name", "Last name", "Primary email", "Primary phone",
              "LinkedIn", "GitHub", "Portfolio"]
    facts = []
    for line in txt.splitlines():
        m = re.match(r"\|\s*([A-Za-z ]+)\s*\|\s*(.+?)\s*(?:\||$)", line)
        if m and m.group(1).strip() in wanted:
            val = m.group(2).strip().strip("|").strip()
            if val and not val.startswith("---"):
                facts.append(f"{m.group(1).strip()} = {val}")
    return "\n".join(dict.fromkeys(facts)) or "(see profile file)"


def render_prompt(batch_file: Path, results_file: Path, done_flag: Path,
                  progress_file: Path, worker_id: str) -> str:
    tpl = (BASE / "worker_prompt.txt").read_text(encoding="utf-8-sig")
    try:
        bdata = json.loads(batch_file.read_text(encoding="utf-8-sig"))
    except Exception:
        bdata = {"jobs": []}
    jobs_inline = "\n".join(
        json.dumps({k: j.get(k) for k in ("id", "company", "title", "url",
                                          "portal", "location")},
                   ensure_ascii=False)
        for j in bdata.get("jobs", []))
    subs = {
        "%%STATE_DIR%%": str(BASE), "%%ROOT%%": str(ROOT),
        "%%BATCH_FILE%%": str(batch_file), "%%RESULTS_FILE%%": str(results_file),
        "%%DONE_FLAG%%": str(done_flag), "%%PROGRESS_FILE%%": str(progress_file),
        "%%PROFILE%%": str(ROOT / "Job_Profile.TEMPLATE.md"),
        "%%RESUME%%": str(ROOT / "Resume.pdf"),
        "%%HEARTBEAT%%": str(heartbeat_file()),
        "%%TRACKER%%": str(ROOT / "job-application-tracker.md"),
        "%%WORKER_ID%%": worker_id,
        "%%JOBS_INLINE%%": jobs_inline,
        "%%PROFILE_FACTS%%": _extract_profile_facts(),
        "%%JOB_BUDGET%%": str(bdata.get("job_time_budget_sec", 150)),
    }
    out = tpl
    for k, v in subs.items():
        out = out.replace(k, v)

    # Windows .cmd shims shred multi-line argv (newlines truncate at first break).
    # Ship the full prompt as a FILE; pass only a short single-line loader command.
    prompt_file = batch_file.parent / f"{batch_file.stem}.prompt.txt"
    prompt_file.write_text(out, encoding="utf-8")
    loader = (f"Read the file {prompt_file} and execute it exactly. Its instructions "
              f"are complete and self-contained; begin immediately with its FIRST ACTION "
              f"step. Do not summarize and do not reply before acting.")
    return loader


def run_batch_real(batch_id: str, worker_id: str, batch: list[dict], cls: str,
                   cfg: dict) -> dict:
    batch_file = batches_dir() / f"{batch_id}.json"
    results_file = results_dir() / f"{batch_id}.jsonl"
    done_flag = results_dir() / f"{batch_id}.done"
    progress_file = results_dir() / f"{batch_id}.progress.json"
    worker_out = p("logs") / f"{batch_id}.out.log"

    save_json(batch_file, {
        "batch_id": batch_id, "worker_id": worker_id, "portal_class": cls,
        "generated_at": now(),
        "job_time_budget_sec": cfg["job_budget_sec"],
        "jobs": [{k: j.get(k) for k in ("id", "company", "title", "url", "portal",
                                        "location", "status", "attempts")} for j in batch],
    })
    for f in (results_file, done_flag, progress_file):
        f.unlink(missing_ok=True)

    exe = shutil.which(cfg["opencode_cmd"]) or cfg["opencode_cmd"]
    argv_base = [exe] + list(cfg.get("opencode_run_extra_args", [])) + ["run"]
    if cfg.get("opencode_model"):
        argv_base += ["--model", cfg["opencode_model"]]

    timeout_cap = (cfg["batch_base_timeout_sec"]
                   + len(batch) * cfg["batch_timeout_per_job_sec"])
    outcome = {"exit_code": None, "how": "clean", "retries": 0}

    def _resulted_ids():
        s = set()
        try:
            for ln in results_file.read_text(encoding="utf-8-sig").splitlines():
                if ln.strip():
                    s.add(json.loads(ln).get("job_id"))
        except Exception:
            pass
        return {i for i in s if i}

    for attempt in range(cfg["max_retries_per_batch"] + 1):
        if attempt > 0:
            done_ids = _resulted_ids()
            if done_ids:
                batch[:] = [j for j in batch if j["id"] not in done_ids]
                save_json(batch_file, {
                    "batch_id": batch_id, "worker_id": wid, "portal_class": cls,
                    "generated_at": now(), "retry_trimmed": True,
                    "job_time_budget_sec": cfg.get("job_budget_sec", 150),
                    "jobs": [{k: j.get(k) for k in ("id", "company", "title", "url",
                             "portal", "location", "status", "attempts")} for j in batch],
                })
        wid = worker_id if attempt == 0 else f"{worker_id}_r{attempt}"
        # re-render per attempt: retries must embed their OWN worker id in the
        # prompt file, else watchdog tag mismatch kills healthy workers
        prompt = render_prompt(batch_file, results_file, done_flag, progress_file, wid)
        argv = argv_base + [prompt]
        tag = f"worker_{wid}"
        # spawn marker must NOT use the watched prefix - only worker-written lines
        # count as liveness, otherwise grace period collapses to hb_timeout
        touch_heartbeat(f"batch_{batch_id}", f"spawn {tag}")
        log_event("batch_start", note=f"{batch_id} class={cls} jobs={len(batch)} attempt={attempt}")
        refresh_checkpoint(phase="batch_active", batch_id=batch_id, worker_id=wid,
                           current_job=None)
        with open(worker_out, "ab") as fh:
            proc = subprocess.Popen(
                argv, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        deadline = time.time() + timeout_cap
        how = "clean"
        spawn_t = time.time()
        seen_hb = False

        while True:
            rc = proc.poll()
            if rc is not None:
                outcome["exit_code"] = rc
                break
            # 60s-flush contract: fold worker progress into checkpoint every poll (<=10s)
            merge_worker_progress(progress_file, wid)
            age = heartbeat_age_of(tag)
            seen_hb = seen_hb or age != float("inf")
            if seen_hb:
                stale_after = cfg["heartbeat_timeout_sec"]
            else:
                stale_after = cfg.get("first_heartbeat_grace_sec", 300)
                age = time.time() - spawn_t
            if age > stale_after:
                kill_tree(proc.pid)
                how = ("heartbeat_stale" if seen_hb else "no_first_heartbeat")
                break
            if time.time() > deadline:
                kill_tree(proc.pid)
                how = "timeout"
                break
            time.sleep(cfg["poll_interval_sec"])

        if how != "clean":
            try:
                proc.wait(timeout=15)
            except Exception:
                pass
            outcome["retries"] = attempt + 1
            outcome["how"] = how
            cp = refresh_checkpoint(phase="recovering",
                                    restart_count=(load_json(checkpoint_file(), {})
                                                   .get("restart_count", 0)) + 1,
                                    history_entry={"ts": now(), "batch_id": batch_id,
                                                   "event": "restart", "reason": how})
            cp["last_restart_reason"] = how
            save_json(checkpoint_file(), cp)
            log_event("restart", note=f"batch {batch_id} killed ({how})", restart_reason=how)
            time.sleep(5)
            continue
        break

    if outcome["how"] == "clean" and outcome["exit_code"] not in (0, None):
        outcome["how"] = f"exit_{outcome['exit_code']}"
    if outcome["how"] == "clean" and not done_flag.exists():
        outcome["how"] = "no_done_flag"
    refresh_checkpoint(phase="merging", batch_id=batch_id)
    return outcome


def merge_worker_progress(progress_file: Path, worker_id: str) -> None:
    prog = load_json(progress_file, None)
    if isinstance(prog, dict) and prog.get("job"):
        refresh_checkpoint(current_job=prog["job"], worker_id=prog.get("worker_id", worker_id))


def run_batch_simulated(batch_id: str, worker_id: str, batch: list[dict],
                        cls: str, cfg: dict) -> dict:
    results_file = results_dir() / f"{batch_id}.jsonl"
    progress_file = results_dir() / f"{batch_id}.progress.json"
    done_flag = results_dir() / f"{batch_id}.done"
    rng = random.Random(batch_id)
    touch_heartbeat(f"worker_{worker_id}", "sim spawn")
    for j in batch:
        time.sleep(0.2)
        roll = rng.random()
        if roll < 0.78:
            res, err = "submitted", ""
        elif roll < 0.85:
            res, err = "review_required", "captcha"
        elif roll < 0.90:
            res, err = "skipped", "hang_sim"
        elif roll < 0.96:
            res, err = "failed_unconfirmed", "no_confirmation_sim"
        else:
            res, err = "failed", "form_error_sim"
        line = {"job_id": j["id"], "result": res, "error": err,
                "evidence": {"confirmation": "SIMULATED"} if res == "submitted" else {}}
        with open(results_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        save_json(progress_file, {"batch_id": batch_id, "worker_id": worker_id,
                                  "job": {"id": j["id"], "url": j.get("url"),
                                          "step": "done"},
                                  "ts": now()})
        touch_heartbeat(f"worker_{worker_id}", f"done {j['id']}")
    done_flag.write_text("", encoding="utf-8")
    return {"exit_code": 0, "how": "clean", "retries": 0}


# ---------------------------------------------------------------- merge

def merge_results(batch: list[dict], results_file: Path, cfg: dict) -> dict:
    jobs = load_jobs()
    by_id = {j["id"]: j for j in jobs["jobs"]}
    got = {}
    if results_file.exists():
        for ln in results_file.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                e = json.loads(ln)
                if isinstance(e, dict) and e.get("job_id"):
                    got[e["job_id"]] = e
            except Exception:
                continue

    tally = {}
    handled = set()
    for jid, e in got.items():
        job = by_id.get(jid)
        if not job or job["status"] in TERMINAL:
            continue
        handled.add(jid)
        raw = (e.get("result") or "").lower()
        status = RESULT_TO_STATUS.get(raw, STATUS_FAILED)
        err = (e.get("error") or "").strip()
        ev = e.get("evidence") or {}
        job["updated_at"] = now()
        if err:
            job["error"] = err
        if ev:
            job["evidence"] = {**(job.get("evidence") or {}), **ev}
        tally[status] = tally.get(status, 0) + 1

        if status == STATUS_SUBMITTED:
            job["status"] = STATUS_SUBMITTED
            log_event("submitted", job_id=jid, note=err or "confirmed")
        elif status == STATUS_SKIPPED:
            job["status"] = STATUS_SKIPPED
            log_event("skipped", job_id=jid, note=err)
        elif status == STATUS_REVIEW:
            job["status"] = STATUS_REVIEW
            register_review(job, err or "unspecified")
            log_event("review_required", job_id=jid, note=err)
        elif status == STATUS_UNCONFIRMED:
            job["attempts"] = job.get("attempts", 0) + 1
            if job["attempts"] >= cfg["max_job_attempts"]:
                job["status"] = STATUS_UNCONFIRMED
                register_failed(job, err or "unconfirmed_no_evidence")
                log_event("failed_unconfirmed", job_id=jid, note="final")
            else:
                job["status"] = STATUS_PENDING
                log_event("retry_scheduled", job_id=jid, note=err or "unconfirmed")
        else:  # STATUS_FAILED
            job["attempts"] = job.get("attempts", 0) + 1
            if job["attempts"] >= cfg["max_job_attempts"]:
                job["status"] = STATUS_FAILED
                register_failed(job, err or "max_attempts")
                log_event("failed", job_id=jid, note=err or "final")
            else:
                job["status"] = STATUS_PENDING
                log_event("retry_scheduled", job_id=jid, note=err)

    for j in batch:                              # worker crashed before reporting
        job = by_id[j["id"]]
        if j["id"] in handled or job["status"] in TERMINAL:
            continue
        job["attempts"] = job.get("attempts", 0) + 1
        job["updated_at"] = now()
        if job["attempts"] >= cfg["max_job_attempts"]:
            job["status"] = STATUS_FAILED
            job["error"] = "worker_crash_no_result"
            register_failed(job, "worker_crash_no_result")
            log_event("failed", job_id=job["id"], note="no result after crash/retry")
        else:
            job["status"] = STATUS_PENDING
            job["error"] = "requeued_worker_crash"

    save_jobs(jobs)
    return tally


# ---------------------------------------------------------------- main loop

def cmd_run(cfg: dict, resume: bool, sim: bool) -> int:
    global RUN_START_TS
    ensure_dirs()
    if not Lock().acquire():
        return 2
    RUN_START_TS = now()
    if cfg.get("keep_system_awake", True) and not DRYRUN:
        keep_system_awake(True)
    try:
        jobs = load_jobs()
        requeue_in_progress(jobs, cfg)
        cur = stats(load_jobs())
        log_event("resumed" if resume else "started",
                  note=f"mode={'simulate' if sim else 'real'} cap={cfg['max_jobs_per_run']} "
                       f"queue={json.dumps(cur)}")
        refresh_checkpoint(phase="starting", restart_count=0)

        processed = 0
        batch_no = 0
        cap = cfg["max_jobs_per_run"] or 10 ** 9

        while True:
            jobs = load_jobs()
            excluded = set(cfg.get("excluded_portal_classes", []))
            pool = [j for j in jobs["jobs"] if j.get("status") == STATUS_PENDING
                    and portal_class(j.get("portal", ""),
                                     cfg["batch_size_by_portal"])[0] not in excluded]
            if not pool or processed >= cap:
                break

            pool.sort(key=lambda j: PROVIDER_RANK.get(
                portal_class(j.get("portal", ""), cfg["batch_size_by_portal"])[0], 9))
            cls, bsize = portal_class(pool[0].get("portal", ""),
                                      cfg["batch_size_by_portal"])
            take = max(1, min(len(pool), bsize, cap - processed))
            batch = pool[:take]
            batch_no += 1
            batch_id = f"b{datetime.now().strftime('%Y%m%d_%H%M%S')}_{batch_no:03d}_{cls}"
            worker_id = f"w{os.getpid()}_{batch_no}"

            for j in batch:
                j["status"] = STATUS_INPROG
                j["updated_at"] = now()
            save_jobs(jobs)

            t0 = time.time()
            runner = run_batch_simulated if sim else run_batch_real
            outcome = runner(batch_id, worker_id, batch, cls, cfg)
            took = round(time.time() - t0, 1)
            processed += take

            tally = merge_results(batch, results_dir() / f"{batch_id}.jsonl", cfg)
            fresh_stats = stats(load_jobs())
            refresh_checkpoint(
                jobs=None, phase="between_batches", batch_id=batch_id,
                current_job={"id": None, "url": None, "step": "batch_complete"},
                history_entry={"ts": now(), "batch_id": batch_id, "portal_class": cls,
                               "outcome": outcome["how"], "took_sec": took,
                               "retries": outcome["retries"], "tally": tally,
                               "stats_after": fresh_stats})
            log_event("batch_done",
                      note=f"{batch_id} how={outcome['how']} took={took}s "
                           f"tally={json.dumps(tally)}")

            remaining = fresh_stats.get(STATUS_PENDING, 0)
            if remaining and processed < cap:
                pause = pause_for(cls, cfg["pause_between_batches_sec"])
                time.sleep(min(pause, 3) if sim else pause)

        report = generate_final_report(sim=sim, cfg=cfg)
        print("\n=== OVERNIGHT RUN COMPLETE ===")
        print(json.dumps(report["totals"], indent=2))
        print(f"runtime           : {report['runtime']}")
        print(f"integrity         : {'PASS' if report['integrity']['ok'] else 'FAIL'}"
              f"  ({report['integrity']['detail']})")
        print(f"full report       : {(BASE / '_dryrun' if DRYRUN else BASE) / 'final_report.json'}")
        return 0
    finally:
        keep_system_awake(False)
        try:
            lf = lock_file()
            if lf.exists() and load_json(lf, {}).get("pid") == os.getpid():
                lf.unlink()
        except Exception:
            pass


def generate_final_report(sim: bool = False, cfg: dict | None = None) -> dict:
    cfg = cfg or dict(DEFAULTS)
    jobs = load_jobs()
    st = stats(jobs)
    excluded = set(cfg.get("excluded_portal_classes", []))
    held_for_manual = [
        {"id": j["id"], "company": j.get("company"), "portal": j.get("portal")}
        for j in jobs["jobs"]
        if j.get("status") == STATUS_PENDING
        and portal_class(j.get("portal", ""), cfg["batch_size_by_portal"])[0] in excluded
    ]
    cp = load_json(checkpoint_file(), {})
    runtime = "-"
    if cp.get("run_started_at"):
        t0 = datetime.fromisoformat(cp["run_started_at"])
        runtime = str(datetime.now() - t0).split(".")[0]

    restarts = []
    try:
        for ln in events_file().read_text(encoding="utf-8-sig").splitlines():
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get("event") == "restart":
                restarts.append({"ts": e.get("ts"), "note": e.get("note", "")[:160]})
    except Exception:
        pass
    restarts = restarts[-100:]

    # integrity: every touched job terminal, nobody submitted twice
    seen_results = {}
    for rf in results_dir().glob("*.jsonl"):
        for ln in rf.read_text(encoding="utf-8-sig").splitlines():
            try:
                e = json.loads(ln)
            except Exception:
                continue
            jid = e.get("job_id")
            if not jid:
                continue
            res = RESULT_TO_STATUS.get((e.get("result") or "").lower())
            seen_results.setdefault(jid, []).append(res)
    touched_unknown, double_submitted = [], []
    for jid, ress in seen_results.items():
        if "submitted" in ress and ress.count("submitted") > 1:
            double_submitted.append(jid)
    for j in jobs["jobs"]:
        s = j.get("status")
        if j["id"] in seen_results and s not in TERMINAL:
            touched_unknown.append({"id": j["id"], "status": s})

    integrity_ok = not touched_unknown and not double_submitted
    detail = (f"unknown_states={len(touched_unknown)} double_submitted={len(double_submitted)}")

    from collections import Counter
    portal_bd = Counter((j.get("portal") or "?").lower() for j in jobs["jobs"])
    company_bd = Counter(j.get("company", "?") for j in jobs["jobs"]
                         if j.get("status") in TERMINAL)
    age_bd = Counter()
    for j in jobs["jobs"]:
        age = (j.get("evidence") or {}).get("posted_age_days")
        if age is None:
            age_bd["unknown"] += 1
        elif age <= 7:
            age_bd["fresh_0_7d"] += 1
        elif age <= 30:
            age_bd["8_30d"] += 1
        else:
            age_bd["older"] += 1

    report = {
        "generated_at": now(),
        "mode": "simulate" if sim else "real",
        "totals": st,
        "portal_breakdown": dict(portal_bd),
        "top_companies_processed": dict(company_bd.most_common(25)),
        "posting_age_breakdown": dict(age_bd),
        "held_for_manual": held_for_manual,
        "held_reason": ("portal excluded from automation by compliance policy "
                        "(excluded_portal_classes) - prepare manually via tracker workflow"),
        "restart_history": restarts,
        "runtime": runtime,
        "checkpoint_updated_at": cp.get("updated_at"),
        "integrity": {"ok": integrity_ok, "detail": detail,
                      "unknown_state_jobs": touched_unknown,
                      "double_submitted_ids": double_submitted},
        "guarantee": ("every processed job has a recorded final state among: "
                      "submitted, failed, failed_unconfirmed, review_required, skipped"
                      ) if integrity_ok else "INTEGRITY FAILURE - inspect jobs.json",
    }
    save_json(p("final_report.json"), report)
    log_event("report_generated", note=f"integrity_ok={integrity_ok}")
    # V4 learning loop: refresh hiring intelligence after every completed run
    try:
        import subprocess as _sp
        _sp.run([sys.executable, str(BASE / "hiring_intelligence.py")],
                capture_output=True, timeout=300)
        _sp.run([sys.executable, str(BASE / "interview_prep.py")],
                capture_output=True, timeout=120)
        _sp.run([sys.executable, str(BASE / "jobops.py"), "refresh"],
                capture_output=True, timeout=300)
    except Exception:
        pass
    return report


# ---------------------------------------------------------------- dashboard

def cmd_status() -> int:
    cp = load_json(checkpoint_file(), {})
    jobs = load_jobs()
    st = stats(jobs)
    live = load_json(live_status_file(), {}) if live_status_file().exists() else {}
    wd = live.get("watchdog", {})
    cj = live.get("current_job") or {}
    hist = [h for h in cp.get("history", []) if h.get("took_sec")]
    print("=" * 62)
    print(" AUTODOSSIER APPLIER - LIVE DASHBOARD")
    print("=" * 62)
    print(f" Applied (submitted) : {st[STATUS_SUBMITTED]}")
    print(f" Pending             : {st[STATUS_PENDING]}")
    print(f" In progress         : {st[STATUS_INPROG]}")
    print(f" Failed              : {st[STATUS_FAILED]}")
    print(f" Failed unconfirmed  : {st[STATUS_UNCONFIRMED]}")
    print(f" Review required     : {st[STATUS_REVIEW]}")
    print(f" Skipped             : {st[STATUS_SKIPPED]}")
    print(f" Current batch       : {live.get('current_batch', '-')}")
    print(f" Current job         : {(cj.get('id') or '-')} "
          f"step={(cj.get('step') or '-')} url={(cj.get('url') or '-')[:70]}")
    print(f" ETA                 : {live.get('eta_minutes', '-')} min "
          f"(for {st[STATUS_PENDING]} pending)")
    print(f" Watchdog            : {wd.get('state', 'idle')} "
          f"(timeout={wd.get('heartbeat_timeout_sec')}s)")
    print(f" Heartbeat age       : {wd.get('heartbeat_age_sec', '-')}s")
    print(f" Restart count       : {wd.get('restart_count', 0)}"
          f"  last={wd.get('last_restart_reason', '-')}")
    print(f" MCP status          : {live.get('mcp_status', 'unknown')}")
    try:
        u = json.loads((BASE / "company_universe.json").read_text(encoding="utf-8-sig"))
        cs = u["companies"]
        print(f" Company Universe    : {len(cs)}/{u.get('target_size')} companies | "
              f"ATS-verified={sum(1 for c in cs if c.get('status')=='verified')} | "
              f"scanned_last={sum(1 for c in cs if c.get('last_scan'))}")
    except Exception:
        pass
    print(f" Phase               : {live.get('phase', 'idle')}")
    print(f" Runtime             : {live.get('run_started_at', '-')}")
    if hist:
        last = hist[-1]
        print(f" Last batch          : {last.get('batch_id')} took={last.get('took_sec')}s "
              f"tally={json.dumps(last.get('tally', {}))}")
    print("=" * 62)
    return 0


# ---------------------------------------------------------------- audit

def cmd_audit() -> int:
    ensure_dirs()
    checks = []

    def check(name, ok, note=""):
        checks.append((name, bool(ok), note))

    jobs_data = load_json(jobs_file(), None)
    check("jobs.json parses + has queue", bool(jobs_data and jobs_data.get("jobs")),
          f"{len((jobs_data or {}).get('jobs', []))} rows")
    jobs = load_jobs()          # triggers alias migration
    check("status vocabulary migrated", all(
        j.get("status") in (TERMINAL | {STATUS_PENDING, STATUS_INPROG}) for j in jobs["jobs"]))

    cp = load_json(checkpoint_file(), None)
    check("checkpoint integrity", isinstance(cp, dict),
          f"v{cp.get('version', '?')} updated={cp.get('updated_at')}")

    urls, dupes = {}, []
    for j in jobs["jobs"]:
        u = (j.get("url") or "").split("?")[0].rstrip("/").lower()
        if u:
            if u in urls:
                dupes.append(j["id"])
            else:
                urls[u] = j["id"]
    if dupes:
        bak = BASE / "jobs.pre_dedupe_backup.json"
        if not DRYRUN:
            shutil.copy2(jobs_file(), bak)
        before = len(jobs["jobs"])
        jobs["jobs"] = [j for i, j in enumerate(jobs["jobs"])
                        if ((j.get("url") or "").split("?")[0].rstrip("/").lower() not in urls
                            or urls[(j.get("url") or "").split("?")[0].rstrip("/").lower()] == j["id"])]
        save_jobs(jobs)
        log_event("dedupe", note=f"removed {before - len(jobs['jobs'])} duplicate rows",
                  removed=dupes)
    check("duplicate removal", True, f"removed={len(dupes)} ids={dupes}")

    orphans = [j["id"] for j in jobs["jobs"] if j.get("status") == STATUS_INPROG]
    if orphans:
        requeue_in_progress(jobs, dict(DEFAULTS, max_job_attempts=2))
    check("no orphan in_progress", True,
          f"found={len(orphans)} requeued={orphans}")

    st = stats(load_jobs())
    check("pending count", True, str(st[STATUS_PENDING]))
    check("applied/submitted count", True, str(st[STATUS_SUBMITTED]))
    check("failed count", True, f"{st[STATUS_FAILED]}+{st[STATUS_UNCONFIRMED]}unconf")
    check("review_required count", True, str(st[STATUS_REVIEW]))

    rv = load_json(review_file(), None)
    check("review_queue.json valid", isinstance(rv, dict) and "items" in rv,
          f"{len((rv or {}).get('items', []))} items")
    fj = load_json(failed_file(), None)
    check("failed_jobs.json valid", isinstance(fj, dict) and "items" in fj,
          f"{len((fj or {}).get('items', []))} items")
    check("heartbeat.log writable", heartbeat_file().exists())
    check("results/ + batches/ dirs", batches_dir().exists() and results_dir().exists())

    unknown = [j["id"] for j in load_jobs()["jobs"]
               if j.get("status") not in TERMINAL | {STATUS_PENDING, STATUS_INPROG}]
    check("no unknown job states", not unknown, str(unknown))

    print("\nAUDIT (static):")
    fails = 0
    for name, ok, note in checks:
        mark = "PASS" if ok else "FAIL"
        fails += 0 if ok else 1
        print(f"  [{mark}] {name}" + (f"  -> {note}" if note else ""))
    print("\nDynamic gates run separately: --selftest-watchdog, opencode smoke test, MCP probe.")
    return 1 if fails else 0


# ---------------------------------------------------------------- misc cmds

def cmd_seed(src: str) -> None:
    rows = json.loads(Path(src).read_text(encoding="utf-8-sig"))
    smap = {"SUBMITTED": STATUS_SUBMITTED, "APPLIED": STATUS_SUBMITTED}
    jobs = []
    for i, r in enumerate(rows, 1):
        evidence = {k: r[k] for k in ("submissionDate", "submissionMethod",
                                      "submissionConfirmation", "failureReason", "notes")
                    if r.get(k)}
        st_raw = (r.get("status") or "").upper()
        status = smap.get(st_raw, STATUS_REVIEW if st_raw == "PENDING_HUMAN"
                          else STATUS_PENDING)
        jobs.append({
            "id": r.get("queueId") or f"job-{i:04d}",
            "company": r.get("company", ""), "title": r.get("role", ""),
            "url": r.get("jobUrl") or r.get("canonicalUrl", ""),
            "portal": (r.get("platform") or "generic").lower(),
            "location": r.get("location", ""), "match_score": r.get("matchScore"),
            "status": status, "attempts": 0, "error": None,
            "evidence": evidence, "updated_at": None,
        })
    existing = load_jobs()
    have = {j["id"] for j in existing["jobs"]}
    merged = existing["jobs"] + [j for j in jobs if j["id"] not in have]
    save_jobs({"jobs": merged})
    refresh_checkpoint(history_entry={"ts": now(), "event_note": "seeded", "total": len(merged)})
    print(f"seeded {len(jobs)} rows; total queue={len(merged)}")


def cmd_selftest_watchdog(cfg: dict) -> int:
    ensure_dirs()
    argv = [sys.executable, "-c", "import time; time.sleep(999)"]
    t0 = time.time()
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    killed = False
    hb_to = min(cfg.get("heartbeat_timeout_sec", 120), 8)
    while True:
        if proc.poll() is not None:
            break
        if heartbeat_age_of("selftest_never_written") > hb_to:
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=15)
            except Exception:
                pass
            killed = True
            break
        time.sleep(1)
    dt = round(time.time() - t0, 1)
    ok = killed and proc.returncode not in (0, None)
    log_event("selftest_watchdog", note=f"{'pass' if ok else 'FAIL'} rc={proc.returncode}")
    print(f"watchdog selftest: {'PASS' if ok else 'FAIL'} "
          f"(hung worker detected+tree-killed, exit={proc.returncode})")
    return 0 if ok else 1


# ---------------------------------------------------------------- cli

def load_cfg(args) -> dict:
    import copy
    cfg = copy.deepcopy(DEFAULTS)
    disk = {} if DRYRUN else load_json(cfg_path(), {})
    for k, v in disk.items():
        if k in cfg:
            cfg[k] = v
    if args.max_jobs is not None:
        cfg["max_jobs_per_run"] = args.max_jobs
    return cfg


def main() -> int:
    global DRYRUN
    ap = argparse.ArgumentParser(description="AutoApply overnight supervisor v2")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--audit", action="store_true", help="static integrity audit + repair")
    ap.add_argument("--seed-from", metavar="SRC")
    ap.add_argument("--dry-run", action="store_true", help="simulation into _dryrun/")
    ap.add_argument("--selftest-watchdog", action="store_true")
    ap.add_argument("--max-jobs", type=int, help="override cap for this run")
    args = ap.parse_args()

    DRYRUN = bool(args.dry_run)

    if args.seed_from:
        ensure_dirs()
        cmd_seed(args.seed_from)
        return 0
    if args.status:
        return cmd_status()
    if args.audit:
        return cmd_audit()
    if args.selftest_watchdog:
        return cmd_selftest_watchdog(load_cfg(args))

    cfg = load_cfg(args)
    return cmd_run(cfg, resume=bool(args.resume), sim=args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[interrupt] stopped; in_progress jobs requeue automatically next boot.")
        sys.exit(130)
