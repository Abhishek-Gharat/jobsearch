
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whatsapp_outreach.py — Recruiter WhatsApp outreach companion.

ISOLATED ADD-ON. Runs separately from the existing pipeline:
  - READS (never writes):  D:\\newjobs\\excel-rows.json  (master job queue)
                           D:\\newjobs\\Job_Profile.TEMPLATE.md (identity source)
  - WRITES ONLY its own files in D:\\newjobs\\:
      whatsapp_contacts.json        recruiter phones/names (you maintain this)
      whatsapp_outreach_queue.json  generated drafts + per-job outreach status
      whatsapp_outreach.log         append-only log

Pipeline per job:
  1. Pick QUALIFIED jobs from excel-rows.json (matchScore >= --min-score,
     default 50; Flutter roles excluded per profile).
  2. Resolve recruiter contact (--phone override > whatsapp_contacts.json
     by queueId > by company > phone-looking text inside the job record).
     If none found -> status NEEDS_CONTACT, no browser action.
  3. Generate a personalized WhatsApp message from VERIFIED fields only
     (profile + job row). Nothing is invented.
  4. Open the chat through BrowserOS Neo MCP:
       https://wa.me/<digits>?text=<url-encoded message>
     The tab is left open with the message PREFILLED.
  5. NEVER auto-presses Send. Waits for YOU to press Send in the browser,
     then asks for confirmation in this terminal.

Usage:
  python whatsapp_outreach.py list [--min-score 50]
  python whatsapp_outreach.py add-contact --company "Swastech" --phone 9876543210 [--name "Riya"] [--queue-id Q034]
  python whatsapp_outreach.py generate [--queue-id Q034] [--min-score 50] [--force]
  python whatsapp_outreach.py open --queue-id Q034 [--phone 9876543210] [--settle 4.0] [--no-browser]
  python whatsapp_outreach.py next [--min-score 50] [--settle 4.0]
  python whatsapp_outreach.py show [--queue-id Q034]

Safety:
  - This script never clicks Send / never sends anything itself.
  - Phone numbers are never guessed. Unknown -> NEEDS_CONTACT.
  - BrowserOS down -> prints the wa.me link + message so you can open it manually.
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
PROFILE_MD = ROOT / "Job_Profile.TEMPLATE.md"
CONTACTS_FILE = ROOT / "whatsapp_contacts.json"
QUEUE_FILE = ROOT / "whatsapp_outreach_queue.json"
LOG_FILE = ROOT / "whatsapp_outreach.log"

MCP_HOST, MCP_PORT, MCP_PATH = "127.0.0.1", 9210, "/mcp"

# ---------------------------------------------------------------- profile (verified, mirrors Job_Profile.TEMPLATE.md)

SENDER = {
    "name": "Alex Morgan",
    "role_line": "Frontend Developer (React / Next.js, 1+ yr)",
    "phone_display": "+91 9876543210",
    "email": "candidate@example.com",
    "linkedin": "https://www.linkedin.com/in/developer-portfolio01/",
    "github": "https://github.com/developer-portfolio/",
    "portfolio": "https://developer-portfolio.vercel.app/",
    "availability": "available immediately (0 days notice)",
    "experience": "1+ year shipping production React and Next.js apps (live client Collection System on Next.js + Ant Design, REST APIs, custom NPM packages; freelance e-commerce frontend)",
}

OUTREACH_STATUSES = {"READY", "OPENED", "SENT", "SKIPPED", "NEEDS_CONTACT", "FAILED"}

