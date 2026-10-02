#!/usr/bin/env python3
"""
discovery_engine.py — Unified Multi-Source Public Job Discovery Engine.

Combines:
1. Direct ATS REST APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee)
   across 300+ verified Indian and global tech companies.
2. DuckDuckGo public web search for obscure startups, unlisted career pages,
   and recruiter hiring posts.
3. Automated contact extraction (recruiter email, phone, LinkedIn profile)
   from public post texts and snippets.
4. Direct ingestion into the canonical master queue (excel-rows.json) via queue_store.py.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UNIVERSE = ROOT / "autoapply" / "company_universe.json"
EXCEL_ROWS = ROOT / "excel-rows.json"

import queue_store
import unified_queue

# Filters matching Alex's target profile
ROLE_MATCH = re.compile(
    r"\b(frontend|front-end|react|next\.?js|javascript|web developer|ui developer|full.?stack|sde\s*1|sde-1|sde\s*i\b|software engineer|software developer)\b",
    re.I
)
ROLE_REJECT = re.compile(
    r"\b(senior|sr\.|sr\b|lead|staff|principal|architect|manager|head of|director|vp|vice president|[3-9]\+?\s*(?:years|yrs|year))\b",
    re.I
)
LOC_OK = re.compile(
    r"(remote|india|mumbai|pune|bangalore|bengaluru|hyderabad|delhi|noida|gurgaon|gurugram|chennai|ahmedabad|kolkata|hybrid|anywhere)",
    re.I
)
LOC_BAD = re.compile(
    r"(united states|usa\b|new york|san francisco|california|london|uk\b|germany|canada|australia|singapore|dubai|uae|poland|netherlands)",
    re.I
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+91[\s\-]?|91[\s\-])?[6-9]\d{4}[\s\-]?\d{5}")

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers=UA_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None

def fetch_ats_jobs(company_info: dict) -> list[dict]:
    prov = company_info.get("ats")
    url = company_info.get("endpoint") or ""
    name = company_info.get("name", "Unknown")
    slug = company_info.get("slug_used") or company_info.get("name", "").lower()

    if not prov or not url:
        return []

    data = get_json(url, timeout=12)
    if not data:
        return []

    results = []

    def check_and_add(title, loc, jurl, posted_raw=None):
        if not title or not jurl:
            return
        t = title.strip()
        l = (loc or "").strip()
        if not ROLE_MATCH.search(t) or ROLE_REJECT.search(t):
            return
        if LOC_BAD.search(l) and not LOC_OK.search(l):
            return

        results.append({
            "company": name,
            "role": t,
            "jobUrl": jurl,
            "location": l or "India / Remote",
            "platform": prov.title(),
            "sourceType": "ATS_API",
            "matchScore": 85 if "react" in t.lower() else 75,
            "matchReason": f"Matched via {prov.title()} official API"
        })

    try:
        if prov == "lever" and isinstance(data, list):
            for jb in data:
                cat = jb.get("categories") or {}
                jurl = f"https://jobs.lever.co/{slug}/{jb.get('id')}"
                check_and_add(jb.get("text"), cat.get("location"), jurl)

        elif prov == "greenhouse" and isinstance(data, dict):
            for jb in data.get("jobs", []):
                loc = (jb.get("location") or {}).get("name")
                jurl = f"https://boards.greenhouse.io/{slug}/jobs/{jb.get('id')}"
                check_and_add(jb.get("title"), loc, jurl)

        elif prov == "ashby" and isinstance(data, dict):
            for jb in data.get("jobs", []):
                check_and_add(jb.get("title"), jb.get("location"), jb.get("jobUrl"))

        elif prov == "smartrecruiters" and isinstance(data, dict):
            for jb in data.get("content", []):
                l = jb.get("location") or {}
                loc = ", ".join(filter(None, [l.get("city"), l.get("region"), l.get("country")]))
                jurl = f"https://jobs.smartrecruiters.com/{name}/{jb.get('id')}"
                check_and_add(jb.get("name"), loc, jurl)

        elif prov == "recruitee" and isinstance(data, dict):
            for jb in data.get("offers", []):
                check_and_add(jb.get("title"), jb.get("location"), jb.get("careers_url"))

    except Exception:
        pass

    return results

def search_duckduckgo(query: str, max_results: int = 15) -> list[dict]:
    """Scrapes DuckDuckGo HTML for fresh job links, titles, and snippets."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers=UA_HEADERS)
    found = []

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read().decode("utf-8", "ignore")

        # Extract result blocks
        matches = re.findall(
            r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            content,
            re.DOTALL
        )
        if not matches:
            # Fallback regex for DDG links
            raw_links = re.findall(r'uddg=([^&"\']+)', content)
            raw_snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', content, re.DOTALL)
            matches = list(zip([urllib.parse.unquote(l) for l in raw_links], raw_snippets))

        for link_target, snippet_raw in matches[:max_results]:
            clean_link = link_target
            if "uddg=" in clean_link:
                m = re.search(r'uddg=([^&]+)', clean_link)
                if m:
                    clean_link = urllib.parse.unquote(m.group(1))

            snippet = html.unescape(re.sub(r'<[^>]+>', ' ', snippet_raw)).strip()

            # Skip aggregators / noisy search roots
            if any(bad in clean_link for bad in ("switchly.in", "indeed.com", "simplyhired")):
                continue

            # Extract contact if present in snippet
            email_m = EMAIL_RE.search(snippet)
            phone_m = PHONE_RE.search(snippet)

            contact_email = email_m.group(0) if email_m else ""
            contact_phone = phone_m.group(0) if phone_m else ""

            # Check fit
            if ROLE_MATCH.search(snippet) and not ROLE_REJECT.search(snippet):
                # Infer company and title
                company_match = re.search(r'at\s+([A-Za-z0-9\s&.-]{2,30})', snippet)
                comp = company_match.group(1).strip() if company_match else "Discovered Startup"

                title = "Frontend Developer"
                if "react" in snippet.lower():
                    title = "React Developer"
                elif "next" in snippet.lower():
                    title = "Next.js Developer"

                found.append({
                    "company": comp,
                    "role": title,
                    "jobUrl": clean_link,
                    "location": "India / Remote",
                    "platform": "Web Search",
                    "sourceType": "WEB_SEARCH",
                    "matchScore": 80,
                    "matchReason": f"Discovered via search: '{snippet[:120]}...'",
                    "recruiterEmail": contact_email,
                    "recruiterPhone": contact_phone,
                    "contactType": "RECRUITER_EMAIL" if contact_email else ("RECRUITER_PHONE" if contact_phone else ""),
                    "contactEvidence": snippet[:200]
                })

    except Exception as e:
        print(f"[DISCOVERY] DuckDuckGo search error for '{query[:30]}...': {e}")

    return found

