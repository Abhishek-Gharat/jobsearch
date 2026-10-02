#!/usr/bin/env python3
"""
Recruiter Outreach Center - draft, review, manually-approved send, tracking.

Standalone companion to AutoApply v2. READ-ONLY toward the application engine:
it imports submitted applications from ../autoapply/jobs.json at seed time and
never writes anything outside this directory.

Pipeline: discovery -> personalized draft -> review -> MANUAL send -> tracking
-> reply detection -> follow-up drafts (never auto-sent).

Safety rules enforced here:
- No automatic sending. Every send is one explicit HTTP call from the dashboard
  after an in-UI confirmation, and the server re-runs duplicate checks before
  touching SMTP.
- No bulk-send endpoint exists.
- Drafts are rendered from structured, verified fields only. Nothing about the
  recruiter, company, or applicant is invented at send-render time.
- Full append-only audit history: recruiter_outreach_audit.jsonl.

Email transport: Gmail SMTP (send) + IMAP (reply detection, sent-history dedupe)
using a Google App Password. Without credentials the system still works for
drafts/review/tracking; sends are refused with setup guidance (no silent
fallbacks). A --dry-run mode simulates sends for testing.

Usage:
  python recruiter_outreach.py seed                 # import submitted applications
  python recruiter_outreach.py add --company ...    # add a recruiter manually
  python recruiter_outreach.py generate [--id ID]   # render personalized drafts
  python recruiter_outreach.py serve [--port 3100]  # dashboard + API
  python recruiter_outreach.py check-replies        # IMAP reply scan
  python recruiter_outreach.py followups            # mark due follow-up drafts
  python recruiter_outreach.py show                 # quick console summary
"""

from __future__ import annotations

import argparse
import email
import email.policy
import email.utils
import imaplib
import json
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent          # D:\newjobs\outreach
ROOT = BASE.parent                              # D:\newjobs
ENGINE_JOBS_FILE = ROOT / "autoapply" / "jobs.json"
HTML_FILE = BASE / "recruiter_outreach.html"
QUEUE_FILE = BASE / "recruiter_outreach.json"
AUDIT_FILE = BASE / "recruiter_outreach_audit.jsonl"
CONFIG_FILE = BASE / "outreach_config.json"
SENT_CACHE_FILE = BASE / "gmail_sent_cache.json"

PORT_DEFAULT = 3100
DRYRUN = False                                  # flipped by CLI flag / env

VALID_STATUSES = {"draft", "ready", "sent", "replied", "followup_due", "skipped"}
SENDABLE_STATUSES = {"draft", "ready", "followup_due"}

FOLLOWUP_OFFSETS_DAYS = (5, 10)

DEFAULT_CONFIG = {
    "gmail_address": "",
    "gmail_app_password": "",       # prefer env GMAIL_APP_PASSWORD; do not commit
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "sender_name": "Alex Morgan",
    "sender_phone": "+91 9876543210",
    "sender_email_fallback": "candidate@example.com",
    "sender_linkedin": "https://www.linkedin.com/in/developer-portfolio01/",
    "sender_github": "https://github.com/developer-portfolio/",
    "sender_portfolio": "https://developer-portfolio.vercel.app/",
    "resume_files": {
        "default": str(ROOT / "Resume.pdf"),
        "react": str(ROOT / "Resume.pdf"),
        "nextjs": str(ROOT / "Resume.pdf"),
        "fullstack": str(ROOT / "Resume.pdf"),
    },
    # Anti-spam pacing: protects your Gmail sender reputation.
    "daily_send_limit": 12,          # max real sends per calendar day
    "min_gap_between_sends_sec": 240,  # forced pause between two sends
}

LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Verified profile facts used by the draft renderer. Sourced from
# ../Job_Profile.TEMPLATE.md and ../autoapply/resumes/*.md. NOTHING here may be
# fabricated; the skill lines below are lifted from the resume variants.

SKILL_LINES = {
    "react": (
        "Most of my work is React and JavaScript (ES6+) - REST API integrations, "
        "responsive UI, and a live client Collection System built on Next.js and "
        "Ant Design, including custom NPM packages."
    ),
    "nextjs": (
        "My recent production work is Next.js-heavy - a live client system on "
        "Next.js + Ant Design with REST-first integrations and working TypeScript."
    ),
    "fullstack": (
        "I ship features across the stack - React on the front, Node.js/Express "
        "with MongoDB or PostgreSQL behind - and built my own open-source tool, "
        "ReactViz, an interactive codebase explorer."
    ),
}

VARIANT_RULES = [
    ("nextjs", re.compile(r"next\.?js|next\.js", re.I)),
    ("fullstack", re.compile(r"full[- ]?stack|\bmern\b|node\.?js|\bphp\b|\bpython\b|backend|api integrations", re.I)),
    ("react", re.compile(r"react|frontend|front-end|front end|ui |web developer|javascript", re.I)),
]


def pick_variant(title: str) -> str:
    t = title or ""
    for name, rx in VARIANT_RULES:
        if rx.search(t):
            return name
    return "react"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ---------------------------------------------------------------- io helpers