# ---------------------------------------------------------------- io helpers


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
    # Tolerant reader: the master queue is maintained by other agents and has
    # been observed truncated (trailing comma, missing closing bracket).
    # We NEVER repair the file on disk (read-only); we only salvage in memory.
    try:
        with open(EXCEL_ROWS, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    for candidate in (text, re.sub(r",\s*$", "", text.rstrip()) + "\n]"):
        try:
            rows = json.loads(candidate)
            if isinstance(rows, dict):  # tolerate {"jobs": [...]} shape
                rows = rows.get("jobs", rows.get("rows", []))
            return rows if isinstance(rows, list) else []
        except Exception:
            continue
    log(f"WARN: {EXCEL_ROWS.name} is unparseable even after salvage; treating as empty.")
    return []


def load_contacts() -> dict:
    data = load_json(CONTACTS_FILE, {})
    data.setdefault("by_queue", {})
    data.setdefault("by_company", {})
    return data


def save_contacts(data: dict) -> None:
    save_json(CONTACTS_FILE, data)


def load_outreach() -> dict:
    data = load_json(QUEUE_FILE, {"updated_at": None, "records": []})
    if isinstance(data, list):
        data = {"updated_at": None, "records": data}
    data.setdefault("records", [])
    return data


def save_outreach(data: dict) -> None:
    data["updated_at"] = now_iso()
    save_json(QUEUE_FILE, data)


def find_outreach(data: dict, queue_id: str):
    for r in data["records"]:
        if r.get("queueId") == queue_id:
            return r
    return None


# ---------------------------------------------------------------- qualification


def norm_company(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def is_qualified(job: dict, min_score: int) -> tuple[bool, str]:
    """Qualified = matchScore >= min_score, not a Flutter role, has company+role."""
    if not job.get("company") or not job.get("role"):
        return False, "missing company/role"
    title = f"{job.get('role', '')} {job.get('notes', '')}".lower()
    if "flutter" in title:
        return False, "excluded: Flutter role per profile"
    try:
        score = int(job.get("matchScore") if job.get("matchScore") is not None else 0)
    except (TypeError, ValueError):
        return False, "matchScore not numeric"
    if score < min_score:
        return False, f"matchScore {score} < {min_score}"
    return True, f"matchScore {score} >= {min_score}"


def qualified_jobs(min_score: int) -> list:
    rows = load_excel_rows()
    out = []
    for j in rows:
        ok, reason = is_qualified(j, min_score)
        if ok:
            out.append(j)
    # Prioritise: SUBMITTED first, then PENDING_HUMAN, then rest; within that, higher score.
    rank = {"SUBMITTED": 0, "PENDING_HUMAN": 1, "NOT_PROCESSED": 2}
    out.sort(key=lambda j: (rank.get((j.get("status") or ""), 3), -(j.get("matchScore") or 0)))
    return out


# ---------------------------------------------------------------- phones


def normalize_phone(raw: str) -> str | None:
    """Return wa.me digits (e.g. +91 9876543210) or None if not a plausible phone."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("00"):
        digits = digits[2:]
    # 10-digit Indian mobile -> prefix 91
    if len(digits) == 10 and digits[0] in "6789":
        digits = "91" + digits
    # 11 digits starting with 0 -> drop trunk 0, prefix 91
    if len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    if not (10 <= len(digits) <= 15):
        return None
    return digits


PHONE_RE = re.compile(r"(?:\+?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}")


def scan_phones_in_job(job: dict) -> list:
    """Best-effort: look for a real phone / wa.me link inside the job row text.

    URLs are ONLY scanned for explicit wa.me links — never for bare digit
    runs (Naukri job IDs like ...501974 contain phone-looking digit runs).
    Free-text fields are scanned with PHONE_RE but require a +91 prefix or
    separator so bare 10-digit runs inside IDs are not misread.
    """
    found: list[str] = []
    for key in ("jobUrl", "canonicalUrl"):
        m = re.search(r"wa\.me/(\d{10,15})", str(job.get(key) or ""))
        if m and m.group(1) not in found:
            found.append(m.group(1))
    for url in job.get("sourceUrls") or []:
        m = re.search(r"wa\.me/(\d{10,15})", str(url))
        if m and m.group(1) not in found:
            found.append(m.group(1))
    strict_phone = re.compile(r"(?:\+91[\s\-]?|91[\s\-])?[6-9]\d{4}[\s\-]\d{5}")
    for key in ("notes", "failureReason", "matchReason"):
        for pm in strict_phone.findall(str(job.get(key) or "")):
            norm = normalize_phone(pm)
            if norm and norm not in found:
                found.append(norm)
    return found


def resolve_contact(job: dict, contacts: dict, override_phone: str = "", override_name: str = "") -> dict:
    """Order: CLI override > contacts by queueId > contacts by company > scan job text."""
    qid = job.get("queueId", "")
    if override_phone:
        norm = normalize_phone(override_phone)
        if norm:
            return {"phone": norm, "name": override_name.strip(), "source": "cli-override"}
        return {"phone": None, "name": "", "source": "cli-override-invalid"}
    hit = contacts.get("by_queue", {}).get(qid)
    if hit and normalize_phone(hit.get("phone", "")):
        return {"phone": normalize_phone(hit["phone"]), "name": hit.get("name", ""),
                "source": f"contacts.by_queue[{qid}]"}
    hit = contacts.get("by_company", {}).get(norm_company(job.get("company", "")))
    if hit and normalize_phone(hit.get("phone", "")):
        return {"phone": normalize_phone(hit["phone"]), "name": hit.get("name", ""),
                "source": "contacts.by_company"}
    scanned = scan_phones_in_job(job)
    if scanned:
        return {"phone": scanned[0], "name": "", "source": "scanned-from-job-record"}
    return {"phone": None, "name": "", "source": "not-found"}


# ---------------------------------------------------------------- message


def build_message(job: dict, recruiter_name: str = "") -> str:
    """Personalized WhatsApp draft from verified fields only. No invention."""
    company = job.get("company", "your company")
    role = job.get("role", "the open role")
    skills = job.get("keyMatchingSkills") or []
    skill_bit = (", ".join(skills[:3]) + " fit") if skills else "frontend fit"
    status = (job.get("status") or "").upper()
    if status == "SUBMITTED":
        intent = f"I recently applied for the *{role}* role at *{company}*"
    else:
        intent = f"I'm interested in the *{role}* role at *{company}*"
    greeting = f"Hi {recruiter_name.strip().split()[0]}," if recruiter_name and recruiter_name.strip() else "Hi,"
    lines = [
        f"{greeting} I'm {SENDER['name']} — {SENDER['role_line']}.",
        f"{intent} ({skill_bit}).",
        f"Exp: {SENDER['experience']}. {SENDER['availability']}.",
        f"Portfolio: {SENDER['portfolio']}",
        f"GitHub: {SENDER['github']} | LinkedIn: {SENDER['linkedin']}",
        f"If the role is still open, happy to share anything useful or hop on a short call. Thanks!",
        f"— {SENDER['name']} | {SENDER['phone_display']} | {SENDER['email']}",
    ]
    return "\n".join(lines)


def build_wa_link(phone_digits: str, message: str) -> str:
    return f"https://wa.me/{phone_digits}?text={urllib.parse.quote(message, safe='')}"


# ---------------------------------------------------------------- BrowserOS MCP (minimal client, copied pattern from bos.py)

class BOS:
    """One BrowserOS Neo MCP session. Single process = single session."""

    def __init__(self, label: str = "whatsapp-outreach"):
        self.cid = 0
        self.sid = None
        obj = self._post({"jsonrpc": "2.0", "id": self._n(), "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": label, "version": "1.0"}}})
        if obj is None:
            raise RuntimeError("MCP initialize failed — is BrowserOS Neo running on 127.0.0.1:9211?")

    def _n(self):
        self.cid += 1
        return self.cid

    def _post(self, payload: dict):
        import json as _json
        body = _json.dumps(payload).encode()
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
            raw = b""
            want = payload.get("id")
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
                            obj = _json.loads(s)
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
        obj = self._post({"jsonrpc": "2.0", "id": self._n(),
                          "method": "tools/call",
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

    def snapshot(self, page: int) -> str:
        text, ok = self.call("snapshot", {"page": page})
        return text if ok else ""

    def read(self, page: int) -> str:
        text, ok = self.call("read", {"page": page, "format": "text"})
        return text if ok else ""


def open_whatsapp_chat(wa_link: str, settle: float) -> dict:
    """Open wa.me link in BrowserOS. Returns {ok, page, note}. Never clicks Send."""
    try:
        bos = BOS("whatsapp-outreach")
    except RuntimeError as exc:
        return {"ok": False, "page": None, "note": str(exc)}
    page = bos.open(wa_link)
    if page is None:
        return {"ok": False, "page": None, "note": "tabs/new failed or timed out"}
    time.sleep(settle)
    snap = bos.snapshot(page)
    body = bos.read(page)
    low = f"{snap}\n{body}".lower()
    if any(p in low for p in ("phone number shared via url is invalid", "invalid phone", "couldn't look up")):
        return {"ok": False, "page": page, "note": "WhatsApp reports invalid phone number"}
    if any(p in low for p in ("scan the qr", "scan qr", "link with phone number", "log in", "login")):
        return {"ok": True, "page": page, "note": "opened; WhatsApp Web login (QR) may be required — left open"}
    return {"ok": True, "page": page, "note": "opened with prefilled message — left open"}


# ---------------------------------------------------------------- commands


def cmd_list(args) -> int:
    rows = load_excel_rows()
    if not rows:
        log(f"No jobs found in {EXCEL_ROWS.name}. Run discovery first.")
        return 1
    contacts = load_contacts()
    outreach = load_outreach()
    o_by_q = {r.get("queueId"): r for r in outreach["records"]}
    qual = qualified_jobs(args.min_score)
    print(f"Qualified: {len(qual)}/{len(rows)} (min-score {args.min_score}) | contacts file: {CONTACTS_FILE.name}")
    print(f"{'QID':6} {'SCORE':5} {'STATUS':13} {'CONTACT':9} {'OUTREACH':12} COMPANY / ROLE")
    for j in qual:
        qid = j.get("queueId", "?")
        c = resolve_contact(j, contacts)
        o = o_by_q.get(qid, {})
        print(f"{qid:6} {str(j.get('matchScore')):5} {(j.get('status') or '-'):13} "
              f"{('YES' if c['phone'] else 'NO'):9} {(o.get('status') or '-'):12} "
              f"{(j.get('company') or '')[:26]} / {(j.get('role') or '')[:40]}")
    no_contact = [j["queueId"] for j in qual if not resolve_contact(j, contacts)["phone"]]
    if no_contact:
        print(f"\nNEEDS_CONTACT ({len(no_contact)}): {', '.join(no_contact)}")
        print(f"Add one: python {Path(__file__).name} add-contact --queue-id <QID> --phone <digits> [--name <Name>]")
    return 0


def cmd_add_contact(args) -> int:
    norm = normalize_phone(args.phone)
    if not norm:
        print(f"Refused: '{args.phone}' is not a plausible phone number (need 10-15 digits). Nothing saved.")
        return 1
    contacts = load_contacts()
    entry = {"phone": norm, "name": args.name.strip(), "updated_at": now_iso()}
    if args.queue_id:
        contacts["by_queue"][args.queue_id] = entry
        log(f"Saved contact by queue: {args.queue_id} -> {norm} ({args.name or '-'})")
    elif args.company:
        contacts["by_company"][norm_company(args.company)] = entry
        log(f"Saved contact by company: {args.company} -> {norm} ({args.name or '-'})")
    else:
        print("Provide --queue-id Qxxx and/or --company. Nothing saved.")
        return 1
    # Mirror to the other key when both given, so either lookup hits.
    if args.queue_id and args.company:
        contacts["by_company"][norm_company(args.company)] = entry
    save_contacts(contacts)
    return 0


def upsert_draft(job: dict, phone: str | None, name: str, source: str, force: bool = False) -> dict:
    outreach = load_outreach()
    rec = find_outreach(outreach, job.get("queueId", ""))
    message = build_message(job, name)
    wa_link = build_wa_link(phone, message) if phone else ""
    if rec is None:
        rec = {"queueId": job.get("queueId"), "company": job.get("company"),
               "role": job.get("role"), "jobUrl": job.get("jobUrl", ""),
               "matchScore": job.get("matchScore"), "applyStatus": job.get("status", "")}
        outreach["records"].append(rec)
    if rec.get("message") and not force and rec.get("status") in ("READY", "OPENED"):
        save_outreach(outreach)
        return rec
    rec.update({"company": job.get("company"), "role": job.get("role"),
                "jobUrl": job.get("jobUrl", ""), "matchScore": job.get("matchScore"),
                "applyStatus": job.get("status", ""), "phone": phone or "",
                "recruiter_name": name, "contact_source": source,
                "message": message, "wa_link": wa_link,
                "status": "READY" if phone else "NEEDS_CONTACT",
                "updated_at": now_iso()})
    save_outreach(outreach)
    return rec


def cmd_generate(args) -> int:
    contacts = load_contacts()
    qual = qualified_jobs(args.min_score)
    if args.queue_id:
        qual = [j for j in qual if j.get("queueId") == args.queue_id]
        if not qual:
            # Still allow drafting a specific row even below threshold, but say so.
            rows = [j for j in load_excel_rows() if j.get("queueId") == args.queue_id]
            if not rows:
                print(f"Unknown queueId {args.queue_id}.")
                return 1
            print(f"Note: {args.queue_id} is below threshold — drafting anyway (explicit --queue-id).")
            qual = rows
    made, need = 0, []
    for job in qual:
        c = resolve_contact(job, contacts)
        rec = upsert_draft(job, c["phone"], c["name"], c["source"], force=args.force)
        if rec["status"] == "READY":
            made += 1
        else:
            need.append(job.get("queueId"))
    log(f"Drafts ready: {made} | NEEDS_CONTACT: {len(need)} {need} | queue: {QUEUE_FILE.name}")
    return 0


def wait_for_human_send(rec: dict) -> str:
    print("\n" + "=" * 64)
    print("HUMAN STEP — this script will NOT press Send for you.")
    print(f"1. In the BrowserOS tab, review the prefilled message for {rec['company']} ({rec['queueId']}).")
    print("2. Press Send in WhatsApp yourself.")
    print("3. Back here, type what happened:")
    print("     ENTER = sent | skip = skipped | fail:<reason> = failed")
    print("=" * 64)
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


def cmd_open(args) -> int:
    rows = {j.get("queueId"): j for j in load_excel_rows()}
    job = rows.get(args.queue_id)
    if not job:
        print(f"Unknown queueId {args.queue_id}. Run `list` to see qualified IDs.")
        return 1
    contacts = load_contacts()
    c = resolve_contact(job, contacts, args.phone or "", args.name or "")
    if not c["phone"]:
        upsert_draft(job, None, "", c["source"])
        print(f"{args.queue_id}: no recruiter phone found (source: {c['source']}).")
        print(f"Add it: python {Path(__file__).name} add-contact --queue-id {args.queue_id} --phone <digits> [--name <Name>]")
        return 2
    rec = upsert_draft(job, c["phone"], c["name"], c["source"], force=True)
    print(f"\nTo: {rec['phone']} ({rec['recruiter_name'] or 'recruiter'} @ {rec['company']}) [{rec['contact_source']}]")
    print(f"Job: {rec['role']} [{rec['queueId']}]")
    print("-" * 64 + f"\n{rec['message']}\n" + "-" * 64)

    opened_note = "manual mode (--no-browser or --dry-run): open the link yourself"
    page = None
    if not args.no_browser and not args.dry_run:
        print(f"\nOpening BrowserOS chat (wa.me/{rec['phone']}) ... leave this process running.")
        res = open_whatsapp_chat(rec["wa_link"], args.settle)
        opened_note = res["note"]
        page = res["page"]
        print(f"Browser: {opened_note}" + (f" (page {page})" if page else ""))
        if not res["ok"] and page is None:
            print(f"Fallback link (paste in any logged-in browser):\n{rec['wa_link']}")
    else:
        print(f"\nLink (open manually):\n{rec['wa_link']}")

    outreach = load_outreach()
    rec2 = find_outreach(outreach, rec["queueId"]) or rec
    rec2.update({"status": "OPENED", "browser_note": opened_note,
                 "browser_page": page, "opened_at": now_iso()})
    # keep the single record object in sync for wait_for_human_send display
    rec.update(rec2)
    save_outreach(outreach)
    log(f"OPENED {rec['queueId']} -> {rec['phone']} (page {page}; {opened_note})")

    if getattr(args, "dry_run", False):
        print("\n--dry-run: stopping before human confirmation (status stays OPENED).")
        return 0

    verdict = wait_for_human_send(rec)
    outreach = load_outreach()
    rec3 = find_outreach(outreach, rec["queueId"])
    if verdict == "SENT":
        rec3.update({"status": "SENT", "sent_at": now_iso()})
        log(f"SENT {rec3['queueId']} -> {rec3['phone']} (human pressed Send in WhatsApp)")
    elif verdict == "SKIPPED":
        rec3.update({"status": "SKIPPED"})
        log(f"SKIPPED {rec3['queueId']} (human chose skip)")
    elif verdict == "INTERRUPTED":
        rec3.update({"status": "OPENED"})
        log(f"INTERRUPTED {rec3['queueId']} (left OPENED; re-run open to confirm later)")
    else:  # FAILED:<reason>
        rec3.update({"status": "FAILED", "fail_reason": verdict})
        log(f"FAILED {rec3['queueId']}: {verdict}")
    save_outreach(outreach)
    return 0 if verdict == "SENT" else 3


def cmd_next(args) -> int:
    contacts = load_contacts()
    outreach = load_outreach()
    done = {r.get("queueId") for r in outreach["records"] if r.get("status") in ("SENT", "SKIPPED")}
    for job in qualified_jobs(args.min_score):
        if job.get("queueId") in done:
            continue
        c = resolve_contact(job, contacts)
        if not c["phone"]:
            continue  # keep `next` to actionable items; `list` shows NEEDS_CONTACT ones
        print(f"Next actionable: {job.get('queueId')} {job.get('company')} — {job.get('role')}")
        args.queue_id = job.get("queueId")
        return cmd_open(args)
    print("Nothing actionable: all qualified jobs are SENT/SKIPPED or NEEDS_CONTACT. Run `list`.")
    return 1


def cmd_show(args) -> int:
    outreach = load_outreach()
    recs = outreach["records"]
    if args.queue_id:
        recs = [r for r in recs if r.get("queueId") == args.queue_id]
    if not recs:
        print("No outreach drafts yet. Run `generate` first.")
        return 1
    for r in recs:
        print(f"\n{'=' * 64}\n{r.get('queueId')} | {r.get('company')} — {r.get('role')}")
        print(f"status={r.get('status')} phone={r.get('phone') or '-'} "
              f"({r.get('contact_source', '?')}) updated={r.get('updated_at', '-')}")
        if r.get("browser_note"):
            print(f"browser: {r['browser_note']}")
        print("-" * 64)
        print(r.get("message", "(no message)"))
        if r.get("wa_link"):
            print(f"\nlink: {r['wa_link'][:120]}...")
    return 0


# ---------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Recruiter WhatsApp outreach (human-send only).")
    ap.add_argument("--dry-run", action="store_true",
                    help="generate + open links but never touch the browser or ask for Send")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show qualified jobs + contact/outreach status")
    p.add_argument("--min-score", type=int, default=50)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("add-contact", help="save a recruiter phone (no guessing)")
    p.add_argument("--queue-id", default="")
    p.add_argument("--company", default="")
    p.add_argument("--phone", required=True, help="digits, e.g. 9876543210 or +91 98765 43210")
    p.add_argument("--name", default="")
    p.set_defaults(func=cmd_add_contact)

    p = sub.add_parser("generate", help="render WhatsApp drafts for qualified jobs")
    p.add_argument("--queue-id", default="")
    p.add_argument("--min-score", type=int, default=50)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("open", help="open wa.me chat in BrowserOS, wait for YOU to press Send")
    p.add_argument("--queue-id", required=True)
    p.add_argument("--phone", default="", help="one-off override, also usable to test")
    p.add_argument("--name", default="")
    p.add_argument("--settle", type=float, default=4.0)
    p.add_argument("--no-browser", action="store_true", help="print link only, no MCP call")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("next", help="open the next actionable qualified job")
    p.add_argument("--min-score", type=int, default=50)
    p.add_argument("--settle", type=float, default=4.0)
    p.add_argument("--phone", default="")
    p.add_argument("--name", default="")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("show", help="print saved drafts + links")
    p.add_argument("--queue-id", default="")
    p.set_defaults(func=cmd_show)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "dry_run", False):
        # dry-run threads through open/generate without browser side effects
        if hasattr(args, "no_browser"):
            args.no_browser = True
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nStopped (outreach state preserved; re-run to continue).")
        return 130


if __name__ == "__main__":
    sys.exit(main())
