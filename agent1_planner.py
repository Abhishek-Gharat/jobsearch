#!/usr/bin/env python3
"""
agent1_planner.py — Agent 1 (Planner / Researcher)

Responsibilities:
- Discover and validate relevant jobs against candidate_core.json
- Check experience fit (0-2 yrs, reject Senior/Lead/Staff/Principal/Architect)
- Check location fit (Remote, Hybrid, Mumbai, Pune, Bangalore)
- Check role fit (Frontend, React, Next.js, TypeScript)
- Deduplicate against master queue (queue_store.py)
- Produce a compact structured task (<500 bytes) in D:\\newjobs\\agent_state\\job_state.json

Does NOT:
- Fill or submit applications
- Reread monolithic profile files
- Pass large conversation transcripts
"""

import json
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import queue_store
from agent_state.cache_validator import is_file_changed

STATE_DIR = ROOT / "agent_state"
CANDIDATE_CORE = STATE_DIR / "candidate_core.json"
if not CANDIDATE_CORE.exists() and (STATE_DIR / "candidate_core.example.json").exists():
    CANDIDATE_CORE = STATE_DIR / "candidate_core.example.json"
JOB_STATE = STATE_DIR / "job_state.json"
SESSION_SUMMARY = STATE_DIR / "session_summary.json"

REJECT_TITLES = (
    "senior", "sr.", "lead", "staff", "principal", "architect",
    "director", "manager", "head of", "vp", "vice president",
    "phd", "ph.d", "intern", "internship", "distinguished"
)
ACCEPT_ROLES = (
    "frontend", "front-end", "react", "next.js", "nextjs", "javascript",
    "web developer", "ui developer", "software engineer", "sde-1", "sde 1", "sde i",
    "software developer", "software development", "full stack", "fullstack",
    "full-stack", "application developer", "technical developer"
)
NON_FRONTEND_KEYWORDS = (
    "python", "kubernetes", "data lakehouse", "data engineer", "devops", "sre",
    "site reliability", "backend", "firmware", "embedded", "hardware", "ios",
    "android", "flutter", "golang", "ruby", "qa engineer", "automation engineer",
    "test engineer", "cloud infrastructure", "security", "linux devices", "kernel",
    "systems", "database", "networking", "robotics", "rust", "c++"
)
INDIAN_LOCATIONS = (
    "india", "mumbai", "pune", "bangalore", "bengaluru", "hyderabad",
    "delhi", "gurgaon", "gurugram", "noida", "chennai", "kolkata",
    "ahmedabad", "jaipur", "kochi", "indore", "chandigarh"
)
REMOTE_KEYWORDS = ("remote", "worldwide", "anywhere", "work from home", "wfh", "home based")
OFFSHORE_MARKERS = (
    "usa", "united states", "uk", "united kingdom", "canada", "germany", "france",
    "netherlands", "singapore", "uae", "dubai", "australia", "ireland", "ie",
    "sweden", "poland", "cologne", "london", "new york", "california", "brazil",
    "latin america", "latam", "emea", "apac", "mexico", "japan", "philippines",
    "austin", "san francisco", "lisbon", "spain", "italy", "israel", "switzerland",
    "toronto", "vancouver", "ontario", "montreal", "chicago", "seattle", "boston",
    "china", "beijing", "korea", "seoul", "americas", "europe", "greece", "athens"
)

def load_candidate_core():
    with open(CANDIDATE_CORE, "r", encoding="utf-8") as f:
        return json.load(f)

