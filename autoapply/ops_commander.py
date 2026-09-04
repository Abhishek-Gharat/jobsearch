#!/usr/bin/env python3
"""Ops Commander - daily/weekly operational cycles using existing modules only."""
import json, subprocess, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
PY = sys.executable

def load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception: return d

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def run(cmd, timeout=900):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=str(BASE))
        tail = "\n".join((r.stdout or "").splitlines()[-2:])
        return f"ok({tail[:80]})" if r.returncode == 0 else f"rc={r.returncode}"
    except Exception as e:
        return f"err:{e}"

def is_sunday():
    return datetime.now(timezone.utc).weekday() == 6

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    steps = []
    t0 = datetime.now(timezone.utc)

    # 1) fresh discovery (existing module)
    steps.append(("discovery", run([PY, "company_universe.py", "discover", "--days", "7"], 600)))
    # 2) application engine health (it runs independently; record snapshot)
    jd = load(BASE/"jobs.json", {"jobs": []})
    live = load(BASE/"status_live.json", {})
    st = Counter(j.get("status") for j in jd["jobs"])
    # 3) intelligence + experiments + analytics refresh (existing modules)
    steps.append(("hiring_intelligence", run([PY, "hiring_intelligence.py"], 300)))
    steps.append(("interview_prep", run([PY, "interview_prep.py"], 120)))
    steps.append(("experiment_engine", run([PY, "experiment_engine.py"], 300)))
    steps.append(("analytics", run([PY, "analytics.py"], 300)))
    steps.append(("jobops_refresh", run([PY, "jobops.py", "refresh"], 300)))

    ee = load(BASE/"email_events.json", {"events": []})
    fu = load(BASE/"followups.json", {"items": []})
    intel = load(BASE/"hiring_intelligence.json", {"companies": []})
    exp = load(BASE/"experiment_engine.json", {})
    today = datetime.now(timezone.utc).date().isoformat()
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()

    new_overnight = [j for j in jd["jobs"]
                     if str((j.get("evidence") or {}).get("posted_age_days")) not in ("None",)
                     and j.get("status") == "pending"
                     and (j.get("updated_at") or "") >= yday]
    applied_overnight = [j for j in jd["jobs"] if j.get("status") == "submitted"
                          and (j.get("updated_at") or "") >= yday]
    due_fu = [i for i in fu.get("items", []) if i["due_date"] <= today]
    invites = [e for e in ee.get("events", []) if e.get("type") == "interview"]
    assess = [e for e in ee.get("events", []) if e.get("type") == "assessment"]
    rel = load(BASE/"reliability.json", {"alerts": [], "counters": {}})
    t1_names = set(next((t["companies"] for t in load(BASE/"tier_schedule.json",
                    {"tiers":[]})["tiers"] if t["tier"]==1), []))
    hot_pending = [j for j in jd["jobs"] if j.get("status") == "pending"
                   and j.get("portal") != "linkedin"
                   and j.get("company") in t1_names][:8]

    # priority actions per standing rules
    actions = []
    if invites: actions.append(f"RESPOND to {len(invites)} interview invitation(s): "
                               + ", ".join(e.get('company','?') for e in invites))
    if assess: actions.append(f"Complete {len(assess)} assessment(s)")
    if due_fu: actions.append(f"Send {len(due_fu)} due follow-up draft(s) from followup_dashboard.html")
    if applied_overnight:
        cos = sorted(set(j['company'] for j in applied_overnight))[:6]
        actions.append("Prep interviews for yesterday's submissions: " + ", ".join(cos))
        actions.append(f"Keep pipeline flowing: {st.get('pending',0)} pending "
                       f"({sum(1 for j in jd['jobs'] if j.get('status')=='pending' and j.get('portal')!='linkedin')} automatable)")
    if len(actions) < 3 and hot_pending:
        names = ", ".join(j["company"] for j in hot_pending[:3])
        actions.append(f"Tier-1 hot targets ready: {names}")
    if len(actions) < 3:
        actions.append("Expand universe verification to raise discovery yield")
    actions = actions[:3]

    brief = f"""# Morning Brief — {today}

## Overnight results
- New pending jobs discovered: **{len(new_overnight)}**
- Applications submitted overnight: **{len(applied_overnight)}**
  - """ + "\n  - ".join(f"{j['id']} {j['company']} — {j['title']}" for j in applied_overnight[:8]) + f"""

## Queue health
""" + json.dumps(dict(st), indent=1).replace('"', "") + f"""
- Automatable ATS pending: {sum(1 for j in jd['jobs'] if j.get('status')=='pending' and j.get('portal')!='linkedin')}
- LinkedIn manual queue: {sum(1 for j in jd['jobs'] if j.get('status')=='pending' and j.get('portal')=='linkedin')}
- Unknown states: 0 (enforced)

## Watchdog / worker
- Phase: {live.get('phase','-')} · Current batch: {live.get('current_batch','-')}
- Heartbeat age: {(live.get('watchdog') or {}).get('heartbeat_age_sec','-')}s · Restarts(total): {(live.get('watchdog') or {}).get('restart_count',0)}
- Reliability alerts: {rel.get('alerts')}

## Recruiter activity
- Interview invitations: {len(invites)} · Assessments: {len(assess)}

## Follow-ups due today: {len(due_fu)}
""" + ("\n".join(f"- {i['company']} ({i['day']}d) — draft ready" for i in due_fu[:6]) or "- none") + f"""

## Most likely to reply today (highest response-rate companies contacted)
""" + ("\n".join(f"- {c['company']} (response rate {c['response_rate_pct']}%, n={c['applied']})"
                 for c in exp.get("company_performance_ranked", [])[:5] if c.get("applied")) or "- insufficient history yet") + f"""

## Priority actions (max 3)
""" + "\n".join(f"{i+1}. {a}" for i, a in enumerate(actions)) + f"""

---
Operational steps executed this cycle:
""" + "\n".join(f"- {k}: {v}" for k, v in steps) + "\n"

    out = BASE / ("weekly_review.md" if mode == "weekly" and is_sunday() else "morning_brief.md")
    if mode == "weekly":
        body = ["# Weekly Review", "", f"Generated: {now()}", "",
                "## Interview conversion trend",
                f"- Interviews to date: {len(invites)} (chart unlocks at n>=30)",
                "", "## Resume experiment progress",
                json.dumps(exp.get("experiments", {}).get("resume_ab_by_role_bucket", {}), indent=1),
                "", "## Best-performing ATS",
                json.dumps(exp.get("ats_comparison", {}), indent=1),
                "", "## Companies worth revisiting",
                "\n".join("- " + c["company"] for c in
                          exp.get("company_performance_ranked", [])[:10]),
                "", "## Measured bottlenecks",
                f"- Restart counters: {rel.get('counters')}",
                f"- Alerts: {rel.get('alerts')}",
                "- Throughput details: performance_optimization.json"]
        out.write_text("\n".join(body) + "\n", encoding="utf-8")
        print(f"weekly_review.md written")
    else:
        out.write_text(brief, encoding="utf-8")
        print(brief)


if __name__ == "__main__":
    main()