def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def audit(action: str, record_id=None, **extra) -> None:
    entry = {"ts": now_iso(), "action": action}
    if record_id is not None:
        entry["id"] = record_id
    entry.update(extra)
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_FILE, {}))
    env_addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    env_pass = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if env_addr:
        cfg["gmail_address"] = env_addr
    if env_pass:
        cfg["gmail_app_password"] = env_pass
    return cfg


def mail_configured(cfg: dict) -> bool:
    return bool(cfg.get("gmail_address") and cfg.get("gmail_app_password"))


# ------------------------------------------------------------- queue handling

def new_record(**kw) -> dict:
    rec = {
        "id": kw.get("id") or next_id(),
        "kind": kw.get("kind", "outreach"),           # outreach | followup
        "parent_id": kw.get("parent_id"),
        "followup_day": kw.get("followup_day"),
        "recruiter_name": kw.get("recruiter_name", ""),
        "recruiter_email": kw.get("recruiter_email", ""),
        "company": kw.get("company", ""),
        "job_id": kw.get("job_id", ""),
        "job_title": kw.get("job_title", ""),
        "application_status": kw.get("application_status", ""),
        "source": kw.get("source", ""),
        "job_url": kw.get("job_url", ""),
        "draft": kw.get("draft", ""),
        "subject": kw.get("subject", ""),
        "resume_variant": kw.get("resume_variant") or pick_variant(kw.get("job_title", "")),
        "resume_file": kw.get("resume_file", ""),
        "created_at": kw.get("created_at") or now_iso(),
        "sent_at": None,
        "message_id": None,
        "reply_status": kw.get("reply_status", ""),
        "reply_details": None,
        "followup_due": kw.get("followup_due"),
        "status": kw.get("status", "draft"),
        "skip_reason": kw.get("skip_reason", ""),
        "notes": kw.get("notes", ""),
    }
    return rec


def next_id() -> str:
    q = load_queue()
    nums = []
    for r in q["records"]:
        m = re.match(r"R(\d+)", r.get("id", ""))
        if m:
            nums.append(int(m.group(1)))
    return f"R{(max(nums) if nums else 0) + 1:04d}"


def load_queue() -> dict:
    data = load_json(QUEUE_FILE, {"updated_at": None, "records": []})
    data.setdefault("records", [])
    return data


def save_queue(q: dict) -> None:
    q["updated_at"] = now_iso()
    save_json(QUEUE_FILE, q)


def find_record(q: dict, rid: str):
    for r in q["records"]:
        if r.get("id") == rid:
            return r
    return None


# ------------------------------------------------------------ seeding/import

def seed_from_engine(force: bool = False) -> dict:
    """Import submitted applications from the engine queue (read-only)."""
    engine = load_json(ENGINE_JOBS_FILE, {})
    q = load_queue()
    existing_keys = {
        (r.get("job_id"), norm_company(r.get("company")))
        for r in q["records"] if r.get("kind") == "outreach"
    }
    added, skipped = 0, 0
    for j in engine.get("jobs", []):
        if j.get("status") != "submitted":
            continue
        key = (j.get("id"), norm_company(j.get("company", "")))
        if key in existing_keys:
            skipped += 1
            continue
        existing_keys.add(key)
        q["records"].append(new_record(
            kind="outreach",
            company=j.get("company", ""),
            job_id=j.get("id", ""),
            job_title=j.get("title", ""),
            application_status="submitted",
            source=j.get("portal", ""),
            job_url=j.get("url", ""),
            notes="Imported from application engine (submitted). Recruiter contact unknown yet.",
        ))
        added += 1
    save_queue(q)
    audit("seed", added=added, skipped_existing=skipped)
    return {"added": added, "skipped_existing": skipped, "total": len(q["records"])}


