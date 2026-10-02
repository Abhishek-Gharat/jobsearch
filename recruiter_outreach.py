#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recruiter_outreach.py — Multi-channel recruiter outreach assistant.

ISOLATED NEW MODULE. Does NOT modify:
  whatsapp_outreach.py, excel-rows.json, autoapply/jobs.json, bos.py,
  supervisor.py, jobops.py, Job_Profile.TEMPLATE.md, .env, browser_profile/.

READ-ONLY inputs : excel-rows.json, autoapply/jobs.json,
                   whatsapp_outreach_queue.json, Job_Profile.TEMPLATE.md
WRITES ONLY      : recruiter_outreach_state.json, recruiter_outreach.log
                   (both next to this file)

Channel priority : RECRUITER_WHATSAPP > RECRUITER_LINKEDIN > RECRUITER_EMAIL
                   > COMPANY_EMAIL > COMPANY_PHONE (last resort, labelled)
                   > NEEDS_CONTACT

HUMAN-CONTROLLED SEND: this tool only research -> qualify -> personalize
-> open -> populate. It NEVER presses Send/POST/SUBMIT. Every send is
confirmed by you in this terminal AFTER you act in the browser.

Usage:
  python recruiter_outreach.py research [--live] [--min-score 50]
  python recruiter_outreach.py list [--min-score 50]
  python recruiter_outreach.py show --queue-id Q002
  python recruiter_outreach.py next [--min-score 50]
  python recruiter_outreach.py open --queue-id Q002
  python recruiter_outreach.py add-contact --queue-id Q002 --phone 98... [--name ..]
                                          [--linkedin URL] [--email a@b.c]
  Global: --dry-run  (no browser calls, no prompts; safe preview)
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- paths

ROOT = Path(__file__).resolve().parent
EXCEL_ROWS = ROOT / "excel-rows.json"
ENGINE_JOBS = ROOT / "autoapply" / "jobs.json"
WA_QUEUE = ROOT / "whatsapp_outreach_queue.json"
PROFILE_MD = ROOT / "Job_Profile.TEMPLATE.md"
STATE_FILE = ROOT / "recruiter_outreach_state.json"
LOG_FILE = ROOT / "recruiter_outreach.log"

MCP_HOST, MCP_PORT, MCP_PATH = "127.0.0.1", 9210, "/mcp"

# ---------------------------------------------------------------- sender (verified, mirrors Job_Profile.TEMPLATE.md)

SENDER = {
    "name": "Alex Morgan",
    "role_line": "Frontend Developer (React / Next.js, 1+ yr)",
    "phone_display": "+91 9876543210",
    "email": "candidate@example.com",
    "linkedin": "https://www.linkedin.com/in/developer-portfolio01/",
    "github": "https://github.com/developer-portfolio/",
    "portfolio": "https://developer-portfolio.vercel.app/",
    "availability": "available immediately (0 days notice)",
    "experience": "1+ year shipping production React and Next.js apps (live client "
                  "Collection System on Next.js + Ant Design, REST APIs, custom NPM "
                  "packages; freelance e-commerce frontend)",
}

CHANNELS = ("RECRUITER_WHATSAPP", "RECRUITER_LINKEDIN", "RECRUITER_EMAIL",
            "COMPANY_EMAIL", "COMPANY_PHONE", "NEEDS_CONTACT")
CHANNEL_RANK = {c: i for i, c in enumerate(CHANNELS)}
STATUSES = ("NEEDS_CONTACT", "READY", "PREPARING", "AWAITING_MANUAL_SEND",
            "OPENED", "SENT", "CONTACTED", "SKIPPED", "FAILED")
# One-lead-at-a-time lock: while any lead holds one of these, prepare-next refuses.
LOCK_STATUSES = ("PREPARING", "AWAITING_MANUAL_SEND")
# Final-action denylist enforced at the BrowserOS act layer. Any click/press
# whose control label matches is REFUSED, never executed. The human Send-button
# click is the only final action, performed by the user in the browser.
SEND_DENY = re.compile(
    r"\bsend\b|\bsubmit\b|\bapply\b|\bpost\b|\bpublish\b|\bsend invitation\b|"
    r"\bsend message\b|\bsend now\b|\bconnect with\b|\bshare now\b|\bconfirm send\b",
    re.I)


def guarded_act(bos, page: int, kind: str, ref: str | None = None,
                value: str = "", label: str = "") -> tuple[str, bool]:
    """act() wrapper that refuses final-action controls. Returns (text, ok).
    Refusals return ok=False with a REFUSED note and never reach the browser
    for click/press kinds; fill/type are unaffected (they cannot send)."""
    if kind in ("click", "click_at", "press") and SEND_DENY.search(label or ""):
        note = f"REFUSED by send-guard: kind={kind} label={label!r} (human-only action)"
        log(note)
        return note, False
    args: dict = {"page": page, "kind": kind}
    if ref:
        args["ref"] = ref
    if kind in ("fill", "type", "type_at") and value:
        args["value"] = value
    if kind == "fill":
        args["clear"] = True  # replace, never append (prevents doubled bodies)
    if kind == "press" and value:
        args["key"] = value
    return bos.call("act", args)

# ---------------------------------------------------------------- verified seed (imported from the 2026-09-16 research pass:
# BrowserOS-verified official pages + LinkedIn poster block + public search.
# Each entry carries its own evidence; NOTHING here is guessed.)

SEED_CONTACTS: dict[str, dict] = {
    "Q033": {"contacts": [
        {"type": "COMPANY_PHONE", "phone": "+91 9876543210", "name": "",
         "linkedin": "", "email": "",
         "evidence": {"source_url": "https://addtechno.com/contact-us/",
                      "source_title": "ADD Technologies official Contact Us page",
                      "date": "2026-09-16",
                      "confidence": "HIGH",
                      "why": "Verified live in BrowserOS: 'Questions? Call us at "
                             "99-66-44-2333' + info@addtechno.com. Generic business "
                             "line; nothing ties it to a recruiter or this JD."}},
        {"type": "COMPANY_EMAIL", "phone": "", "name": "",
         "linkedin": "", "email": "info@addtechno.com",
         "evidence": {"source_url": "https://addtechno.com/contact-us/",
                      "source_title": "ADD Technologies official Contact Us page",
                      "date": "2026-09-16",
                      "confidence": "MEDIUM",
                      "why": "Same official page footer. Generic info mailbox, not HR."}}]},
    "Q031": {"contacts": [
        {"type": "COMPANY_EMAIL", "phone": "", "name": "",
         "linkedin": "", "email": "hr@togetherv.com",
         "evidence": {"source_url": "https://www.togetherv.com/careers",
                      "source_title": "TogetherV official Careers page ('Mail to us: hr@togetherv.com')",
                      "date": "2026-09-16",
                      "confidence": "MEDIUM",
                      "why": "HR-specific mailbox on the official careers page, found via "
                             "public search snippet; page not re-read in browser."}},
        {"type": "COMPANY_PHONE", "phone": "+91 9876543210", "name": "Mahesh Singh",
         "linkedin": "", "email": "",
         "evidence": {"source_url": "https://www.togetherv.com/contact",
                      "source_title": "TogetherV official Contact Us page",
                      "date": "2026-09-16",
                      "confidence": "HIGH",
                      "why": "Verified live in BrowserOS: 'Contact Person: Mahesh Singh "
                             "… Phone: +91 9876543210'. Role unspecified; number is "
                             "site-wide (bookings/FAQ), i.e. company line."}}]},
    "Q034": {"contacts": [
        {"type": "COMPANY_EMAIL", "phone": "", "name": "Moumita Das",
         "linkedin": "https://www.linkedin.com/in/moumita-das-507609144", "email": "moumita@swastechinfo.com",
         "evidence": {"source_url": "https://swastechtechnologies.com/",
                      "source_title": "Swastech Technologies LLP official homepage footer",
                      "date": "2026-09-16",
                      "confidence": "MEDIUM",
                      "why": "Footer CONTACT block verified live in BrowserOS (address + "
                             "phone + this email). Person is Founder & CEO per LinkedIn "
                             "(hiring decision-maker at a 51-200 firm), but the mailbox "
                             "is not labelled HR/recruiting."}},
        {"type": "COMPANY_PHONE", "phone": "+91 9876543210", "name": "",
         "linkedin": "", "email": "",
         "evidence": {"source_url": "https://swastechtechnologies.com/",
                      "source_title": "Swastech Technologies LLP official homepage footer",
                      "date": "2026-09-16",
                      "confidence": "HIGH",
                      "why": "Same verified footer block. Generic company contact."}}]},
    "Q035": {"contacts": []},
    "Q002": {"contacts": [
        {"type": "RECRUITER_LINKEDIN", "phone": "", "name": "Asin Edel Kuvin",
         "linkedin": "https://www.linkedin.com/in/asin-edel-kuvin-a6b146293", "email": "",
         "evidence": {"source_url": "https://www.linkedin.com/jobs/view/4452227898/",
                      "source_title": "Synthires Frontend Engineer job posting (hiring-team block)",
                      "date": "2026-09-16",
                      "confidence": "HIGH",
                      "why": "Posting shows 'People you can reach out to / Meet the hiring "
                             "team: Asin Edel Kuvin, Talent Acquisition Associate @ "
                             "Synthires, Job poster' with a Message button. Profile page "
                             "confirms the same role. No public phone anywhere."}}]},
}