def run_discovery_cycle(limit_endpoints: int | None = None) -> list[dict]:
    print("\n" + "=" * 65)
    print("UNIFIED JOB DISCOVERY & CONTACT HARVESTER")
    print("=" * 65)

    all_discovered = []

    # 1. Harvest ATS Endpoints
    if UNIVERSE.exists():
        try:
            with open(UNIVERSE, "r", encoding="utf-8-sig") as f:
                cu = json.load(f)
            endpoints = [c for c in cu.get("companies", []) if c.get("endpoint")]
            if limit_endpoints:
                endpoints = endpoints[:limit_endpoints]

            print(f"[DISCOVERY] Scanning {len(endpoints)} verified company ATS endpoints in parallel...")
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = {executor.submit(fetch_ats_jobs, c): c for c in endpoints}
                for fut in as_completed(futures):
                    try:
                        res = fut.result()
                        if res:
                            all_discovered.extend(res)
                    except Exception:
                        pass
            print(f"[DISCOVERY] ATS scan completed in {time.time()-t0:.1f}s. Found {len(all_discovered)} potential jobs.")
        except Exception as e:
            print(f"[DISCOVERY] Error loading company universe: {e}")

    # 2. Scrape DuckDuckGo for startup hiring posts & unlisted career openings
    search_queries = [
        'React Developer hiring "send resume" India 2026',
        'Frontend Developer "we are hiring" Mumbai Pune remote',
        'Junior React Developer immediate joiner India',
        'site:greenhouse.io "Frontend" "India" -senior',
        'site:lever.co "React" "India" -senior',
        'site:ashbyhq.com "Frontend" "India"'
    ]

    print(f"[DISCOVERY] Executing {len(search_queries)} search discovery passes...")
    for q in search_queries:
        ddg_results = search_duckduckgo(q)
        print(f"  Query: '{q[:40]}...' -> {len(ddg_results)} matching leads")
        all_discovered.extend(ddg_results)
        time.sleep(1.0)

    print(f"\n[DISCOVERY] Total raw discovered opportunities: {len(all_discovered)}")

    # 3. Deduplicate and ingest into canonical queue
    existing_jobs = queue_store.load(EXCEL_ROWS, quiet=True)
    existing_urls = {unified_queue.clean_url(j.get("jobUrl") or j.get("canonicalUrl") or "") for j in existing_jobs}
    existing_cr = {( (j.get("company") or "").strip().lower(), unified_queue.clean_role(j.get("role") or "").strip().lower() ) for j in existing_jobs}

    all_existing_qids = {j.get("queueId", "") for j in existing_jobs}
    max_existing_num = 0
    for q in all_existing_qids:
        m = re.search(r"(\d+)", q)
        if m:
            max_existing_num = max(max_existing_num, int(m.group(1)))

    new_jobs = []
    seen_this_run = set()
    current_counter = max_existing_num

    for d in all_discovered:
        url = unified_queue.clean_url(d.get("jobUrl"))
        comp = (d.get("company") or "").strip()
        role = unified_queue.clean_role(d.get("role") or "")
        cr_key = (comp.lower(), role.lower())

        if not url or len(url) < 15 or not comp or not role:
            continue
        if url in existing_urls or cr_key in existing_cr or url in seen_this_run:
            continue

        seen_this_run.add(url)
        current_counter += 1
        qid = f"D{current_counter:03d}"
        while qid in all_existing_qids:
            current_counter += 1
            qid = f"D{current_counter:03d}"
        all_existing_qids.add(qid)

        row = {
            "queueId": qid,
            "company": comp,
            "role": role,
            "platform": d.get("platform", "Web Discovery"),
            "jobUrl": d.get("jobUrl"),
            "canonicalUrl": url,
            "location": d.get("location", "India / Remote"),
            "matchScore": d.get("matchScore", 75),
            "matchReason": d.get("matchReason", "Discovered in automated search pass"),
            "status": "UNPROCESSED",
            "sourceType": d.get("sourceType", "WEB_DISCOVERY"),
            "recruiterName": d.get("recruiterName", ""),
            "recruiterTitle": d.get("recruiterTitle", ""),
            "recruiterEmail": d.get("recruiterEmail", ""),
            "recruiterPhone": d.get("recruiterPhone", ""),
            "linkedinUrl": d.get("linkedinUrl", ""),
            "contactType": d.get("contactType", ""),
            "contactEvidence": d.get("contactEvidence", ""),
            "contactConfidence": "HIGH" if d.get("recruiterEmail") or d.get("recruiterPhone") else "MEDIUM"
        }
        new_jobs.append(row)

    print(f"[DISCOVERY] Filtered and deduplicated: {len(new_jobs)} genuinely fresh opportunities to add!")

    if new_jobs:
        combined = existing_jobs + new_jobs
        queue_store.save(combined, EXCEL_ROWS)
        print(f"[DISCOVERY] Successfully added {len(new_jobs)} new jobs to canonical queue ({EXCEL_ROWS})!")

        # Sync downstream
        unified_queue.sync_all_queues(commit=True)
    else:
        print("[DISCOVERY] No new opportunities found beyond existing queue.")

    return new_jobs

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-endpoints", type=int, default=None, help="Limit number of ATS endpoints to scan")
    args = parser.parse_args()

    run_discovery_cycle(limit_endpoints=args.limit_endpoints)