def norm_company(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def norm_email(s: str) -> str:
    return (s or "").strip().lower()


def display_status(rec: dict) -> str:
    """Effective status; follow-up drafts become followup_due once past due."""
    st = rec.get("status", "draft")
    if st == "draft" and rec.get("kind") == "followup":
        due = parse_iso(rec.get("followup_due") or "")
        if due and datetime.now(timezone.utc).astimezone() >= due:
            return "followup_due"
    return st


# ------------------------------------------------------------ draft rendering

def first_name(name: str) -> str:
    n = (name or "").strip()
    return n.split()[0] if n else ""


def build_subject(rec: dict) -> str:
    return f"Application for {rec.get('job_title','')} at {rec.get('company','')} - {DEFAULT_CONFIG['sender_name']}"


def build_body(rec: dict, cfg: dict) -> str:
    rname = first_name(rec.get("recruiter_name"))
    greeting = f"Hi {rname}," if rname else "Hello,"
    company = rec.get("company", "")
    role = rec.get("job_title", "")
    variant = SKILL_LINES.get(rec.get("resume_variant") or "react", SKILL_LINES["react"])

    lines = [
        greeting,
        "",
        f"I applied for the {role} role at {company} recently and wanted to reach "
        f"out to you directly.",
        "",
        variant,
        "",
        f"If the role is still open, I'd be glad to share anything useful or hop on "
        f"a short call whenever convenient. I'm available immediately.",
        "",
        "Best regards,",
        cfg.get("sender_name", ""),
        f"{cfg.get('sender_phone','')} | {cfg.get('sender_email_fallback','')}",
        f"LinkedIn: {cfg.get('sender_linkedin','')} | GitHub: {cfg.get('sender_github','')}",
        f"Portfolio: {cfg.get('sender_portfolio','')}",
    ]
    return "\n".join(lines)


def ensure_draft(rec: dict, cfg: dict, force: bool = False) -> bool:
    if not rec.get("recruiter_email"):
        return False
    if rec.get("draft") and not force:
        return False
    rec["subject"] = build_subject(rec)
    rec["draft"] = build_body(rec, cfg)
    return True


def build_followup_body(parent: dict, day: int, cfg: dict) -> tuple[str, str]:
    base_subject = parent.get("subject") or build_subject(parent)
    subject = f"Re: {base_subject}"
    rname = first_name(parent.get("recruiter_name"))
    greeting = f"Hi {rname}," if rname else "Hello,"
    role = parent.get("job_title", "")
    company = parent.get("company", "")
    if day == 5:
        body = "\n".join([
            greeting,
            "",
            f"Just following up on my application for the {role} role at {company}. "
            f"Still very interested - happy to share anything else you need. "
            f"I'm available immediately.",
            "",
            "Best regards,",
            cfg.get("sender_name", ""),
        ])
    else:
        body = "\n".join([
            greeting,
            "",
            f"Checking in once more on the {role} role at {company}. If the timeline "
            f"has shifted or the role is filled, no problem at all - I'd appreciate "
            f"any update when convenient.",
            "",
            "Best regards,",
            cfg.get("sender_name", ""),
        ])
    return subject, body


def schedule_followups(q: dict, parent: dict, cfg: dict) -> int:
    """Create Day-5 / Day-10 follow-up draft records. Never auto-sent."""
    made = 0
    have = {r.get("followup_day") for r in q["records"]
            if r.get("kind") == "followup" and r.get("parent_id") == parent["id"]
            and r.get("status") != "skipped"}
    sent_dt = parse_iso(parent["sent_at"])
    for day in FOLLOWUP_OFFSETS_DAYS:
        if day in have:
            continue
        subject, body = build_followup_body(parent, day, cfg)
        due = (sent_dt or datetime.now(timezone.utc).astimezone()) + timedelta(days=day)
        q["records"].append(new_record(
            kind="followup",
            parent_id=parent["id"],
            followup_day=day,
            recruiter_name=parent.get("recruiter_name", ""),
            recruiter_email=parent.get("recruiter_email", ""),
            company=parent.get("company", ""),
            job_id=parent.get("job_id", ""),
            job_title=parent.get("job_title", ""),
            application_status=parent.get("application_status", ""),
            source=parent.get("source", ""),
            job_url=parent.get("job_url", ""),
            draft=body,
            subject=subject,
            resume_variant=parent.get("resume_variant", ""),
            status="draft",
            followup_due=due.isoformat(timespec="seconds"),
            notes=f"Auto-drafted Day-{day} follow-up (manual send only).",
        ))
        made += 1
    # parent-level pointer to next due follow-up
    pend = [parse_iso(r["followup_due"]) for r in q["records"]
            if r.get("kind") == "followup" and r.get("parent_id") == parent["id"]
            and r.get("status") in ("draft", "followup_due") and r.get("followup_due")]
    pend = [p for p in pend if p]
    parent["followup_due"] = min(pend).isoformat(timespec="seconds") if pend else None
    return made


def refresh_parent_followup_pointer(q: dict, parent_id: str) -> None:
    parent = find_record(q, parent_id)
    if not parent:
        return
    pend = [parse_iso(r["followup_due"]) for r in q["records"]
            if r.get("kind") == "followup" and r.get("parent_id") == parent["id"]
            and r.get("status") in ("draft", "followup_due") and r.get("followup_due")]
    pend = [p for p in pend if p]
    parent["followup_due"] = min(pend).isoformat(timespec="seconds") if pend else None


def mark_due_followups() -> int:
    changed = 0
    q = load_queue()
    for r in q["records"]:
        if display_status(r) == "followup_due" and r.get("status") != "followup_due":
            r["status"] = "followup_due"
            changed += 1
    if changed:
        save_queue(q)
        audit("followups_marked_due", changed=changed)
    return changed


def short_date(iso_str) -> str:
    d = parse_iso(iso_str or "") if iso_str else None
    return d.strftime("%d %b %Y") if d else "unknown date"


# --------------------------------------------------------- duplicate checking

_SENT_CACHE_TTL = 3600


def gmail_sent_history(cfg: dict, to_email: str, refresh: bool = False) -> list:
    """Return cached list of {date, subject} we previously sent to to_email."""
    if not mail_configured(cfg):
        return []
    cache = load_json(SENT_CACHE_FILE, {})
    ent = cache.get(to_email)
    if ent and not refresh and time.time() - ent.get("fetched_at", 0) < _SENT_CACHE_TTL:
        return ent.get("items", [])
    items = []
    try:
        imap = imap_connect(cfg)
        maildir = find_imap_dir(imap, ["Sent Mail", "Sent", "[Gmail]/Sent Mail"])
        typ, _ = imap.select(maildir, readonly=True)
        if typ == "OK":
            since = (datetime.now() - timedelta(days=365)).strftime("%d-%b-%Y")
            typ, data = imap.search(None, f'(SINCE "{since}" HEADER TO "{to_email}")')
            if typ == "OK" and data and data[0]:
                for num in data[0].split():
                    typ2, msgdata = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])")
                    if typ2 != "OK" or not msgdata or not msgdata[0]:
                        continue
                    raw = msgdata[0][1]
                    msg = email.message_from_bytes(raw, policy=email.policy.default)
                    items.append({
                        "subject": str(msg.get("Subject", "")),
                        "date": str(msg.get("Date", "")),
                    })
        imap.logout()
    except Exception as e:
        audit("gmail_sent_lookup_error", error=str(e), to=to_email)
    cache[to_email] = {"fetched_at": time.time(), "items": items}
    save_json(SENT_CACHE_FILE, cache)
    return items


