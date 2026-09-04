#!/usr/bin/env python3
"""Hiring Intelligence Engine - learns from every run, ranks companies, schedules scans."""
import json, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent

def load(p, default):
    try: return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception: return default

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

ROLE_CAT = [
    ("react", r"\breact\b"), ("nextjs", r"next\.?js"), ("fullstack", r"full.?stack"),
    ("mern", r"\bmern\b"), ("frontend", r"frontend|front-end|front end|web developer|ui engineer"),
    ("javascript", r"javascript"), ("swe", r"software engineer|\bsde\b"),
]

def classify(title):
    cats = [c for c, pat in ROLE_CAT if re.search(pat, title or "", re.I)]
    return cats or ["other"]

def main():
    jd = load(BASE/"jobs.json", {"jobs": []})
    u = load(BASE/"company_universe.json", {"companies": []})
    uni_by_name = {c["name"].lower(): c for c in u["companies"]}

    # ---- aggregate per-company signal from queue history
    agg = defaultdict(lambda: {
        "roles": Counter(), "total_postings": 0, "fresh_7d": 0,
        "india": 0, "remote": 0, "submitted": 0, "attempts": 0,
        "ages": [], "last_discovery": None, "term_states": Counter(),
    })
    for j in jd["jobs"]:
        co = j.get("company", "?")
        a = agg[co]
        a["total_postings"] += 1
        for c in classify(j.get("title")):
            a["roles"][c] += 1
        loc = (j.get("location") or "").lower()
        if "india" in loc or any(x in loc for x in ("bangalore","bengaluru","mumbai","pune","hyderabad","delhi","noida","gurugram","chennai")):
            a["india"] += 1
        if "remote" in loc: a["remote"] += 1
        age = (j.get("evidence") or {}).get("posted_age_days")
        if age is not None:
            a["ages"].append(age)
            if age <= 7: a["fresh_7d"] += 1
        ts = j.get("updated_at")
        if ts and (a["last_discovery"] is None or ts > a["last_discovery"]):
            a["last_discovery"] = ts
        st = j.get("status")
        a["term_states"][st] += 1
        if st == "submitted": a["submitted"] += 1
        a["attempts"] += max(j.get("attempts", 0), 1 if st in ("submitted","failed","failed_unconfirmed") else 0)

    # ---- outcomes from results files
    res_out = Counter()
    for f in BASE.glob("results/*.jsonl"):
        try:
            for ln in f.read_text(encoding="utf-8-sig").splitlines():
                if ln.strip():
                    res_out[json.loads(ln).get("result")] += 1
        except Exception:
            pass

    # ---- build intelligence records
    intel = []
    name_to_job_status = defaultdict(Counter)
    for j in jd["jobs"]:
        name_to_job_status[j.get("company","?")][j.get("status")] += 1

    for c in u["companies"]:
        name = c["name"]
        a = agg.get(name)
        rec = {
            "company": name,
            "ats": c.get("ats"),
            "endpoint_verified": c.get("status") == "verified",
            "is_hiring": bool(c.get("is_hiring")),
            "india_hiring": bool(c.get("india_hiring")),
            "remote_hiring": bool(c.get("remote_hiring")),
            "browser_extraction_success": bool(c.get("browser_discovered")),
            "last_scan": c.get("last_scan"),
            "last_successful_discovery": (a or {}).get("last_discovery"),
            "avg_posting_age_days": (round(sum(a["ages"])/len(a["ages"]),1)
                                      if a and a["ages"] else None),
            "history": {
                "react": (a["roles"]["react"] if a else 0),
                "nextjs": (a["roles"]["nextjs"] if a else 0),
                "fullstack": (a["roles"]["fullstack"] if a else 0),
                "frontend": (a["roles"]["frontend"] if a else 0),
                "mern": (a["roles"]["mern"] if a else 0),
                "javascript": (a["roles"]["javascript"] if a else 0),
                "total_postings_seen": (a["total_postings"] if a else 0),
                "fresh_7d": (a["fresh_7d"] if a else 0),
            },
            "application": {
                "discovered": (a["total_postings"] if a else 0),
                "submitted": name_to_job_status[name].get("submitted", 0),
                "failed": name_to_job_status[name].get("failed", 0),
                "review_required": name_to_job_status[name].get("review_required", 0),
                "pending": name_to_job_status[name].get("pending", 0),
            },
        }
        # ---- scoring 0..100
        s = 0.0
        hist = rec["history"]
        target_roles = hist["react"] + hist["nextjs"] + hist["fullstack"] + hist["frontend"] + hist["mern"]
        s += min(30, target_roles * 6)                      # role fit volume
        if a and a["total_postings"]:
            s += 15 * (a["fresh_7d"] / a["total_postings"]) # freshness ratio
        if rec["is_hiring"]: s += 8
        if rec["india_hiring"]: s += 7
        if rec["remote_hiring"]: s += 4
        if rec["endpoint_verified"]: s += 12                 # reliable public ATS API
        if rec["browser_extraction_success"]: s += 6
        app = rec["application"]
        denom = app["submitted"] + app["failed"] + app["review_required"]
        if denom: s += 15 * (app["submitted"] / denom)       # conversion
        if hist["fresh_7d"] >= 2: s += 3
        rec["hiring_score"] = round(min(100.0, s), 1)
        rec["tier"] = None
        intel.append(rec)

    intel.sort(key=lambda r: -r["hiring_score"])
    for i, r in enumerate(intel):
        r["rank"] = i + 1
        r["tier"] = 1 if i < 100 else (2 if i < 300 else 3)

    out = {"version": 1, "generated_at": now(),
           "universe_size": len(intel),
           "score_formula": "role_fit(<=30)+freshness(<=15)+hiring(8)+india(7)+remote(4)"
                            "+verified_api(12)+browser(6)+conversion(<=15)+multi_fresh(3)",
           "companies": intel}
    (BASE/"hiring_intelligence.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- tier schedule
    t1 = [r["company"] for r in intel if r["tier"] == 1]
    t2 = [r["company"] for r in intel if r["tier"] == 2]
    t3 = [r["company"] for r in intel if r["tier"] == 3]
    t = now()
    def due(hours): return (datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(timespec="seconds")
    sched = {
        "generated_at": t,
        "tiers": [
            {"tier": 1, "policy": "scan every 6 hours", "count": len(t1),
             "next_scan_due": due(6), "companies": t1},
            {"tier": 2, "policy": "scan daily", "count": len(t2),
             "next_scan_due": due(24), "companies_sample": t2[:50]},
            {"tier": 3, "policy": "scan every 3 days", "count": len(t3),
             "next_scan_due": due(72)},
        ],
    }
    (BASE/"tier_schedule.json").write_text(json.dumps(sched, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- rankings file (compact)
    rank = [{"rank": r["rank"], "company": r["company"], "score": r["hiring_score"],
             "tier": r["tier"], "target_roles_seen": target_roles if False else
             sum(r["history"][k] for k in ("react","nextjs","fullstack","frontend","mern")),
             "submitted": r["application"]["submitted"], "ats": r["ats"]}
            for r in intel]
    (BASE/"company_rankings.json").write_text(json.dumps(rank, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- provider performance
    prov = defaultdict(lambda: {"verified_endpoints": 0, "companies": 0,
                                 "jobs_in_queue": 0, "submissions": 0})
    for c in u["companies"]:
        p = c.get("ats") or "unknown"
        prov[p]["companies"] += 1
        if c.get("status") == "verified": prov[p]["verified_endpoints"] += 1
    for j in jd["jobs"]:
        p = (j.get("portal") or "unknown").split(":")[0]
        prov[p]["jobs_in_queue"] += 1
        if j.get("status") == "submitted": prov[p]["submissions"] += 1
    perf = {"generated_at": now(), "providers": {k: v for k, v in sorted(prov.items())}}
    (BASE/"provider_performance_report.json").write_text(json.dumps(perf, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- weekly discovery report
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    wk_jobs = [j for j in jd["jobs"] if (j.get("evidence") or {}).get("source")
               or (j.get("updated_at") or "") >= cutoff]
    weekly = {
        "generated_at": now(), "window": "7 days",
        "jobs_discovered_total": len(jd["jobs"]),
        "by_source": dict(Counter((j.get("evidence") or {}).get("source", "seed/manual").split(":")[0]
                                   for j in jd["jobs"])),
        "by_portal": dict(Counter(j.get("portal","?") for j in jd["jobs"])),
        "top_companies": dict(Counter(j.get("company","?") for j in jd["jobs"]).most_common(20)),
        "submitted_this_week": sum(1 for j in jd["jobs"] if j.get("status")=="submitted"
                                    and (j.get("updated_at") or "") >= cutoff),
        "outcome_counts_from_results": dict(res_out),
    }
    (BASE/"weekly_discovery_report.json").write_text(json.dumps(weekly, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"intelligence: {len(intel)} companies scored")
    print(f"T1={len(t1)} T2={len(t2)} T3={len(t3)}")
    print("Top 12:")
    for r in intel[:12]:
        print(f"  #{r['rank']:>3} {r['hiring_score']:>5} {r['company'][:28]:<28} "
              f"tier{r['tier']} roles={sum(r['history'][k] for k in ('react','nextjs','fullstack','frontend','mern'))}")

if __name__ == "__main__":
    main()
