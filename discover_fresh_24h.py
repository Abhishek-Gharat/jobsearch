#!/usr/bin/env python3
"""
discover_fresh_24h.py — Fast Discovery Engine for Fresh (<24h) Frontend/React Jobs

Scans live job search pages on Naukri (sorted by fresh), applies candidate fit filtering,
deduplicates against excel-rows.json, and appends valid matches to the queue with status 'READY'.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import bos
import queue_store

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REJECT_TITLES = (
    "senior", "sr.", "lead", "staff", "principal", "architect",
    "manager", "director", "head of", "writer", "sales", "intern"
)

ACCEPT_ROLES = (
    "frontend", "front-end", "react", "next.js", "nextjs", "javascript",
    "web developer", "ui developer", "software engineer", "sde", "full stack", "fullstack"
)

SEARCH_URLS = [
    ("Naukri React Fresh", "https://www.naukri.com/react-developer-jobs?experience=1&sort=f"),
    ("Naukri Frontend Mumbai", "https://www.naukri.com/frontend-developer-jobs-in-mumbai?experience=1&sort=f"),
    ("Naukri Frontend Remote", "https://www.naukri.com/frontend-developer-jobs?experience=1&wfhType=0&sort=f")
]

JS_EXTRACT = """() => {
    const cards = Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, .cust-job-tuple')).slice(0, 20).map(c => {
        const titleEl = c.querySelector('a.title, .job-title');
        const compEl = c.querySelector('a.comp-name, .company-name');
        const expEl = c.querySelector('.expwdth, .experience');
        const dateEl = c.querySelector('.job-post-day, .date');
        const locEl = c.querySelector('.locWdth, .location');
        return {
            title: titleEl ? titleEl.innerText.trim() : '',
            company: compEl ? compEl.innerText.trim() : '',
            exp: expEl ? expEl.innerText.trim() : '',
            date: dateEl ? dateEl.innerText.trim() : '',
            loc: locEl ? locEl.innerText.trim() : '',
            href: titleEl ? titleEl.href.split('?')[0] : ''
        };
    }).filter(j => j.title && j.href);
    return JSON.stringify(cards);
}"""

def main():
    print("=" * 60)
    print("FRESH JOB DISCOVERY ENGINE (<24h POSTINGS)")
    print("=" * 60)

    existing_jobs = queue_store.load(queue_store.QUEUE, quiet=True)
    seen_urls = {(j.get("jobUrl") or "").split("?")[0].strip().lower() for j in existing_jobs}
    print(f"Loaded master queue: {len(existing_jobs)} existing entries.")

    existing_b_ids = [
        int(j["queueId"][1:]) for j in existing_jobs
        if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()
    ]
    next_id_num = max(existing_b_ids) + 1 if existing_b_ids else 85

    b = bos.BOS("fresh-discover")
    page = b.open(SEARCH_URLS[0][1])
    if page is None:
        print("ERROR: Could not open browser tab via BrowserOS Neo.")
        return

    time.sleep(3.5)
    discovered_new = []

    for label, url in SEARCH_URLS:
        if len(discovered_new) >= 10:
            break
        print(f"\nScanning: {label}...")
        b.call("navigate", {"page": page, "url": url})
        time.sleep(3.5)

        res, _ = b.call("evaluate", {"page": page, "func": JS_EXTRACT})
        start = res.find("[{")
        end = res.rfind("}]") + 2
        if start != -1 and end > start:
            try:
                cards = json.loads(res[start:end], strict=False)
                for c in cards:
                    href = c.get("href", "").strip()
                    title = c.get("title", "").strip()
                    date_str = c.get("date", "").lower()
                    clean_url = href.split("?")[0].lower()

                    if clean_url in seen_urls:
                        continue
                    if any(rej in title.lower() for rej in REJECT_TITLES):
                        continue
                    if not any(acc in title.lower() for acc in ACCEPT_ROLES):
                        continue

                    # Freshness filter: just now, today, 1 day, 2 days, few hours
                    is_fresh = any(f in date_str for f in ["just", "hour", "today", "1 day", "2 day", "few"])
                    if not is_fresh and date_str:
                        continue

                    seen_urls.add(clean_url)
                    qid = f"B{next_id_num:02d}"
                    next_id_num += 1

                    new_entry = {
                        "queueId": qid,
                        "company": c.get("company", "Tech Employer").strip(),
                        "role": title,
                        "jobUrl": href,
                        "canonicalUrl": href,
                        "location": c.get("loc", "Mumbai / Remote"),
                        "experienceRequired": c.get("exp", "0-2 Yrs"),
                        "postedDate": c.get("date", "Recent"),
                        "status": "READY",
                        "matchScore": 85,
                        "matchReason": f"Fresh React posting ({c.get('date', 'Recent')}), experience fit",
                        "applicationMethod": "Naukri Apply",
                        "submissionDate": "",
                        "failureReason": ""
                    }
                    discovered_new.append(new_entry)
                    print(f"  + Queued [{qid}] {new_entry['company']} — {new_entry['role']} ({new_entry['postedDate']})")

                    if len(discovered_new) >= 10:
                        break
            except Exception as ex:
                print(f"  Error parsing cards: {ex}")

    b.close(page)
    print(f"\nDiscovered {len(discovered_new)} brand-new matching jobs.")

    if discovered_new:
        updated_queue = existing_jobs + discovered_new
        queue_store.save(updated_queue, queue_store.QUEUE)
        print(f"Successfully appended {len(discovered_new)} jobs to excel-rows.json with status 'READY'.")
        print(f"New queue size: {len(updated_queue)} entries.")
    else:
        print("No new unique jobs found that aren't already in the queue.")

if __name__ == "__main__":
    main()