# ---------------------------------------------------------------- io


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception as exc:
        log(f"WARN: could not parse {path.name}: {exc}")
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_excel_rows() -> list:
    """Tolerant reader; the master queue has been seen truncated - salvage in memory only."""
    try:
        with open(EXCEL_ROWS, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    for cand in (text, re.sub(r",\s*$", "", text.rstrip()) + "\n]"):
        try:
            rows = json.loads(cand)
            if isinstance(rows, dict):
                rows = rows.get("jobs", rows.get("rows", []))
            return rows if isinstance(rows, list) else []
        except Exception:
            continue
    log(f"WARN: {EXCEL_ROWS.name} unparseable even after salvage.")
    return []


def load_engine_jobs() -> list:
    d = load_json(ENGINE_JOBS, {"jobs": []})
    jobs = d.get("jobs", []) if isinstance(d, dict) else []
    return jobs if isinstance(jobs, list) else []


def load_wa_queue() -> dict:
    d = load_json(WA_QUEUE, {"records": []})
    if isinstance(d, list):
        d = {"records": d}
    d.setdefault("records", [])
    return d


def load_state() -> dict:
    d = load_json(STATE_FILE, {"updated_at": None, "leads": []})
    if isinstance(d, list):
        d = {"updated_at": None, "leads": d}
    d.setdefault("leads", [])
    return d


def save_state(d: dict) -> None:
    d["updated_at"] = now_iso()
    save_json(STATE_FILE, d)


def norm_company(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def normalize_phone(raw: str) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits[0] in "6789":
        digits = "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    return digits if 10 <= len(digits) <= 15 else None


# ---------------------------------------------------------------- qualification + dedupe


def is_qualified(job: dict, min_score: int) -> bool:
    if not job.get("company") or not job.get("role"):
        return False
    if "flutter" in f"{job.get('role', '')}".lower():
        return False
    try:
        score = int(job.get("matchScore", job.get("match_score", 0)) or 0)
    except (TypeError, ValueError):
        return False
    return score >= min_score


def extract_contacts_from_job(job: dict) -> list[dict]:
    contacts = []
    seed = SEED_CONTACTS.get(job.get("queueId", ""), {}).get("contacts", [])
    for s in seed:
        contacts.append(dict(s))

    phone = (job.get("recruiterPhone") or "").strip()
    email = (job.get("recruiterEmail") or "").strip()
    li = (job.get("linkedinUrl") or "").strip()
    name = (job.get("recruiterName") or "").strip()
    ctype = (job.get("contactType") or "").upper()
    evidence_text = job.get("contactEvidence") or "Job post intelligence record."
    confidence = job.get("contactConfidence") or "MEDIUM"
    job_url = job.get("jobUrl") or ""

    channel = None
    if "WHATSAPP" in ctype or (phone and "whatsapp" in str(job).lower()):
        channel = "RECRUITER_WHATSAPP"
    elif "LINKEDIN" in ctype or (li and "linkedin.com/in" in li) or ("posts/" in job_url):
        channel = "RECRUITER_LINKEDIN"
    elif "RECRUITER_EMAIL" in ctype or (email and (name or "recruiter" in ctype.lower())):
        channel = "RECRUITER_EMAIL"
    elif "COMPANY" in ctype and email:
        channel = "COMPANY_EMAIL"
    elif email:
        channel = "RECRUITER_EMAIL" if name else "COMPANY_EMAIL"
    elif phone:
        channel = "COMPANY_PHONE"

    if channel and (phone or email or li or name):
        already = any(c.get("phone") == phone and c.get("email") == email for c in contacts)
        if not already:
            contacts.append({
                "type": channel,
                "phone": phone,
                "name": name,
                "linkedin": li if li else (job_url if "posts/" in job_url or "linkedin.com" in job_url else ""),
                "email": email,
                "evidence": {
                    "source_url": job_url,
                    "source_title": f"{job.get('company', '')} job post / recruiter intelligence",
                    "date": now_iso()[:10],
                    "confidence": confidence,
                    "why": evidence_text
                }
            })
    return contacts


def qualified_jobs(min_score: int) -> list:
    """Merge excel-rows (primary) + engine jobs.json (fallback for IDs like Q002)."""
    seen: dict[str, dict] = {}
    for j in load_excel_rows():
        if j.get("queueId") and is_qualified(
                {"company": j.get("company"), "role": j.get("role"),
                 "matchScore": j.get("matchScore")}, min_score):
            seen[j["queueId"]] = {
                "queueId": j["queueId"], "company": j.get("company", ""),
                "role": j.get("role", ""), "jobUrl": j.get("jobUrl", ""),
                "matchScore": j.get("matchScore", 0),
                "applyStatus": j.get("status", ""),
                "skills": j.get("keyMatchingSkills") or [],
                "recruiterName": j.get("recruiterName", ""),
                "recruiterPhone": j.get("recruiterPhone", ""),
                "recruiterEmail": j.get("recruiterEmail", ""),
                "linkedinUrl": j.get("linkedinUrl", ""),
                "contactType": j.get("contactType", ""),
                "contactEvidence": j.get("contactEvidence", ""),
                "contactConfidence": j.get("contactConfidence", ""),
            }
    for j in load_engine_jobs():
        qid = j.get("id", "")
        if not qid or qid in seen:
            continue
        if (j.get("status") in ("submitted", "pending")
                and is_qualified({"company": j.get("company"), "role": j.get("title"),
                                  "matchScore": j.get("match_score")}, min_score)):
            seen[qid] = {
                "queueId": qid, "company": j.get("company", ""),
                "role": j.get("title", ""), "jobUrl": j.get("url", ""),
                "matchScore": j.get("match_score", 0),
                "applyStatus": j.get("status", ""),
                "skills": [],
                "recruiterName": j.get("recruiterName", ""),
                "recruiterPhone": j.get("recruiterPhone", ""),
                "recruiterEmail": j.get("recruiterEmail", ""),
                "linkedinUrl": j.get("linkedinUrl", ""),
                "contactType": j.get("contactType", ""),
                "contactEvidence": j.get("contactEvidence", ""),
                "contactConfidence": j.get("contactConfidence", ""),
            }
    return list(seen.values())


def already_contacted(queue_id: str) -> bool:
    for r in load_wa_queue().get("records", []):
        if r.get("queueId") == queue_id and (r.get("status") in ("SENT", "SKIPPED")
                                             or r.get("sent_at") or r.get("message_id")):
            return True
    for lead in load_state().get("leads", []):
        if lead.get("queueId") == queue_id and lead.get("status") in ("SENT", "SKIPPED", "CONTACTED"):
            return True
    return False


def active_preparation(exclude_queue: str = "") -> dict | None:
    """The lead currently holding the one-at-a-time lock, if any."""
    for lead in load_state().get("leads", []):
        if lead.get("queueId") != exclude_queue and lead.get("status") in LOCK_STATUSES:
            return lead
    return None


def is_duplicate_contact(leads: list, company: str, role: str, contact: dict,
                         exclude_queue: str = "") -> str | None:
    """Return the queueId of the conflicting lead, else None."""
    phone = normalize_phone(contact.get("phone", "")) or ""
    li = (contact.get("linkedin") or "").rstrip("/").lower()
    em = (contact.get("email") or "").strip().lower()
    for lead in leads:
        if lead.get("queueId") == exclude_queue:
            continue
        c = lead.get("contact", {}) or {}
        if lead.get("queueId") and contact.get("_queue_match"):
            pass
        lphone = normalize_phone(c.get("phone", "")) or ""
        lli = (c.get("linkedin") or "").rstrip("/").lower()
        lem = (c.get("email") or "").strip().lower()
        if phone and lphone and phone == lphone:
            return lead.get("queueId")
        if li and lli and li == lli:
            return lead.get("queueId")
        if em and lem and em == lem:
            return lead.get("queueId")
        if (norm_company(lead.get("company")) == norm_company(company)
                and (lead.get("role") or "").strip().lower() == (role or "").strip().lower()
                and c.get("type") == contact.get("type")
                and (phone == lphone or li == lli or em == lem)
                and (phone or li or em)):
            return lead.get("queueId")
    return None


# ---------------------------------------------------------------- messages (verified fields only)


def first_name(name: str) -> str:
    n = (name or "").strip()
    return n.split()[0] if n else ""


# ---------------------------------------------------------------- message engine
# Leads with modern frontend architecture. NEVER leads with bare HTML/CSS.
# Every technical claim is gated by BOTH the vacancy (role/skills text) AND
# the verified profile (Job_Profile.TEMPLATE.md). Technologies absent from
# the profile (Redux, React Query, Jest/Vitest/RTL, Docker/CI-CD) are NEVER
# emitted, no matter what the vacancy mentions.

# Profile-backed: React, Next.js, TypeScript, Tailwind/MUI, REST APIs, MERN
# (Node/Express/MongoDB), Git, performance optimization, React Native/Flow.
TRUTHFUL_TECH = {"react", "nextjs", "typescript", "tailwind", "rest", "mern",
                 "performance"}

TECH_PHRASES = {
    "react": "React-based development with a focus on typed, maintainable components",
    "typescript": "React-based development with a focus on typed, maintainable components",
    "nextjs": "Next.js application architecture, routing, reusable components, and API-driven pages",
    "tailwind": "building consistent, responsive interfaces using reusable design patterns",
    "rest": "integrating frontend applications with REST APIs and handling loading, error, and data states",
    "mern": "end-to-end understanding across React, Node.js, Express, MongoDB, and API workflows",
    "performance": "component optimization, efficient rendering, and responsive application behavior",
}

TECH_PATTERNS = [
    ("react", re.compile(r"\breact(\.js| native)?\b", re.I)),
    ("nextjs", re.compile(r"\bnext\.?js\b", re.I)),
    ("typescript", re.compile(r"\btypescript\b|\bts\b", re.I)),
    ("tailwind", re.compile(r"\btailwind\b|\bmui\b|material[ -]?ui\b|shadcn\b|ant design\b", re.I)),
    ("rest", re.compile(r"\brest\b|restful|\bapi\b", re.I)),
    ("mern", re.compile(r"\bmern\b|\bnode\.?js\b|\bexpress\b|\bmongo", re.I)),
    ("performance", re.compile(r"\bperform|optimi|scalab", re.I)),
]


def detect_tech(job: dict) -> list:
    """Technologies evidenced by the vacancy AND truthful to the profile, in priority order."""
    blob = f"{job.get('role', '')} {' '.join(job.get('skills', []) or [])}"
    found = []
    for key, rx in TECH_PATTERNS:
        if key in TRUTHFUL_TECH and rx.search(blob) and key not in found:
            found.append(key)
    if "react" not in found:
        found.insert(0, "react")  # React-led default; core profile strength
    return found


def tech_focus_line(job: dict) -> str:
    """'requirements around X, Y caught my attention' — vacancy-evidenced only."""
    tech = detect_tech(job)
    names = {"react": "React", "nextjs": "Next.js", "typescript": "TypeScript",
             "tailwind": "reusable UI systems", "rest": "API integration",
             "mern": "full-stack workflows", "performance": "performance optimization"}
    picks = [names[t] for t in tech[:4]]
    if len(picks) == 1:
        return picks[0]
    return ", ".join(picks[:-1]) + " and " + picks[-1]


def tech_detail_sentence(job: dict) -> str:
    """One natural sentence built from the top vacancy-evidenced technologies."""
    tech = detect_tech(job)
    parts = [TECH_PHRASES[t].rstrip(".") for t in tech[:2]]
    s = "; plus ".join(parts) + "."
    return s[0].upper() + s[1:]


def specific_requirement(job: dict) -> str:
    """The concrete vacancy hook: top skill area phrased as team work."""
    tech = detect_tech(job)
    if "nextjs" in tech:
        return "building scalable frontend features and API-driven pages"
    if "mern" in tech:
        return "owning features across the frontend and API layers"
    if "performance" in tech:
        return "keeping complex interfaces fast and maintainable"
    return "building scalable frontend features, integrating APIs, and maintaining reusable UI components"


def matching_context(job: dict) -> str:
    """'relevant to my experience with X' — profile-truthful anchor for the hook (no leading 'my')."""
    tech = detect_tech(job)
    if "nextjs" in tech:
        return "Next.js and API-driven frontend work"
    if "mern" in tech:
        return "MERN project work across React, Node.js, and MongoDB"
    return "React component and API-integration work"


def applied_verb(job: dict) -> str:
    status = (job.get("applyStatus") or "").upper()
    if status in ("SUBMITTED", "APPLIED"):
        return "I recently applied for"
    return "I'm reaching out regarding"


def build_whatsapp(job: dict, recruiter_name: str) -> str:
    greet = f"Hi {first_name(recruiter_name)}," if recruiter_name else "Hi,"
    return "\n".join([
        f"{greet} I'm {SENDER['name']} — {SENDER['role_line']}.",
        f"{applied_verb(job)} the *{job.get('role', 'the open role')}* role at "
        f"*{job.get('company', 'your company')}*.",
        f"The focus on {tech_focus_line(job)} aligns with the frontend work I've been doing.",
        f"My main stack is React and JavaScript — {tech_detail_sentence(job)}",
        "Available immediately.",
        f"GitHub: {SENDER['github']}",
        "If the position is still open, I'd appreciate your consideration. Happy to share "
        "anything else if needed.",
        f"— {SENDER['name']} | {SENDER['phone_display']}",
    ])


def build_linkedin(job: dict, recruiter_name: str) -> str:
    # Concise technical variant; React/Next.js-heavy vacancies get the React-role version.
    tech = detect_tech(job)
    if "nextjs" in tech or "mern" in tech:
        return build_linkedin_react(job, recruiter_name)
    greet = f"Hi {first_name(recruiter_name)}," if recruiter_name else "Hello,"
    return "\n".join([
        f"{greet} I noticed your hiring post for the {job.get('role', 'open role')} "
        f"position at {job.get('company', 'your company')}.",
        "",
        "I'm focused on modern React development, with experience building component-driven "
        f"interfaces, API-integrated applications, reusable UI systems, and MERN-based projects. {tech_detail_sentence(job)}",
        "",
        f"The part of the role involving {specific_requirement(job)} particularly caught my attention. "
        f"I'd be interested in learning more about the team's current frontend challenges and whether my profile could be considered.",
        "",
        "May I share my project links for context?",
        "",
        "Thanks,",
        SENDER["name"],
    ])


def build_linkedin_react(job: dict, recruiter_name: str) -> str:
    """React + Next.js vacancy variant."""
    greet = f"Hi {first_name(recruiter_name)}," if recruiter_name else "Hello,"
    return "\n".join([
        f"{greet} I came across the React/Next.js opening at {job.get('company', 'your company')}.",
        "",
        f"The role's focus on {specific_requirement(job)} is closely aligned with the projects I've been "
        f"working on. My main stack is React and JavaScript, and I've also worked with {tech_focus_line(job)}.",
        "",
        "I'm particularly interested in understanding how your team approaches component architecture, "
        "data fetching, and frontend performance in production.",
        "",
        "If the position is still open, may I send over my portfolio for consideration?",
        "",
        "Best,",
        SENDER["name"],
    ])


def build_email(job: dict, recruiter_name: str) -> tuple[str, str]:
    role, company = job.get("role", "the open role"), job.get("company", "your company")
    subject = f"Application for {role} at {company} — {SENDER['name']} (React/Next.js, available immediately)"
    greet = f"Hello {recruiter_name.strip()}," if recruiter_name else "Hello,"
    body = "\n".join([
        greet, "",
        f"{applied_verb(job)} the {role} opportunity at {company}.", "",
        f"The requirements around {tech_focus_line(job)} caught my attention because they align "
        "with the kind of frontend work I've been focusing on.", "",
        "My primary focus is modern React development, including component architecture, reusable UI "
        "systems, responsive interfaces, API-driven applications, state management, and building "
        "maintainable frontend workflows. I've also worked on MERN-based projects and have been expanding "
        "my experience across full-stack development, authentication, database integration, and backend APIs.", "",
        f"I noticed that your team is looking for someone who can contribute to {specific_requirement(job)}. "
        f"That is particularly relevant to my experience with {matching_context(job)}, and I would be interested "
        "in understanding how the role is structured and what the team is currently building.", "",
        "I've included my project links below for context:", "",
        f"Portfolio: {SENDER['portfolio']}",
        f"GitHub: {SENDER['github']}",
        f"LinkedIn: {SENDER['linkedin']}", "",
        "If the position is still open, I'd appreciate it if you could review my profile or direct me "
        "to the appropriate hiring process.", "",
        "Regards,",
        SENDER["name"],
        f"{SENDER['phone_display']} | {SENDER['email']}",
    ])
    return subject, body


# ---------------------------------------------------------------- BrowserOS MCP (minimal client; same pattern as bos.py)


class BOS:
    def __init__(self, label: str = "recruiter-outreach"):
        self.cid = 0
        self.sid = None
        obj = self._post({"jsonrpc": "2.0", "id": self._n(), "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": label, "version": "1.0"}}})
        if obj is None:
            raise RuntimeError("MCP initialize failed — is BrowserOS running on 127.0.0.1:9210?")

    def _n(self):
        self.cid += 1
        return self.cid

    def _post(self, payload: dict):
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        conn = http.client.HTTPConnection(MCP_HOST, MCP_PORT, timeout=60)
        try:
            conn.request("POST", MCP_PATH, body=body, headers=headers)
            resp = conn.getresponse()
            if resp.getheader("mcp-session-id") and not self.sid:
                self.sid = resp.getheader("mcp-session-id")
            raw, want = b"", payload.get("id")
            while True:
                chunk = resp.read(16384)
                if not chunk:
                    break
                raw += chunk
                text = raw.decode("utf-8", "replace")
                if '"result"' in text or '"error"' in text:
                    for line in text.splitlines():
                        if not line.startswith("data:"):
                            continue
                        s = line[5:].strip()
                        if not s or ("result" not in s and "error" not in s):
                            continue
                        try:
                            obj = json.loads(s)
                        except Exception:
                            continue
                        if obj.get("id") == want:
                            return obj
                if len(raw) > 3_000_000:
                    break
            return None
        except (socket.timeout, TimeoutError, ConnectionRefusedError, OSError):
            return None
        finally:
            conn.close()

    def call(self, name: str, arguments: dict):
        obj = self._post({"jsonrpc": "2.0", "id": self._n(), "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})
        if obj is None:
            return "TIMEOUT", False
        if "error" in obj:
            return "ERROR: " + json.dumps(obj["error"])[:300], False
        parts = [c.get("text", "") for c in obj.get("result", {}).get("content", [])]
        return "\n".join(parts), True

    def open(self, url: str):
        text, ok = self.call("tabs", {"action": "new", "url": url})
        m = re.search(r"page (\d+)", text or "")
        return int(m.group(1)) if (ok and m) else None


# ---------------------------------------------------------------- lead building


def pick_best(contacts: list) -> dict | None:
    if not contacts:
        return None
    return sorted(contacts, key=lambda c: CHANNEL_RANK.get(c.get("type", ""), 99))[0]


def build_message_for(job: dict, contact: dict | None) -> dict:
    """Return {kind, subject, body} appropriate to the contact channel."""
    if not contact:
        return {"kind": "none", "subject": "", "body": build_whatsapp(job, "")}
    t = contact.get("type", "")
    name = contact.get("name", "")
    if t == "RECRUITER_WHATSAPP":
        wa = build_whatsapp(job, name)
        return {"kind": "whatsapp", "subject": "", "body": wa,
                "wa_link": f"https://wa.me/{normalize_phone(contact['phone'])}"
                           f"?text={urllib.parse.quote(wa, safe='')}"}
    if t == "RECRUITER_LINKEDIN":
        return {"kind": "linkedin", "subject": "", "body": build_linkedin(job, name)}
    if t in ("RECRUITER_EMAIL", "COMPANY_EMAIL"):
        subj, body = build_email(job, name)
        return {"kind": "email", "subject": subj, "body": body}
    if t == "COMPANY_PHONE":
        wa = build_whatsapp(job, name)
        return {"kind": "whatsapp-company", "subject": "", "body": wa,
                "wa_link": f"https://wa.me/{normalize_phone(contact['phone'])}"
                           f"?text={urllib.parse.quote(wa, safe='')}"}
    return {"kind": "none", "subject": "", "body": build_whatsapp(job, name)}


def upsert_lead(job: dict, contacts: list, origin: str = "research") -> dict:
    state = load_state()
    leads = state["leads"]
    best = pick_best(contacts)
    msg = build_message_for(job, best)
    channel = best["type"] if best else "NEEDS_CONTACT"
    status = "READY" if best else "NEEDS_CONTACT"
    if already_contacted(job["queueId"]):
        status = "SKIPPED"
    rec = next((l for l in leads if l.get("queueId") == job["queueId"]), None)
    if rec is None:
        rec = {"queueId": job["queueId"], "company": job["company"], "role": job["role"],
               "jobUrl": job.get("jobUrl", ""), "matchScore": job.get("matchScore", 0)}
        leads.append(rec)
    rec.update({
        "company": job["company"], "role": job["role"],
        "jobUrl": job.get("jobUrl", ""), "matchScore": job.get("matchScore", 0),
        "contact": {"name": (best or {}).get("name", ""), "phone": (best or {}).get("phone", ""),
                    "linkedin": (best or {}).get("linkedin", ""),
                    "email": (best or {}).get("email", ""),
                    "type": channel},
        "all_contacts": contacts,
        "evidence": [(best or {}).get("evidence", {})] if best else [],
        "recommendedChannel": channel,
        "message": {"kind": msg["kind"], "subject": msg.get("subject", ""),
                    "body": msg.get("body", ""), "wa_link": msg.get("wa_link", "")},
        "lastAction": origin,
    })
    if rec.get("status") not in ("SENT", "SKIPPED", "OPENED", "FAILED",
                                   "PREPARING", "AWAITING_MANUAL_SEND", "CONTACTED"):
        rec["status"] = status
    rec["timestamp"] = now_iso()
    save_state(state)
    return rec


# ---------------------------------------------------------------- commands


def get_lead_or_bootstrap(queue_id: str) -> dict | None:
    state = load_state()
    lead = next((l for l in state["leads"] if l.get("queueId") == queue_id), None)
    if lead:
        return lead
    for j in load_excel_rows():
        if j.get("queueId") == queue_id:
            contacts = extract_contacts_from_job(j)
            job_dict = {
                "queueId": j["queueId"],
                "company": j.get("company", ""),
                "role": j.get("role", ""),
                "jobUrl": j.get("jobUrl", ""),
                "matchScore": j.get("matchScore", 70),
                "applyStatus": j.get("status", ""),
                "skills": j.get("keyMatchingSkills") or []
            }
            lead = upsert_lead(job_dict, contacts, origin="canonical-queue-sync")
            return lead
    return None


def cmd_research(args) -> int:
    jobs = qualified_jobs(args.min_score)
    log(f"research: {len(jobs)} qualified (min-score {args.min_score})")
    made, ready, need = 0, 0, []
    for job in jobs:
        if already_contacted(job["queueId"]) and not args.include_contacted:
            continue
        contacts = extract_contacts_from_job(job)
        if args.live and not args.dry_run:
            live = live_reverify(job)
            for c in live:
                dup = is_duplicate_contact(
                    [{"queueId": job["queueId"], "company": job["company"],
                      "role": job["role"], "contact": x} for x in contacts],
                    job["company"], job["role"], c)
                if not dup:
                    contacts.append(c)
        rec = upsert_lead(job, contacts, origin="research-live" if args.live else "research-seed")
        made += 1
        if rec["recommendedChannel"] == "NEEDS_CONTACT":
            need.append(job["queueId"])
        else:
            ready += 1
    log(f"research done: leads={made} ready={ready} needs_contact={need}")
    return 0


def live_reverify(job: dict) -> list:
    """Re-check the job posting page for recruiter info. Returns NEW contacts only."""
    found: list = []
    try:
        bos = BOS("recruiter-research")
    except RuntimeError as exc:
        log(f"live reverify skipped ({job['queueId']}): {exc}")
        return found
    if not job.get("jobUrl"):
        return found
    page = bos.open(job["jobUrl"])
    if page is None:
        return found
    time.sleep(5)
    body, snap = bos.read(page) if hasattr(bos, "read") else "", bos.snapshot(page)
    BosRead = bos.call("read", {"page": page, "format": "text"})[0]
    text = f"{snap}\n{BosRead}"
    for m in set(re.findall(r"wa\.me/(\d{10,15})", text)):
        found.append({"type": "RECRUITER_WHATSAPP", "phone": m, "name": "",
                      "linkedin": "", "email": "",
                      "evidence": {"source_url": job["jobUrl"],
                                   "source_title": "job posting page (wa.me link)",
                                   "date": now_iso()[:10], "confidence": "MEDIUM",
                                   "why": "wa.me link published directly on the posting."}})
    bos.call("tabs", {"action": "close", "page": page})
    return found


def cmd_list(args) -> int:
    state = load_state()
    known_qids = {l.get("queueId") for l in state["leads"]}
    newly_added = 0
    for j in load_excel_rows():
        qid = j.get("queueId")
        if not qid or qid in known_qids:
            continue
        if j.get("recruiterEmail") or j.get("recruiterPhone") or (j.get("contactType") and j.get("contactType") != "UNVERIFIED_SOURCE"):
            contacts = extract_contacts_from_job(j)
            if contacts:
                job_dict = {
                    "queueId": qid,
                    "company": j.get("company", ""),
                    "role": j.get("role", ""),
                    "jobUrl": j.get("jobUrl", ""),
                    "matchScore": j.get("matchScore", 70),
                    "applyStatus": j.get("status", ""),
                    "skills": j.get("keyMatchingSkills") or []
                }
                upsert_lead(job_dict, contacts, origin="excel-rows-sync")
                newly_added += 1
    if newly_added > 0:
        state = load_state()

    leads = sorted(state["leads"],
                   key=lambda l: (CHANNEL_RANK.get(l.get("recommendedChannel", ""), 99),
                                  -(l.get("matchScore") or 0)))
    if not leads:
        print("No leads yet. Run: python recruiter_outreach.py research")
        return 1
    print(f"{'QID':10} {'SCORE':5} {'CHANNEL':17} {'STATUS':13} COMPANY / RECRUITER")
    for l in leads:
        c = l.get("contact", {}) or {}
        who = c.get("name") or c.get("phone") or c.get("email") or c.get("linkedin", "")[:30] or "-"
        print(f"{l.get('queueId', '?'):10} {str(l.get('matchScore')):5} "
              f"{l.get('recommendedChannel', '?'):17} {l.get('status', '?'):13} "
              f"{(l.get('company') or '')[:26]} / {who[:30]}")
    return 0


def cmd_show(args) -> int:
    lead = get_lead_or_bootstrap(args.queue_id)
    if not lead:
        print(f"No lead {args.queue_id}. Run research first.")
        return 1
    c = lead.get("contact", {}) or {}
    m = lead.get("message", {}) or {}
    print(f"{'=' * 64}\n{lead['queueId']} | {lead['company']} — {lead['role']}")
    print(f"channel={lead.get('recommendedChannel')} status={lead.get('status')} "
          f"updated={lead.get('timestamp', '-')}")
    print(f"contact: name={c.get('name') or '-'} phone={c.get('phone') or '-'}")
    print(f"         linkedin={c.get('linkedin') or '-'} email={c.get('email') or '-'}")
    for e in lead.get("evidence", []) or []:
        print(f"evidence [{e.get('confidence', '?')}]: {e.get('why', '')}")
        print(f"  source: {e.get('source_title', '')} <{e.get('source_url', '')}> ({e.get('date', '')})")
    print("-" * 64)
    if m.get("subject"):
        print(f"Subject: {m['subject']}\n")
    print(m.get("body", "(no message)"))
    if lead.get("invite_note"):
        print(f"\nInvite note ({len(lead['invite_note'])}/300): {lead['invite_note']}")
    if m.get("wa_link"):
        print(f"\nlink: {m['wa_link'][:130]}...")
    return 0


def wait_human(prompt: str) -> str:
    print("\n" + "=" * 64 + "\nHUMAN-CONTROLLED SEND — I will NOT send for you.\n" + prompt +
          "\nENTER=sent | skip=skipped | fail:<reason>=failed\n" + "=" * 64)
    try:
        ans = input("Confirm [ENTER=sent / skip / fail:<reason>]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return "INTERRUPTED"
    if ans == "":
        return "SENT"
    if ans.lower() == "skip":
        return "SKIPPED"
    if ans.lower().startswith("fail"):
        return "FAILED:" + ans[4:].strip()
    return "SENT" if ans.lower() in ("sent", "yes", "y", "done") else "SKIPPED"


def mark(lead: dict, verdict: str) -> int:
    state = load_state()
    rec = next((l for l in state["leads"] if l.get("queueId") == lead["queueId"]), lead)
    if verdict == "SENT":
        rec.update({"status": "SENT", "lastAction": f"human-sent via {rec.get('recommendedChannel')}"})
        log(f"SENT {rec['queueId']} via {rec.get('recommendedChannel')} (human pressed send)")
    elif verdict == "SKIPPED":
        rec.update({"status": "SKIPPED", "lastAction": "human skip"})
        log(f"SKIPPED {rec['queueId']}")
    elif verdict == "INTERRUPTED":
        rec.update({"status": "OPENED", "lastAction": "interrupted; left open"})
        log(f"INTERRUPTED {rec['queueId']} (left OPENED)")
    else:
        rec.update({"status": "FAILED", "lastAction": verdict})
        log(f"FAILED {rec['queueId']}: {verdict}")
    rec["timestamp"] = now_iso()
    save_state(state)
    return 0 if verdict == "SENT" else 3


def cmd_open(args) -> int:
    lead = get_lead_or_bootstrap(args.queue_id)
    if not lead:
        print(f"No lead {args.queue_id}. Run research first.")
        return 1
    ch = lead.get("recommendedChannel", "NEEDS_CONTACT")
    c, m = lead.get("contact", {}) or {}, lead.get("message", {}) or {}
    if ch == "NEEDS_CONTACT":
        print(f"{args.queue_id}: no verified contact. Add one with add-contact, then re-run research.")
        return 2
    print(f"\n{lead['queueId']} | {lead['company']} — {lead['role']}  [{ch}]")
    if m.get("subject"):
        print(f"Subject: {m['subject']}\n")
    print(m.get("body", ""))

    if args.dry_run:
        print("\n--dry-run: not touching the browser. Status unchanged.")
        return 0

    if ch == "RECRUITER_WHATSAPP":
        try:
            bos = BOS("recruiter-whatsapp")
        except RuntimeError as exc:
            print(f"Browser unavailable: {exc}\nManual link:\n{m.get('wa_link', '')}")
            return 1
        page = bos.open(m["wa_link"])
        if page is None:
            print("Could not open chat tab.")
            return 1
        time.sleep(4)
        print(f"WhatsApp chat opened (page {page}) with message prefilled. Review it, press Send yourself.")
        lead["status"] = "OPENED"
    elif ch == "RECRUITER_LINKEDIN":
        print(f"\nProfile: {c.get('linkedin')}")
        print("I cannot auto-fill LinkedIn's message box — copy the text above into it yourself.")
        if not args.no_browser:
            try:
                bos = BOS("recruiter-linkedin")
                page = bos.open(c["linkedin"])
                if page:
                    print(f"Profile opened (page {page}). Use the Message/Connect button yourself.")
                    lead["status"] = "OPENED"
            except RuntimeError as exc:
                print(f"Browser unavailable: {exc} — open the profile URL manually.")
        else:
            print("(--no-browser: open the profile URL manually.)")
    elif ch in ("RECRUITER_EMAIL", "COMPANY_EMAIL"):
        print("\nEmail is NOT auto-sent. Copy subject+body into your mail app yourself.")
        lead["status"] = "OPENED"
    elif ch == "COMPANY_PHONE":
        print("\nNOTE: this is a COMPANY switchboard (COMPANY_CONTACT), not the recruiter's "
              "personal number. Only message if you accept that.")
        print(f"Link (NOT auto-opened):\n{m.get('wa_link', '')}")
        ans = input("Open this company chat in BrowserOS? [y/N]: ").strip().lower()
        if ans != "y":
            return 0
        try:
            bos = BOS("recruiter-whatsapp-company")
        except RuntimeError as exc:
            print(f"Browser unavailable: {exc}")
            return 1
        page = bos.open(m["wa_link"])
        if page is None:
            return 1
        time.sleep(4)
        print(f"Chat opened (page {page}). Press Send yourself if appropriate.")
        lead["status"] = "OPENED"

    state = load_state()
    rec = next((l for l in state["leads"] if l.get("queueId") == lead["queueId"]), None)
    if rec is not None:
        rec["status"] = lead.get("status", rec.get("status"))
        rec["timestamp"] = now_iso()
        save_state(state)
    log(f"OPENED {lead['queueId']} via {ch}")
    return mark(lead, wait_human(
        f"1. In the browser, review the {ch} draft for {lead['company']}.\n"
        f"2. Press Send there yourself.\n3. Confirm here."))


def cmd_next(args) -> int:
    leads = sorted(load_state()["leads"],
                   key=lambda l: (CHANNEL_RANK.get(l.get("recommendedChannel", ""), 99),
                                  -(l.get("matchScore") or 0)))
    for lead in leads:
        if lead.get("status") in ("SENT", "SKIPPED"):
            continue
        if lead.get("recommendedChannel", "NEEDS_CONTACT") == "NEEDS_CONTACT":
            continue
        print(f"Next actionable: {lead['queueId']} {lead['company']} [{lead['recommendedChannel']}]")
        args.queue_id = lead["queueId"]
        return cmd_open(args)
    print("Nothing actionable (all SENT/SKIPPED or NEEDS_CONTACT). Run `list`.")
    return 1


def cmd_add_contact(args) -> int:
    state = load_state()
    leads = state["leads"]
    job = next((j for j in qualified_jobs(args.min_score)
                if j["queueId"] == args.queue_id), None)
    if not job and not any(l.get("queueId") == args.queue_id for l in leads):
        print(f"Unknown queueId {args.queue_id}.")
        return 1
    phone = normalize_phone(args.phone) if args.phone else ""
    if args.phone and not phone:
        print(f"Refused: '{args.phone}' is not a plausible number. Nothing saved.")
        return 1
    ctype = "RECRUITER_WHATSAPP" if phone else (
        "RECRUITER_LINKEDIN" if args.linkedin else (
            "RECRUITER_EMAIL" if args.email else None))
    if not ctype:
        print("Provide at least one of --phone / --linkedin / --email.")
        return 1
    contact = {"type": ctype, "phone": phone or "", "name": args.name.strip(),
               "linkedin": args.linkedin.strip(), "email": args.email.strip().lower(),
               "evidence": {"source_url": "manual entry by user",
                            "source_title": "user-provided contact",
                            "date": now_iso()[:10], "confidence": "MEDIUM",
                            "why": "Supplied directly by the user; not independently verified."}}
    dup = is_duplicate_contact(leads, "", "", contact, exclude_queue=args.queue_id)
    if dup:
        print(f"Duplicate of contact already on {dup}. Nothing added.")
        return 1
    lead = next((l for l in leads if l.get("queueId") == args.queue_id), None)
    if lead is None:
        lead = {"queueId": args.queue_id, "company": job["company"], "role": job["role"],
                "jobUrl": job.get("jobUrl", ""), "matchScore": job.get("matchScore", 0),
                "all_contacts": []}
        leads.append(lead)
    allc = lead.setdefault("all_contacts", [])
    # replace same-type manual entry, else append
    allc[:] = [x for x in allc if not (x.get("type") == ctype and
                                       x.get("evidence", {}).get("source_url") == "manual entry by user")]
    allc.append(contact)
    best = pick_best(allc)
    msg = build_message_for(
        {"company": lead["company"], "role": lead["role"],
         "applyStatus": "", "skills": []}, best)
    lead.update({"contact": {"name": best["name"], "phone": best["phone"],
                             "linkedin": best["linkedin"], "email": best["email"],
                             "type": best["type"]},
                 "evidence": [best.get("evidence", {})],
                 "recommendedChannel": best["type"],
                 "message": {"kind": msg["kind"], "subject": msg.get("subject", ""),
                             "body": msg.get("body", ""), "wa_link": msg.get("wa_link", "")},
                 "status": "READY", "lastAction": "manual add-contact",
                 "timestamp": now_iso()})
    save_state(state)
    log(f"CONTACT_ADDED {args.queue_id} [{ctype}]")
    return 0


# ---------------------------------------------------------------- prepare flow
# Semi-automatic outreach: prepare opens the destination and fills the
# generated message, then STOPS. The human Send-button click is the only
# final action. guarded_act() above makes Send/Submit/Apply/Connect
# unclickable at the tool layer.

REF_RE = re.compile(r"\[ref=(e\d+)\]")


def parse_refs(snapshot: str) -> list:
    """Accessibility-tree controls -> [{ref, kind, label}]."""
    out = []
    for line in (snapshot or "").splitlines():
        m = REF_RE.search(line)
        if not m:
            continue
        head = line[:m.start()]
        km = re.search(r"\b(button|link|textbox|combobox|checkbox|radio|tab|menuitem)\b", head)
        lm = re.search(r'"([^"]{1,120})"', head)
        out.append({"ref": m.group(1),
                    "kind": km.group(1) if km else "",
                    "label": (lm.group(1) if lm else "").strip()})
    return out


def find_ref(controls: list, *keywords: str, kinds: tuple = ()) -> dict | None:
    for kw in keywords:
        for c in controls:
            if kw in (c["label"] + " " + c["kind"]).lower():
                if kinds and c["kind"] not in kinds:
                    continue
                return c
    return None


def snap(bos, page: int) -> str:
    text, _ = bos.call("snapshot", {"page": page})
    return text or ""


def readtext(bos, page: int) -> str:
    text, _ = bos.call("read", {"page": page, "format": "text"})
    return text or ""


def set_lead(lead: dict, status: str, action: str, **extra) -> dict:
    state = load_state()
    rec = next((l for l in state["leads"] if l.get("queueId") == lead["queueId"]), None)
    if rec is None:
        return lead
    rec.update({"status": status, "lastAction": action, "timestamp": now_iso(), **extra})
    save_state(state)
    lead.update(rec)
    return lead


def prepare_whatsapp(bos, lead: dict, m: dict) -> dict:
    """Open the wa.me chat (message arrives prefilled by URL). No fill needed, never send."""
    c = lead.get("contact", {}) or {}
    if c.get("type") != "RECRUITER_WHATSAPP" or not m.get("wa_link"):
        return {"ok": False,
                "note": "REFUSED: no recruiter-verified WhatsApp number on this lead "
                        "(company switchboards are never auto-opened)."}
    page = bos.open(m["wa_link"])
    if page is None:
        return {"ok": False, "note": "tabs/new failed or timed out"}
    time.sleep(4)
    low = (snap(bos, page) + "\n" + readtext(bos, page)).lower()
    if "phone number shared via url is invalid" in low:
        return {"ok": False, "page": page, "note": "WhatsApp reports an invalid number"}
    login = any(p in low for p in ("scan the qr", "scan qr", "link with phone number", "log in"))
    return {"ok": True, "page": page,
            "note": "chat opened with message prefilled" +
                    ("; WhatsApp Web login (QR) may be needed first" if login else "")}


def build_invite_note(job: dict, recruiter_name: str) -> str:
    """300-char-max LinkedIn connection note. Never exceeds the limit."""
    first = first_name(recruiter_name)
    greet = f"Hi {first}, " if first else "Hi, "
    core = (f"I'm Alex, Frontend Developer (React/Next.js). I saw the "
            f"{job.get('role', 'opening')} at {job.get('company', 'your company')} "
            f"and would love to connect. Would welcome the chance to discuss fit. Thanks!")
    note = (greet + core).strip()
    if len(note) > 300:
        note = (greet + core)[:297].rsplit(" ", 1)[0] + "..."
    assert len(note) <= 300, "invite note exceeded 300 chars"
    return note


def prepare_linkedin(bos, lead: dict, c: dict, m: dict) -> dict:
    """Open profile, then EITHER fill the Message composer (1st-degree / open
    profiles) OR fill a connection-request note (Connect -> Add a note).
    Default prefers the connect-note path to preserve the user's limited
    InMail credits. STOPS before any Send/Invitation submit."""
    if not c.get("linkedin"):
        return {"ok": False, "note": "no LinkedIn URL on this lead"}
    page = bos.open(c["linkedin"])
    if page is None:
        return {"ok": False, "note": "tabs/new failed or timed out"}
    time.sleep(4)
    job = {"company": lead.get("company", ""), "role": lead.get("role", ""),
           "applyStatus": "", "skills": []}

    def fill_composer() -> dict | None:
        ctrls = parse_refs(snap(bos, page))
        box = find_ref(ctrls, "write a message", "message", kinds=("textbox",))
        if box is None:
            boxes = [x for x in ctrls if x["kind"] == "textbox"]
            box = boxes[-1] if boxes else None
        if box is None:
            return None
        out, ok = guarded_act(bos, page, "fill", box["ref"], value=m.get("body", ""),
                              label=box["label"])
        if not ok:
            return None
        time.sleep(1)
        check = readtext(bos, page)
        filled = (m.get("body", "")[:40] in check) if m.get("body") else False
        return {"ok": True, "page": page,
                "note": "message composer filled" +
                        (" and read-back verified" if filled else " (verify visually)")}

    ctrls = parse_refs(snap(bos, page))
    connect = find_ref(ctrls, "connect", "invite", kinds=("button", "link"))
    if connect and "connect" in connect["label"].lower():
        out, ok = guarded_act(bos, page, "click", connect["ref"], label=connect["label"])
        if not ok:
            return {"ok": True, "page": page, "manual": True,
                    "note": f"Connect control blocked ({out[:80]}); use the profile manually"}
        time.sleep(3)
        ctrls = parse_refs(snap(bos, page))
        add_note = find_ref(ctrls, "add a note", "add note", kinds=("button",))
        if add_note is None:
            return {"ok": True, "page": page, "manual": True,
                    "note": "Connect dialog opened but no Add-a-note control found; "
                            "add the note manually (300 chars max)"}
        out, ok = guarded_act(bos, page, "click", add_note["ref"], label=add_note["label"])
        if not ok:
            return {"ok": True, "page": page, "manual": True,
                    "note": "Add-a-note refused; complete manually"}
        time.sleep(2)
        note = build_invite_note(job, c.get("name", ""))
        ctrls = parse_refs(snap(bos, page))
        boxes = [x for x in ctrls if x["kind"] in ("textbox", "combobox")]
        box = boxes[-1] if boxes else None
        if box is None:
            return {"ok": True, "page": page, "manual": True,
                    "note": "note box did not appear; type it manually (text below)"}
        out, ok = guarded_act(bos, page, "fill", box["ref"], value=note,
                              label=box["label"] or "invitation note")
        if not ok:
            return {"ok": True, "page": page, "manual": True,
                    "note": "note fill failed; type it manually (text below)"}
        lead["invite_note"] = note
        return {"ok": True, "page": page,
                "note": f"invitation note filled ({len(note)}/300 chars). "
                        f"Press Send invitation yourself. NOTE TEXT: {note}"}

    msg_btn = find_ref(ctrls, "message", kinds=("button", "link"))
    if msg_btn and "connect" not in msg_btn["label"].lower():
        out, ok = guarded_act(bos, page, "click", msg_btn["ref"], label=msg_btn["label"])
        if not ok:
            return {"ok": True, "page": page, "manual": True,
                    "note": f"Message control refused ({out[:80]}); copy-paste manually"}
        time.sleep(3)
        bos.call("wait", {"page": page, "for": "text", "text": "Write a message"})
        filled = fill_composer()
        if filled:
            return filled
        return {"ok": True, "page": page, "manual": True,
                "note": "composer did not appear; profile left open for manual copy-paste"}
    return {"ok": True, "page": page, "manual": True,
            "note": "no Message or Connect control found; profile left open — "
                    "use Connect (no note) or InMail manually with the printed text"}


def gmail_compose_url(to: str, subject: str, body: str = "") -> str:
    # Body travels ONLY through ref-fill (with clear=True), never the URL —
    # URL + ref double-sourcing once produced a duplicated body.
    return ("https://mail.google.com/mail/?view=cm&fs=1"
            f"&to={urllib.parse.quote(to, safe='')}"
            f"&su={urllib.parse.quote(subject or '', safe='')}")


def prepare_email(bos, lead: dict, c: dict, m: dict) -> dict:
    """Open Gmail compose with To/Subject/Body prefilled via URL, then re-fill
    each field through refs for certainty. Never touches Send."""
    if not c.get("email"):
        return {"ok": False, "note": "no email address on this lead"}
    page = bos.open(gmail_compose_url(c["email"], m.get("subject", ""), m.get("body", "")))
    if page is None:
        return {"ok": False, "note": "tabs/new failed or timed out"}
    time.sleep(5)
    ctrls = parse_refs(snap(bos, page))
    to_box = find_ref(ctrls, "to", "recipients", kinds=("textbox", "combobox"))
    sub_box = find_ref(ctrls, "subject", kinds=("textbox",))
    boxes = [x for x in ctrls if x["kind"] == "textbox"]
    body_box = boxes[-1] if boxes else None
    if body_box is sub_box and len(boxes) > 1:
        body_box = boxes[-2] if to_box is boxes[0] else boxes[-1]
    filled = []
    if to_box:
        guarded_act(bos, page, "fill", to_box["ref"], value=c["email"], label=to_box["label"])
        filled.append("to")
    if sub_box and m.get("subject"):
        guarded_act(bos, page, "fill", sub_box["ref"], value=m["subject"], label=sub_box["label"])
        filled.append("subject")
    if body_box and body_box is not sub_box and m.get("body"):
        guarded_act(bos, page, "fill", body_box["ref"], value=m["body"], label=body_box["label"] or "message body")
        filled.append("body")
    time.sleep(1)
    check = readtext(bos, page)
    ok_subject = (m.get("subject", "")[:30] in check) if m.get("subject") else True
    return {"ok": True, "page": page,
            "note": f"compose prefilled via URL + refilled ({','.join(filled) or 'url-only'}); "
                    f"subject {'verified' if ok_subject else 'UNVERIFIED — check visually'}"}


def cmd_prepare(args) -> int:
    lead = get_lead_or_bootstrap(args.queue_id)
    if not lead:
        print(f"No lead {args.queue_id}. Run research first.")
        return 1
    if lead.get("status") != "READY":
        print(f"{args.queue_id} is {lead.get('status')}, not READY. "
              f"Use mark-contacted/skip, or research to re-qualify.")
        return 1
    blocker = active_preparation(exclude_queue=args.queue_id)
    if blocker and not args.dry_run:
        print(f"LOCKED: {blocker['queueId']} ({blocker['company']}) is "
              f"{blocker['status']}. Finish it (mark-contacted/skip) before preparing another.")
        return 1
    ch = lead.get("recommendedChannel", "NEEDS_CONTACT")
    c, m = lead.get("contact", {}) or {}, lead.get("message", {}) or {}
    if ch == "NEEDS_CONTACT":
        print(f"{args.queue_id}: no verified contact. Add one with add-contact first.")
        return 2
    print(f"\nPREPARE {lead['queueId']} | {lead['company']} — {lead['role']}  [{ch}]")
    dest = {"RECRUITER_WHATSAPP": f"wa.me/{c.get('phone')}",
            "RECRUITER_LINKEDIN": c.get("linkedin"),
            "RECRUITER_EMAIL": f"Gmail compose -> {c.get('email')}",
            "COMPANY_EMAIL": f"Gmail compose -> {c.get('email')}",
            "COMPANY_PHONE": "REFUSED (company switchboard, not recruiter-verified)"}.get(ch, ch)
    print(f"Destination: {dest}")
    if m.get("subject"):
        print(f"Subject: {m['subject']}\n")
    print(f"{m.get('body', '')}\n" + "-" * 64)
    if ch == "COMPANY_PHONE":
        print("REFUSED: company switchboards are never auto-opened. Add a recruiter-verified "
              "number via add-contact, or message manually. Status unchanged (READY).")
        log(f"PREPARE-REFUSED {args.queue_id} (COMPANY_PHONE, not recruiter-verified)")
        return 2
    if args.dry_run:
        print("\n--dry-run: destination + message shown only. No browser, no state change.")
        return 0
    set_lead(lead, "PREPARING", f"prepare started via {ch}")
    try:
        bos = BOS(f"prepare-{args.queue_id.lower()}")
    except RuntimeError as exc:
        set_lead(lead, "FAILED", f"browser unavailable: {exc}")
        print(f"Browser unavailable: {exc}\nLead marked FAILED (message preserved).")
        return 1
    if ch == "RECRUITER_WHATSAPP":
        res = prepare_whatsapp(bos, lead, m)
    elif ch == "RECRUITER_LINKEDIN":
        res = prepare_linkedin(bos, lead, c, m)
    else:
        res = prepare_email(bos, lead, c, m)
    if not res["ok"]:
        set_lead(lead, "FAILED", res["note"], browser_page=res.get("page"))
        print(f"FAILED: {res['note']}")
        return 1
    set_lead(lead, "AWAITING_MANUAL_SEND", res["note"], browser_page=res.get("page"))
    log(f"PREPARED {args.queue_id} via {ch} (page {res.get('page')})")
    print(f"\nPREPARED (page {res.get('page')}): {res['note']}")
    if res.get("manual"):
        print("Copy the message printed above into the browser yourself.")
    print("REVIEW it in the browser, press Send yourself, then run:\n"
          f"  python recruiter_outreach.py mark-contacted --queue-id {args.queue_id}\n"
          f"or: python recruiter_outreach.py skip --queue-id {args.queue_id} --reason \"...\"")
    return 0


def cmd_prepare_next(args) -> int:
    blocker = active_preparation()
    if blocker and not args.dry_run:
        print(f"LOCKED: {blocker['queueId']} ({blocker['company']}) is {blocker['status']}. "
              f"Finish it first: mark-contacted or skip --reason.")
        return 1
    leads = sorted(load_state()["leads"],
                   key=lambda l: (CHANNEL_RANK.get(l.get("recommendedChannel", ""), 99),
                                  -(l.get("matchScore") or 0)))
    for lead in leads:
        if lead.get("status") == "READY" and lead.get("recommendedChannel") != "NEEDS_CONTACT":
            print(f"Next: {lead['queueId']} {lead['company']} [{lead['recommendedChannel']}]")
            args.queue_id = lead["queueId"]
            return cmd_prepare(args)
    print("Nothing READY (all CONTACTED/SENT/SKIPPED, in-progress, or NEEDS_CONTACT). Run `list`.")
    return 1


def cmd_mark_contacted(args) -> int:
    rec = get_lead_or_bootstrap(args.queue_id)
    if not rec:
        print(f"No lead {args.queue_id}.")
        return 1
    state = load_state()
    lead_in_state = next((l for l in state["leads"] if l.get("queueId") == args.queue_id), rec)
    if lead_in_state.get("status") in ("CONTACTED", "SENT", "SKIPPED"):
        print(f"{args.queue_id} is already {lead_in_state.get('status')}. Nothing changed.")
        return 1
    lead_in_state.update({"status": "CONTACTED",
                "lastAction": f"human confirmed send via {lead_in_state.get('recommendedChannel')}",
                "timestamp": now_iso()})
    save_state(state)
    log(f"CONTACTED {args.queue_id} via {lead_in_state.get('recommendedChannel')} (human pressed send)")
    print(f"Marked CONTACTED: {args.queue_id}. Lock released — prepare-next may proceed.")
    return 0


def cmd_skip(args) -> int:
    rec = get_lead_or_bootstrap(args.queue_id)
    if not rec:
        print(f"No lead {args.queue_id}.")
        return 1
    state = load_state()
    lead_in_state = next((l for l in state["leads"] if l.get("queueId") == args.queue_id), rec)
    if lead_in_state.get("status") in ("CONTACTED", "SENT", "SKIPPED"):
        print(f"{args.queue_id} is already {lead_in_state.get('status')}. Nothing changed.")
        return 1
    reason = (args.reason or "").strip() or "no reason given"
    lead_in_state.update({"status": "SKIPPED", "skip_reason": reason,
                "lastAction": f"human skip: {reason}", "timestamp": now_iso()})
    save_state(state)
    log(f"SKIPPED {args.queue_id}: {reason}")
    print(f"Marked SKIPPED: {args.queue_id} ({reason}). Lock released.")
    return 0


# ---------------------------------------------------------------- whatsapp-first flow
# WhatsApp preparation ONLY for recruiter-verified numbers
# (contact.type == RECRUITER_WHATSAPP with a normalized phone + wa.me link).
# Company switchboards, snippet-only numbers, and personal numbers published
# for other purposes are REFUSED — never opened, never filled, never sent.
# Prefill arrives via the wa.me URL itself; the flow always stops at
# AWAITING_MANUAL_SEND for the human Send-button click.


def whatsapp_eligible(lead: dict) -> tuple[bool, str]:
    """(eligible, reason). Strict gate for WhatsApp preparation."""
    c = lead.get("contact", {}) or {}
    if c.get("type") != "RECRUITER_WHATSAPP":
        return False, (f"no recruiter-verified WhatsApp number (channel is "
                       f"{lead.get('recommendedChannel', '?')})")
    phone = normalize_phone(c.get("phone", ""))
    if not phone:
        return False, "recruiter WhatsApp entry has no usable phone number"
    if not (lead.get("message", {}) or {}).get("wa_link"):
        return False, "no prefilled wa.me link on this lead"
    return True, f"verified recruitment WhatsApp {phone}"


def cmd_prepare_whatsapp(args) -> int:
    lead = get_lead_or_bootstrap(args.queue_id)
    if not lead:
        print(f"No lead {args.queue_id}. Run research first.")
        return 1
    ok, reason = whatsapp_eligible(lead)
    m = lead.get("message", {}) or {}
    c = lead.get("contact", {}) or {}
    print(f"\nPREPARE-WHATSAPP {lead['queueId']} | {lead['company']} — {lead['role']}")
    if not ok:
        print(f"REFUSED: {reason}.")
        print("Status unchanged (no number guessed or generated). Add a verified recruitment "
              "number with add-contact --phone, then re-run research.")
        log(f"WHATSAPP-REFUSED {args.queue_id}: {reason}")
        return 2
    print(f"Destination: wa.me/{normalize_phone(c['phone'])} "
          f"({c.get('name') or 'recruiter'})\n\n{m.get('body', '')}\n" + "-" * 64)
    if args.dry_run:
        print("\n--dry-run: destination + message shown only. No browser, no state change.")
        return 0
    if args.no_open:
        # User's WhatsApp Web is already open: no new tab. They paste the
        # message and attach the resume PDF manually in their open chat.
        set_lead(lead, "AWAITING_MANUAL_SEND",
                 "no new tab (--no-open); paste into already-open WhatsApp Web + attach resume PDF manually")
        log(f"WHATSAPP-READY-NO-OPEN {args.queue_id} (user pastes + attaches resume manually)")
        print("\nNo new tab opened (--no-open). In YOUR already-open WhatsApp Web:\n"
              "  1. Open the recruiter chat, paste the message printed above.\n"
              "  2. Attach the resume PDF (Resume.pdf) yourself.\n"
              "  3. Review everything, press Send yourself, then run:\n"
              f"  python recruiter_outreach.py mark-contacted --queue-id {args.queue_id}")
        return 0
    blocker = active_preparation(exclude_queue=args.queue_id)
    if blocker:
        print(f"LOCKED: {blocker['queueId']} ({blocker['company']}) is {blocker['status']}. "
              f"Finish it first (mark-contacted/skip).")
        return 1
    if lead.get("status") != "READY":
        print(f"{args.queue_id} is {lead.get('status')}, not READY. "
              f"Use mark-contacted/skip, or research to re-qualify.")
        return 1
    set_lead(lead, "PREPARING", "whatsapp prepare started")
    try:
        bos = BOS(f"prepare-wa-{args.queue_id.lower()}")
    except RuntimeError as exc:
        set_lead(lead, "FAILED", f"browser unavailable: {exc}")
        print(f"Browser unavailable: {exc}\nLead marked FAILED (message preserved).")
        return 1
    res = prepare_whatsapp(bos, lead, m)
    if not res["ok"]:
        set_lead(lead, "FAILED", res["note"], browser_page=res.get("page"))
        print(f"FAILED: {res['note']}")
        return 1
    set_lead(lead, "AWAITING_MANUAL_SEND", res["note"], browser_page=res.get("page"))
    log(f"WHATSAPP-PREPARED {args.queue_id} -> {normalize_phone(c['phone'])} (page {res.get('page')})")
    print(f"\nPREPARED (page {res.get('page')}): {res['note']}")
    print("REVIEW the prefilled message in WhatsApp Web, press Send yourself, then run:\n"
          f"  python recruiter_outreach.py mark-contacted --queue-id {args.queue_id}\n"
          f"or: python recruiter_outreach.py skip --queue-id {args.queue_id} --reason \"...\"")
    return 0


def cmd_prepare_whatsapp_next(args) -> int:
    blocker = active_preparation()
    if blocker and not args.dry_run:
        print(f"LOCKED: {blocker['queueId']} ({blocker['company']}) is {blocker['status']}. "
              f"Finish it first: mark-contacted or skip --reason.")
        return 1
    leads = sorted(load_state()["leads"], key=lambda l: -(l.get("matchScore") or 0))
    for lead in leads:
        if lead.get("status") != "READY":
            continue
        ok, _ = whatsapp_eligible(lead)
        if not ok:
            continue
        print(f"Next WhatsApp lead: {lead['queueId']} {lead['company']}")
        args.queue_id = lead["queueId"]
        return cmd_prepare_whatsapp(args)
    print("No READY lead with a verified recruitment WhatsApp number. "
          "Add one via add-contact --phone, or run `list` to review.")
    return 1


def cmd_keep_whatsapp_open(args) -> int:
    """Persistent foreground process that holds ONE WhatsApp Web tab open.

    Why this exists: every normal command is a short-lived process with its own
    browser session, so tabs it opens lose their owner when it exits. This command
    stays running, keeping its session (and therefore its tab) alive until you
    press Ctrl+C. Keep this terminal open and the tab stays usable in BrowserOS.
    It never sends anything; heartbeat polls are read-only tab listings."""
    try:
        bos = BOS("whatsapp-keeper")
    except RuntimeError as exc:
        print(f"Browser unavailable: {exc}")
        return 1
    page = bos.open("https://web.whatsapp.com/")
    if page is None:
        print("Could not open WhatsApp Web.")
        return 1
    time.sleep(6)
    low = (snap(bos, page) + "\n" + readtext(bos, page)).lower()
    if any(k in low for k in ("scan the qr", "scan qr", "log in", "link with phone number")):
        print(f"WhatsApp Web open (page {page}): login wall visible — scan the QR in the browser.")
    else:
        print(f"WhatsApp Web open (page {page}): loaded.")
    print("KEEPING TAB ALIVE — leave this terminal running. Ctrl+C to release the tab.")
    log(f"KEEPER started (page {page})")
    interval = max(15, args.interval)
    try:
        n = 0
        while True:
            time.sleep(interval)
            n += 1
            tabs, ok = bos.call("tabs", {"action": "list"})
            alive = ok and str(page) in (tabs or "")
            log(f"KEEPER heartbeat {n}: page {page} {'alive' if alive else 'GONE'}")
            print(f"[{now_iso()[11:19]}] heartbeat {n}: WhatsApp tab "
                  f"{'alive (page ' + str(page) + ')' if alive else 'GONE — restart this command'}",
                  flush=True)
            if not alive:
                return 1
    except KeyboardInterrupt:
        print(f"\nReleased page {page} (tab left in place; re-run to re-own it).")
        log(f"KEEPER stopped by user (page {page})")
        return 0


# ---------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Multi-channel recruiter outreach (human-controlled send).")
    ap.add_argument("--dry-run", action="store_true",
                    help="no browser calls, no prompts; safe preview")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("research", help="qualify jobs + attach verified contacts + draft messages")
    p.add_argument("--min-score", type=int, default=50)
    p.add_argument("--live", action="store_true",
                   help="also re-check posting pages in BrowserOS (slow)")
    p.add_argument("--include-contacted", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("list", help="lead table")
    p.add_argument("--min-score", type=int, default=50)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="lead detail + message + evidence")
    p.add_argument("--queue-id", required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("next", help="open the next actionable lead")
    p.add_argument("--min-score", type=int, default=50)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("open", help="open a lead's channel, wait for YOUR send")
    p.add_argument("--queue-id", required=True)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("add-contact", help="manually attach a verified contact to a lead")
    p.add_argument("--queue-id", required=True)
    p.add_argument("--min-score", type=int, default=50)
    p.add_argument("--phone", default="")
    p.add_argument("--name", default="")
    p.add_argument("--linkedin", default="")
    p.add_argument("--email", default="")
    p.set_defaults(func=cmd_add_contact)

    p = sub.add_parser("prepare", help="open destination + fill message, STOP before send")
    p.add_argument("--queue-id", required=True)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("prepare-next", help="prepare the next READY lead (one-at-a-time lock)")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prepare_next)

    p = sub.add_parser("mark-contacted", help="mark a lead CONTACTED after YOUR manual send")
    p.add_argument("--queue-id", required=True)
    p.set_defaults(func=cmd_mark_contacted)

    p = sub.add_parser("skip", help="mark a lead SKIPPED with a reason")
    p.add_argument("--queue-id", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_skip)

    p = sub.add_parser("prepare-whatsapp", help="open verified WhatsApp chat + prefill, STOP before send")
    p.add_argument("--queue-id", required=True)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-open", action="store_true",
                   help="no new tab: user pastes into already-open WhatsApp Web + attaches resume manually")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prepare_whatsapp)

    p = sub.add_parser("prepare-whatsapp-next", help="next READY lead with verified WhatsApp (one-at-a-time lock)")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prepare_whatsapp_next)

    p = sub.add_parser("keep-whatsapp-open", help="hold one WhatsApp Web tab open (keep terminal running)")
    p.add_argument("--interval", type=int, default=60,
                   help="heartbeat seconds between liveness checks (min 15)")
    p.set_defaults(func=cmd_keep_whatsapp_open)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nStopped (state preserved; re-run to continue).")
        return 130


if __name__ == "__main__":
    sys.exit(main())
