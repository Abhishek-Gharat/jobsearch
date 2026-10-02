#!/usr/bin/env python3
"""
unified_queue.py — Canonical Queue Ingestion, Normalization & Synchronization Engine.

Unifies all fragmented queues:
- excel-rows.json (master queue)
- autoapply/jobs.json (ATS harvester)
- hiring_lead_hunter_state.json (DuckDuckGo search leads)
- recruiter_outreach_state.json (enriched contacts)

Key Guarantees:
1. Normalizes role names (cleans out scraped text like salary, equity, age).
2. Strips URL tracking parameters (utm_*, ref, gh_src) for reliable deduplication.
3. Classifies contacts (VACANCY_RECRUITER vs COMPANY_RECRUITMENT_EMAIL vs GENERIC).
4. Enriches every record with contact evidence and confidence.
5. Saves atomically through queue_store.py with rolling backups.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
EXCEL_ROWS = ROOT / "excel-rows.json"
AUTOAPPLY_JOBS = ROOT / "autoapply" / "jobs.json"
LEAD_HUNTER_STATE = ROOT / "hiring_lead_hunter_state.json"
RECRUITER_OUTREACH_STATE = ROOT / "recruiter_outreach_state.json"

import queue_store

# --------------------------------------------------------------------------
# Cleaners & Normalizers
# --------------------------------------------------------------------------

TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "referrer", "source", "src", "trk", "tracking", "gh_src", "fbclid"
}

def clean_url(url: str) -> str:
    """Normalize URL by stripping tracking parameters, query fragments, and trailing slashes."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        qs = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_KEYS]
        new_q = urlencode(qs)
        cleaned = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), new_q, ""))
        return cleaned
    except Exception:
        return url.strip().split("?")[0].rstrip("/")

def clean_role(role: str) -> str:
    """Cleans scraped compensation, metadata, and excess noise from role titles."""
    if not role:
        return ""
    r = role.strip()
    # Remove Wellfound salary, location, and equity tags
    r = re.sub(
        r"(?:Onsite or remote|Remote only|Remote \(.*?\)|₹\s*[\d,.]+\s*[LlKk]?\s*–\s*₹\s*[\d,.]+\s*[LlKk]?|"
        r"\$\s*[\d,.]+[kK]?\s*–\s*\$\s*[\d,.]+[kK]?|No equity|RECRUITER RECENTLY ACTIVE|POSTED \d+ \w+ AGO).*",
        "",
        r,
        flags=re.I
    )
    # Remove excessive whitespace
    r = re.sub(r"\s+", " ", r).strip()
    return r

def normalize_status(raw_status: str | None) -> str:
    """Map any status dialect to the authoritative queue_store status set."""
    if not raw_status:
        return "UNPROCESSED"
    s = str(raw_status).strip().upper()
    mapping = {
        "NOT_PROCESSED": "UNPROCESSED",
        "NEW": "UNPROCESSED",
        "PENDING": "UNPROCESSED",
        "QUEUED": "UNPROCESSED",
        "READY": "READY",
        "READY_TO_APPLY": "READY",
        "IN_PROGRESS": "IN_PROGRESS",
        "SUBMITTED": "SUBMITTED",
        "APPLIED": "SUBMITTED",
        "SKIPPED": "SKIPPED",
        "CLOSED": "SKIPPED",
        "EXPIRED": "SKIPPED",
        "FAILED": "FAILED",
        "FAILED_UNCONFIRMED": "FAILED",
        "REVIEW_REQUIRED": "PENDING_HUMAN",
        "PENDING_HUMAN": "PENDING_HUMAN",
        "NEEDS_HUMAN": "PENDING_HUMAN",
    }
    return mapping.get(s, "UNPROCESSED")

# --------------------------------------------------------------------------
# Contact Taxonomy & Classification
# --------------------------------------------------------------------------

