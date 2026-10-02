#!/usr/bin/env python3
"""
run_two_agent_pipeline.py — Two-Agent Orchestrator

Coordinates Agent 1 (Planner/Researcher) and Agent 2 (Executor) via lightweight
shared state files in D:\\newjobs\\agent_state\\.

Usage:
  python run_two_agent_pipeline.py --dry-run          # Dry-run test on 1 job (no submit)
  python run_two_agent_pipeline.py --jobs 3           # Process up to 3 jobs sequentially
  python run_two_agent_pipeline.py --plan-only        # Run Agent 1 only
  python run_two_agent_pipeline.py --execute-only     # Run Agent 2 on active job_state.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import agent1_planner
import agent2_executor

STATE_DIR = ROOT / "agent_state"
JOB_STATE = STATE_DIR / "job_state.json"
SESSION_SUMMARY = STATE_DIR / "session_summary.json"

def run_pipeline(max_jobs: int = 1, dry_run: bool = False, plan_only: bool = False, execute_only: bool = False):
    print("=" * 65)
    print("TWO-AGENT JOB APPLICATION ORCHESTRATOR")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'} | Max Jobs: {max_jobs}")
    print("=" * 65)

    if execute_only:
        print("[ORCHESTRATOR] Running Agent 2 on existing job_state.json...")
        outcome, reason = agent2_executor.run_executor(dry_run=dry_run)
        print(f"\n[ORCHESTRATOR] Result: {outcome} | {reason}")
        return

    processed = 0
    seen_ids = set()
    for idx in range(1, max_jobs + 1):
        print(f"\n>>> PROCESSING CYCLE {idx} of {max_jobs} <<<")

        # Step 1: Agent 1 (Planner / Researcher)
        task = agent1_planner.run_planner(exclude_ids=seen_ids)
        if not task:
            print("[ORCHESTRATOR] No further jobs selected by Agent 1. Stopping.")
            break

        seen_ids.add(task.get("queue_id"))

        if plan_only:
            print("[ORCHESTRATOR] --plan-only specified. Task created, stopping.")
            processed += 1
            break

        # Step 2: Agent 2 (Executor)
        outcome, reason = agent2_executor.run_executor(dry_run=dry_run)
        processed += 1

        # Safeguard: ensure status is updated in queue if still unprocessed
        qid = task.get("queue_id")
        if outcome and qid and not dry_run:
            try:
                import queue_store
                jobs = queue_store.load(queue_store.QUEUE, quiet=True)
                for j in jobs:
                    if j.get("queueId") == qid and str(j.get("status", "")).upper() in ("UNPROCESSED", "NOT_PROCESSED", "READY", "PENDING", ""):
                        j["status"] = outcome
                        j["failureReason"] = reason
                        queue_store.save(jobs, queue_store.QUEUE)
                        break
            except Exception:
                pass

        print(f"\n[ORCHESTRATOR] Cycle {idx} finished with outcome: {outcome} ({reason})")

        # Session summary report
        if SESSION_SUMMARY.exists():
            with open(SESSION_SUMMARY, "r", encoding="utf-8") as f:
                sess = json.load(f)
            print(f"[SESSION SUMMARY] Completed: {sess.get('completed_jobs', 0)} | "
                  f"Pending Human: {sess.get('pending_human_jobs', 0)} | "
                  f"Skipped: {sess.get('skipped_jobs', 0)} | "
                  f"Failed: {sess.get('failed_jobs', 0)}")

        if idx < max_jobs:
            time.sleep(2)

    print("\n" + "=" * 65)
    print(f"ORCHESTRATION RUN COMPLETED: {processed} job(s) processed.")
    print("=" * 65)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode (stops before submit)")
    parser.add_argument("--jobs", type=int, default=1, help="Max jobs to process (default: 1)")
    parser.add_argument("--plan-only", action="store_true", help="Run Agent 1 only")
    parser.add_argument("--execute-only", action="store_true", help="Run Agent 2 only")
    parser.add_argument("--headed", action="store_true", help="Launch visible browser window to watch live on screen")
    parser.add_argument("--engine", choices=("browseros", "fortress"), default=None,
                        help="Browser engine: browseros (default MCP browser) or fortress (stealth Chromium)")
    args = parser.parse_args()

    if args.headed:
        os.environ["FORTRESS_HEADLESS"] = "0"
        print("[ORCHESTRATOR] Visible browser enabled: Chromium window will display on screen.")

    if args.engine:
        os.environ["BROWSER_ENGINE"] = args.engine
        print(f"[ORCHESTRATOR] BROWSER_ENGINE={args.engine}")

    run_pipeline(
        max_jobs=args.jobs,
        dry_run=args.dry_run,
        plan_only=args.plan_only,
        execute_only=args.execute_only
    )
