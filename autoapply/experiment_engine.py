#!/usr/bin/env python3
"""V7 Experiment Engine - A/B resume variants, follow-up timing, ATS/company conversion."""
import json, re, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
MIN_N_SIGNIFICANT = 30

def load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception: return d

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def role_bucket(title):
    t = (title or "").lower()
    if any(k in t for k in ("full stack","fullstack","mern","node")): return "fullstack_mern"
    if any(k in t for k in ("react","next","frontend","front-end","ui engineer","web developer","javascript")):
        return "frontend_react"
    return "other_swe"

def main():
    jd = load(BASE/"jobs.json", {"jobs": []})
    ri = load(BASE/"resume_intelligence.json", {"selections": []})
    oc = load(BASE/"outcomes.json", {"outcomes": []})
    ee = load(BASE/"email_events.json", {"events": []})
    fu = load(BASE/"followups.json", {"items": []})

    varmap = {s["job_id"]: s.get("chosen") for s in ri.get("selections", [])}
    ev_by_co = defaultdict(list)
    for e in sorted(ee.get("events", []), key=lambda e: e.get("detected_at") or ""):
        ev_by_co[(e.get("company") or "").lower()].append(e)

    records = []
    for j in jd["jobs"]:
        if j.get("status") in ("pending", "in_progress"):
            continue
        co = (j.get("company") or "").lower()
        applied_at = None
        for f in BASE.glob("results/*.jsonl"):
            try:
                for ln in f.read_text(encoding="utf-8-sig").splitlines():
                    if not ln.strip(): continue
                    r = json.loads(ln)
                    if r.get("job_id") == j["id"] and r.get("result") == "submitted":
                        applied_at = r.get("ts") or applied_at
            except Exception:
                pass
        responses = [e for e in ev_by_co.get(co, [])
                     if not applied_at or (e.get("detected_at") or "") >= applied_at]
        resp_type = responses[0].get("type") if responses else None
        records.append({
            "company": j.get("company"), "ats_provider": (j.get("portal") or "").split(":")[0],
            "role": j.get("title"), "role_bucket": role_bucket(j.get("title")),
            "location": j.get("location"),
            "resume_variant": varmap.get(j["id"], "REACT(default)"),
            "cover_letter_used": False,
            "application_timestamp": applied_at,
            "recruiter_response": resp_type,
            "interview_outcome": ("interview" if resp_type == "interview" else None),
            "rejection": resp_type == "rejection",
            "offer": resp_type == "offer",
            "state": j.get("status"),
        })

    # ---------- resume A/B within similar role buckets ----------
    ab_resume = {}
    for bucket in ("frontend_react", "fullstack_mern"):
        arms = defaultdict(lambda: {"applied":0,"responses":0,"interviews":0})
        for r in records:
            if r["role_bucket"] != bucket: continue
            v = r["resume_variant"]; a = arms[v]
            a["applied"] += 1
            a["responses"] += 1 if r["recruiter_response"] else 0
            a["interviews"] += 1 if r["interview_outcome"] else 0
        for v, a in arms.items():
            a["response_rate_pct"] = round(100*a["responses"]/a["applied"],1) if a["applied"] else None
            a["significance"] = ("significant" if a["applied"] >= MIN_N_SIGNIFICANT else
                                  f"directional_only(n={a['applied']}<{MIN_N_SIGNIFICANT})")
        ab_resume[bucket] = {v: a for v, a in arms.items()}

    # ---------- follow-up cohorts (design ready; sends are manual) ----------
    fu_cohorts = defaultdict(lambda: {"companies":0,"responses":0})
    for item in fu.get("items", []):
        cohort = f"day{item['day']}" if item.get("status") != "scheduled" else "not_yet_sent"
        fu_cohorts[cohort]["companies"] += 1
    fu_cohorts["control_no_followup_yet"] = {
        "companies": sum(1 for r in records if r["state"] == "submitted")}
    fu_status = {"note": ("no follow-up has been sent yet (MANUAL_ONLY policy); "
                          "experiment activates once you send drafts and log replies "
                          "via emails_inbox/"),
                  "cohorts": dict(fu_cohorts)}

    # ---------- company performance ----------
    comp = defaultdict(lambda: {"applied":0,"responses":0,"interviews":0,"offers":0,"resp_h":[]})
    for o in oc.get("outcomes", []):
        c = o.get("company")
        d = comp[c]
        if o.get("state") in ("submitted","failed","failed_unconfirmed","review_required"):
            d["applied"] += 1
        if o.get("state") == "submitted": pass
        if o.get("response_time_hours") is not None:
            d["responses"] += 1; d["resp_h"].append(o["response_time_hours"])
    for r in records:
        d = comp[r["company"]]
        if r["interview_outcome"]: d["interviews"] += 1
        if r["offer"]: d["offers"] += 1
    comp_rows = []
    for c, d in comp.items():
        rh = d.pop("resp_h")
        comp_rows.append({"company": c, **d,
            "response_rate_pct": round(100*d["responses"]/max(1,d["applied"]),1),
            "avg_response_h": round(statistics.mean(rh),1) if rh else None})
    comp_rows.sort(key=lambda x: (-x["response_rate_pct"], x["avg_response_h"] or 999))
    priority_boost = [c["company"] for c in comp_rows
                      if c["applied"] >= 3 and c["response_rate_pct"] >= 50]

    # ---------- ATS comparison ----------
    ats = defaultdict(lambda: {"discovered":0,"applications":0,"responses":0,"interviews":0,"failures":0})
    for j in jd["jobs"]:
        p = (j.get("portal") or "unknown").split(":")[0]
        ats[p]["discovered"] += 1
    for r in records:
        a = ats[r["ats_provider"]]
        a["applications"] += 1
        a["responses"] += 1 if r["recruiter_response"] else 0
        a["interviews"] += 1 if r["interview_outcome"] else 0
        a["failures"] += 1 if r["state"] == "failed" else 0
    for p, a in ats.items():
        a["conversion_pct"] = round(100*a["responses"]/max(1,a["applications"]),1) if a["applications"] else None

    engine = {
        "version": 1, "generated_at": now(),
        "min_n_for_significance": MIN_N_SIGNIFICANT,
        "applications": records,
        "experiments": {
            "resume_ab_by_role_bucket": ab_resume,
            "followup_timing": fu_status,
        },
        "ats_comparison": {p: a for p, a in sorted(ats.items(),
                            key=lambda kv: -(kv[1]["conversion_pct"] or 0))},
        "company_performance_ranked": comp_rows[:50],
        "recommended_discovery_priority_boost": priority_boost or
            ["none yet - needs >=3 applications and >=50% response rate per company"],
    }
    (BASE/"experiment_engine.json").write_text(json.dumps(engine, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---------- weekly experiment report ----------
    def winner_section(name, arms):
        lines = [f"### {name}"]
        ranked = sorted(arms.items(), key=lambda kv: -(kv[1].get("response_rate_pct") or -1))
        for v, a in ranked:
            sig = "SIGNIFICANT" if "significant" == str(a.get("significance")) else "directional"
            lines.append(f"- **{v}**: applied={a['applied']}, responses={a['responses']}, "
                         f"rate={a.get('response_rate_pct')}% ({sig})")
        if not ranked:
            lines.append("- insufficient data")
        top = ranked[0] if ranked and ranked[0][1]["applied"] >= MIN_N_SIGNIFICANT else None
        lines.append(f"- WINNER: {top[0] if top else 'TBD - insufficient n'}\n")
        return "\n".join(lines)

    md = [
        "# Weekly Experiment Report", "", f"Generated: {now()}", "",
        "## Resume A/B (within similar role buckets)",
        winner_section("Frontend/React bucket", ab_resume.get("frontend_react", {})),
        winner_section("FullStack/MERN bucket", ab_resume.get("fullstack_mern", {})),
        "## Follow-up Timing Experiment",
        "- Status: " + fu_status["note"], "",
        "## Best ATS for this profile",
    ]
    for p, a in sorted(ats.items(), key=lambda kv: -(kv[1]["conversion_pct"] or 0)):
        if a["applications"]:
            md.append(f"- **{p}**: apps={a['applications']}, responses={a['responses']}, "
                      f"conversion={a['conversion_pct']}%, failures={a['failures']}")
    md += ["", "## Company segments responding best"]
    md += [f"- **{c['company']}**: rate={c['response_rate_pct']}% "
           f"(n={c['applied']}), avg_response={c['avg_response_h']}h"
           for c in comp_rows[:8] if c["applied"]]
    md += ["", "## Interview conversion trend", "- Interviews so far: "
           f"{sum(1 for r in records if r['interview_outcome'])} — trend chart unlocks at n>=30.",
           "", "## Data-backed recommendations",
           "- Continue Tier-1 scan cadence; Ashby/Greenhouse endpoints yield most target roles.",
           "- Send Day-5 drafts manually when due to activate follow-up experiment.",
           "- Drop recruiter replies into emails_inbox/ so outcome learning engages."]
    (BASE/"weekly_experiment_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"records={len(records)}")
    print(json.dumps(ab_resume, indent=2)[:600])
    print("priority_boost:", priority_boost)


if __name__ == "__main__":
    sys.exit(main())