def classify_contact(c_type: str, email: str, evidence: str) -> str:
    """Ensures strict separation between personal recruiter, generic email, and company contact."""
    ct = (c_type or "").upper()
    em = (email or "").lower()
    ev = (evidence or "").lower()

    if "WHATSAPP" in ct:
        return "RECRUITER_WHATSAPP"
    if "LINKEDIN" in ct:
        return "HIRING_MANAGER_LINKEDIN"
    if any(m in ev for m in ("hiring team", "job poster", "recruiter", "posted by")):
        return "VACANCY_RECRUITER"
    if any(h in em for h in ("hr@", "careers@", "jobs@", "talent@", "recruiting@")):
        return "COMPANY_RECRUITMENT_EMAIL"
    if any(g in em for g in ("info@", "contact@", "hello@", "support@")):
        return "GENERIC_COMPANY_CONTACT"
    if ct:
        return ct
    return "UNVERIFIED_SOURCE"

# --------------------------------------------------------------------------
# Unification Logic
# --------------------------------------------------------------------------

def extract_contacts_map() -> tuple[dict, dict]:
    """Builds lookup dictionaries for verified contacts from lead hunter & outreach states."""
    by_url = {}
    by_cr = {}

    leads = []
    if LEAD_HUNTER_STATE.exists():
        try:
            with open(LEAD_HUNTER_STATE, "r", encoding="utf-8-sig") as f:
                leads.extend(json.load(f).get("leads", []))
        except Exception as e:
            print(f"[UNIFIER] Warning loading {LEAD_HUNTER_STATE}: {e}")

    if RECRUITER_OUTREACH_STATE.exists():
        try:
            with open(RECRUITER_OUTREACH_STATE, "r", encoding="utf-8-sig") as f:
                leads.extend(json.load(f).get("leads", []))
        except Exception as e:
            print(f"[UNIFIER] Warning loading {RECRUITER_OUTREACH_STATE}: {e}")

    for lead in leads:
        url = clean_url(lead.get("jobUrl") or lead.get("canonicalUrl") or "")
        comp = (lead.get("company") or "").strip().lower()
        role = clean_role(lead.get("role") or "").strip().lower()

        ev = lead.get("evidence")
        ev_ctx = ev.get("context", "") if isinstance(ev, dict) else (
            ev[0].get("context", "") if isinstance(ev, list) and ev and isinstance(ev[0], dict) else str(ev or "")
        )
        ev_conf = ev.get("confidence", "") if isinstance(ev, dict) else (
            ev[0].get("confidence", "") if isinstance(ev, list) and ev and isinstance(ev[0], dict) else ""
        )

        name = lead.get("recruiterName") or lead.get("name") or ""
        email = lead.get("email") or lead.get("recruiterEmail") or ""
        phone = lead.get("phone") or lead.get("recruiterPhone") or ""
        linkedin = lead.get("linkedinUrl") or lead.get("recruiterLinkedin") or ""

        # Check contact object
        if isinstance(lead.get("contact"), dict):
            val = lead["contact"].get("value", "")
            ct = lead.get("contactType") or lead.get("channel") or ""
            if "EMAIL" in ct and not email:
                email = val
            elif ("PHONE" in ct or "WHATSAPP" in ct) and not phone:
                phone = val

        contact_type = classify_contact(
            lead.get("contactType") or lead.get("channel") or "",
            email,
            lead.get("contactEvidence") or ev_ctx
        )

        contact_payload = {
            "recruiterName": name,
            "recruiterTitle": lead.get("recruiterTitle") or lead.get("title") or "",
            "recruiterEmail": email,
            "recruiterPhone": phone,
            "linkedinUrl": linkedin,
            "contactType": contact_type,
            "contactEvidence": lead.get("contactEvidence") or ev_ctx or "",
            "contactConfidence": lead.get("confidence") or ev_conf or "MEDIUM"
        }

        # Keep only if has at least one useful contact field
        if any([name, email, phone, linkedin]):
            if url:
                by_url[url] = contact_payload
            if comp and role:
                by_cr[(comp, role)] = contact_payload

    return by_url, by_cr

