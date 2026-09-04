"""
BrowserOS neo MCP driver for the job-application pipeline.

Talks to the local BrowserOS neo MCP server (Streamable HTTP on 127.0.0.1:9211/mcp)
without needing the WorkBuddy MCP tools to be registered in-session.

IMPORTANT: MCP pages are owned by the session that created them, so every multi-step
flow must run inside a single process invocation. One bash call = one session.

Commands:
    validate   open each unprocessed job URL and report live/dead
    apply      attempt an end-to-end application for one job URL
"""

import argparse
from pathlib import Path
import http.client
import json
import os
import re
import socket
import sys
import time

HOST, PORT, PATH = "127.0.0.1", 9211, "/mcp"
BASE_DIR = Path(__file__).resolve().parent
QUEUE = str(BASE_DIR / "excel-rows.json")

DEAD_PHRASES = (
    "no longer accepting", "expired", "no longer available",
    "not accepting applications", "this job has been closed",
    "job is no longer", "position has been filled",
)
SUCCESS_PHRASES = (
    "application sent", "application submitted", "application received",
    "successfully applied", "thank you for applying", "your application was sent",
    "application has been submitted", "applied successfully",
)
BLOCK_PHRASES = ("captcha", "recaptcha", "verify you are human", "one-time code", "enter the code")

RESUME_CANDIDATES = (
    str(Path.home() / "Downloads" / "Resume.docx"),
    str(BASE_DIR / "Resume.pdf"),
)

FIELD_VALUES = [
    (("first name", "firstname", "given name"), "YourFirstName"),
    (("last name", "lastname", "surname", "family name"), "YourLastName"),
    (("full name", "your name", "name"), "Your Name"),
    (("email", "e-mail"), "your.email@example.com"),
    (("phone", "mobile", "contact number", "whatsapp"), "98XXXXXXXX"),
    (("linkedin",), "https://www.linkedin.com/in/your-profile/"),
    (("github",), "https://github.com/your-username/"),
    (("portfolio", "website", "personal site", "blog"), "https://your-portfolio.example.com/"),
    (("city", "current location", "location"), "Mumbai"),
    (("expected ctc", "expected salary", "desired salary"), "5.5"),
    (("current ctc", "current salary"), "3"),
    (("notice period",), "0"),
    (("total experience", "years of experience", "experience in years"), "1"),
    (("summary", "about you", "why should we hire", "cover letter", "tell us about yourself"),
     "Frontend Developer with 1+ year of experience shipping production React and Next.js "
     "applications. Worked on a live client Collection System (Next.js, Ant Design, REST API "
     "integration) and delivered client e-commerce projects as a freelancer. Full-stack capable "
     "with Node.js, Express, TypeScript, MongoDB and PostgreSQL. Built my own developer tools "
     "like ReactViz. Available immediately."),
]


def pick_resume():
    for p in RESUME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def load_queue():
    with open(QUEUE, encoding="utf-8-sig") as f:
        return json.load(f)


def save_queue(jobs):
    with open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=4, ensure_ascii=False)


