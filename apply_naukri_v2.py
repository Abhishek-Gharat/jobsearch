#!/usr/bin/env python3
"""
apply_naukri_v2.py - Working Naukri applier.

Naukri apply flow (verified 2026-09-28):
  1. Job page renders TWO "Apply" buttons (desktop + hidden mobile). The hidden one
     silently no-ops. Always click the visible one, and retry until the page moves.
  2. Some jobs open a chatbot drawer (.chatbot_Drawer) with recruiter questions
     instead of a modal. Answer each question (label options) then click .sendMsg.
  3. Submission is confirmed by the saveApply page showing: Applied to "<role>".
     A saveApply navigation alone is NOT success - it can render
     "Your application was not accepted due to incomplete information".
"""

import json
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

MARKER = re.compile(r"\[/?UNTRUSTED_PAGE_CONTENT[^\]]*\]")

# Question keyword -> option index to select. Index 0 is the least-assertive option
# for most scales, which matches a 1.2 yr candidate who has no GenAI project work.
RULES = [
    (r"hands-on experience with AI", 3),
    (r"technical experience with AI", 3),
    (r"interested.{0,25}(in|are you in).{0,30}AI", 1),
    (r"notice period", 0),
    (r"current (ctc|salary)", 0),
    (r"expected (ctc|salary)", 0),
    (r"relocat", 0),
    (r"immediately|joining date|when can you", 0),
    (r"have you worked|industry experience", 0),
    (r"bond", -2),
]

# Skill -> years, supplied by the candidate. Only these are ever auto-answered;
# anything else stops the run rather than inventing experience.
SKILL_YEARS = {
    "software testing": 0.5,
    "ui/ux": 1,
    "ui-ux": 1,
    "ux": 1,
    "jquery": 1,
}

JS_CLICK_APPLY = """() => {
    const all = Array.from(document.querySelectorAll('button')).filter(b => (b.innerText||'').trim() === 'Apply');
    const t = all.find(b => b.offsetParent !== null) || all[0];
    if (!t) return 'NO_BTN';
    t.scrollIntoView({block:'center'});
    t.click();
    return 'CLICKED';
}"""

JS_STATE = """() => {
    const b = document.body.innerText;
    const d = document.querySelector('.chatbot_Drawer');
    const opts = d ? Array.from(d.querySelectorAll('label')).filter(l => l.offsetParent !== null)
                    .map(l => (l.innerText||'').replace(/\\s+/g,' ').trim()) : [];
    const spans = d ? Array.from(d.querySelectorAll('span'))
                     .map(s => (s.innerText||'').replace(/\\s+/g,' ').trim())
                     .filter(t => t.length > 25) : [];
    const fields = d ? Array.from(d.querySelectorAll('input[type=text], textarea'))
                       .filter(i => i.offsetParent !== null).length : 0;
    return JSON.stringify({
        applied: /Applied to/i.test(b),
        rejected: /was not accepted/i.test(b),
        drawer: !!d,
        question: spans.length ? spans[spans.length-1] : '',
        options: opts,
        textfields: fields,
        url: location.href
    });
}"""

JS_ANSWER = """(idx) => {
    const ls = Array.from(document.querySelectorAll('.chatbot_Drawer label')).filter(l => l.offsetParent !== null);
    if (!ls.length) return 'NO_OPTS';
    const i = Math.min(idx, ls.length - 1);
    ls[i].click();
    return 'PICKED:' + (ls[i].innerText||'').trim().slice(0, 60);
}"""

JS_SEND = """() => {
    const b = document.querySelector('.chatbot_Drawer .sendMsg');
    if (!b) return 'NO_SEND';
    b.click();
    return 'SENT';
}"""

JS_FILL = """(vals) => {
    const d = document.querySelector('.chatbot_Drawer');
    if (!d) return 0;
    let n = 0;
    for (const inp of Array.from(d.querySelectorAll('input[type=text], textarea')).filter(i => i.offsetParent !== null)) {
        const ctx = ((inp.placeholder||'') + ' ' + ((inp.closest('div')||{innerText:''}).innerText||'')).toLowerCase();
        let v = null;
        if (/current ctc|current salary/.test(ctx)) v = '3';
        else if (/expected ctc|expected salary/.test(ctx)) v = '5.5';
        else if (/notice/.test(ctx)) v = '0';
        else if (/experience|years/.test(ctx)) v = '1';
        else if (/city|location/.test(ctx)) v = 'Mumbai';
        else if (/full name|name/.test(ctx)) v = 'Alex Morgan';
        else if (/email/.test(ctx)) v = 'candidate@example.com';
        else if (/phone|mobile/.test(ctx)) v = '9876543210';
        if (v !== null) {
            inp.focus();
            inp.value = v;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            n++;
        }
    }
    return n;
}"""


def ev(b, page, func, *args):
    payload = {"page": page, "func": func}
    if args:
        payload["args"] = list(args)
    text, _ = b.call("evaluate", payload)
    return MARKER.sub("", text or "").strip()


def state(b, page):
    raw = ev(b, page, JS_STATE)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except Exception:
        return None


