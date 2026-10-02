#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hiring_lead_hunter.py — Autonomous hiring-lead research + vacancy discovery.

ISOLATED MODULE. Creates/updates ONLY:
  hiring_lead_hunter.py, hiring_leads.xlsx, hiring_leads.md,
  hiring_lead_hunter_state.json, hiring_lead_hunter.log
(all next to this file, under D:\\newjobs).

READ-ONLY context: excel-rows.json, autoapply/jobs.json,
  whatsapp_outreach_queue.json, Job_Profile.TEMPLATE.md — never modified.

RESEARCH ONLY. Never sends WhatsApp/LinkedIn/email, never applies,
never clicks Send/Post/Submit/Apply. Final outreach is human-controlled.

Discovery: built-in query batches -> DuckDuckGo HTML (stdlib urllib, no key)
  -> candidate URLs -> BrowserOS MCP live verification (vacancy + contacts).
Seed: previously verified findings (2026-09-16 research pass) are embedded
  with evidence and re-verifiable via --run --live.

Commands:
  python hiring_lead_hunter.py --help
  python hiring_lead_hunter.py --dry-run     (plan + seed preview, no network)
  python hiring_lead_hunter.py --status      (dashboard)
  python hiring_lead_hunter.py --run         (seed + discovery batches; resumable)
  python hiring_lead_hunter.py --resume      (continue from saved state)
  python hiring_lead_hunter.py --export      (rewrite xlsx + md from state)
  python hiring_lead_hunter.py --report      (final report to stdout)
  python hiring_lead_hunter.py --reset-research
      (clear frontier/counters but KEEP verified leads)