def duplicate_check(q: dict, cfg: dict, rec: dict, include_gmail: bool = True) -> dict:
    """Block if this recruiter/company/job was already contacted."""
    result = {"blocked": False, "reasons": [], "warnings": [], "layers": {}}

    email_n = norm_email(rec.get("recruiter_email"))
    comp_n = norm_company(rec.get("company"))
    job_id = rec.get("job_id", "")

    local_hits = []
    pair_hits = []
    for r in q["records"]:
        if r["id"] == rec["id"] or r.get("kind") != "outreach":
            continue
        if r.get("status") == "skipped" and not r.get("message_id"):
            continue
        already_sent = bool(r.get("sent_at") or r.get("message_id"))
        if not already_sent:
            continue
        same_triple = (
            email_n and norm_email(r.get("recruiter_email")) == email_n
            and comp_n and norm_company(r.get("company")) == comp_n
            and job_id and r.get("job_id") == job_id
        )
        if same_triple:
            local_hits.append(r)
        elif email_n and norm_email(r.get("recruiter_email")) == email_n \
                and comp_n and norm_company(r.get("company")) == comp_n:
            pair_hits.append(r)

    result["layers"]["queue"] = "checked"
    if local_hits:
        h = sorted(local_hits, key=lambda r: r.get("sent_at") or "")[-1]
        result["blocked"] = True
        result["reasons"].append({
            "type": "duplicate_outreach",
            "detail": f"Already contacted for this recruiter/company/job ({h['id']})",
            "previous_date": h.get("sent_at"),
        })
    elif pair_hits:
        h = sorted(pair_hits, key=lambda r: r.get("sent_at") or "")[-1]
        result["warnings"].append(
            f"Same recruiter+company previously contacted for a different job ({h['id']} on {short_date(h.get('sent_at'))})"
        )

    if include_gmail and email_n:
        history = gmail_sent_history(cfg, email_n)
        result["layers"]["gmail_sent_history"] = f"{len(history)} prior emails found" if mail_configured(cfg) else "skipped (gmail not configured)"
        comp_token = norm_company(rec.get("company"))
        for item in history:
            subj = item.get("subject", "")
            if comp_token and comp_token in norm_company(subj):
                result["blocked"] = True
                result["reasons"].append({
                    "type": "gmail_sent_history",
                    "detail": f"Gmail Sent contains: \"{subj}\"",
                    "previous_date": item.get("date"),
                })
                break
    else:
        result["layers"]["gmail_sent_history"] = "skipped (not requested)"

    return result


# ------------------------------------------------------------------- sending

def resolve_attachment(cfg: dict, rec: dict):
    path_s = rec.get("resume_file") or cfg.get("resume_files", {}).get(rec.get("resume_variant") or "default", "")
    if not path_s:
        return None
    p = Path(path_s)
    return p if p.is_file() else None


def smtp_send(cfg: dict, rec: dict, dryrun: bool = False) -> str:
    from email.message import EmailMessage
    msg = EmailMessage()
    sender = cfg.get("gmail_address")
    msg["From"] = f"{cfg.get('sender_name')} <{sender}>"
    msg["To"] = rec["recruiter_email"]
    msg["Subject"] = rec.get("subject") or build_subject(rec)
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain="gmail.com")
    body = rec.get("draft", "")
    html_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    msg.set_content(body)
    msg.add_alternative(
        f"<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.55\">{html_body}</div>",
        subtype="html",
    )

    att = resolve_attachment(cfg, rec)
    att_note = None
    if att:
        ctype, _ = mimetypes.guess_type(str(att))
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(att.read_bytes(), maintype=maintype or "application",
                           subtype=subtype or "octet-stream", filename=att.name)
        att_note = str(att)

    if dryrun:
        audit("send_dryrun", rec["id"], to=rec["recruiter_email"], attachment=att_note)
        return msg["Message-ID"]

    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(sender, cfg["gmail_app_password"])
        s.send_message(msg)
    audit("smtp_sent", rec["id"], to=rec["recruiter_email"], message_id=msg["Message-ID"],
          attachment=att_note)
    return msg["Message-ID"]


