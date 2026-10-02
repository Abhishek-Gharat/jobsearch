#!/usr/bin/env python3
"""
pipeline.py — Master Orchestrator for the Complete Job Intelligence & Application System.

Commands:
  python pipeline.py status                   # Display complete system dashboard & health
  python pipeline.py sync                     # Unify, clean, enrich, and deduplicate all queues
  python pipeline.py discover [--endpoints N] # Multi-source discovery (ATS APIs + web search)
  python pipeline.py verify [--limit N]       # Fast pre-flight verification of live vacancies
  python pipeline.py run [--jobs N] [--dry-run] [--engine {browseros, fortress}] # Execute applications
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import queue_store
import unified_queue
import discovery_engine
import verifier
import run_two_agent_pipeline

EXCEL_ROWS = ROOT / "excel-rows.json"
SESSION_SUMMARY = ROOT / "agent_state" / "session_summary.json"

def show_status():
    print("\n" + "=" * 65)
    print("JOB INTELLIGENCE & APPLICATION SYSTEM — STATUS DASHBOARD")
    print("=" * 65)

    if not EXCEL_ROWS.exists():
        print("[ERROR] Master queue excel-rows.json not found.")
        return

    jobs = queue_store.load(EXCEL_ROWS, quiet=True)
    status_counts = Counter(j.get("status", "UNKNOWN") for j in jobs)
    contact_count = sum(1 for j in jobs if any([j.get("recruiterName"), j.get("recruiterEmail"), j.get("recruiterPhone"), j.get("linkedinUrl")]))
    contact_types = Counter(j.get("contactType") for j in jobs if j.get("contactType"))

    print(f"\n1. CANONICAL QUEUE: {len(jobs)} Total Jobs")
    print(f"   - UNPROCESSED / PENDING : {status_counts.get('UNPROCESSED', 0) + status_counts.get('NOT_PROCESSED', 0)}")
    print(f"   - READY (Verified Live) : {status_counts.get('READY', 0)}")
    print(f"   - PENDING_HUMAN Checkpt : {status_counts.get('PENDING_HUMAN', 0)}")
    print(f"   - SUBMITTED (Applied)   : {status_counts.get('SUBMITTED', 0)}")
    print(f"   - SKIPPED (Closed/Exp)  : {status_counts.get('SKIPPED', 0)}")
    print(f"   - FAILED                : {status_counts.get('FAILED', 0)}")

    print(f"\n2. RECRUITER & CONTACT INTELLIGENCE:")
    print(f"   - Total Jobs with Contacts: {contact_count}")
    for ctype, cnt in contact_types.most_common():
        print(f"     * {ctype:28}: {cnt}")

    if SESSION_SUMMARY.exists():
        try:
            with open(SESSION_SUMMARY, "r", encoding="utf-8") as f:
                sess = json.load(f)
            print(f"\n3. RECENT EXECUTION SESSION:")
            print(f"   - Current Phase : {sess.get('current_phase', 'IDLE')}")
            print(f"   - Active Job ID : {sess.get('current_job_id', 'None')}")
            print(f"   - Last Action   : {sess.get('last_successful_action', 'None')}")
            print(f"   - Completed     : {sess.get('completed_jobs', 0)} | Checkpoints: {sess.get('pending_human_jobs', 0)}")
        except Exception:
            pass

    print("\n" + "=" * 65 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Master Job Intelligence & Application Orchestrator")
    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", help="Show system status and queue statistics")

    # sync
    subparsers.add_parser("sync", help="Synchronize and normalize all queue sources")

    # discover
    p_disc = subparsers.add_parser("discover", help="Discover fresh opportunities from ATS APIs & web")
    p_disc.add_argument("--endpoints", type=int, default=50, help="Number of ATS endpoints to scan")

    # verify
    p_ver = subparsers.add_parser("verify", help="Run pre-flight verification on unprocessed vacancies")
    p_ver.add_argument("--limit", type=int, default=30, help="Number of vacancies to verify")
    p_ver.add_argument("--dry-run", action="store_true", help="Preview verification without writing")

    # run
    p_run = subparsers.add_parser("run", help="Run two-agent planner and executor pipeline")
    p_run.add_argument("--jobs", type=int, default=1, help="Max jobs to process sequentially")
    p_run.add_argument("--dry-run", action="store_true", help="Verify without submitting")
    p_run.add_argument("--headed", action="store_true", help="Open visible browser window to watch live on screen")
    p_run.add_argument("--engine", choices=("browseros", "fortress"), default=None, help="Browser engine")

    args = parser.parse_args()

    if args.command == "status" or args.command is None:
        show_status()
    elif args.command == "sync":
        unified_queue.sync_all_queues(commit=True)
        show_status()
    elif args.command == "discover":
        discovery_engine.run_discovery_cycle(limit_endpoints=args.endpoints)
        show_status()
    elif args.command == "verify":
        verifier.verify_queue(limit=args.limit, dry_run=args.dry_run)
        show_status()
    elif args.command == "run":
        if args.engine:
            os.environ["BROWSER_ENGINE"] = args.engine
        if args.headed:
            os.environ["FORTRESS_HEADLESS"] = "0"
            print("[PIPELINE] Visible browser enabled: Chromium window will display on screen.")
        run_two_agent_pipeline.run_pipeline(
            max_jobs=args.jobs,
            dry_run=args.dry_run
        )
        show_status()

if __name__ == "__main__":
    main()