def pick_option(question, options):
    if not options:
        return None
    q = question or ""

    # Skill-specific experience must be resolved first: only ever answer from the
    # candidate-supplied SKILL_YEARS table, never guess.
    m = re.search(r"years of experience.*?\bin\s+([^?]+?)\s*\??$", q, re.I) or \
        re.search(r"experience (?:do you have )?in\s+([^?]+?)\s*\??$", q, re.I)
    if m:
        skill = m.group(1).strip().strip(".").lower()
        for known, yrs in SKILL_YEARS.items():
            if known in skill or skill in known:
                want = str(yrs)
                for i, opt in enumerate(options):
                    if re.fullmatch(rf"\s*(?:[a-e]\.\s*)?{re.escape(want)}(?:\+|\s*years?)?\s*", opt, re.I):
                        return i
        return None

    for pattern, idx in RULES:
        if not re.search(pattern, q, re.I):
            continue
        if idx == -2:
            # Bond question: candidate confirmed acceptance of a one-year bond.
            for i, opt in enumerate(options):
                if re.match(r"\s*(a\.\s*)?yes\b", opt, re.I):
                    return i
            return 0
        return min(idx, len(options) - 1)
    return None


def apply_one(b, job, max_questions=8):
    qid, url = job["queueId"], job["jobUrl"]
    print(f"\n[{qid}] {job.get('company')} - {job.get('role')}")
    page = b.open(url)
    if page is None:
        return "FAILED", "could not open tab"
    try:
        time.sleep(4.0)
        st = state(b, page)
        if st and st.get("applied"):
            return "ALREADY", "page shows already applied"

        for attempt in range(3):
            ev(b, page, JS_CLICK_APPLY)
            time.sleep(3.5)
            st = state(b, page)
            if st and (st.get("applied") or st.get("rejected") or st.get("drawer")):
                break

        for _ in range(max_questions):
            st = state(b, page)
            if not st:
                return "FAILED", "unreadable page state"
            if st.get("applied"):
                return "SUBMITTED", st.get("url", "")[:90]
            if st.get("rejected"):
                return "REJECTED", "Naukri: incomplete mandatory information"
            if not st.get("drawer"):
                time.sleep(2.0)
                st = state(b, page)
                if st and st.get("applied"):
                    return "SUBMITTED", st.get("url", "")[:90]
                return "FAILED", "no questionnaire and no confirmation"
            if st.get("textfields"):
                n = ev(b, page, JS_FILL)
                print(f"    filled {n} text field(s)")
            idx = pick_option(st.get("question", ""), st.get("options", []))
            if idx is None:
                return "NEEDS_HUMAN", f"unmapped question: {st.get('question','')[:110]}"
            ev(b, page, JS_ANSWER, idx)
            time.sleep(0.7)
            ev(b, page, JS_SEND)
            time.sleep(2.8)
            print(f"    answered: {st['options'][idx][:58]}")

        st = state(b, page)
        if st and st.get("applied"):
            return "SUBMITTED", st.get("url", "")[:90]
        return "NEEDS_HUMAN", "questionnaire did not finish"
    finally:
        b.close(page)


SKIP_ROLE_PATTERNS = (
    r"angular", r"\bjava\b", r"back[\s-]?end", r"backend", r"test engineer",
    r"\bqa\b", r"designer", r"\bux\b", r"php", r"laravel", r"devops", r"data scientist",
)


def role_allowed(role):
    low = (role or "").lower()
    return not any(re.search(p, low) for p in SKIP_ROLE_PATTERNS)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    only = sys.argv[2].split(",") if len(sys.argv) > 2 else None

    rows = queue_store.load(quiet=True)
    todo = [
        r for r in rows
        if r.get("status") in ("READY", "UNPROCESSED", "PENDING", "PENDING_HUMAN", "")
        and "naukri.com/job-listings" in (r.get("jobUrl") or "")
        and role_allowed(r.get("role"))
        and (not only or r.get("queueId") in only)
    ]
    print(f"Queue: {len(rows)} rows | Naukri targets: {len(todo)} | limit {limit}")

    b = bos.BOS("applier-v2")
    tally = {}
    for job in todo[:limit]:
        try:
            outcome, detail = apply_one(b, job)
        except Exception as ex:
            outcome, detail = "FAILED", repr(ex)[:120]
        print(f"  -> {outcome}: {detail}")
        if outcome == "SUBMITTED":
            queue_store.set_status(job["queueId"], "SUBMITTED", detail) if hasattr(queue_store, "set_status") else None
        rows = queue_store.load(quiet=True)
        for r in rows:
            if r.get("queueId") == job["queueId"]:
                if outcome == "SUBMITTED":
                    r["status"], r["failureReason"], r["submissionDate"] = "SUBMITTED", "", "2026-09-28"
                elif outcome in ("REJECTED", "NEEDS_HUMAN", "FAILED"):
                    r["status"] = "PENDING_HUMAN"
                    r["failureReason"] = f"{outcome}: {detail}"[:200]
                break
        queue_store.save(rows, queue_store.QUEUE)
        tally[outcome] = tally.get(outcome, 0) + 1

    print("\n" + "=" * 55)
    print("TALLY: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