class BOS:
    """One BrowserOS neo MCP session."""

    def __init__(self, label="workbuddy-jobs"):
        self.cid = 0
        self.sid = None
        obj = self._post({"jsonrpc": "2.0", "id": self._n(), "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": label, "version": "1.0"}}})
        if obj is None:
            raise RuntimeError("MCP initialize failed - is BrowserOS neo running?")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _n(self):
        self.cid += 1
        return self.cid

    def _post(self, payload):
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        conn = http.client.HTTPConnection(HOST, PORT, timeout=60)
        try:
            conn.request("POST", PATH, body=body, headers=headers)
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
                            obj = json.loads(s)
                        except Exception:
                            continue
                        if obj.get("id") == want:
                            return obj
                if len(raw) > 3_000_000:
                    break
            return None
        except (socket.timeout, TimeoutError):
            return None
        finally:
            conn.close()

    def call(self, name, arguments):
        obj = self._post({"jsonrpc": "2.0", "id": self._n(),
                          "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})
        if obj is None:
            return "TIMEOUT", False
        if "error" in obj:
            return "ERROR: " + json.dumps(obj["error"])[:300], False
        parts = [c.get("text", "") for c in obj.get("result", {}).get("content", [])]
        return "\n".join(parts), True

    # -- page helpers -------------------------------------------------
    def open(self, url):
        text, ok = self.call("tabs", {"action": "new", "url": url})
        m = re.search(r"page (\d+)", text)
        return int(m.group(1)) if (ok and m) else None

    def close(self, page):
        self.call("tabs", {"action": "close", "page": page})

    def snapshot(self, page):
        text, ok = self.call("snapshot", {"page": page})
        return text if ok else ""

    def read(self, page):
        text, ok = self.call("read", {"page": page, "format": "text"})
        return text if ok else ""


REF_RE = re.compile(r'\[ref=(e\d+)\]')


def parse_controls(snapshot):
    """-> list of dicts: {ref, kind, label}"""
    out = []
    for line in snapshot.splitlines():
        m = REF_RE.search(line)
        if not m:
            continue
        head = line[:m.start()]
        km = re.search(r'\b(button|link|textbox|combobox|checkbox|radio|tab|menuitem|file)\b', head)
        lm = re.search(r'"([^"]{1,120})"', head)
        out.append({
            "ref": m.group(1),
            "kind": km.group(1) if km else "",
            "label": (lm.group(1) if lm else "").strip(),
            "raw": head.strip(),
        })
    return out


def find_control(controls, *keywords, kinds=()):
    for kw in keywords:
        for c in controls:
            if kw in (c["label"] + " " + c["raw"]).lower():
                if kinds and c["kind"] not in kinds:
                    continue
                return c
    return None


def value_for(label):
    low = label.lower()
    for keys, val in FIELD_VALUES:
        for k in keys:
            if k in low:
                return val
    return None


def cmd_validate(args):
    bos = BOS("job-validator")
    jobs = load_queue()
    todo = [j for j in jobs if not j.get("status")]
    print("unprocessed:", len(todo), "| checking:", min(len(todo), args.limit))
    results = []
    for j in todo[:args.limit]:
        page = bos.open(j["jobUrl"])
        if page is None:
            print(f"{j['queueId']} OPEN_FAILED | {j['company']}")
            results.append((j["queueId"], "UNKNOWN", j["company"], j["role"]))
            continue
        time.sleep(args.settle)
        txt = bos.read(page)
        low = txt.lower()
        dead = [d for d in DEAD_PHRASES if d in low]
        status = "DEAD" if dead else "LIVE"
        print(f"{j['queueId']} {status} | {j['company']} | {j['role'][:45]}"
              + (f" | {dead[0]}" if dead else ""))
        results.append((j["queueId"], status, j["company"], j["role"]))
        bos.close(page)
        time.sleep(0.5)
    live = [r for r in results if r[1] == "LIVE"]
    print(f"\nSUMMARY: {len(live)} live / {len(results)} checked")
    for r in live:
        print("  LIVE", r[0], r[2], "|", r[3][:50])


def cmd_apply(args):
    resume = pick_resume()
    if not resume:
        print("RESUME NOT FOUND - checked:", RESUME_CANDIDATES)
        return
    print("resume:", resume)

    bos = BOS("job-applier")
    page = bos.open(args.url)
    if page is None:
        print("RESULT: OPEN_FAILED")
        return
    time.sleep(args.settle)

    outcome, reason = run_apply(bos, page, resume, args)

    print(f"\nRESULT: {outcome} | {reason}")
    print("PAGE:", page, "(left open)" if outcome in ("PENDING_HUMAN", "FAILED") else "")

    if args.queue_id and outcome in ("SUBMITTED", "FAILED", "PENDING_HUMAN", "SKIPPED"):
        jobs = load_queue()
        for j in jobs:
            if j.get("queueId") == args.queue_id:
                j["status"] = outcome
                j["failureReason"] = reason
                if outcome == "SUBMITTED":
                    j["submissionDate"] = time.strftime("%Y-%m-%d")
                break
        save_queue(jobs)
        print("queue updated:", args.queue_id, "->", outcome)


def run_apply(bos, page, resume, args):
    snap = bos.snapshot(page)
    body = bos.read(page)
    if any(d in body.lower() for d in DEAD_PHRASES):
        return "SKIPPED", "posting expired"

    apply_btn = find_control(parse_controls(snap), "easy apply", "apply now", "apply",
                             kinds=("button", "link"))
    if not apply_btn:
        return "FAILED", "no apply control found on job page"

    bos.call("act", {"page": page, "kind": "click", "ref": apply_btn["ref"]})
    time.sleep(args.settle)

    uploaded = False
    for step in range(args.steps):
        snap = bos.snapshot(page)
        ctrls = parse_controls(snap)
        low_all = (snap + "\n" + bos.read(page)).lower()

        if any(s in low_all for s in SUCCESS_PHRASES):
            return "SUBMITTED", "confirmation text observed"
        if any(b in low_all for b in BLOCK_PHRASES):
            return "PENDING_HUMAN", "CAPTCHA / human verification required"
        if not ctrls:
            time.sleep(args.settle)
            continue

        # upload resume when a file input is present
        if not uploaded:
            file_ctrl = find_control(ctrls, "resume", "cv", "upload", kinds=("file", "button"))
            if file_ctrl:
                out, ok = bos.call("upload", {"page": page, "ref": file_ctrl["ref"],
                                              "paths": [resume]})
                if ok and "error" not in out.lower():
                    uploaded = True
                    time.sleep(args.settle)
                    continue

        # fill empty textboxes we have a truthful value for
        filled = False
        for c in ctrls:
            if c["kind"] != "textbox" or not c["label"]:
                continue
            val = value_for(c["label"])
            if val:
                bos.call("act", {"page": page, "kind": "fill",
                                 "ref": c["ref"], "value": val})
                filled = True
        if filled:
            time.sleep(1)
            continue

        # advance
        submit = find_control(ctrls, "submit application", "submit", kinds=("button",))
        nxt = find_control(ctrls, "review", "next", "continue", kinds=("button",))
        if nxt and not submit:
            bos.call("act", {"page": page, "kind": "click", "ref": nxt["ref"]})
            time.sleep(args.settle)
            continue
        if submit:
            bos.call("act", {"page": page, "kind": "click", "ref": submit["ref"]})
            time.sleep(args.settle + 1)
            low_all = (bos.snapshot(page) + "\n" + bos.read(page)).lower()
            if any(s in low_all for s in SUCCESS_PHRASES):
                return "SUBMITTED", "confirmation after submit"
            if any(b in low_all for b in BLOCK_PHRASES):
                return "PENDING_HUMAN", "human verification after submit"
            continue

        time.sleep(args.settle)

    return "FAILED", "could not reach a verified submission state"


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--limit", type=int, default=12)
    v.add_argument("--settle", type=float, default=3.5)
    v.set_defaults(func=cmd_validate)

    a = sub.add_parser("apply")
    a.add_argument("--url", required=True)
    a.add_argument("--queue-id", default=None)
    a.add_argument("--settle", type=float, default=3.5)
    a.add_argument("--steps", type=int, default=12)
    a.set_defaults(func=cmd_apply)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
