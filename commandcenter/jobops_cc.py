#!/usr/bin/env python3
"""
JobOps Command Center - single read-only frontend server.

READ-ONLY toward the AutoApply engine: every engine file is opened for reading
only, cached by mtime, and never written. The only mutating endpoints are thin
proxies to the EXISTING recruiter outreach server (127.0.0.1:3100), which keeps
enforcing its own 12/day limit, 4-minute gap and duplicate protection.

Run:  python jobops_cc.py [--port 3200]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent          # <PROJECT_ROOT>\commandcenter
ROOT = BASE.parent                              # <PROJECT_ROOT>
ENGINE = ROOT / "autoapply"
OUTREACH_DIR = ROOT / "outreach"
HTML_FILE = BASE / "index.html"

OUTREACH_UPSTREAM = ("127.0.0.1", 3100)

F = {
    "jobs": ENGINE / "jobs.json",
    "dedupe": ENGINE / "applied_dedupe_index.json",
    "truth": ENGINE / "gmail_truth_index.json",
    "scanlog": ENGINE / "gmail_scan_log.json",
    "parser": ENGINE / "gmail_parser_metrics.json",
    "outcomes": ENGINE / "outcomes.json",
    "followups": ENGINE / "followups.json",
    "dashboard": ENGINE / "dashboard.json",
    "analytics": ENGINE / "analytics.json",
    "resume_analytics": ENGINE / "resume_analytics.json",
    "resume_intel": ENGINE / "resume_intelligence.json",
    "hiring": ENGINE / "hiring_intelligence.json",
    "universe": ENGINE / "company_universe.json",
    "rankings": ENGINE / "company_rankings.json",
    "providers": ENGINE / "provider_performance_report.json",
    "reliability": ENGINE / "reliability.json",
    "perf": ENGINE / "performance_optimization.json",
    "discovery_week": ENGINE / "weekly_discovery_report.json",
    "discovery_rate": ENGINE / "discovery_success_rate.json",
    "review": ENGINE / "review_queue.json",
    "failed": ENGINE / "failed_jobs.json",
    "status_live": ENGINE / "status_live.json",
    "checkpoint": ENGINE / "checkpoint.json",
    "morning_brief": ENGINE / "morning_brief.md",
    "outreach_queue": OUTREACH_DIR / "recruiter_outreach.json",
}

_cache: dict = {}


# --------------------------------------------------------------- io helpers

def load_cached(key: str):
    path = F.get(key)
    if path is None:
        path = Path(key)
    try:
        mt = path.stat().st_mtime
    except OSError:
        return None
    ent = _cache.get(str(path))
    if ent and ent[0] == mt:
        return ent[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ent[1] if ent else None          # fall back to last good copy
    _cache[str(path)] = (mt, data)
    return data


def load_text(key: str, tail_lines: int = 0) -> str | None:
    path = F.get(key) or Path(key)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if tail_lines > 0:
        lines = text.strip().splitlines()
        return "\n".join(lines[-tail_lines:])
    return text


def now_local():
    return datetime.now(timezone.utc).astimezone()


def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(s)
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Source files contain double-encoded UTF-8 (UTF-8 bytes decoded as cp1252).
# Verified against real data: "–" arrives as â + € + " (U+201C). Clean at
# DISPLAY time only; engine files are never rewritten.
_MOJIBAKE = [
    ("\u00e2\u20ac\u201c", "–"),   # en dash   (E2 80 93 -> â€œ)
    ("\u00e2\u20ac\u201d", "—"),   # em dash   (E2 80 94 -> â€)
    ("\u00e2\u20ac\u2122", "'"),   # ' (E2 80 99 -> â„¢)
    ("\u00e2\u20ac\u02dc", "'"),   # ' (E2 80 98 -> â€˜)
    ("\u00e2\u20ac\u0153", '"'),   # " (E2 80 9C -> â€œ)
    ("\u00e2\u20ac\u009d", '"'),   # " (E2 80 9D -> â€)
    ("\u00e2\u20ac\u00a6", "..."), # … (E2 80 A6 -> â€¦)
    ("\u00c2", ""),                # stray Â from 2-byte chars
]


def fix_text(s):
    if not isinstance(s, str):
        return s
    for bad, good in _MOJIBAKE:
        if bad in s:
            s = s.replace(bad, good)
    return s


def clean_job(j: dict) -> dict:
    out = dict(j)
    for k in ("company", "title", "location", "portal", "error"):
        out[k] = fix_text(out.get(k))
    ev = out.get("evidence")
    if isinstance(ev, dict):
        out["evidence"] = {k: fix_text(v) if isinstance(v, str) else v for k, v in ev.items()}
    return out


def slugify_company(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s


# ------------------------------------------------------------ derived helpers

def jobs_data():
    d = load_cached("jobs") or {"jobs": []}
    return d.get("jobs", [])


def hiring_map() -> dict:
    h = load_cached("hiring") or {}
    out = {}
    for c in h.get("companies", []):
        out[norm_name(c.get("company", ""))] = c
    return out


def resume_selection_map() -> dict:
    ri = load_cached("resume_intel") or {}
    return {s.get("job_id"): s for s in ri.get("selections", [])}


def kit_dirs() -> dict:
    """company-norm -> kit directory name"""
    root = ENGINE / "interviews"
    out = {}
    try:
        for p in root.iterdir():
            if p.is_dir():
                out[norm_name(p.name.replace("_", " "))] = p.name
    except OSError:
        pass
    return out


def kit_files(slug: str) -> list:
    d = ENGINE / "interviews" / slug
    if not d.is_dir():
        return []
    return sorted([p.name for p in d.glob("*.md")])


def suggest_variant(title: str) -> str:
    t = (title or "").lower()
    if re.search(r"next\.?js", t):
        return "NEXTJS"
    if re.search(r"full[- ]?stack|mern|node|backend|php|python", t):
        return "FULLSTACK"
    return "REACT"


def outreach_records() -> list:
    d = load_cached("outreach_queue") or {}
    return d.get("records", [])


def outreach_summary() -> dict:
    recs = outreach_records()
    parents = [r for r in recs if r.get("kind") != "followup"]
    today = now_local().date()
    sent_today = 0
    replied = 0
    drafts_ready = 0
    for r in recs:
        st = r.get("display_status") or r.get("status")
        t = parse_dt(r.get("sent_at") or "")
        if t and t.date() == today:
            sent_today += 1
        if st == "replied":
            replied += 1
        if st == "ready" or (st == "draft" and r.get("draft")):
            drafts_ready += 1
    return {
        "total_parents": len(parents),
        "sent_today": sent_today,
        "daily_limit": 12,
        "replied": replied,
        "drafts_ready": drafts_ready,
    }


def gmail_confirmations() -> list:
    t = load_cached("truth") or {}
    out = []
    for key, e in t.get("entries", {}).items():
        out.append({
            "key": key,
            "company": e.get("company"),
            "role_hint": e.get("role_hint") or None,   # often absent -> shown as "-"
            "date": e.get("date"),
            "msg_id": e.get("msg_id"),                 # frequently null in index
            "confidence": e.get("confidence"),
        })
    return out


def confirmations_by_company() -> dict:
    out = {}
    for e in gmail_confirmations():
        out.setdefault(norm_name(e.get("company", "")), []).append(e)
    return out


def followup_items() -> list:
    f = load_cached("followups") or {}
    return f.get("items", [])


def followups_with_state(items=None):
    items = followup_items() if items is None else items
    today = now_local().date()
    recs = outreach_records()
    fu_recs = [r for r in recs if r.get("kind") == "followup"]
    # prefer richer outreach follow-up records when they carry sent context
    enriched = []
    for it in items:
        due = parse_dt(it.get("due_date") or "")
        days = (due.date() - today).days if due else None
        linked_parent = next((r for r in fu_recs
                              if r.get("job_id") == it.get("job_id")
                              and str(r.get("followup_day")) == str(it.get("day"))), None)
        recruiter = None
        rec_id = None
        if linked_parent and linked_parent.get("parent_id"):
            parent = next((r for r in recs if r["id"] == linked_parent["parent_id"]), {})
            recruiter = parent.get("recruiter_email") or None
            if linked_parent.get("message_id") is None and linked_parent.get("draft"):
                rec_id = linked_parent.get("id")
        enriched.append({**it, "_days_until_due": days, "_recruiter": recruiter,
                         "_rec_id": rec_id})
    return enriched


def heartbeat_tail(n=6):
    raw = load_text(ENGINE / "heartbeat.log", tail_lines=n) or ""
    out = []
    for line in raw.strip().splitlines()[::-1]:
        parts = line.split("|", 2)
        out.append({"ts": parts[0] if parts else "", "batch": parts[1] if len(parts) > 1 else "",
                    "event": parts[2] if len(parts) > 2 else ""})
    return out


def heartbeat_age_sec() -> float | None:
    raw = load_text(ENGINE / "heartbeat.log", tail_lines=1)
    if not raw:
        return None
    ts = raw.strip().split("|", 1)[0]
    d = parse_dt(ts)
    if not d:
        return None
    return round((now_local() - d).total_seconds(), 1)


def supervisor_running() -> bool:
    age = heartbeat_age_sec()
    if age is None:
        return False
    return age < 900          # 15 min without any heartbeat => treat as stopped


def status_live() -> dict:
    return load_cached("status_live") or {}


# ---------------------------------------------------------------- API builders

def api_overview():
    jobs = [clean_job(j) for j in jobs_data()]
    dash = load_cached("dashboard") or {}
    counts = {}
    for j in jobs:
        counts[j.get("status", "?")] = counts.get(j.get("status", "?"), 0) + 1

    today = now_local().date()
    week_ago = today - timedelta(days=7)
    fu_items = followups_with_state()
    fu_due_today = [i for i in fu_items if i.get("_days_until_due") is not None and i["_days_until_due"] <= 0]
    osu = outreach_summary()

    disc = load_cached("discovery_week") or {}

    kpis = {
        "submitted": counts.get("submitted", 0),
        "applications_today": dash.get("applications_today"),
        "applications_this_week": dash.get("applications_this_week"),
        "pending": counts.get("pending", 0),
        "in_progress": counts.get("in_progress", 0),
        "review_required": counts.get("review_required", 0),
        "recruiter_replies": osu["replied"],
        "interview_kits": dash.get("interview_kits_ready"),
        "scheduled_interviews": None,          # unavailable: no invitation data source yet
        "assessments_pending": dash.get("assessments_pending"),
        "offers": dash.get("offers"),
        "followups_due_today": len(fu_due_today),
        "new_jobs_discovered_week": disc.get("jobs_discovered_total"),
        "_labels": {
            "applications_today": "engine report",
            "scheduled_interviews": "unavailable - no invitations detected yet",
        },
    }

    # ---- ACTION REQUIRED (sorted P0..P3) ----
    actions = []

    sup_up = supervisor_running()
    if not sup_up and counts.get("pending", 0) > 0:
        actions.append({"priority": "P0", "type": "system", "title": "Automation stopped with pending work",
                        "detail": f"{counts.get('pending')} pending jobs but no supervisor heartbeat.",
                        "link": "#health"})
    for r in (load_cached("review") or {}).get("items", []):
        actions.append({"priority": "P0", "type": "manual_task",
                        "title": f"Manual action needed: {r.get('company')}",
                        "detail": f"{r.get('reason','task').upper()} - forms often already filled.",
                        "link": "#applications", "job_id": r.get("id")})
    rel = load_cached("reliability") or {}
    for alert in rel.get("alerts", []):
        if alert and "none" not in alert.lower():
            actions.append({"priority": "P1", "type": "system", "title": "Reliability alert",
                            "detail": alert, "link": "#health"})
    for i in fu_due_today:
        actions.append({"priority": "P1", "type": "followup",
                        "title": f"Follow-up due today: {i.get('company')}",
                        "detail": f"Day-{i.get('day')} follow-up for {i.get('job_id')}. Draft ready.",
                        "link": "#followups", "job_id": i.get("job_id")})
    for i in fu_items:
        if i.get("_days_until_due") is not None and 0 < i["_days_until_due"] <= 3:
            actions.append({"priority": "P2", "type": "followup",
                            "title": f"Follow-up in {i['_days_until_due']}d: {i.get('company')}",
                            "detail": f"Day-{i.get('day')} scheduled.", "link": "#followups"})
    if osu["replied"]:
        actions.append({"priority": "P0", "type": "reply",
                        "title": f"{osu['replied']} recruiter repl{'y' if osu['replied']==1 else 'ies'} detected",
                        "detail": "Open Outreach to respond.", "link": "#outreach"})
    if osu["drafts_ready"]:
        actions.append({"priority": "P2", "type": "outreach",
                        "title": f"{osu['drafts_ready']} outreach draft(s) ready to review",
                        "detail": f"Sent today: {osu['sent_today']}/{osu['daily_limit']}.",
                        "link": "#outreach"})
    prio_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    actions.sort(key=lambda a: prio_order.get(a["priority"], 9))

    # ---- SYSTEM STATUS ----
    sl = status_live()
    hb_age = heartbeat_age_sec()
    wd = sl.get("watchdog", {})
    scan = load_cached("scanlog") or {}
    system = {
        "supervisor": "running" if sup_up else "stopped",
        "phase": sl.get("phase") or dash.get("phase"),
        "worker_id": sl.get("worker_id"),
        "current_job": sl.get("current_job"),
        "current_batch": sl.get("current_batch"),
        "queue_size": counts.get("pending", 0),
        "heartbeat_age_sec": hb_age,
        "watchdog_state": wd.get("state"),
        "gmail_last_sync": (load_cached("truth") or {}).get("generated_at"),
        "gmail_last_scan": scan.get("scan_started"),
        "browseros": None,                     # unavailable: not tracked anywhere
        "last_discovery": disc.get("generated_at"),
    }

    return {"kpis": kpis, "actions": actions[:14], "system": system,
            "generated_at": now_local().isoformat(timespec="seconds")}


def api_applications():
    jobs = jobs_data()
    conf = confirmations_by_company()
    sel = resume_selection_map()
    fu = followup_items()
    fu_by_job = {}
    for i in fu:
        fu_by_job.setdefault(i.get("job_id"), []).append(i)
    osu = {(r.get("job_id"), norm_name(r.get("company", ""))): r
           for r in outreach_records() if r.get("kind") == "outreach"}
    outcomes = {o.get("job_id"): o for o in (load_cached("outcomes") or {}).get("outcomes", [])}
    exp = {a.get("job_id"): a for a in (load_cached(
        ENGINE / "experiment_engine.json") or {}).get("applications", [])}
    kits = kit_dirs()

    rows = []
    for j in jobs:
        cid = j.get("id")
        comp_n = norm_name(j.get("company", ""))
        conf_hit = conf.get(comp_n, [])
        o = outcomes.get(cid, {}) or {}
        x = exp.get(cid, {}) or {}
        ou = osu.get((cid, comp_n))
        rows.append({
            "id": cid,
            "company": j.get("company"),
            "role": j.get("title"),
            "portal": j.get("portal"),
            "location": j.get("location"),
            "status": j.get("status"),
            "match_score": j.get("match_score"),
            "attempts": j.get("attempts"),
            "url": j.get("url"),
            "updated_at": j.get("updated_at"),
            "evidence": j.get("evidence") or {},
            "resume_variant": o.get("resume_variant") or x.get("resume_variant")
                or ((j.get("evidence") or {}).get("resume_variant"))
                or suggest_variant(j.get("title", "")),
            "resume_source": "outcome" if o.get("resume_variant") else
                             ("selection" if x.get("resume_variant") else "suggested"),
            "gmail_confirmed": bool(conf_hit),
            "gmail_confirmed_at": conf_hit[0]["date"] if conf_hit else None,
            "followups": len(fu_by_job.get(cid, [])),
            "outreach_status": (ou or {}).get("status"),
            "outreach_reply": (ou or {}).get("reply_status") or None,
            "response_detected": o.get("response_detected_at"),
            "error": j.get("error"),
            "kit_slug": kits.get(comp_n),
        })
    return {"rows": rows, "generated_at": now_local().isoformat(timespec="seconds")}


def api_priority():
    hm = hiring_map()
    jobs = [clean_job(j) for j in jobs_data() if j.get("status") == "pending"]
    sel = resume_selection_map()
    rows = []
    for j in jobs:
        h = hm.get(norm_name(j.get("company", ""))) or {}
        match = j.get("match_score") or 0
        hs = h.get("hiring_score") or 0
        age = h.get("avg_posting_age_days")
        hist = h.get("history", {}) or {}
        fresh_bonus = max(0.0, 1.0 - (age or 30) / 30.0)
        india = bool(h.get("india_hiring"))
        remote = bool(h.get("remote_hiring"))
        score = round(0.55 * match + 0.30 * hs + 8 * fresh_bonus
                      + (3 if india else 0) + (2 if remote else 0), 1)
        rows.append({
            "id": j.get("id"), "company": j.get("company"), "role": j.get("title"),
            "location": j.get("location"), "ats": j.get("portal") or h.get("ats"),
            "match_score": match, "hiring_score": hs,
            "freshness_days": age, "tier": h.get("tier"),
            "in_universe": bool(h),
            "is_hiring": h.get("is_hiring"), "india": bool(h.get("india_hiring")),
            "remote": bool(h.get("remote_hiring")),
            "endpoint_verified": h.get("endpoint_verified"),
            "last_scan": h.get("last_scan"),
            "postings_seen": hist.get("total_postings_seen", 0),
            "fresh_7d": hist.get("fresh_7d", 0),
            "react_seen": hist.get("react", 0), "nextjs_seen": hist.get("nextjs", 0),
            "fullstack_seen": hist.get("fullstack", 0),
            "frontend_seen": hist.get("frontend", 0), "mern_seen": hist.get("mern", 0),
            "js_seen": hist.get("javascript", 0),
            "company_discovered": (h.get("application") or {}).get("discovered"),
            "company_submitted": (h.get("application") or {}).get("submitted"),
            "resume_recommendation": (sel.get(j.get("id")) or {}).get("chosen")
                                     or suggest_variant(j.get("title", "")),
            "resume_why": (sel.get(j.get("id")) or {}).get("why"),
            "url": j.get("url"), "notes": (j.get("evidence") or {}).get("notes"),
            "evidence": j.get("evidence") or {},
            "updated_at": j.get("updated_at"),
            "priority_score": score,
        })
    rows.sort(key=lambda r: -r["priority_score"])
    return {"rows": rows, "formula": "0.55*match + 0.30*hiring + freshness + geo bonuses",
            "generated_at": now_local().isoformat(timespec="seconds")}


def api_discovery():
    wk = load_cached("discovery_week") or {}
    rate = load_cached("discovery_rate") or {}
    prov = load_cached("providers") or {}
    dash = load_cached("dashboard") or {}
    dh = dash.get("discovery_health", {})
    jobs = [clean_job(j) for j in jobs_data()]
    pending = sorted([j for j in jobs if j.get("status") == "pending"],
                     key=lambda j: j.get("updated_at") or "", reverse=True)[:60]
    metrics = {
        "companies_scanned": rate.get("companies_scanned"),
        "with_matches": rate.get("with_matches"),
        "skipped_companies": rate.get("skipped"),
        "skip_reasons": rate.get("skip_reasons"),
        "universe_size": dh.get("universe_size") or (load_cached("hiring") or {}).get("universe_size"),
        "verified_endpoints": dh.get("verified_endpoints"),
        "jobs_discovered_total": wk.get("jobs_discovered_total"),
        "by_source": wk.get("by_source"),
        "by_portal": wk.get("by_portal"),
        "window": wk.get("window"),
    }
    return {"metrics": metrics, "recent": pending,
            "generated_at": now_local().isoformat(timespec="seconds")}


def api_gmail():
    scan = load_cached("scanlog") or {}
    parser = load_cached("parser") or {}
    truth = gmail_confirmations()
    osu = outreach_records()
    replied_companies = {norm_name(r.get("company", ""))
                         for r in osu if (r.get("reply_status") == "replied")}
    contacted = {norm_name(r.get("company", "")) for r in osu if r.get("sent_at")}

    confirmations, recruiter, other = [], [], []
    for e in truth:
        cn = norm_name(e.get("company", ""))
        item = {**e, "bucket_reason": ""}
        if cn in replied_companies:
            item["bucket_reason"] = "recruiter replied in outreach"
            recruiter.append(item)
        elif cn in contacted:
            item["bucket_reason"] = "active outreach conversation"
            recruiter.append(item)
        else:
            confirmations.append(item)
    # OTP / manual-action emails are NOT captured anywhere -> say so honestly
    return {
        "action_required": [],
        "action_required_note": "unavailable - the existing Gmail scan indexes application "
                                "confirmations only; it does not export interview/OTP emails",
        "confirmations": confirmations,
        "recruiter": recruiter,
        "other_note": f"{(parser.get('classified') or {}).get('generic:other', 0)} unclassified "
                      f"emails counted by the parser (content not exported)",
        "scan": scan, "parser": parser,
        "generated_at": now_local().isoformat(timespec="seconds"),
    }


def api_interviews():
    root = ENGINE / "interviews"
    kits = []
    try:
        for p in sorted(root.iterdir()):
            if p.is_dir():
                kits.append({"slug": p.name, "files": kit_files(p.name)})
    except OSError:
        pass
    dash = load_cached("dashboard") or {}
    return {
        "scheduled": [],       # unavailable: no invitation/date source exists yet
        "note": "no interview invitations detected by the existing Gmail pipeline; "
                "kits below are auto-prepared from submitted applications",
        "kits_prepared": dash.get("interview_kits_ready"),
        "kits": kits,
    }


def api_assessments():
    dash = load_cached("dashboard") or {}
    return {"pending": dash.get("assessments_pending"), "items": [],
            "note": "no assessment deadlines tracked by the existing system yet"}


def api_followups():
    items = followups_with_state()
    today = now_local().date()
    overdue, due_today, upcoming, done = [], [], [], []
    for i in items:
        d = i.get("_days_until_due")
        st = (i.get("status") or "").lower()
        if st in ("sent", "done"):
            done.append(i)
        elif d is None:
            upcoming.append(i)
        elif d < 0:
            overdue.append(i)
        elif d == 0:
            due_today.append(i)
        else:
            upcoming.append(i)
    keyf = lambda i: (parse_dt(i.get("due_date")) or now_local())
    return {"overdue": sorted(overdue, key=keyf),
            "due_today": sorted(due_today, key=keyf),
            "upcoming": sorted(upcoming, key=keyf)[:80],
            "policy": (load_cached("followups") or {}).get("policy"),
            "generated_at": now_local().isoformat(timespec="seconds")}


def api_analytics():
    a = load_cached("analytics") or {}
    jobs = jobs_data()
    outcomes = (load_cached("outcomes") or {}).get("outcomes", [])
    exp = (load_cached(ENGINE / "experiment_engine.json") or {}).get("applications", [])

    def breakdown(keyfn):
        agg = {}
        for o in outcomes:
            k = keyfn(o)
            if not k:
                continue
            g = agg.setdefault(k, {"applied": 0, "submitted": 0, "responses": 0})
            g["applied"] += 1
            if (o.get("state") or "") == "submitted":
                g["submitted"] += 1
            if o.get("response_detected_at"):
                g["responses"] += 1
        return [{"group": k, **v} for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["applied"])]

    resp_times = [o.get("response_time_hours") for o in outcomes if o.get("response_time_hours")]
    return {
        "funnel": a.get("funnel"),
        "conversions": a.get("conversions"),
        "rates": {
            "response_rate_pct": (load_cached("dashboard") or {}).get("response_rate_pct"),
            "interview_rate_pct": (a.get("conversions") or {}).get("applied_to_interview_pct"),
            "offer_count": (load_cached("dashboard") or {}).get("offers"),
            "avg_response_time_hours": round(sum(resp_times) / len(resp_times), 1) if resp_times else None,
            "avg_response_time_note": None if resp_times else "unavailable - no responses recorded yet",
        },
        "ats_performance": (a.get("ats_performance") or {}).get("providers", []),
        "breakdowns": {
            "by_ats": breakdown(lambda o: o.get("ats_provider")),
            "by_company": breakdown(lambda o: o.get("company")),
            "by_resume_variant": breakdown(lambda o: o.get("resume_variant")),
            "by_location": breakdown(lambda o: o.get("location")),
            "by_role_bucket": breakdown(lambda o: (exp_by := next(
                (x for x in exp if x.get("job_id") == o.get("job_id")), {})).get("role_bucket")),
        },
        "generated_at": now_local().isoformat(timespec="seconds"),
    }


def api_resume():
    ra = load_cached("resume_analytics") or {}
    ri = load_cached("resume_intel") or {}
    sels = ri.get("selections", [])
    chosen = {}
    for s in sels:
        chosen[s.get("chosen")] = chosen.get(s.get("chosen"), 0) + 1
    return {
        "variants": ra.get("variants"),
        "recommendations": ra.get("recommendations"),
        "selection_counts": chosen,
        "recent_selections": sels[-12:][::-1],
        "paths": ri.get("variants"),
        "generated_at": ra.get("generated_at"),
    }


def api_companies(q="", flt="", page=1, per=40):
    uni = load_cached("universe") or {}
    hm = hiring_map()
    jobs = jobs_data()
    apps_by_co, subs_by_co = {}, {}
    for j in jobs:
        k = norm_name(j.get("company", ""))
        apps_by_co[k] = apps_by_co.get(k, 0) + 1
        if j.get("status") == "submitted":
            subs_by_co[k] = subs_by_co.get(k, 0) + 1
    truth = confirmations_by_company()

    qn = q.strip().lower()
    flts = set(flt.split(",")) if flt else set()
    rows = []
    for c in uni.get("companies", []):
        name = c.get("name", "")
        nn = norm_name(name)
        if qn and qn not in name.lower():
            continue
        h = hm.get(nn, {})
        hist = h.get("history", {}) or {}
        row = {
            "company": name,
            "ats": c.get("ats") or h.get("ats"),
            "endpoint_status": c.get("status"),
            "india_hiring": c.get("india_hiring") if c.get("india_hiring") is not None else h.get("india_hiring"),
            "remote_hiring": c.get("remote_hiring") if c.get("remote_hiring") is not None else h.get("remote_hiring"),
            "is_hiring": h.get("is_hiring"),
            "hiring_score": h.get("hiring_score"),
            "tier": h.get("tier"),
            "rank": h.get("rank"),
            "react": hist.get("react", 0), "nextjs": hist.get("nextjs", 0),
            "fullstack": hist.get("fullstack", 0), "frontend": hist.get("frontend", 0),
            "postings_seen": hist.get("total_postings_seen", 0),
            "last_scan": c.get("last_scan") or h.get("last_scan"),
            "jobs_in_queue": apps_by_co.get(nn, 0),
            "submitted": subs_by_co.get(nn, 0) or h.get("application", {}).get("submitted", 0),
            "gmail_confirms": len(truth.get(nn, [])),
        }
        if flts:
            ok = True
            if "dream" in flts and not (row["tier"] == 1):
                ok = False
            if "hiring" in flts and not row["is_hiring"]:
                ok = False
            if "india" in flts and not row["india_hiring"]:
                ok = False
            if "remote" in flts and not row["remote_hiring"]:
                ok = False
            if "react" in flts and not row["react"]:
                ok = False
            if "nextjs" in flts and not row["nextjs"]:
                ok = False
            if "fullstack" in flts and not row["fullstack"]:
                ok = False
            if not ok:
                continue
        rows.append(row)
    rows.sort(key=lambda r: (-(r["hiring_score"] or 0), -(r["postings_seen"])))
    total = len(rows)
    page = max(1, page)
    start = (page - 1) * per
    return {"rows": rows[start:start + per], "total": total, "page": page,
            "pages": (total + per - 1) // per}


def api_health():
    sl = status_live()
    cp = load_cached("checkpoint") or {}
    rel = load_cached("reliability") or {}
    perf = load_cached("perf") or {}
    hb_age = heartbeat_age_sec()
    counts = cp.get("stats", {})
    unknown_states = [j for j in jobs_data() if j.get("status") == "in_progress"]
    hist = (cp.get("history") or [])
    restarts = [h for h in hist if h.get("event") == "restart"][-15:][::-1]
    scan = load_cached("scanlog") or {}
    return {
        "supervisor": {"running": supervisor_running(),
                       "run_started_at": sl.get("run_started_at"),
                       "runtime": None},
        "worker": {"worker_id": sl.get("worker_id"), "phase": sl.get("phase")},
        "current_job": sl.get("current_job"),
        "current_batch": sl.get("current_batch"),
        "queue": counts,
        "unknown_state_jobs": [{"id": j["id"], "since": j.get("updated_at")} for j in unknown_states],
        "watchdog": sl.get("watchdog", {}),
        "heartbeat": {"age_sec": hb_age, "recent": heartbeat_tail(6)},
        "gmail": {"last_sync": (load_cached("truth") or {}).get("generated_at"),
                  "last_scan": scan.get("scan_started"),
                  "emails_scanned": scan.get("emails_scanned")},
        "browseros": None,
        "reliability": rel.get("counters"),
        "alerts": rel.get("alerts"),
        "restart_history": restarts,
        "performance": perf.get("measurements"),
        "optimization_recommendations": perf.get("optimization_recommendations"),
        "eta_minutes": sl.get("eta_minutes"),
        "generated_at": now_local().isoformat(timespec="seconds"),
    }


def api_global_search(q):
    qn = (q or "").strip().lower()
    if len(qn) < 2:
        return {"groups": []}
    groups = {"Applications/Jobs": [], "Companies": [], "Recruiters": [], "Emails": [], "Interview Kits": []}
    for j in jobs_data():
        hay = " ".join([str(j.get(k, "")) for k in ("id", "company", "title", "url")]).lower()
        if qn in hay:
            groups["Applications/Jobs"].append({"title": f'{j.get("company")} - {j.get("title")}',
                                                "sub": f'{j.get("id")} · {j.get("status")} · {j.get("portal")}',
                                                "link": f"#application/{j.get('id')}"})
    uni = load_cached("universe") or {}
    n = 0
    for c in uni.get("companies", []):
        if qn in str(c.get("name", "")).lower():
            groups["Companies"].append({"title": c.get("name"),
                                        "sub": f'ATS: {c.get("ats")} · status: {c.get("status")}',
                                        "link": f"#companies?q={urllib.parse.quote(c.get('name',''))}"})
            n += 1
            if n >= 8:
                break
    for r in outreach_records():
        hay = json.dumps(r, ensure_ascii=False).lower()
        if qn in hay and r.get("kind") != "followup":
            groups["Recruiters"].append({"title": f'{r.get("recruiter_name") or "(unnamed)"} @ {r.get("company")}',
                                         "sub": f'{r.get("recruiter_email")} · {r.get("status")}',
                                         "link": "#outreach"})
            if len(groups["Recruiters"]) >= 8:
                break
    for e in gmail_confirmations():
        if qn in str(e.get("company", "")).lower():
            groups["Emails"].append({"title": f'Confirmation: {e.get("company")}',
                                     "sub": f'{e.get("date")} · confidence {e.get("confidence")}',
                                     "link": "#gmail"})
            if len(groups["Emails"]) >= 5:
                break
    for slug, files in [(k["slug"], k["files"]) for k in api_interviews()["kits"]]:
        if qn in slug.lower():
            groups["Interview Kits"].append({"title": slug, "sub": f"{len(files)} kit files",
                                             "link": f"#interviews/{slug}"})
            if len(groups["Interview Kits"]) >= 5:
                break
    return {"groups": {k: v for k, v in groups.items() if v}}


# ------------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    server_version = "JobOpsCC/1.0"

    def log_message(self, fmt, *args):
        pass                                            # keep console quiet

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _proxy_outreach(self, method: str):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        url = f"http://{OUTREACH_UPSTREAM[0]}:{OUTREACH_UPSTREAM[1]}{self.path}"
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return self._json(resp.status, json.loads(resp.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            try:
                return self._json(e.code, json.loads(e.read().decode("utf-8")))
            except Exception:
                return self._json(e.code, {"error": f"upstream_{e.code}"})
        except Exception:
            return self._json(503, {"error": "outreach_server_offline",
                                    "detail": "Start it: cd <PROJECT_ROOT>\\outreach && python recruiter_outreach.py serve"})
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        q = lambda k, d="": (qs.get(k, [""])[0] or d)

        if route == "/":
            try:
                content = HTML_FILE.read_bytes()
            except FileNotFoundError:
                return self._json(500, {"error": "index.html missing"})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if route == "/api/overview":
            return self._json(200, api_overview())
        if route == "/api/applications":
            return self._json(200, api_applications())
        if route == "/api/priority":
            return self._json(200, api_priority())
        if route == "/api/discovery":
            return self._json(200, api_discovery())
        if route == "/api/gmail":
            return self._json(200, api_gmail())
        if route == "/api/interviews":
            return self._json(200, api_interviews())
        if route == "/api/kit":
            slug = q("slug")
            fname = q("file")
            safe = re.fullmatch(r"[a-z0-9_\-]+", slug or "") and re.fullmatch(r"[A-Za-z0-9_\-.]+", fname or "")
            if not safe:
                return self._json(400, {"error": "bad_params"})
            p = ENGINE / "interviews" / slug / fname
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return self._json(404, {"error": "not_found"})
            return self._json(200, {"slug": slug, "file": fname, "content": txt})
        if route == "/api/assessments":
            return self._json(200, api_assessments())
        if route == "/api/followups":
            return self._json(200, api_followups())
        if route == "/api/analytics":
            return self._json(200, api_analytics())
        if route == "/api/resume":
            return self._json(200, api_resume())
        if route == "/api/companies":
            return self._json(200, api_companies(q("q"), q("flt"), int(q("page", "1") or 1)))
        if route == "/api/health":
            return self._json(200, api_health())
        if route == "/api/search":
            return self._json(200, api_global_search(q("q")))
        if route.startswith("/api/outreach/"):
            self.path = "/api" + self.path[len("/api/outreach"):]     # /outreach -> /api/outreach
            return self._proxy_outreach("GET")
        if route.startswith("/api/brief"):
            return self._json(200, {"brief": load_text("morning_brief")})
        return self._json(404, {"error": "unknown_route"})

    def do_POST(self):
        if self.path.startswith("/api/outreach/"):
            self.path = "/api" + self.path[len("/api/outreach"):]     # /send -> /api/send
            return self._proxy_outreach("POST")
        return self._json(404, {"error": "read_only_endpoint"})


def main() -> int:
    ap = argparse.ArgumentParser(description="JobOps Command Center (read-only)")
    ap.add_argument("--port", type=int, default=3200)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"JobOps Command Center: http://127.0.0.1:{args.port}/  (read-only)")
    print(f"Outreach proxy target : http://{OUTREACH_UPSTREAM[0]}:{OUTREACH_UPSTREAM[1]} (must be started separately)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())