"""

from __future__ import annotations

import argparse
import html as _html
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    HAVE_XLSX = True
except ImportError:
    HAVE_XLSX = False

# ---------------------------------------------------------------- paths

ROOT = Path(__file__).resolve().parent
EXCEL_ROWS = ROOT / "excel-rows.json"
ENGINE_JOBS = ROOT / "autoapply" / "jobs.json"
WA_QUEUE = ROOT / "whatsapp_outreach_queue.json"
STATE_FILE = ROOT / "hiring_lead_hunter_state.json"
XLSX_FILE = ROOT / "hiring_leads.xlsx"
MD_FILE = ROOT / "hiring_leads.md"
LOG_FILE = ROOT / "hiring_lead_hunter.log"

MCP_HOST, MCP_PORT, MCP_PATH = "127.0.0.1", 9210, "/mcp"
TARGET = 50

# ---------------------------------------------------------------- profile (verified subset of Job_Profile.TEMPLATE.md)

CANDIDATE = {
    "name": "Alex Morgan",
    "target": "Frontend Developer (React/Next.js, ~1.2 yr)",
    "skills": ["react", "javascript", "next.js", "nextjs", "typescript", "html",
               "css", "tailwind", "bootstrap", "redux", "rest api", "node.js",
               "nodejs", "express", "mongodb", "mern", "react native", "react flow",
               "ant design", "firebase", "postgresql", "ui", "frontend", "web developer"],
    "locations": ["remote", "mumbai", "pune", "bangalore", "bengaluru", "hyderabad",
                  "india", "noida", "kolkata", "delhi", "chennai", "gujarat"],
}

CLOSED_PHRASES = ("no longer accepting", "no longer available", "position has been filled",
                  "this job has been closed", "job is no longer", "not accepting applications",
                  "job closed", "expired", "position filled", "applications have closed",
                  "this position is no longer", "job has expired", "removed by the employer")
LIVE_PHRASES = ("apply now", "easy apply", "apply for this job", "apply for this role",
                "apply here", "apply to this", "application link", "submit your application",
                "interested candidates", "get referral", "actively reviewing",
                "accepting applications", "all openings", "open roles", "current openings",
                "we are hiring", "we're hiring", "posted ", "applicants")

CONTACT_TYPES = ("RECRUITER_WHATSAPP", "RECRUITER_PHONE", "RECRUITER_EMAIL",
                 "HIRING_MANAGER_LINKEDIN", "COMPANY_RECRUITMENT_EMAIL",
                 "COMPANY_RECRUITMENT_PHONE", "RECRUITMENT_AGENCY_CONTACT")
TYPE_RANK = {"RECRUITER_WHATSAPP": 0, "RECRUITER_PHONE": 1, "RECRUITER_EMAIL": 2,
             "COMPANY_RECRUITMENT_EMAIL": 3, "HIRING_MANAGER_LINKEDIN": 4,
             "COMPANY_RECRUITMENT_PHONE": 5, "RECRUITMENT_AGENCY_CONTACT": 6}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+91[\s\-]?|91[\s\-])?[6-9]\d{4}[\s\-]?\d{5}")
RECRUIT_MAIL_HINT = re.compile(r"(hr|career|job|recruit|talent|hiring|people|workwithus)[a-z0-9._%+-]*@", re.I)

# ---------------------------------------------------------------- query batches (rotated; exhausted ones are skipped via state)

QUERY_BATCHES = [
    ("vacancy/linkedin-naukri", [
        "React Developer hiring India LinkedIn jobs",
        "Frontend Developer hiring Mumbai Pune remote",
        "Next.js Developer hiring India",
        "JavaScript Developer hiring Bangalore Hyderabad",
        "MERN Developer hiring remote India",
    ]),
    ("vacancy/startup-boards", [
        "React Developer hiring Wellfound startup India",
        "Frontend Engineer hiring Cutshort Instahyre India",
        "Junior React Developer hiring immediate joiner India",
        "React Native Developer hiring India remote",
        "Frontend Developer fresher hiring India 2026",
    ]),
    ("contact/hiring-posts", [
        "React Developer hiring WhatsApp India",
        "Frontend Developer hiring WhatsApp Pune Mumbai",
        "React Developer recruiter email India hiring",
        "Frontend Developer HR email hiring India",
        "site:linkedin.com/posts React hiring WhatsApp",
    ]),
    ("contact/hr-email", [
        "JavaScript Developer recruiter contact email India",
        "React Developer talent acquisition email hiring",
        "Frontend Engineer hiring contact email startup India",
        "MERN developer hiring HR email",
        "site:linkedin.com/posts frontend developer hiring email",
    ]),
    ("vacancy/remote", [
        "Remote React Developer India startup hiring",
        "Remote Frontend Developer India SaaS hiring",
        "Frontend Developer work from home India hiring",
        "React Developer contract remote India",
        "UI Developer remote India hiring",
    ]),
    ("vacancy/ats-boards", [
        "React Developer jobs Lever Greenhouse India",
        "Frontend Developer jobs Ashby Workable India",
        "JavaScript Developer SmartRecruiters India hiring",
        "React hiring Lever.co India frontend",
        "Frontend hiring boards.greenhouse.io India",
    ]),
]

# ---------------------------------------------------------------- previously verified seed (2026-09-16 pass; evidence embedded)

SEED_LEADS = [
    {"company": "Synthires", "role": "Frontend Engineer (Remote)", "location": "Remote (India)",
     "remote": "yes", "jobUrl": "https://www.linkedin.com/jobs/view/4452227898/",
     "vacancyStatus": "CLOSED", "recruiterName": "Asin Edel Kuvin",
     "recruiterTitle": "Talent Acquisition Associate @ Synthires",
     "linkedinUrl": "https://www.linkedin.com/in/asin-edel-kuvin-a6b146293",
     "contactType": "HIRING_MANAGER_LINKEDIN", "confidence": "HIGH",
     "matchScore": 85, "matchReason": "Remote frontend, React/JS/TS evaluation work; profile strong fit.",
     "keyMatchingSkills": ["React", "JavaScript", "TypeScript"], "missingSkills": ["AI eval experience"],
     "vacEvidence": "Posting read live 2026-09-16: 'No longer accepting applications', application SUBMITTED.",
     "contactEvidence": "Hiring-team block lists Asin as Job poster w/ Message button; profile confirms role.",
     "vacSource": "https://www.linkedin.com/jobs/view/4452227898/",
     "contactSource": "https://www.linkedin.com/jobs/view/4452227898/"},
    {"company": "Add Technologies", "role": "JavaScript Developer", "location": "Hyderabad, India",
     "remote": "no", "jobUrl": "https://www.naukri.com/job-listings-javascript-developer-add-technologies-hyderabad-1-to-3-years-100426501356",
     "vacancyStatus": "CURRENT", "recruiterName": "", "recruiterTitle": "",
     "linkedinUrl": "", "phone": "", "email": "info@addtechno.com",
     "contactType": "COMPANY_RECRUITMENT_PHONE", "confidence": "HIGH",
     "matchScore": 78, "matchReason": "JS/CSS/Bootstrap/Vue, 1-3 yr matches profile.",
     "keyMatchingSkills": ["JavaScript", "CSS", "Bootstrap"], "missingSkills": ["Vue.js", "Angular"],
     "vacEvidence": "Naukri posting read live 2026-09-16: live, 100+ applicants, no closure text.",
     "contactEvidence": "Official contact page 'Call us at 99-66-44-2333' + info@addtechno.com (company line).",
     "vacSource": "https://www.naukri.com/job-listings-javascript-developer-add-technologies-hyderabad-1-to-3-years-100426501356",
     "contactSource": "https://addtechno.com/contact-us/", "company_phone": "+91 9876543210"},
    {"company": "Togetherv", "role": "Js Developer", "location": "Noida, India",
     "remote": "no", "jobUrl": "https://www.naukri.com/job-listings-js-developer-togetherv-noida-1-to-4-years-080725501974",
     "vacancyStatus": "CURRENT", "recruiterName": "", "recruiterTitle": "",
     "linkedinUrl": "", "phone": "", "email": "hr@togetherv.com",
     "contactType": "COMPANY_RECRUITMENT_EMAIL", "confidence": "MEDIUM",
     "matchScore": 75, "matchReason": "JS/HTML/CSS + React-family familiarity, 1-4 yr.",
     "keyMatchingSkills": ["JavaScript", "HTML", "CSS"], "missingSkills": ["React depth"],
     "vacEvidence": "Naukri posting read live 2026-09-16: live, 100+ applicants.",
     "contactEvidence": "Official careers page 'Mail to us: hr@togetherv.com'; contact page lists Mahesh Singh +91 9876543210 (company line).",
     "vacSource": "https://www.naukri.com/job-listings-js-developer-togetherv-noida-1-to-4-years-080725501974",
     "contactSource": "https://www.togetherv.com/careers", "company_phone": "+91 9876543210"},
    {"company": "Swastech Technologies LLP", "role": "React Native Developer",
     "location": "Kolkata, India", "remote": "no",
     "jobUrl": "https://www.linkedin.com/jobs/view/4463902553/",
     "vacancyStatus": "CLOSED", "recruiterName": "Moumita Das",
     "recruiterTitle": "Founder & CEO, Swastech Technologies LLP",
     "linkedinUrl": "https://www.linkedin.com/in/moumita-das-507609144",
     "phone": "", "email": "moumita@swastechinfo.com",
     "contactType": "COMPANY_RECRUITMENT_EMAIL", "confidence": "MEDIUM",
     "matchScore": 70, "matchReason": "React Native + React/JS overlap.",
     "keyMatchingSkills": ["React Native", "React", "JavaScript"], "missingSkills": ["TypeScript"],
     "vacEvidence": "LinkedIn posting read live 2026-09-16: 'Not currently accepting applications'.",
     "contactEvidence": "Official site footer CONTACT block (address + phone + this email); person = Founder/CEO per LinkedIn.",
     "vacSource": "https://www.linkedin.com/jobs/view/4463902553/",
     "contactSource": "https://swastechtechnologies.com/", "company_phone": "+91 9876543210"},
    {"company": "Tata Consultancy Services", "role": "Front End Developer (React)",
     "location": "Kolkata, India", "remote": "no",
     "jobUrl": "https://www.linkedin.com/jobs/view/4464196415/",
     "vacancyStatus": "CURRENT", "recruiterName": "", "recruiterTitle": "",
     "linkedinUrl": "", "phone": "", "email": "",
     "contactType": "", "confidence": "",
     "matchScore": 65, "matchReason": "React/JS/HTML/CSS match; large-org process hiring.",
     "keyMatchingSkills": ["React", "JavaScript", "HTML", "CSS"], "missingSkills": ["TypeScript"],
     "vacEvidence": "LinkedIn posting read live 2026-09-16: live, actively reviewing, 100+ applicants.",
     "contactEvidence": "No public recruiter contact found (broker number rejected as UNVERIFIED).",
     "vacSource": "https://www.linkedin.com/jobs/view/4464196415/",
     "contactSource": ""},
]

# ---------------------------------------------------------------- io


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
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


def blank_state() -> dict:
    return {"target": TARGET, "verified_count": 0, "current_count": 0,
            "status": "running", "last_run": "", "queries_completed": [],
            "urls_visited": [], "companies_seen": [], "contacts_seen": [],
            "frontier": [], "leads": [], "rejected": [], "duplicates": [],
            "statistics": {}, "batches_done": 0}


def load_state() -> dict:
    d = load_json(STATE_FILE, None)
    if not isinstance(d, dict):
        return blank_state()
    base = blank_state()
    base.update(d)
    return base


def save_state(d: dict) -> None:
    d["last_run"] = now_iso()
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


def to_e164(digits: str) -> str:
    return "+" + digits


# ---------------------------------------------------------------- BrowserOS MCP (minimal client)


class BOS:
    def __init__(self, label: str = "hiring-lead-hunter"):
        self.cid = 0
        self.sid = None
        obj = self._post({"jsonrpc": "2.0", "id": self._n(), "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": label, "version": "1.0"}}})
        if obj is None:
            raise RuntimeError("MCP initialize failed — is BrowserOS running?")

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
            return "ERROR: " + json.dumps(obj["error"])[:200], False
        parts = [c.get("text", "") for c in obj.get("result", {}).get("content", [])]
        return "\n".join(parts), True

    def open(self, url: str):
        text, ok = self.call("tabs", {"action": "new", "url": url})
        m = re.search(r"page (\d+)", text or "")
        return int(m.group(1)) if (ok and m) else None

    def close(self, page: int) -> None:
        try:
            self.call("tabs", {"action": "close", "page": page})
        except Exception:
            pass

    def read_text(self, page: int) -> str:
        text, ok = self.call("read", {"page": page, "format": "text"})
        return text if ok else ""


# ---------------------------------------------------------------- discovery (stdlib DuckDuckGo HTML, no key)

DDG_URL = "https://html.duckduckgo.com/html/?q="


def ddg_search(query: str, max_results: int = 12) -> list:
    """Return [(title, url)] from DuckDuckGo HTML endpoint. Empty list on failure."""
    out: list = []
    try:
        req = urllib.request.Request(
            DDG_URL + urllib.parse.quote(query),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            html = resp.read().decode("utf-8", "replace")
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                             html, re.S):
            raw, title = m.group(1), re.sub(r"<.*?>", "", m.group(2)).strip()
            title = _html.unescape(title)
            if raw.startswith("//duckduckgo.com/l/?uddg="):
                raw = urllib.parse.parse_qs(
                    urllib.parse.urlparse("https:" + raw).query).get("uddg", [""])[0]
                raw = urllib.parse.unquote(raw)
            if raw.startswith("http") and "duckduckgo.com" not in raw:
                out.append((title, raw))
            if len(out) >= max_results:
                break
    except Exception as exc:
        log(f"WARN: search failed [{query[:60]}]: {str(exc)[:120]}")
    return out


# ---------------------------------------------------------------- match scoring


def match_score(title: str, text: str, location: str = "") -> tuple[int, str, list, list]:
    t = f"{title} {text}".lower().replace("–", "-").replace("—", "-")
    weights = {"react": 12, "frontend": 10, "front-end": 10, "front end": 10,
               "javascript": 8, "next.js": 8, "nextjs": 8, "typescript": 5,
               "tailwind": 5, "html": 3, "css": 3, "redux": 4, "node": 4, "mern": 6,
               "react native": 6, "ui developer": 6, "web developer": 5,
               "rest api": 4, "immediate joiner": 4, "0-2 years": 4, "1-3 years": 3,
               "2+ years": 3, "2 years": 3,
               "fresher": 3, "junior": 3, "entry-level": 3, "entry level": 3}
    score, hits = 0, []
    for kw, w in weights.items():
        if kw in t:
            score += w
            hits.append(kw)
    loc = (location + " " + t)
    loc_hit = any(l in loc for l in CANDIDATE["locations"])
    if loc_hit:
        score += 6
    if "flutter" in t and "react" not in t:
        return 0, "excluded: Flutter-only", [], ["flutter-only"]
    if re.search(r"[6-9]\s*[-–]\s*\d+\s*years|[6-9]\+?\s*years", t):
        score -= 15
    score = max(0, min(100, score))
    missing = [s for s in ("typescript", "next.js", "redux", "node.js") if s not in t]
    reason = f"keyword fit ({', '.join(hits[:6]) or 'generic frontend'})" \
             f"{'; India/remote' if loc_hit else ''}"
    return score, reason, hits[:8], missing


# ---------------------------------------------------------------- vacancy + contact verification via BrowserOS


def verify_page(url: str, settle: float = 5.0) -> dict:
    """Open a public page, return {ok, text, snapshot, title_hint}."""
    try:
        bos = BOS("lead-verify")
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "text": "", "snapshot": ""}
    page = bos.open(url)
    if page is None:
        return {"ok": False, "error": "tabs/new failed", "text": "", "snapshot": ""}
    time.sleep(settle)
    snap, _ = bos.call("snapshot", {"page": page})
    text = bos.read_text(page)
    bos.close(page)
    return {"ok": True, "text": text or "", "snapshot": snap or ""}


def vacancy_status(text: str) -> str:
    low = text.lower()
    closed = any(p in low for p in CLOSED_PHRASES)
    live = any(p in low for p in LIVE_PHRASES)
    # Closure words often leak from sidebars ("expired jobs" links); only trust
    # them when the page shows no live-application signal at all.
    if closed and not live:
        return "CLOSED"
    if live:
        return "CURRENT"
    return "UNCERTAIN"


def extract_contacts(text: str, company: str) -> list:
    """Public-page contact extraction. Conservative by design:
    - strips the MCP content wrapper + all URLs first (kills LinkedIn
      activity IDs, job IDs, tracking numbers being read as phones);
    - bare 10-digit runs are IGNORED unless they carry +91, separators,
      or sit next to phone-words (call/phone/whatsapp/mobile/contact)."""
    clean = re.sub(r"\[UNTRUSTED_PAGE_CONTENT.*?\]\s*", " ", text)
    clean = re.sub(r"\[END_UNTRUSTED_PAGE_CONTENT[^\]]*\]", " ", clean)
    clean = re.sub(r"nonce=[A-Za-z0-9]+", " ", clean)
    urls = re.findall(r"https?://\S+|www\.\S+", clean)
    clean_nourl = re.sub(r"https?://\S+|www\.\S+", " ", clean)
    found: list = []
    for m in set(re.findall(r"wa\.me/(\d{10,15})", " ".join(urls) + " " + clean)):
        i = clean.find(m)
        found.append({"kind": "phone", "value": m, "explicit": True,
                      "ctx": "wa.me link published on page"})
    for em in set(EMAIL_RE.findall(clean_nourl)):
        el = em.lower()
        if any(bad in el for bad in ("example.", "sentry", "wixpress", ".png", ".jpg",
                                     "schema.", "wordpress", "godaddy", "lorem")):
            continue
        kind = "recruit_mail" if RECRUIT_MAIL_HINT.search(el) else "mail"
        i = clean_nourl.lower().find(el)
        found.append({"kind": kind, "value": em, "explicit": True,
                      "ctx": clean_nourl[max(0, i - 80):i + 120].replace("\n", " ")[:200]})
    for pm in set(re.findall(r"\+91[\s\-]?\d{5}[\s\-]?\d{5}|\b91[\s\-][6-9]\d{4}[\s\-]\d{5}\b",
                             clean_nourl)):
        norm = normalize_phone(pm)
        if norm:
            i = clean_nourl.find(pm)
            found.append({"kind": "phone", "value": norm, "explicit": True,
                          "ctx": clean_nourl[max(0, i - 80):i + 120].replace("\n", " ")[:200]})
    for pm in set(re.findall(r"\b[6-9]\d{4}[\s\-]\d{5}\b", clean_nourl)):
        norm = normalize_phone(pm)
        if norm and norm not in {f["value"] for f in found}:
            i = clean_nourl.find(pm)
            found.append({"kind": "phone", "value": norm, "explicit": True,
                          "ctx": clean_nourl[max(0, i - 80):i + 120].replace("\n", " ")[:200]})
    for pm in set(re.findall(r"\b[6-9]\d{9}\b", clean_nourl)):
        i = clean_nourl.find(pm)
        window = clean_nourl[max(0, i - 60):i + 60].lower()
        if re.search(r"(call|phone|mobile|whatsapp|contact|tel|ph\.?[\s:])", window):
            norm = normalize_phone(pm)
            if norm and norm not in {f["value"] for f in found}:
                found.append({"kind": "phone", "value": norm, "explicit": False,
                              "ctx": window[:200]})
    return found


def classify_contact(c: dict, company: str, page_url: str, page_title: str) -> dict | None:
    """Map raw findings to contact types. Returns None when evidence is weak.

    A bare phone/email on a vacancy page is NOT enough: it must either be a
    hiring-channel address (hr/careers/jobs/…) or sit next to hiring language,
    or live on an official careers/contact page. Everything else is rejected
    (prevents personal mobiles, IDs, and footer noise becoming 'leads')."""
    kind, val = c["kind"], c["value"]
    official = bool(re.search(r"(career|contact|about|hiring|recruit)", page_url, re.I))
    ctx = (c.get("ctx") or "").lower()
    hiring_ctx = bool(re.search(r"(hir|recruit|career|apply|vacan|talent|hr\b|resume|cv\b)", ctx))
    if kind == "recruit_mail":
        return {"contactType": "COMPANY_RECRUITMENT_EMAIL", "email": val, "phone": "",
                "confidence": "HIGH" if official else "MEDIUM"}
    if kind == "mail":
        return None  # generic mailbox without hiring tie -> not counted
    if kind == "phone":
        if "wa.me" in ctx or "whatsapp" in ctx:
            # Number explicitly published as a WhatsApp hiring line (e.g. a
            # recruiter's "WhatsApp: 98XXX XXXXX" in a hiring post).
            return {"contactType": "RECRUITER_WHATSAPP", "email": "", "phone": val,
                    "confidence": "HIGH" if (official or hiring_ctx) else "MEDIUM"}
        if c.get("explicit") and hiring_ctx:
            return {"contactType": "RECRUITER_PHONE", "email": "", "phone": val,
                    "confidence": "MEDIUM"}
        if c.get("explicit") and official:
            return {"contactType": "COMPANY_RECRUITMENT_PHONE", "email": "", "phone": val,
                    "confidence": "HIGH"}
        return None
    return None


# ---------------------------------------------------------------- lead records + dedupe


def next_lead_id(state: dict) -> str:
    nums = [int(re.search(r"HL(\d+)", l.get("leadId", "")).group(1))
            for l in state["leads"] if re.search(r"HL(\d+)", l.get("leadId", ""))]
    return f"HL{(max(nums) if nums else 0) + 1:03d}"


def duplicate_of(state: dict, company: str, role: str, job_url: str,
                 phone: str, email: str, linkedin: str) -> str | None:
    cu = (job_url or "").split("?")[0].rstrip("/").lower()
    cnorm = norm_company(company)
    company_known = bool(company and cnorm not in ("unknown", "")
                         and len(company.strip()) >= 3
                         and cnorm != norm_company(role)
                         and not re.search(r"(developer|engineer|hiring|frontend|manager|designer)\b",
                                            cnorm))
    role_specific = bool(re.search(r"(react|frontend|front end|full.?stack|developer|"
                                   r"engineer|\bui\b|javascript|mern|next)", (role or "").lower()))
    for l in state["leads"]:
        if cu and (l.get("canonicalUrl", "") or "").split("?")[0].rstrip("/").lower() == cu:
            return l["leadId"]
        if company_known and role_specific \
                and norm_company(l.get("company")) == norm_company(company) \
                and (l.get("role") or "").strip().lower() == (role or "").strip().lower():
            return l["leadId"]
        if phone and l.get("phone") == phone and phone:
            return l["leadId"]
        if email and (l.get("email") or "").lower() == email.lower() and email:
            return l["leadId"]
        if linkedin and (l.get("linkedinUrl") or "").rstrip("/").lower() == linkedin.rstrip("/").lower():
            return l["leadId"]
    return None


def recommend_channel(contact_type: str) -> str:
    order = ["RECRUITER_WHATSAPP", "RECRUITER_PHONE", "RECRUITER_EMAIL",
             "COMPANY_RECRUITMENT_EMAIL", "HIRING_MANAGER_LINKEDIN",
             "COMPANY_RECRUITMENT_PHONE"]
    return contact_type if contact_type in order else "NEEDS_CONTACT"


def add_lead(state: dict, *, company: str, role: str, location: str, remote: str,
             job_url: str, source_urls: list, posted_date: str, vacancy_status_: str,
             recruiter: str, recruiter_title: str, linkedin: str, phone: str,
             email: str, contact_type: str, contact_url: str, contact_title: str,
             contact_evidence: str, confidence: str, score: int, reason: str,
             skills: list, missing: list, notes: str = "") -> dict | None:
    dup = duplicate_of(state, company, role, job_url, phone, email, linkedin)
    if dup:
        state["duplicates"].append({"company": company, "role": role, "url": job_url,
                                    "duplicateOf": dup, "ts": now_iso()})
        return None
    lid = next_lead_id(state)
    lead = {"leadId": lid, "queueId": "", "company": company, "role": role,
            "location": location, "remote": remote, "jobUrl": job_url,
            "canonicalUrl": (job_url or "").split("?")[0],
            "sourceUrls": source_urls or ([job_url] if job_url else []),
            "postedDate": posted_date, "lastCheckedAt": now_iso()[:10],
            "vacancyStatus": vacancy_status_, "recruiterName": recruiter,
            "recruiterTitle": recruiter_title, "linkedinUrl": linkedin,
            "phone": to_e164(phone) if phone else "", "email": email,
            "contactType": contact_type, "contactSourceUrl": contact_url,
            "contactSourceTitle": contact_title, "contactEvidence": contact_evidence,
            "confidence": confidence, "matchScore": score, "matchReason": reason,
            "keyMatchingSkills": skills, "missingSkills": missing,
            "recommendedChannel": recommend_channel(contact_type),
            "researchStatus": "VERIFIED" if vacancy_status_ == "CURRENT" and contact_type else "PARTIAL",
            "lastAction": "added", "notes": notes}
    state["leads"].append(lead)
    return lead


def reject(state: dict, company: str, role: str, url: str, reason: str) -> None:
    state["rejected"].append({"company": company, "role": role, "url": url,
                              "reason": reason, "ts": now_iso()})


# ---------------------------------------------------------------- seed import (previously verified evidence)


def seed_import(state: dict) -> int:
    if state.get("seed_done"):
        return 0
    n = 0
    for s in SEED_LEADS:
        dup = duplicate_of(state, s["company"], s["role"], s["jobUrl"], "", "", "")
        if dup:
            continue
        lid = next_lead_id(state)
        lead = {"leadId": lid, "queueId": "", "company": s["company"], "role": s["role"],
                "location": s["location"], "remote": s["remote"], "jobUrl": s["jobUrl"],
                "canonicalUrl": s["jobUrl"].split("?")[0], "sourceUrls": [s["jobUrl"]],
                "postedDate": "", "lastCheckedAt": "2026-09-16",
                "vacancyStatus": s["vacancyStatus"], "recruiterName": s.get("recruiterName", ""),
                "recruiterTitle": s.get("recruiterTitle", ""),
                "linkedinUrl": s.get("linkedinUrl", ""),
                "phone": to_e164(s["company_phone"]) if s.get("company_phone") else "",
                "email": s.get("email", ""), "contactType": s.get("contactType", "") or "",
                "contactSourceUrl": s.get("contactSource", ""),
                "contactSourceTitle": "2026-09-16 BrowserOS verification pass",
                "contactEvidence": s.get("contactEvidence", ""),
                "confidence": s.get("confidence", ""),
                "matchScore": s["matchScore"], "matchReason": s["matchReason"],
                "keyMatchingSkills": s["keyMatchingSkills"], "missingSkills": s["missingSkills"],
                "recommendedChannel": recommend_channel(s.get("contactType", "") or "NEEDS_CONTACT")
                if s.get("contactType") else "NEEDS_CONTACT",
                "researchStatus": "VERIFIED" if s["vacancyStatus"] == "CURRENT" and s.get("contactType")
                else "PARTIAL",
                "lastAction": "seed-import", "notes": "Vacancy evidence: " + s.get("vacEvidence", "")}
        state["leads"].append(lead)
        n += 1
    state["seed_done"] = True
    log(f"seed imported {n} previously-verified leads")
    return n


# ---------------------------------------------------------------- research loop


def research_batch(state: dict, batch_name: str, queries: list, dry_run: bool = False,
                   max_pages: int = 10) -> dict:
    stat = {"batch": batch_name, "queries": 0, "pages": 0, "new": 0,
            "duplicates": 0, "rejected": 0}
    # Frontier first: agent-fed {title, url, query} candidates (state-only, no extra files).
    while state.get("frontier") and stat["pages"] < max_pages and not dry_run:
        cand = state["frontier"].pop(0)
        url = cand.get("url", "")
        if not url or url in state["urls_visited"]:
            stat["duplicates"] += 1
            continue
        state["urls_visited"].append(url)
        stat["pages"] += 1
        outcome = process_candidate(state, cand.get("title", url), url,
                                    query=cand.get("query", "frontier"), dry_run=dry_run)
        stat[outcome] += 1
        save_state(state)
        log(f"batch[{batch_name}] frontier: {url[:80]} -> {outcome}")
    for q in queries:
        if q in state["queries_completed"]:
            continue
        stat["queries"] += 1
        res = [] if dry_run else ddg_search(q)
        if not dry_run:
            state["queries_completed"].append(q)
        visited = 0
        for title, url in res:
            if stat["pages"] >= max_pages:
                break
            if url in state["urls_visited"]:
                stat["duplicates"] += 1
                continue
            if any(x in url for x in ("youtube.com", "facebook.com/login", "instagram.com/accounts")):
                continue
            state["urls_visited"].append(url)
            visited += 1
            stat["pages"] += 1
            outcome = process_candidate(state, title, url, query=q, dry_run=dry_run)
            stat[outcome] += 1
            save_state(state)
        log(f"batch[{batch_name}] query done: '{q[:60]}' pages={visited} "
            f"new={stat['new']} dup={stat['duplicates']} rej={stat['rejected']}")
    state["batches_done"] = state.get("batches_done", 0) + 1
    save_state(state)
    return stat


def process_candidate(state: dict, title: str, url: str, query: str = "",
                      dry_run: bool = False) -> str:
    """Returns 'new', 'duplicates' or 'rejected'."""
    if dry_run:
        return "rejected"
    pg = verify_page(url)
    if not pg["ok"]:
        reject(state, "", "", url, f"page open failed: {pg.get('error', '')[:80]}")
        return "rejected"
    text = pg["text"] or ""
    if len(text) < 400:
        reject(state, "", "", url, "page unreadable/empty (login wall or JS-only)")
        return "rejected"
    vstat = vacancy_status(text)
    if vstat == "CLOSED":
        reject(state, "", "", url, "vacancy CLOSED per page text")
        return "rejected"
    # company/role guess from title + page
    m = re.match(r"\s*(.+?)\s+ hiring (.+?) (?:in|at|for) (.+)", title, re.I)
    company = guess_company(text, title)
    role = guess_role(title, text)
    location = guess_location(text, title)
    score, reason, skills, missing = match_score(role + " " + title, text, location)
    if score < 50:
        reject(state, company, role, url, f"match {score} < 50 or Flutter-only")
        return "rejected"
    if "flutter" in (role + title).lower() and "react" not in (role + title).lower():
        reject(state, company, role, url, "Flutter-only role")
        return "rejected"
    contacts = extract_contacts(text, company)
    best, best_ev = None, ""
    for c in contacts:
        cl = classify_contact(c, company, url, title)
        if cl and (best is None or TYPE_RANK[cl["contactType"]] < TYPE_RANK[best["contactType"]]):
            best, best_ev = cl, f"Found '{c['value']}' on vacancy page ({c.get('ctx', '')[:150]})."
    if best is None:
        # vacancy usable but contactless -> keep as PARTIAL lead (not counted verified)
        if vstat == "CURRENT":
            lead = add_lead(state, company=company, role=role, location=location,
                            remote="yes" if "remote" in location.lower() else "unknown",
                            job_url=url, source_urls=[url], posted_date="",
                            vacancy_status_="CURRENT", recruiter="", recruiter_title="",
                            linkedin="", phone="", email="", contact_type="",
                            contact_url="", contact_title="", contact_evidence="",
                            confidence="", score=score, reason=reason,
                            skills=skills, missing=missing,
                            notes="Vacancy CURRENT, contact hunt pending.")
            return "new" if lead else "duplicates"
        reject(state, company, role, url, "no verifiable contact and vacancy not CURRENT")
        return "rejected"
    lead = add_lead(state, company=company, role=role, location=location,
                    remote="yes" if "remote" in location.lower() else "unknown",
                    job_url=url, source_urls=[url], posted_date="",
                    vacancy_status_=vstat,
                    recruiter="", recruiter_title="", linkedin="",
                    phone=best.get("phone", ""), email=best.get("email", ""),
                    contact_type=best["contactType"], contact_url=url,
                    contact_title=title[:150], contact_evidence=best_ev,
                    confidence=best.get("confidence", "MEDIUM"), score=score,
                    reason=reason, skills=skills, missing=missing)
    return "new" if lead else "duplicates"


def guess_company(text: str, title: str) -> str:
    for pat in (r"(?:at|@|hiring (?:at|for)|join)\s+([A-Z][A-Za-z0-9&.\- ]{2,40})",
                r"([A-Z][A-Za-z0-9&.\- ]{2,40})\s+(?:is hiring|careers|hiring)",
                r"About\s+([A-Z][A-Za-z0-9&.\- ]{2,40})",
                r"©\s*\d{4}\s+([A-Z][A-Za-z0-9&.\- ]{2,40})",
                r"We are ([A-Z][A-Za-z0-9&.\- ]{2,40})[,.]",
                # LinkedIn hiring posts: author's employer is the hiring company
                r"(?:Talent Acquisition|Talent Aquisition|Human Resources|HR Manager|"
                r"HR Executive|Recruiter|Chief of Staff|Founder|Co-Founder|CTO|CEO)"
                r"[^@\n]{0,40}?\bat\s+([A-Z][A-Za-z0-9&.\- ]{2,40})",
                r"\[([A-Z][A-Za-z0-9&.\- ]{2,30})\]"):
        m = re.search(pat, (title + "\n" + text[:3000]))
        if m:
            cand = m.group(1).strip()
            if not re.search(r"^(the|this|our|your|for|with|from|role|job|team)\b", cand, re.I):
                return cand
    return "UNKNOWN"


def guess_role(title: str, text: str) -> str:
    pat = (r"React Native Developer|Frontend Engineer|Front End Developer|"
           r"Frontend Developer|React Developer|Next\.js Developer|JavaScript Developer|"
           r"MERN Developer|Full Stack Developer|UI Developer|Web Developer|"
           r"Software Engineer[^|,]{0,30}")
    m = re.search(pat, title + " " + text[:1500], re.I)
    if m:
        return m.group(0).strip()
    return title[:80]


def guess_location(text: str, title: str) -> str:
    blob = (title + " " + text[:3000]).lower()
    if "remote" in blob:
        for city in ("india", "mumbai", "pune", "bangalore", "bengaluru", "hyderabad",
                     "noida", "delhi", "kolkata", "chennai"):
            if city in blob:
                return f"Remote ({city.title()})"
        return "Remote"
    for city in ("mumbai", "pune", "bangalore", "bengaluru", "hyderabad", "noida",
                 "delhi", "kolkata", "chennai", "gurgaon", "ahmedabad"):
        if city in blob:
            return city.title() + ", India"
    return "India"


# ---------------------------------------------------------------- statistics


def compute_stats(state: dict) -> dict:
    leads = state["leads"]
    verified = [l for l in leads if l.get("researchStatus") == "VERIFIED"]
    current = [l for l in leads if l.get("vacancyStatus") == "CURRENT"]
    by_type: dict[str, int] = {}
    for l in verified:
        by_type[l.get("contactType", "?")] = by_type.get(l.get("contactType", "?"), 0) + 1
    stats = {"target": TARGET, "verified": len(verified), "current": len(current),
             "remaining": max(0, TARGET - len(verified)),
             "by_type": by_type, "total_leads": len(leads),
             "rejected": len(state["rejected"]), "duplicates": len(state["duplicates"]),
             "queries": len(state["queries_completed"]),
             "pages": len(state["urls_visited"]), "batches": state.get("batches_done", 0)}
    state["statistics"] = stats
    state["verified_count"] = len(verified)
    state["current_count"] = len(current)
    return stats


# ---------------------------------------------------------------- Excel + Markdown export


def export_excel(state: dict) -> bool:
    if not HAVE_XLSX:
        log("WARN: openpyxl missing — skipping xlsx (pip install openpyxl).")
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "Verified Leads"
    cols = ["leadId", "queueId", "company", "role", "location", "remote", "jobUrl",
            "canonicalUrl", "sourceUrls", "postedDate", "lastCheckedAt", "vacancyStatus",
            "recruiterName", "recruiterTitle", "linkedinUrl", "phone", "email",
            "contactType", "contactSourceUrl", "contactSourceTitle", "contactEvidence",
            "confidence", "matchScore", "matchReason", "keyMatchingSkills",
            "missingSkills", "recommendedChannel", "researchStatus", "lastAction", "notes"]
    hdr = PatternFill("solid", fgColor="1F4E78")
    for j, c in enumerate(cols, 1):
        cell = ws.cell(row=1, column=j, value=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = hdr
    for i, l in enumerate(state["leads"], 2):
        for j, c in enumerate(cols, 1):
            v = l.get(c, "")
            if isinstance(v, list):
                v = "; ".join(str(x) for x in v)
            ws.cell(row=i, column=j, value=v)
    ws2 = wb.create_sheet("Contact Summary")
    c2 = ["leadId", "company", "contactName", "contactType", "phone", "email",
          "linkedinUrl", "sourceUrl", "confidence", "verificationStatus"]
    for j, c in enumerate(c2, 1):
        cell = ws2.cell(row=1, column=j, value=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = hdr
    for i, l in enumerate(state["leads"], 2):
        ws2.cell(row=i, column=1, value=l.get("leadId", ""))
        ws2.cell(row=i, column=2, value=l.get("company", ""))
        ws2.cell(row=i, column=3, value=l.get("recruiterName", ""))
        ws2.cell(row=i, column=4, value=l.get("contactType", ""))
        ws2.cell(row=i, column=5, value=l.get("phone", ""))
        ws2.cell(row=i, column=6, value=l.get("email", ""))
        ws2.cell(row=i, column=7, value=l.get("linkedinUrl", ""))
        ws2.cell(row=i, column=8, value=l.get("contactSourceUrl", ""))
        ws2.cell(row=i, column=9, value=l.get("confidence", ""))
        ws2.cell(row=i, column=10, value=l.get("researchStatus", ""))
    ws3 = wb.create_sheet("Research Log")
    c3 = ["timestamp", "query", "source", "pagesVisited", "newLeads", "duplicates",
          "rejected", "reason", "notes"]
    for j, c in enumerate(c3, 1):
        cell = ws3.cell(row=1, column=j, value=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = hdr
    for i, r in enumerate(state.get("research_log", []), 2):
        for j, c in enumerate(c3, 1):
            ws3.cell(row=i, column=j, value=r.get(c, ""))
    ws4 = wb.create_sheet("Statistics")
    st = state.get("statistics", {})
    rows = [("Target leads", TARGET), ("Verified leads found", st.get("verified", 0)),
            ("Current vacancies", st.get("current", 0)),
            ("Remaining target", st.get("remaining", TARGET)),
            ("Total lead rows", st.get("total_leads", 0)),
            ("Rejected records", st.get("rejected", 0)),
            ("Duplicate records", st.get("duplicates", 0)),
            ("Queries completed", st.get("queries", 0)),
            ("Pages visited", st.get("pages", 0)),
            ("Batches done", st.get("batches", 0))]
    for k, v in (st.get("by_type") or {}).items():
        rows.append((f"contact:{k}", v))
    for i, (k, v) in enumerate(rows, 1):
        ws4.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws4.cell(row=i, column=2, value=v)
    wb.save(XLSX_FILE)
    log(f"exported {XLSX_FILE.name} ({len(state['leads'])} leads)")
    return True


def export_markdown(state: dict) -> None:
    lines = ["# Hiring Leads — Verified Research", "",
             f"Updated: {now_iso()} | Target: {TARGET} | "
             f"Verified: {state.get('verified_count', 0)}", ""]
    for l in state["leads"]:
        skills = ", ".join(l.get("keyMatchingSkills", []) or [])
        missing = ", ".join(l.get("missingSkills", []) or [])
        lines += [
            f"## {l.get('leadId', '')} — {l.get('company', '')} — {l.get('role', '')}", "",
            f"* Location: {l.get('location', '')}", f"* Remote: {l.get('remote', '')}",
            f"* Vacancy status: {l.get('vacancyStatus', '')}",
            f"* Job URL: {l.get('jobUrl', '')}",
            f"* Posted date: {l.get('postedDate', '') or 'unknown'}",
            f"* Last checked: {l.get('lastCheckedAt', '')}",
            f"* Recruiter: {l.get('recruiterName', '') or '-'}",
            f"* Recruiter title: {l.get('recruiterTitle', '') or '-'}",
            f"* LinkedIn: {l.get('linkedinUrl', '') or '-'}",
            f"* Phone: {l.get('phone', '') or '-'}",
            f"* Email: {l.get('email', '') or '-'}",
            f"* Contact type: {l.get('contactType', '') or 'NONE'}",
            f"* Contact source: {l.get('contactSourceTitle', '')} <{l.get('contactSourceUrl', '')}>",
            f"* Vacancy source: <{l.get('jobUrl', '')}>",
            f"* Confidence: {l.get('confidence', '') or '-'}",
            f"* Match score: {l.get('matchScore', '')}",
            f"* Matching skills: {skills}", f"* Missing skills: {missing}",
            f"* Recommended channel: {l.get('recommendedChannel', '')}",
            f"* Evidence: {l.get('contactEvidence', '') or l.get('notes', '')}",
            f"* Notes: {l.get('notes', '')}", ""]
    MD_FILE.write_text("\n".join(lines), encoding="utf-8")
    log(f"exported {MD_FILE.name}")


# ---------------------------------------------------------------- dashboard + report


def show_status(state: dict) -> None:
    st = compute_stats(state)
    by = st.get("by_type", {})
    print("HIRING LEAD HUNTER\n")
    print(f"Target: {TARGET} verified leads")
    print(f"Verified: {st['verified']}")
    print(f"Remaining: {st['remaining']}\n")
    print(f"Current vacancies: {st['current']}")
    for k in ("RECRUITER_WHATSAPP", "RECRUITER_PHONE", "RECRUITER_EMAIL",
              "COMPANY_RECRUITMENT_EMAIL", "HIRING_MANAGER_LINKEDIN",
              "COMPANY_RECRUITMENT_PHONE", "RECRUITMENT_AGENCY_CONTACT"):
        print(f"{k}: {by.get(k, 0)}")
    print(f"\nPages searched: {st['pages']}")
    print(f"Queries completed: {st['queries']}")
    print(f"Duplicates skipped: {st['duplicates']}")
    print(f"Rejected: {st['rejected']}")
    print(f"State: {state.get('status', '?')}")


def final_report(state: dict) -> None:
    st = compute_stats(state)
    print("=" * 60 + "\nFINAL REPORT — HIRING LEAD HUNTER\n" + "=" * 60)
    print(f"1. Total verified leads: {st['verified']}")
    print(f"2. Total current vacancies: {st['current']}")
    by = st.get("by_type", {})
    print(f"3. Recruiter WhatsApp: {by.get('RECRUITER_WHATSAPP', 0)}")
    print(f"4. Recruiter phone: {by.get('RECRUITER_PHONE', 0)}")
    print(f"5. Recruiter emails: {by.get('RECRUITER_EMAIL', 0)}")
    print(f"6. Company recruitment emails: {by.get('COMPANY_RECRUITMENT_EMAIL', 0)}")
    print(f"7. LinkedIn recruiter contacts: {by.get('HIRING_MANAGER_LINKEDIN', 0)}")
    print(f"8. Company contacts: {by.get('COMPANY_RECRUITMENT_PHONE', 0)}")
    print(f"9. Duplicates: {st['duplicates']}")
    print(f"10. Rejected: {st['rejected']}")
    print(f"11. Remaining target: {st['remaining']}")
    print(f"12. Sources used: agent web-search batches + BrowserOS MCP live verification + prior verified pass")
    print("13. Top leads:")
    ranked = sorted(
        [l for l in state["leads"] if l.get("researchStatus") == "VERIFIED"],
        key=lambda l: (TYPE_RANK.get(l.get("contactType", ""), 99), -(l.get("matchScore") or 0)))[:10]
    for l in ranked:
        print(f"  {l['leadId']} {l['company']} — {l['role']} "
              f"[{l.get('contactType')}] {l.get('phone') or l.get('email') or l.get('linkedinUrl')}")


# ---------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Hiring lead hunter (research only, human send).")
    ap.add_argument("--dry-run", action="store_true", help="plan only, no network/browser")
    ap.add_argument("--status", action="store_true", help="dashboard")
    ap.add_argument("--run", action="store_true", help="seed + discovery batches (resumable)")
    ap.add_argument("--resume", action="store_true", help="continue from saved state")
    ap.add_argument("--export", action="store_true", help="rewrite xlsx + md from state")
    ap.add_argument("--report", action="store_true", help="final report")
    ap.add_argument("--reset-research", action="store_true",
                    help="clear frontier/counters, KEEP verified leads")
    ap.add_argument("--live", action="store_true", help="with --run: live BrowserOS verification")
    ap.add_argument("--max-batches", type=int, default=6)
    ap.add_argument("--max-pages", type=int, default=10)
    return ap


def cmd_run(args, state: dict) -> int:
    seed_import(state)
    save_state(state)
    if args.dry_run:
        nb = sum(len(q) for _, q in QUERY_BATCHES)
        log(f"DRY-RUN plan: {len(QUERY_BATCHES)} batches, {nb} queries, "
            f"{len(state['leads'])} seeded leads. No network calls made.")
        export_excel(state)
        export_markdown(state)
        compute_stats(state)
        save_state(state)
        return 0
    done = 0
    if state.get("frontier") and not args.dry_run:
        log(f"--- FRONTIER CATCH-UP: {len(state['frontier'])} queued pages ---")
        stat = research_batch(state, "frontier-catchup", [], dry_run=False,
                              max_pages=args.max_pages)
        state.setdefault("research_log", []).append(
            {"timestamp": now_iso(), "query": "frontier-catchup", "source": "agent+browseros",
             "pagesVisited": stat["pages"], "newLeads": stat["new"],
             "duplicates": stat["duplicates"], "rejected": stat["rejected"],
             "reason": "", "notes": ""})
        compute_stats(state)
        export_excel(state)
        export_markdown(state)
        save_state(state)
        show_status(state)
        done += 1
    for name, queries in QUERY_BATCHES:
        if done >= args.max_batches:
            break
        if all(q in state["queries_completed"] for q in queries):
            continue
        log(f"--- BATCH {state.get('batches_done', 0) + 1}: {name} ---")
        stat = research_batch(state, name, queries, dry_run=False, max_pages=args.max_pages)
        state.setdefault("research_log", []).append(
            {"timestamp": now_iso(), "query": f"batch:{name}", "source": "ddg+browseros",
             "pagesVisited": stat["pages"], "newLeads": stat["new"],
             "duplicates": stat["duplicates"], "rejected": stat["rejected"],
             "reason": "", "notes": ""})
        compute_stats(state)
        export_excel(state)
        export_markdown(state)
        save_state(state)
        show_status(state)
        done += 1
        if state["statistics"]["verified"] >= TARGET:
            state["status"] = "complete"
            save_state(state)
            break
    return 0


def main() -> int:
    args = build_parser().parse_args()
    state = load_state()
    if args.reset_research:
        keep = state["leads"]
        fresh = blank_state()
        fresh["leads"] = keep
        fresh["seed_done"] = state.get("seed_done", False)
        state = fresh
        compute_stats(state)
        save_state(state)
        log(f"research reset; kept {len(keep)} verified leads")
        return 0
    if args.status:
        show_status(state)
        return 0
    if args.report:
        final_report(state)
        return 0
    if args.export:
        compute_stats(state)
        export_excel(state)
        export_markdown(state)
        save_state(state)
        return 0
    if args.dry_run and not (args.run or args.resume):
        seed_import(state)
        compute_stats(state)
        export_excel(state)
        export_markdown(state)
        save_state(state)
        nb = sum(len(q) for _, q in QUERY_BATCHES)
        log(f"DRY-RUN: {len(state['leads'])} seeded leads; plan {len(QUERY_BATCHES)} "
            f"batches / {nb} queries. No network calls made.")
        show_status(state)
        return 0
    if args.run or args.resume or args.dry_run:
        return cmd_run(args, state)
    build_parser().print_help()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped — state saved, resume with --resume.")
        try:
            save_state(load_state())
        except Exception:
            pass
        sys.exit(130)
