#!/usr/bin/env python3
"""JobOps OS - outcomes, followups, dashboard, weekly report, email parsing, coldstart."""
import json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent

def load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception: return d

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

RESUME_SELECTOR = {
    "nextjs": {"weights": {"next.js": 5, "ssr": 3, "typescript": 2, "vercel": 2}, "variant": "NEXTJS"},
    "fullstack": {"weights": {"mern": 4, "node": 3, "express": 3, "mongo": 3,
                               "postgresql": 3, "full stack": 4, "backend": 2}, "variant": "FULLSTACK"},
    "react": {"weights": {"react": 4, "redux": 3, "ant design": 3, "hooks": 2,
                           "frontend": 3, "component": 2}, "variant": "REACT"},
}

def select_variant(title="", jd_text=""):
    text = f"{title} {jd_text}".lower()
    scores = {}
    for prov, cfg in RESUME_SELECTOR.items():
        scores[cfg["variant"]] = sum(w for kw, w in cfg["weights"].items() if kw in text)
    best = max(scores, key=scores.get)
    reason = ", ".join(f"{k}:{v}" for k, v in sorted(scores.items(), reverse=True))
    return best, scores, f"keyword_fit[{reason}]"


def cmd_resume_select(job_id=None):
    jd = load(BASE/"jobs.json", {"jobs": []})
    ri = load(BASE/"resume_intelligence.json", {"variants": {}, "selections": []})
    have = {s["job_id"] for s in ri.get("selections", [])}
    n = 0
    for j in jd["jobs"]:
        if j.get("status") != "submitted" or (job_id and j["id"] != job_id):
            continue
        if j["id"] in have:
            continue
        variant, scores, why = select_variant(j.get("title",""))
        ri.setdefault("selections", []).append({
            "job_id": j["id"], "company": j.get("company"), "title": j.get("title"),
            "scores": scores, "chosen": variant, "why": why, "ts": now()})
        j["evidence"] = {**(j.get("evidence") or {}), "resume_variant": variant}
        n += 1
    ri["variants"] = {
        "REACT": "resumes/react_resume.md",
        "NEXTJS": "resumes/nextjs_resume.md",
        "FULLSTACK": "resumes/fullstack_resume.md",
    }
    (BASE/"jobs.json").write_text(json.dumps(jd, indent=2, ensure_ascii=False), encoding="utf-8")
    (BASE/"resume_intelligence.json").write_text(json.dumps(ri, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"resume selections logged: {n}")


def cmd_outcomes():
    jd = load(BASE/"jobs.json", {"jobs": []})
    ri = load(BASE/"resume_intelligence.json", {"selections": []})
    varmap = {s["job_id"]: s["chosen"] for s in ri.get("selections", [])}
    ee = load(BASE/"email_events.json", {"events": []})
    ev_by_company = defaultdict(list)
    for e in ee.get("events", []):
        ev_by_company[(e.get("company") or "").lower()].append(e)
    out = []
    for j in jd["jobs"]:
        if j.get("status") in ("pending", "in_progress"):
            continue
        submitted_at = None
        for f in BASE.glob("results/*.jsonl"):
            try:
                for ln in f.read_text(encoding="utf-8-sig").splitlines():
                    if not ln.strip(): continue
                    r = json.loads(ln)
                    if r.get("job_id") == j["id"] and r.get("result") == "submitted":
                        submitted_at = r.get("ts") or submitted_at
            except Exception:
                pass
        resp = None
        for e in ev_by_company.get((j.get("company") or "").lower(), []):
            if submitted_at:
                resp = e.get("detected_at"); break
        out.append({
            "job_id": j["id"], "company": j.get("company"), "role": j.get("title"),
            "location": j.get("location"), "ats_provider": j.get("portal"),
            "state": j.get("status"),
            "applied_at": submitted_at,
            "response_detected_at": resp,
            "response_time_hours": ((datetime.fromisoformat(resp) -
                                      datetime.fromisoformat(submitted_at)).total_seconds()/3600
                if resp and submitted_at else None),
            "resume_variant": varmap.get(j["id"], "REACT(default)"),
            "error": j.get("error"),
        })
    data = {"generated_at": now(), "count": len(out), "outcomes": out}
    (BASE/"outcomes.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"outcomes.json written: {len(out)} tracked")


def cmd_followups():
    jd = load(BASE/"jobs.json", {"jobs": []})
    items = []
    today = datetime.now(timezone.utc)
    for j in jd["jobs"]:
        if j.get("status") != "submitted":
            continue
        base_dt = None
        try:
            base_dt = datetime.fromisoformat(str(j.get("updated_at")))
            if base_dt.tzinfo is None: base_dt = base_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        co = j.get("company")
        for day in (5, 10, 21):
            due = base_dt + timedelta(days=day)
            draft = {
                5: f"Hi, I recently applied for the {j.get('title')} role at {co}. I remain very interested and available immediately — happy to share anything else useful.",
                10: f"Following up on my application for {j.get('title')} at {co}. Since applying I shipped additional work on my REACTVIZ tool (repo/portfolio updated) — glad to walk through it.",
                21: f"Hi, checking whether the {j.get('title')} opening at {co} is still active. If the timeline shifted, no problem — I'd appreciate any update.",
            }[day]
            items.append({"job_id": j["id"], "company": co, "due_date": due.date().isoformat(),
                           "day": day, "status": ("DUE_NOW" if due <= today else "scheduled"),
                           "draft": draft, "send_mode": "MANUAL_ONLY"})
    (BASE/"followups.json").write_text(json.dumps({
        "generated_at": now(), "policy": "drafts only - never auto-send", "items": items},
        indent=2, ensure_ascii=False), encoding="utf-8")
    due_now = sum(1 for i in items if i["status"] == "DUE_NOW")
    print(f"followups.json: {len(items)} scheduled ({due_now} due now)")


def cmd_dashboard():
    jd = load(BASE/"jobs.json", {"jobs": []})
    live = load(BASE/"status_live.json", {})
    intel = load(BASE/"hiring_intelligence.json", {"companies": []})
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    st = Counter(j.get("status") for j in jd["jobs"])
    submitted_week = sum(1 for j in jd["jobs"] if j.get("status") == "submitted"
                          and (j.get("updated_at") or "") >= week_ago)
    iv_dirs = list((BASE/"interviews").glob("*")) if (BASE/"interviews").exists() else []
    rankings = [r for r in intel.get("companies", [])][:5]
    dash = {
        "generated_at": now(),
        "applications_today": sum(1 for j in jd["jobs"]
                                   if j.get("status") == "submitted"
                                   and (j.get("updated_at") or "")[:10] == now()[:10]),
        "applications_this_week": submitted_week,
        "queue": dict(st),
        "interview_kits_ready": len(iv_dirs),
        "assessments_pending": sum(1 for e in load(BASE/"email_events.json", {}).get("events", [])
                                    if e.get("type") == "assessment"),
        "offers": sum(1 for e in load(BASE/"email_events.json", {}).get("events", [])
                       if e.get("type") == "offer"),
        "response_rate_pct": round(100 * len(load(BASE/"email_events.json", {}).get("events", []))
                                    / max(1, st.get("submitted", 0)), 1),
        "top_companies_by_score": [{"company": r["company"], "score": r["hiring_score"]}
                                    for r in rankings],
        "discovery_health": {"universe_size": len(intel.get("companies", [])),
                              "verified_endpoints": sum(1 for c in intel.get("companies", [])
                                                         if c.get("endpoint_verified"))},
        "watchdog_health": live.get("watchdog", {}),
        "phase": live.get("phase", "idle"),
        "current_batch": live.get("current_batch"),
    }
    (BASE/"dashboard.json").write_text(json.dumps(dash, indent=2, ensure_ascii=False), encoding="utf-8")
    print("dashboard.json written")


def cmd_email_scan():
    inbox = BASE / "emails_inbox"
    inbox.mkdir(exist_ok=True)
    ee = load(BASE/"email_events.json", {"generated_at": None, "events": []})
    seen = {(e.get("company"), e.get("subject")) for e in ee.get("events", [])}
    pats = [("interview", r"interview|schedule a call|availability"),
            ("assessment", r"assessment|coding (test|challenge)|hackerrank|codility"),
            ("recruiter_reply", r"(regarding your application|your profile|opportunity)"),
            ("rejection", r"unfortunately|we regret|decided to move forward with other"),
            ("offer", r"offer letter|compensation package|welcome aboard")]
    added = 0
    for f in inbox.glob("*.*"):
        text = f.read_text(encoding="utf-8", errors="replace")
        subject = text.splitlines()[0] if text else f.name
        etype = next((t for t, pat in pats if re.search(pat, text, re.I)), "unclear")
        comp = f.name.split(".")[0].replace("_", " ").replace("-", " ").strip().title()
        key = (comp, subject)
        if key in seen:
            continue
        ee.setdefault("events", []).append({
            "type": etype, "company": comp, "subject": subject[:120],
            "source_file": f.name, "detected_at": now(),
            "action_required": etype in ("interview", "assessment"),
            "raw_first_lines": "\n".join(text.splitlines()[:6])[:500],
        })
        seen.add(key); added += 1
    ee["generated_at"] = now()
    (BASE/"email_events.json").write_text(json.dumps(ee, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"email events parsed: +{added} (drop .eml/.txt files into emails_inbox/)")


def cmd_coldstart():
    checks = []
    jd = load(BASE/"jobs.json", None); checks.append(("jobs.json parses", jd is not None))
    urls = [j.get("url","").split("?")[0].rstrip("/").lower() for j in (jd or {}).get("jobs", []) if j.get("url")]
    checks.append(("no duplicate URLs", len(urls) == len(set(urls))))
    ids = [j.get("id") for j in (jd or {}).get("jobs", [])]
    checks.append(("unique job ids", len(ids) == len(set(ids))))
    cp = load(BASE/"checkpoint.json", None); checks.append(("checkpoint parses", isinstance(cp, dict)))
    unknown = [j["id"] for j in (jd or {}).get("jobs", [])
               if j.get("status") not in ("pending","in_progress","submitted","skipped",
                                           "failed","failed_unconfirmed","review_required","needs_human")]
    checks.append(("no unknown states", not unknown))
    hi = load(BASE/"hiring_intelligence.json", None); checks.append(("intelligence survives", hi is not None))
    db = (BASE/"dashboard.json").exists(); checks.append(("dashboard present", db))
    fails = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
    print("COLDSTART:", "PASS" if not fails else f"FAIL {fails}")


def cmd_weekly():
    jd = load(BASE/"jobs.json", {"jobs": []})
    oc = load(BASE/"outcomes.json", {"outcomes": []})
    rank = load(BASE/"company_rankings.json", [])
    perf = load(BASE/"provider_performance_report.json", {"providers": {}})
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    wk = [o for o in oc.get("outcomes", []) if (o.get("applied_at") or "") >= week_ago]
    by_var = Counter(o["resume_variant"] for o in wk)
    by_ats = Counter(o["ats_provider"] for o in wk)
    lines = [
        "# Weekly Intelligence Report", "",
        f"Generated: {now()}", "",
        f"- Companies discovered (all-time): {len(set(j.get('company') for j in jd['jobs']))}",
        f"- Applications this week: {len(wk)}",
        f"- Interview/assessment events: see email_events.json",
        f"- Response rate: needs email history (0 responses detected so far)", "",
        "## Resume variant performance (this week)",
    ] + [f"- {v}: {c} applications" for v, c in by_var.most_common()] + ["", "## ATS providers (this week)"] + \
      [f"- {a}: {c}" for a, c in by_ats.most_common()] + [
        "", "## Top companies by hiring score",
    ] + [f"- #{r['rank']} {r['company']} ({r['score']})" for r in rank[:10]] + [
        "", "## Provider performance", "",
    ] + [f"- {p}: endpoints={d.get('verified_endpoints',0)}, queue={d.get('jobs_in_queue',0)}, submissions={d.get('submissions',0)}"
         for p, d in list(perf.get("providers", {}).items()) if d.get("verified_endpoints")] + [
        "", "## Recommendations",
        "- Keep Tier-1 companies on 6h scans; they produce freshest frontend roles.",
        "- Ashby/Greenhouse boards convert best through public APIs - prioritize their discovery.",
        "- Complete Q030 captcha + LinkedIn manual batch to compound volume.",
        "- Drop resume PDFs per selected variant before interviews (MD variants are source-of-truth).",
    ]
    (BASE/"weekly_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("weekly_report.md written")

def norm_hit(c): return c

if __name__ == "__main__":
    cmds = {"resume-select": lambda: cmd_resume_select(sys.argv[2] if len(sys.argv) > 2 else None),
            "outcomes": cmd_outcomes, "followups": cmd_followups, "dashboard": cmd_dashboard,
            "email-scan": cmd_email_scan, "coldstart": cmd_coldstart, "weekly": cmd_weekly}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "refresh":
        cmd_resume_select(); cmd_outcomes(); cmd_followups(); cmd_email_scan()
        cmd_dashboard(); cmd_weekly()
    elif cmd in cmds:
        cmds[cmd]()
    else:
        print("usage: jobops.py refresh|resume-select|outcomes|followups|dashboard|email-scan|coldstart|weekly")