def evaluate_job_fit(role: str, exp_req: str, location: str, job_url: str = "") -> tuple[bool, int, str]:
    role_low = (role or "").lower()
    exp_low = (exp_req or "").lower()
    loc_low = (location or "").lower()
    url_low = (job_url or "").lower()

    # Reject senior titles
    if any(rej in role_low for rej in REJECT_TITLES):
        return False, 0, f"Rejected role title: senior/lead tier ({role})"

    # Role fit
    role_match = any(acc in role_low for acc in ACCEPT_ROLES)
    if not role_match:
        return False, 10, f"Role not closely aligned with Frontend/React ({role})"

    # Domain mismatch check (exclude purely backend/devops/python/firmware roles)
    if any(k in role_low for k in NON_FRONTEND_KEYWORDS) and not any(f in role_low for f in ("frontend", "front-end", "react", "ui developer", "full stack", "fullstack")):
        return False, 0, f"Role domain mismatch for Frontend profile ({role})"

    # Experience fit check (0-3 years acceptable) - inspect explicit req, URL, and role text
    # First check URL directly so false '0-2 Yrs' metadata cannot hide a senior URL like '8-to-13-years'
    m_url = re.search(r"(\d+)\s*(?:-to-|-|to)\s*(\d+)\s*(?:-years|-year|years|yrs)", url_low)
    if m_url and int(m_url.group(1)) > 3:
        return False, 20, f"URL specifies {m_url.group(1)}-{m_url.group(2)} yrs experience"

    exp_combined = f"{exp_low} {role_low}"
    m = re.search(r"(\d+)\s*(?:-to-|-|to)\s*(\d+)\s*(?:-years|-year|years|yrs)", exp_combined)
    if m:
        min_exp = int(m.group(1))
        if min_exp > 3:
            return False, 20, f"Experience minimum {min_exp} yrs exceeds target (0-2 yrs)"
    else:
        m_single = re.search(r"(\d+)\+?\s*(?:-years|years|yrs|year)", exp_combined)
        if m_single and int(m_single.group(1)) > 3:
            return False, 20, f"Experience {m_single.group(1)}+ yrs exceeds target (0-2 yrs)"

    # Location fit check
    is_india = any(city in loc_low for city in INDIAN_LOCATIONS)
    is_remote = any(r in loc_low for r in REMOTE_KEYWORDS)
    is_offshore = any(m in loc_low for m in OFFSHORE_MARKERS)

    if is_offshore and not is_india:
        return False, 30, f"Offshore location ({location}) outside India-based search"

    if not is_india and not is_remote and loc_low.strip():
        return False, 30, f"Non-India on-site location ({location})"

    # Match scoring
    score = 70
    if "react" in role_low:
        score += 15
    if "next" in role_low or "typescript" in role_low:
        score += 10
    if is_india or is_remote:
        score += 5
    if "remote" in loc_low or "mumbai" in loc_low:
        score += 5

    return True, min(score, 98), "Fits Frontend/React target profile"

def is_duplicate(url: str, company: str, role: str, queue_jobs: list) -> bool:
    clean_url = (url or "").split("?")[0].strip().lower()
    comp_norm = (company or "").strip().lower()
    role_norm = (role or "").strip().lower()

    for item in queue_jobs:
        item_url = (item.get("jobUrl") or item.get("canonicalUrl") or "").split("?")[0].strip().lower()
        if clean_url and item_url and clean_url == item_url:
            return True
        item_comp = (item.get("company") or "").strip().lower()
        item_role = (item.get("role") or "").strip().lower()
        if comp_norm and item_comp and comp_norm == item_comp and role_norm and item_role and role_norm == item_role:
            return True
    return False

MIN_FIT_SCORE = 60

ROOT_PATHS = {"", "/", "/careers", "/career", "/jobs", "/job", "/openings"}

def is_valid_job_url(url: str) -> bool:
    if not url or len(url) < 22:
        return False
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url.strip())
        if not parts.netloc:
            return False
        if parts.path.rstrip("/") in ROOT_PATHS:
            return False
        return True
    except Exception:
        return False

