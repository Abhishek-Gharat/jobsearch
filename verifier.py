#!/usr/bin/env python3
"""
verifier.py — Pre-Flight Live Vacancy Verifier & Status Assessor.

Responsibilities:
1. Fast, lightweight HTTP pre-flight checks (headers, redirect detection, status codes).
2. Content verification: scans page body for dead phrases ("position filled", "no longer accepting", "closed").
3. Login-wall detection: flags pages requiring candidate authentication (e.g., Darwinbox/Workday candidate login).
4. Updates canonical queue (excel-rows.json) atomically:
   - Live & open -> marks 'READY'
   - Closed / 404 -> marks 'SKIPPED' (with explicit reason)
   - Login / CAPTCHA wall -> marks 'PENDING_HUMAN'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXCEL_ROWS = ROOT / "excel-rows.json"

import queue_store
import unified_queue

CLOSED_PHRASES = (
    "no longer accepting applications",
    "position has been filled",
    "this job has been closed",
    "job is no longer available",
    "this position is no longer open",
    "applications have closed",
    "job has expired",
    "the job you are looking for is no longer open",
    "this requisition is closed",
    "job not found",
    "404 not found"
)

LOGIN_WALL_PHRASES = (
    "candidate/login",
    "sign in to apply",
    "login to apply",
    "create an account to apply"
)

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def verify_single_url(url: str, timeout: int = 10) -> tuple[str, str]:
    """
    Returns (status, reason):
    - ('LIVE', 'HTTP 200 and open phrases detected')
    - ('CLOSED', 'Dead phrase or 404 detected')
    - ('LOGIN_REQUIRED', 'Redirected to login wall')
    - ('UNCERTAIN', 'Could not determine via fast HTTP')
    """
    if not url or len(url) < 15:
        return "CLOSED", "Invalid or missing URL"

    req = urllib.request.Request(url, headers=UA_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl().lower()
            code = resp.getcode()

            if any(lw in final_url for lw in LOGIN_WALL_PHRASES):
                return "LOGIN_REQUIRED", f"Redirected to candidate login wall: {final_url}"

            content = resp.read().decode("utf-8", "ignore").lower()

            for cp in CLOSED_PHRASES:
                if cp in content:
                    return "CLOSED", f"Observed closed phrase: '{cp}'"

            return "LIVE", "Page verified live and accessible"

    except urllib.error.HTTPError as he:
        if he.code in (404, 410):
            return "CLOSED", f"HTTP {he.code} Not Found"
        if he.code in (401, 403):
            return "LOGIN_REQUIRED", f"HTTP {he.code} Access Restricted"
        return "UNCERTAIN", f"HTTP {he.code}"
    except Exception as e:
        return "UNCERTAIN", str(e)

def verify_queue(limit: int = 50, dry_run: bool = False) -> dict:
    print("\n" + "=" * 65)
    print("PRE-FLIGHT LIVE VACANCY VERIFIER")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE UPDATE'} | Limit: {limit}")
    print("=" * 65)

    jobs = queue_store.load(EXCEL_ROWS, quiet=True)
    unprocessed = [
        j for j in jobs
        if j.get("status") in ("UNPROCESSED", "NOT_PROCESSED", "PENDING")
        and len(j.get("jobUrl", "")) > 20
    ][:limit]

    print(f"[VERIFIER] Checking {len(unprocessed)} unprocessed jobs...")

    results = {"LIVE": 0, "CLOSED": 0, "LOGIN_REQUIRED": 0, "UNCERTAIN": 0}
    updates = {}

    def worker(job):
        qid = job.get("queueId")
        url = job.get("jobUrl") or job.get("canonicalUrl")
        comp = job.get("company")
        status, reason = verify_single_url(url)
        return qid, comp, url, status, reason

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(worker, j): j for j in unprocessed}
        for fut in as_completed(futures):
            qid, comp, url, status, reason = fut.result()
            results[status] += 1
            updates[qid] = (status, reason)
            print(f"  [{qid}] {comp} -> {status} ({reason[:60]})")

    print(f"\n[VERIFIER] Scan finished in {time.time()-t0:.1f}s. Results: {results}")

    if not dry_run and updates:
        for j in jobs:
            qid = j.get("queueId")
            if qid in updates:
                st, rsn = updates[qid]
                if st == "CLOSED":
                    j["status"] = "SKIPPED"
                    j["failureReason"] = rsn
                elif st == "LOGIN_REQUIRED":
                    j["status"] = "PENDING_HUMAN"
                    j["failureReason"] = rsn
                elif st == "LIVE":
                    j["status"] = "READY"
                    j["matchReason"] = (j.get("matchReason", "") + f" | Verified live ({rsn})").strip(" |")

        queue_store.save(jobs, EXCEL_ROWS)
        print(f"[VERIFIER] Updated {len(updates)} records in {EXCEL_ROWS}")

    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30, help="Number of jobs to verify")
    parser.add_argument("--dry-run", action="store_true", help="Preview verification without saving")
    args = parser.parse_args()

    verify_queue(limit=args.limit, dry_run=args.dry_run)