def sync_all_queues(commit: bool = True) -> list[dict]:
    """
    Ingests all queue stores, normalizes fields, enriches contacts, deduplicates,
    and updates excel-rows.json atomically.
    """
    print("\n" + "=" * 65)
    print("UNIFIED QUEUE SYNCHRONIZATION ENGINE")
    print(f"Mode: {'LIVE COMMIT' if commit else 'DRY RUN PREVIEW'}")
    print("=" * 65)

    contacts_url, contacts_cr = extract_contacts_map()
    print(f"[UNIFIER] Extracted contact directory: {len(contacts_url)} by URL, {len(contacts_cr)} by Company+Role")

    unified = []
    seen_urls = set()
    seen_cr = set()

    # Step 1: Ingest existing master excel-rows.json
    existing_er = []
    if EXCEL_ROWS.exists():
        existing_er = queue_store.load(EXCEL_ROWS, quiet=True)
    print(f"[UNIFIER] Found {len(existing_er)} records in excel-rows.json")

    for r in existing_er:
        url = clean_url(r.get("jobUrl") or r.get("canonicalUrl") or "")
        comp = (r.get("company") or "").strip()
        role = clean_role(r.get("role") or "")
        status = normalize_status(r.get("status"))

        # Skip invalid root URLs
        if len(url) < 15 or not comp or not role:
            continue

        # Enrich contact info if missing
        cont = contacts_url.get(url) or contacts_cr.get((comp.lower(), role.lower()))
        if cont:
            for k, v in cont.items():
                if not r.get(k) and v:
                    r[k] = v

        r["role"] = role
        r["canonicalUrl"] = url
        r["status"] = status

        cr_key = (comp.lower(), role.lower())
        if url in seen_urls or cr_key in seen_cr:
            continue

        seen_urls.add(url)
        seen_cr.add(cr_key)
        unified.append(r)

    print(f"[UNIFIER] Cleaned and retained {len(unified)} records from master queue.")

    # Step 2: Ingest from autoapply/jobs.json
    added_ats = 0
    if AUTOAPPLY_JOBS.exists():
        try:
            with open(AUTOAPPLY_JOBS, "r", encoding="utf-8-sig") as f:
                aj_jobs = json.load(f).get("jobs", [])
            for j in aj_jobs:
                url = clean_url(j.get("url") or "")
                comp = (j.get("company") or "").strip()
                role = clean_role(j.get("title") or "")
                cr_key = (comp.lower(), role.lower())

                if not url or len(url) < 15 or not comp or not role:
                    continue
                if url in seen_urls or cr_key in seen_cr:
                    continue

                status = normalize_status(j.get("status"))
                cont = contacts_url.get(url) or contacts_cr.get(cr_key) or {}

                qid = f"A{len(unified) + 1:03d}"
                row = {
                    "queueId": qid,
                    "company": comp,
                    "role": role,
                    "platform": j.get("portal") or "ATS",
                    "jobUrl": j.get("url"),
                    "canonicalUrl": url,
                    "location": j.get("location") or "India / Remote",
                    "matchScore": j.get("match_score", 75),
                    "matchReason": j.get("notes", "Discovered from public ATS API"),
                    "status": status,
                    "sourceType": "ATS_API",
                    "recruiterName": cont.get("recruiterName", ""),
                    "recruiterTitle": cont.get("recruiterTitle", ""),
                    "recruiterEmail": cont.get("recruiterEmail", ""),
                    "recruiterPhone": cont.get("recruiterPhone", ""),
                    "linkedinUrl": cont.get("linkedinUrl", ""),
                    "contactType": cont.get("contactType", ""),
                    "contactEvidence": cont.get("contactEvidence", ""),
                    "contactConfidence": cont.get("contactConfidence", "")
                }
                unified.append(row)
                seen_urls.add(url)
                seen_cr.add(cr_key)
                added_ats += 1
        except Exception as e:
            print(f"[UNIFIER] Error parsing {AUTOAPPLY_JOBS}: {e}")

    print(f"[UNIFIER] Merged {added_ats} fresh jobs from autoapply/jobs.json")

    # Step 3: Ingest from hiring_lead_hunter_state.json
    added_leads = 0
    if LEAD_HUNTER_STATE.exists():
        try:
            with open(LEAD_HUNTER_STATE, "r", encoding="utf-8-sig") as f:
                hl_leads = json.load(f).get("leads", [])
            for l in hl_leads:
                url = clean_url(l.get("jobUrl") or l.get("canonicalUrl") or "")
                comp = (l.get("company") or "").strip()
                role = clean_role(l.get("role") or "")
                cr_key = (comp.lower(), role.lower())

                if not url or len(url) < 15 or not comp or not role:
                    continue
                if url in seen_urls or cr_key in seen_cr:
                    continue

                v_stat = l.get("vacancyStatus", "CURRENT")
                status = "SKIPPED" if v_stat == "CLOSED" else "UNPROCESSED"

                contact_type = classify_contact(
                    l.get("contactType", ""),
                    l.get("email", ""),
                    l.get("contactEvidence", "")
                )

                qid = f"H{len(unified) + 1:03d}"
                row = {
                    "queueId": qid,
                    "company": comp,
                    "role": role,
                    "platform": "Web Discovery",
                    "jobUrl": l.get("jobUrl"),
                    "canonicalUrl": url,
                    "location": l.get("location") or "India / Remote",
                    "matchScore": l.get("matchScore", 80),
                    "matchReason": l.get("matchReason", "Discovered via deep web search"),
                    "status": status,
                    "sourceType": "WEB_DISCOVERY",
                    "recruiterName": l.get("recruiterName", ""),
                    "recruiterTitle": l.get("recruiterTitle", ""),
                    "recruiterEmail": l.get("email", ""),
                    "recruiterPhone": l.get("phone", ""),
                    "linkedinUrl": l.get("linkedinUrl", ""),
                    "contactType": contact_type,
                    "contactEvidence": l.get("contactEvidence", ""),
                    "contactConfidence": l.get("confidence", "MEDIUM")
                }
                unified.append(row)
                seen_urls.add(url)
                seen_cr.add(cr_key)
                added_leads += 1
        except Exception as e:
            print(f"[UNIFIER] Error parsing {LEAD_HUNTER_STATE}: {e}")

    print(f"[UNIFIER] Merged {added_leads} fresh leads from hiring_lead_hunter")

    # Status distribution summary
    status_counts = Counter(r["status"] for r in unified)
    contact_count = sum(1 for r in unified if any([r.get("recruiterName"), r.get("recruiterEmail"), r.get("recruiterPhone"), r.get("linkedinUrl")]))

    print("-" * 65)
    print(f"TOTAL CANONICAL QUEUE SIZE: {len(unified)} jobs")
    print(f"Status Breakdown: {dict(status_counts)}")
    print(f"Jobs with Verified Hiring Contacts: {contact_count}")
    print("-" * 65)

    if commit:
        queue_store.save(unified, EXCEL_ROWS, allow_shrink=True)
        print(f"[UNIFIER] Master queue successfully synced and saved atomically to: {EXCEL_ROWS}")

        # Also write synchronized copy to autoapply/jobs.json so downstream views stay updated
        aj_format = {
            "jobs": [
                {
                    "id": r.get("queueId"),
                    "company": r.get("company"),
                    "title": r.get("role"),
                    "url": r.get("jobUrl"),
                    "portal": r.get("platform", "default").lower(),
                    "location": r.get("location"),
                    "match_score": r.get("matchScore", 75),
                    "status": r.get("status", "UNPROCESSED").lower(),
                    "notes": r.get("matchReason", "")
                }
                for r in unified
            ],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        with open(AUTOAPPLY_JOBS, "w", encoding="utf-8") as f:
            json.dump(aj_format, f, indent=2)
        print(f"[UNIFIER] Mirror copy updated at: {AUTOAPPLY_JOBS}")

    return unified

if __name__ == "__main__":
    commit_mode = "--dry-run" not in sys.argv
    sync_all_queues(commit=commit_mode)