def antispam_gate(q: dict, cfg: dict, rec_id: str):
    """Rate limiting so Gmail does not flag the account as a spammer.

    Two rules, both configurable in outreach_config.json:
    - daily_send_limit: max real sends per calendar day (default 12)
    - min_gap_between_sends_sec: forced pause between consecutive sends (default 240s)
    Returns an error payload when blocked, or None when allowed.
    """
    now = datetime.now(timezone.utc).astimezone()
    today = now.date()

    sent_today = 0
    last_dt = None
    for r in q["records"]:
        t = parse_iso(r.get("sent_at") or "") if r.get("sent_at") else None
        if not t:
            continue
        if t.date() == today:
            sent_today += 1
        if last_dt is None or t > last_dt:
            last_dt = t

    limit = int(cfg.get("daily_send_limit", 12))
    if sent_today >= limit:
        audit("send_blocked_daily_limit", rec_id, sent_today=sent_today, limit=limit)
        return {
            "error": "daily_limit_reached",
            "detail": f"Daily send limit reached ({sent_today}/{limit}). "
                      f"Sending more than ~12 cold emails/day from personal Gmail is the "
                      f"fastest way to land in spam. Continue tomorrow.",
        }

    gap = int(cfg.get("min_gap_between_sends_sec", 240))
    if last_dt is not None:
        elapsed = (now - last_dt).total_seconds()
        if elapsed < gap:
            wait = int(gap - elapsed)
            audit("send_blocked_cooldown", rec_id, retry_after_sec=wait)
            return {
                "error": "cooldown_active",
                "detail": f"Pacing: wait another {wait}s before the next send "
                          f"({gap}s minimum between emails protects deliverability).",
                "retry_after_sec": wait,
            }
    return None


def perform_send(q: dict, cfg: dict, rec: dict) -> dict:
    dup = duplicate_check(q, cfg, rec)
    if dup["blocked"]:
        audit("send_blocked_duplicate", rec["id"], reasons=dup["reasons"])
        return {"ok": False, "blocked": True, "duplicate": dup}

    mid = smtp_send(cfg, rec, dryrun=DRYRUN)
    rec["sent_at"] = now_iso()
    rec["message_id"] = mid
    rec["status"] = "sent"
    rec["send_mode"] = "dryrun" if DRYRUN else "smtp"
    made = schedule_followups(q, rec, cfg)
    save_queue(q)
    audit("sent", rec["id"], message_id=mid, followups_created=made,
          mode=rec.get("send_mode"))
    return {"ok": True, "message_id": mid, "sent_at": rec["sent_at"],
            "followups_created": made, "mode": rec.get("send_mode")}


# ------------------------------------------------------------ gmail / replies

def imap_connect(cfg: dict):
    imap = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg["imap_port"]))
    imap.login(cfg["gmail_address"], cfg["gmail_app_password"])
    return imap


def find_imap_dir(imap, candidates) -> str:
    try:
        typ, boxes = imap.list()
        names = []
        if typ == "OK" and boxes:
            for b in boxes:
                try:
                    parts = b.decode("utf-8", "ignore").rsplit('"/"', 1)
                    names.append(parts[-1].strip('"') if len(parts) > 1 else "")
                except Exception:
                    continue
        for c in candidates:
            for n in names:
                if c.lower() in n.lower():
                    return n
    except Exception:
        pass
    return '"INBOX"'


def check_replies() -> dict:
    cfg = get_config()
    if not mail_configured(cfg):
        return {"ok": False, "error": "gmail_not_configured"}
    q = load_queue()
    sent_recs = [r for r in q["records"]
                 if r.get("message_id") and r.get("status") in ("sent", "replied")]
    if not sent_recs:
        return {"ok": True, "checked": 0, "new_replies": 0}
    imap = imap_connect(cfg)
    imap.select("INBOX", readonly=True)
    new_replies = []
    try:
        for rec in sent_recs:
            hits = []
            msgid = rec["message_id"]
            for crit in (f'(HEADER In-Reply-To "{msgid}")',
                         f'(HEADER References "{msgid}")'):
                typ, data = imap.search(None, crit)
                if typ == "OK" and data and data[0]:
                    hits.extend(data[0].split())
            if not hits:
                frm = rec.get("recruiter_email", "")
                subj_token = (rec.get("subject") or "").split(" - ")[0]
                safe = re.sub(r'"', "'", subj_token)
                typ, data = imap.search(
                    None, f'(FROM "{frm}" SUBJECT "{safe}")')
                if typ == "OK" and data and data[0]:
                    hits.extend(data[0].split())
            for num in sorted(set(hits)):
                typ2, md = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])")
                if typ2 != "OK" or not md or not md[0]:
                    continue
                m = email.message_from_bytes(md[0][1], policy=email.policy.default)
                rm_id = str(m.get("Message-ID", ""))
                if rec.get("reply_details", {}) and (rec.get("reply_details") or {}).get("message_id") == rm_id:
                    continue
                rec["reply_status"] = "replied"
                rec["status"] = "replied"
                rec["reply_details"] = {
                    "received_at": str(m.get("Date", "")),
                    "sender": str(m.get("From", "")),
                    "subject": str(m.get("Subject", "")),
                    "message_id": rm_id,
                    "detected_at": now_iso(),
                }
                new_replies.append({"id": rec["id"], "subject": rec["reply_details"]["subject"],
                                    "sender": rec["reply_details"]["sender"]})
                audit("reply_detected", rec["id"], from_=rec["reply_details"]["sender"],
                      message_id=rm_id)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    save_queue(q)
    return {"ok": True, "checked": len(sent_recs), "new_replies": new_replies}