def select_next_unprocessed_job(exclude_ids: set | None = None) -> dict | None:
    """Finds next unprocessed or discovered job from the master queue or discovery outputs."""
    jobs = queue_store.load(queue_store.QUEUE, quiet=True)
    exclude = exclude_ids or set()
    unprocessed = [
        j for j in jobs
        if str(j.get("status", "")).upper() in ("UNPROCESSED", "NOT_PROCESSED", "PENDING", "READY", "")
        and is_valid_job_url(j.get("jobUrl", ""))
        and not any(p in (j.get("jobUrl") or "").lower() for p in ("/posts/", "/pulse/"))
        and determine_application_method(j) in ("ATS_DIRECT", "WELLFOUND_APPLY", "LINKEDIN_APPLY", "ATS")
        and j.get("queueId") not in exclude
    ]
    if not unprocessed:
        return None

    candidate = load_candidate_core()
    evaluated = []
    for j in unprocessed:
        fit, score, reason = evaluate_job_fit(
            j.get("role", ""),
            str(j.get("experienceRequired", "")),
            j.get("location", ""),
            j.get("jobUrl", "")
        )
        if fit and score >= MIN_FIT_SCORE:
            j["matchScore"] = score
            j["matchReason"] = reason
            evaluated.append(j)

    def platform_priority(job: dict) -> int:
        url = (job.get("jobUrl") or "").lower()
        if any(ats in url for ats in ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com")):
            return 100
        if job.get("recruiterEmail") or job.get("recruiterPhone"):
            return 90
        if "wellfound.com" in url:
            return 70
        if "naukri.com" in url:
            return 40
        return 60

    if evaluated:
        # Sort descending by matchScore (actual React/Frontend fit), then platform priority
        evaluated.sort(key=lambda x: (x.get("matchScore", 0), platform_priority(x)), reverse=True)
        return evaluated[0]

    return None

def determine_application_method(job_data: dict) -> str:
    url = (job_data.get("jobUrl") or "").lower()
    ct = (job_data.get("contactType") or "").upper()
    email = job_data.get("recruiterEmail") or ""
    phone = job_data.get("recruiterPhone") or ""

    if "WHATSAPP" in ct and phone:
        return "WHATSAPP_OUTREACH"
    if "posts/" in url or "pulse/" in url:
        if email:
            return "DIRECT_EMAIL"
        return "LINKEDIN_POST_OUTREACH"
    if "wellfound.com" in url:
        return "WELLFOUND_APPLY"
    if "linkedin.com/jobs" in url:
        return "LINKEDIN_APPLY"
    if any(ats in url for ats in ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com")):
        return "ATS_DIRECT"
    if email and (ct in ("RECRUITER_EMAIL", "VACANCY_RECRUITER", "COMPANY_RECRUITMENT_EMAIL", "COMPANY_EMAIL") or "email" in ct.lower()):
        return "DIRECT_EMAIL"
    if email and not any(ats in url for ats in ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com")):
        return "DIRECT_EMAIL"
    return "ATS"

def create_job_task(job_data: dict) -> dict:
    """Produces the compact state (<700 bytes) for Agent 2."""
    app_method = determine_application_method(job_data)
    is_outreach = app_method in ("WHATSAPP_OUTREACH", "DIRECT_EMAIL", "LINKEDIN_POST_OUTREACH")
    task = {
        "queue_id": job_data.get("queueId", "PENDING"),
        "company": job_data.get("company", "").strip(),
        "role": job_data.get("role", "").strip(),
        "job_url": job_data.get("jobUrl", "").strip(),
        "match_score": job_data.get("matchScore", 80),
        "experience_fit": True,
        "location_fit": True,
        "status": "READY_TO_APPLY",
        "resume_path": r"D:\newjobs\Resume.pdf",
        "application_method": app_method,
        "requires_human": is_outreach,
        "contact": {
            "name": job_data.get("recruiterName", ""),
            "title": job_data.get("recruiterTitle", ""),
            "email": job_data.get("recruiterEmail", ""),
            "phone": job_data.get("recruiterPhone", ""),
            "type": job_data.get("contactType", ""),
            "confidence": job_data.get("contactConfidence", "")
        },
        "next_action": "PREPARE_OUTREACH" if is_outreach else "OPEN_AND_APPLY",
        "notes": job_data.get("matchReason", "Verified by Agent 1")
    }

    # Write compact payload
    with open(JOB_STATE, "w", encoding="utf-8") as f:
        json.dump(task, f, indent=2)

    # Update session summary
    if SESSION_SUMMARY.exists():
        try:
            with open(SESSION_SUMMARY, "r", encoding="utf-8") as f:
                sess = json.load(f)
        except Exception:
            sess = {}
        sess["current_job_id"] = task["queue_id"]
        sess["current_phase"] = "PLANNER_DISPATCHED"
        sess["last_successful_action"] = f"Created task for {task['company']} ({task['queue_id']}) via {app_method}"
        sess["next_action"] = "AGENT2_EXECUTE"
        with open(SESSION_SUMMARY, "w", encoding="utf-8") as f:
            json.dump(sess, f, indent=2)

    return task

def run_planner(exclude_ids: set | None = None) -> dict | None:
    print("\n--- [AGENT 1: PLANNER / RESEARCHER] ---")
    candidate = load_candidate_core()
    print(f"Loaded compact profile: {candidate['name']} ({candidate['experience_label']})")
    print(f"Target roles: {', '.join(candidate['target_roles'][:3])}")

    job = select_next_unprocessed_job(exclude_ids=exclude_ids)
    if not job:
        print("[AGENT 1] No pending unapplied jobs found in queue.")
        return None

    print(f"[AGENT 1] Selected: [{job.get('queueId')}] {job.get('company')} — {job.get('role')}")
    fit, score, reason = evaluate_job_fit(
        job.get("role", ""),
        str(job.get("experienceRequired", "")),
        job.get("location", ""),
        job.get("jobUrl", "")
    )
    print(f"[AGENT 1] Fit Score: {score}/100 | {reason}")

    task = create_job_task(job)
    print(f"[AGENT 1] Compact task written to: {JOB_STATE} ({os.path.getsize(JOB_STATE)} bytes)")
    return task

if __name__ == "__main__":
    run_planner()