# ------------------------------------------------------------------ reporting

def summary_line(q: dict) -> str:
    counts = {}
    for r in q["records"]:
        eff = display_status(r)
        counts[eff] = counts.get(eff, 0) + 1
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items()))


def cmd_show() -> int:
    q = load_queue()
    print(f"Recruiter Outreach Queue  ({q.get('updated_at')})")
    print(summary_line(q))
    print("-" * 100)
    rows = []
    for r in q["records"]:
        kind = "FU" + str(r.get("followup_day")) if r.get("kind") == "followup" else "--"
        rows.append((
            r["id"], kind, (r.get("company") or "")[:24], (r.get("job_title") or "")[:32],
            (r.get("recruiter_name") or "-")[:18], (r.get("recruiter_email") or "-")[:30],
            display_status(r),
            short_date(r.get("sent_at")),
        ))
    print(f"{'ID':6} {'K':4} {'COMPANY':24} {'ROLE':32} {'RECRUITER':18} {'EMAIL':30} {'STATUS':14} {'SENT':11}")
    for row in rows:
        print(f"{row[0]:6} {row[1]:4} {row[2]:24} {row[3]:32} {row[4]:18} {row[5]:30} {row[6]:14} {row[7]:11}")
    return 0


# ------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "RecruiterOutreach/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("[http] " + (fmt % args) + "\n")

    # helpers ---------------------------------------------------------------
    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _html(self) -> None:
        try:
            content = HTML_FILE.read_bytes()
        except FileNotFoundError:
            self._json(500, {"error": f"missing {HTML_FILE}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _with_queue(self, fn):
        with LOCK:
            q = load_queue()
            out = fn(q)
            save_queue(q)
        return out

    # routes ----------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        cfg = get_config()
        if route == "/":
            return self._html()
        if route == "/api/outreach":
            with LOCK:
                q = load_queue()
                mark_due_inplace(q)
                save_queue(q)
                # annotate: effective status + next follow-up pointer for parents
                kids_by_parent = {}
                for r in q["records"]:
                    if r.get("kind") == "followup" and r.get("parent_id"):
                        kids_by_parent.setdefault(r["parent_id"], []).append(r)
                for r in q["records"]:
                    r["display_status"] = display_status(r)
                for pid, kids in kids_by_parent.items():
                    parent = find_record(q, pid)
                    if not parent:
                        continue
                    pend = [parse_iso(k.get("followup_due") or "") for k in kids
                            if k.get("status") in ("draft", "followup_due") and k.get("followup_due")]
                    pend = [p for p in pend if p]
                    parent["next_fu_due"] = min(pend).isoformat(timespec="seconds") if pend else None
                order = {"replied": 0, "followup_due": 1, "ready": 2, "draft": 3,
                         "sent": 4, "skipped": 5}
                recs = sorted(
                    q["records"],
                    key=lambda r: (
                        order.get(display_status(r), 9),
                        -(ts_or_zero(r.get("reply_details", {}).get("detected_at") if isinstance(r.get("reply_details"), dict) else None)),
                        -(ts_or_zero(r.get("sent_at"))),
                        r["id"],
                    ),
                )
                return self._json(200, {
                    "updated_at": q.get("updated_at"),
                    "summary": summary_line(q),
                    "mail_configured": mail_configured(cfg),
                    "address": cfg.get("gmail_address", ""),
                    "dryrun": DRYRUN,
                    "daily_limit": int(cfg.get("daily_send_limit", 12)),
                    "sent_today": sum(
                        1 for r in q["records"]
                        if r.get("sent_at")
                        and (parse_iso(r["sent_at"]) or datetime.now(timezone.utc).astimezone()).date()
                        == datetime.now(timezone.utc).astimezone().date()
                    ),
                    "records": recs,
                })
        if route == "/api/preflight":
            rid = (qs.get("id") or [""])[0]
            with LOCK:
                q = load_queue()
                rec = find_record(q, rid)
                if not rec:
                    return self._json(404, {"error": "not_found"})
                if rec.get("status") == "sent" or rec.get("message_id"):
                    return self._json(409, {"error": "already_sent"})
                dup = duplicate_check(q, cfg, rec)
                att = resolve_attachment(cfg, rec)
                return self._json(200, {
                    "id": rid,
                    "to": rec.get("recruiter_email"),
                    "subject": rec.get("subject") or build_subject(rec),
                    "body": rec.get("draft"),
                    "attachment": str(att) if att else None,
                    "duplicate": dup,
                    "can_send": bool(rec.get("recruiter_email") and rec.get("draft")) and not dup["blocked"],
                })
        if route == "/api/config":
            return self._json(200, {
                "mail_configured": mail_configured(cfg),
                "address": cfg.get("gmail_address", ""),
                "dryrun": DRYRUN,
                "config_example": str(CONFIG_FILE.with_suffix(".example.json")),
            })
        if route == "/healthz":
            return self._json(200, {"ok": True, "ts": now_iso()})
        return self._json(404, {"error": "unknown_route"})

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        body = self._body()
        cfg = get_config()

        if route == "/api/draft":
            def op(q):
                rec = find_record(q, body.get("id", ""))
                if not rec:
                    return (404, {"error": "not_found"})
                if rec.get("message_id"):
                    return (409, {"error": "already_sent_cannot_edit"})
                for field in ("recruiter_name", "recruiter_email", "draft", "subject",
                              "resume_variant", "notes"):
                    if field in body:
                        rec[field] = str(body[field])
                if body.get("generate"):
                    ensure_draft(rec, cfg, force=True)
                    audit("draft_generated", rec["id"])
                else:
                    audit("draft_edited", rec["id"])
                return (200, {"ok": True, "record": rec})
            code, payload = self._with_queue(op)
            return self._json(code, payload)

        if route == "/api/generate":
            def op(q):
                target = body.get("id")
                made = []
                for rec in q["records"]:
                    if target and rec["id"] != target:
                        continue
                    if rec.get("message_id"):
                        continue
                    if not rec.get("recruiter_email"):
                        continue
                    if ensure_draft(rec, cfg, force=bool(body.get("force"))):
                        if rec.get("status") == "draft":
                            rec["status"] = "ready"
                        made.append(rec["id"])
                        audit("draft_generated", rec["id"])
                return (200, {"ok": True, "generated": made})
            code, payload = self._with_queue(op)
            return self._json(code, payload)

        if route == "/api/send":
            global DRYRUN
            if not body.get("confirm"):
                return self._json(400, {"error": "confirmation_required",
                                        "detail": "Send requires {confirm:true} after UI confirmation."})
            if DRYRUN:
                pass  # allowed; simulated
            elif not mail_configured(cfg):
                return self._json(503, {
                    "error": "gmail_not_configured",
                    "detail": "Set gmail_address + gmail_app_password (app password) in "
                              "outreach_config.json or GMAIL_ADDRESS/GMAIL_APP_PASSWORD env vars.",
                })

            def op(q):
                rec = find_record(q, body.get("id", ""))
                if not rec:
                    return (404, {"error": "not_found"})
                if rec.get("message_id") or rec.get("status") == "sent":
                    return (409, {"error": "already_sent",
                                  "sent_at": rec.get("sent_at")})
                if not rec.get("recruiter_email") or not rec.get("draft"):
                    return (400, {"error": "missing_recipient_or_draft"})
                if not DRYRUN:
                    block = antispam_gate(q, cfg, rec["id"])
                    if block:
                        return (429, block)
                res = perform_send(q, cfg, rec)
                return (200 if res.get("ok") else 409, res)
            code, payload = self._with_queue(op)
            return self._json(code, payload)

        if route == "/api/skip":
            def op(q):
                rec = find_record(q, body.get("id", ""))
                if not rec:
                    return (404, {"error": "not_found"})
                if rec.get("message_id"):
                    return (409, {"error": "already_sent"})
                rec["status"] = "skipped"
                rec["skip_reason"] = str(body.get("reason", ""))
                if rec.get("kind") == "outreach":
                    for r in q["records"]:
                        if r.get("parent_id") == rec["id"]:
                            r["status"] = "skipped"
                            r["skip_reason"] = "parent skipped"
                if rec.get("kind") == "followup" and rec.get("parent_id"):
                    refresh_parent_followup_pointer(q, rec["parent_id"])
                audit("skipped", rec["id"], reason=rec["skip_reason"])
                return (200, {"ok": True, "record": rec})
            code, payload = self._with_queue(op)
            return self._json(code, payload)

        if route == "/api/requeue":
            def op(q):
                rec = find_record(q, body.get("id", ""))
                if not rec:
                    return (404, {"error": "not_found"})
                if rec.get("message_id"):
                    return (409, {"error": "already_sent_never_resend"})
                rec["status"] = "draft"
                audit("requeued", rec["id"])
                return (200, {"ok": True, "record": rec})
            code, payload = self._with_queue(op)
            return self._json(code, payload)

        if route == "/api/add":
            def op(q):
                email_v = norm_email(body.get("recruiter_email", ""))
                if not email_v or "@" not in email_v:
                    return (400, {"error": "valid_recruiter_email_required"})
                if not body.get("company"):
                    return (400, {"error": "company_required"})
                rec = new_record(
                    kind="outreach",
                    recruiter_name=str(body.get("recruiter_name", "")),
                    recruiter_email=email_v,
                    company=str(body.get("company", "")),
                    job_id=str(body.get("job_id", "")) or next_id(),
                    job_title=str(body.get("job_title", "")),
                    application_status=str(body.get("application_status", "cold-outreach")),
                    source=str(body.get("source", "manual")),
                    job_url=str(body.get("job_url", "")),
                    resume_variant=pick_variant(body.get("job_title", "")),
                    notes=str(body.get("notes", "")),
                )
                q["records"].append(rec)
                audit("added", rec["id"], company=rec["company"], to=email_v)
                return (200, {"ok": True, "record": rec})
            code, payload = self._with_queue(op)
            return self._json(code, payload)

        if route == "/api/check-replies":
            with LOCK:
                res = check_replies()
            return self._json(200 if res.get("ok") else 503, res)

        if route == "/api/mark-due":
            return self._json(200, {"changed": mark_due_followups()})

        return self._json(404, {"error": "unknown_route"})


def ts_or_zero(iso_str):
    d = parse_iso(iso_str or "") if iso_str else None
    return d.timestamp() if d else 0


def mark_due_inplace(q: dict) -> None:
    changed = False
    for r in q["records"]:
        if display_status(r) == "followup_due" and r.get("status") != "followup_due":
            r["status"] = "followup_due"
            changed = True
    if changed:
        audit("followups_marked_due_auto")


def cmd_serve(port: int) -> int:
    url = f"http://127.0.0.1:{port}/"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Recruiter Outreach Center serving at {url}")
    print(f"Queue: {QUEUE_FILE}")
    print(f"Dry-run: {'ON' if DRYRUN else 'off'}   Gmail: {'configured' if mail_configured(get_config()) else 'NOT configured (sends disabled)'}")
    print("Press Ctrl+C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


# ----------------------------------------------------------------------- cli

def cmd_add(args) -> int:
    def op(q):
        rec = new_record(
            kind="outreach",
            recruiter_name=args.name,
            recruiter_email=norm_email(args.email),
            company=args.company,
            job_id=args.job_id or next_id(),
            job_title=args.title,
            application_status=args.application_status,
            source=args.source,
            job_url=args.url,
        )
        q["records"].append(rec)
        audit("added", rec["id"], company=rec["company"], to=rec["recruiter_email"])
        return rec
    with LOCK:
        q = load_queue()
        rec = op(q)
        save_queue(q)
    print(f"Added {rec['id']} -> {rec['company']} / {rec['recruiter_email']}")
    return 0


def cmd_generate(args) -> int:
    cfg = get_config()
    with LOCK:
        q = load_queue()
        made = []
        for rec in q["records"]:
            if args.id and rec["id"] != args.id:
                continue
            if not rec.get("recruiter_email") or rec.get("message_id"):
                continue
            if ensure_draft(rec, cfg, force=args.force):
                if rec.get("status") == "draft":
                    rec["status"] = "ready"
                made.append(rec["id"])
        save_queue(q)
    print(f"Generated {len(made)} draft(s): {', '.join(made) if made else '(none eligible - recruiters need email addresses)'}")
    audit("cli_generate", count=len(made))
    return 0


def main() -> int:
    global DRYRUN
    ap = argparse.ArgumentParser(description="Recruiter Outreach Center")
    ap.add_argument("--dry-run", action="store_true", help="simulate sends (no SMTP)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("seed", help="import submitted applications from the engine (read-only)")

    p_add = sub.add_parser("add", help="add a recruiter manually")
    p_add.add_argument("--company", required=True)
    p_add.add_argument("--title", default="")
    p_add.add_argument("--email", required=True)
    p_add.add_argument("--name", default="")
    p_add.add_argument("--job-id", default="")
    p_add.add_argument("--url", default="")
    p_add.add_argument("--source", default="manual")
    p_add.add_argument("--application-status", default="cold-outreach")

    p_gen = sub.add_parser("generate", help="render personalized drafts")
    p_gen.add_argument("--id", default="")
    p_gen.add_argument("--force", action="store_true")

    p_srv = sub.add_parser("serve", help="run the dashboard + API")
    p_srv.add_argument("--port", type=int, default=PORT_DEFAULT)

    p_show = sub.add_parser("show", help="console summary")
    p_rep = sub.add_parser("check-replies", help="scan Gmail INBOX for replies (IMAP)")
    p_fu = sub.add_parser("followups", help="mark due follow-up drafts")

    args = ap.parse_args()
    DRYRUN = args.dry_run or os.environ.get("OUTREACH_DRYRUN") == "1"

    if args.cmd == "seed":
        with LOCK:
            res = seed_from_engine()
        print(f"Seeded {res['added']} new outreach record(s); skipped {res['skipped_existing']} existing. Total: {res['total']}")
        return 0
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "generate":
        return cmd_generate(args)
    if args.cmd == "serve":
        return cmd_serve(args.port)
    if args.cmd == "show":
        return cmd_show()
    if args.cmd == "check-replies":
        res = check_replies()
        print(json.dumps(res, indent=2)[:2000])
        return 0 if res.get("ok") else 1
    if args.cmd == "followups":
        print(f"Marked {mark_due_followups()} follow-up(s) as due.")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
