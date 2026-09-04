#!/usr/bin/env python3
"""V6 Operations & Analytics - funnel, leaderboards, reliability, dashboards."""
import json, re, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent

def load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception: return d

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def results_map():
    out = {}
    for f in BASE.glob("results/*.jsonl"):
        for ln in f.read_text(encoding="utf-8-sig").splitlines():
            if not ln.strip(): continue
            try:
                r = json.loads(ln)
                jid = r.get("job_id")
                if r.get("result") == "submitted":
                    out[jid] = r.get("ts")
            except Exception:
                pass
    return out

def build_funnel(jd, ee):
    st = Counter(j.get("status") for j in jd["jobs"])
    evc = Counter(e.get("type") for e in ee.get("events", []))
    discovered = len(jd["jobs"])
    queued = st.get("pending", 0) + st.get("in_progress", 0)
    applied = st.get("submitted", 0)
    interview = evc.get("interview", 0)
    assessment = evc.get("assessment", 0)
    offer = evc.get("offer", 0)
    rejected = evc.get("rejection", 0) + st.get("failed", 0)
    ghosted = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=21)).date().isoformat()
    for j in jd["jobs"]:
        if j.get("status") == "submitted":
            ap = (j.get("updated_at") or "")[:10]
            co = (j.get("company") or "").lower()
            has_resp = any(e.get("company","").lower() == co for e in ee.get("events", []))
            if ap and ap < cutoff and not has_resp:
                ghosted += 1
    def rate(a, b): return round(100 * a / b, 1) if b else None
    funnel = {"discovered": discovered, "queued": queued, "applied": applied,
              "interview": interview, "assessment": assessment,
              "offer": offer, "rejected": rejected, "ghosted": ghosted}
    convs = {
        "queued_to_applied_pct": rate(applied, max(1, queued + applied)),
        "applied_to_interview_pct": rate(interview, applied),
        "applied_to_response_pct": rate(interview + assessment + rejected + offer, applied),
        "interview_to_offer_pct": rate(offer, interview) if interview else None,
    }
    return {"generated_at": now(), "funnel": funnel, "conversions": convs}

def ats_performance(jd, oc, ee):
    prov = defaultdict(lambda: {"discovered":0,"applications":0,"submissions":0,
                                 "failures":0,"interviews":0,"response_times_h":[]})
    varmap = {s["job_id"]: s.get("chosen") for s in load(
        BASE/"resume_intelligence.json", {}).get("selections", [])}
    ev_by_co = defaultdict(list)
    for e in ee.get("events", []):
        ev_by_co[(e.get("company") or "").lower()].append(e)
    for j in jd["jobs"]:
        p = (j.get("portal") or "unknown").split(":")[0]
        d = prov[p]; d["discovered"] += 1
        st = j.get("status")
        if st == "submitted":
            d["applications"] += 1; d["submissions"] += 1
            rv = varmap.get(j["id"])
        elif st == "failed":
            d["failures"] += 1
    for o in oc.get("outcomes", []):
        p = (o.get("ats_provider") or "unknown").split(":")[0]
        if o.get("response_time_hours") is not None:
            prov[p]["response_times_h"].append(o["response_time_hours"])
        if o.get("state") == "submitted" and o.get("resume_variant"):
            pass
    rows = []
    for p, d in prov.items():
        rts = d.pop("response_times_h")
        d["avg_response_time_h"] = round(statistics.mean(rts), 1) if rts else None
        d["submission_rate_pct"] = round(100*d["submissions"]/max(1,d["applications"] or d["discovered"]),1)
        rows.append({"provider": p, **d})
    rows.sort(key=lambda r: (-r.get("submissions",0), r["provider"]))
    return {"generated_at": now(), "providers": rows}

def leaderboard(intel, ee, oc):
    ev_by_co = defaultdict(list)
    for e in ee.get("events", []):
        ev_by_co[(e.get("company") or "").lower()].append(e)
    resp_h = []
    for o in oc.get("outcomes", []):
        if o.get("response_time_hours") is not None:
            resp_h.append((o["company"], o["response_time_hours"]))
    fast = {c.lower(): h for c, h in resp_h}
    rows = []
    for r in intel.get("companies", []):
        co = r["company"]; app = r.get("application", {})
        h = r.get("history", {})
        iv = len(ev_by_co.get(co.lower(), []))
        target_relevance = sum(h.get(k,0) for k in ("react","nextjs","fullstack","frontend"))
        score = (r.get("hiring_score",0)*0.4 +
                 min(25, target_relevance*8) +
                 min(20, app.get("submitted",0)*10) +
                 (15 if iv else 0) +
                 (10 if co.lower() in fast else 0))
        rows.append({"rank": r.get("rank"), "company": co,
                     "composite_score": round(score,1),
                     "hiring_score": r.get("hiring_score"),
                     "target_roles_seen": target_relevance,
                     "submitted": app.get("submitted",0),
                     "interview_events": iv,
                     "fastest_response_h": fast.get(co.lower()),
                     "india_hiring": r.get("india_hiring"),
                     "remote_hiring": r.get("remote_hiring")})
    rows.sort(key=lambda x: -x["composite_score"])
    for i, r in enumerate(rows): r["leaderboard_rank"] = i+1
    return {"generated_at": now(), "leaderboard": rows[:150]}

def resume_analytics(oc):
    by_var = defaultdict(lambda: {"applied":0,"submitted":0})
    for o in oc.get("outcomes", []):
        v = o.get("resume_variant","REACT(default)")
        by_var[v]["applied"] += 1
        if o.get("state") == "submitted": by_var[v]["submitted"] += 1
    rows = []
    for v, d in by_var.items():
        conv = round(100*d["submitted"]/d["applied"],1) if d["applied"] else None
        rows.append({"variant": v, **d, "conversion_pct": conv})
    recs = []
    best = max([r for r in rows if r["conversion_pct"] is not None],
               key=lambda r: (r["conversion_pct"], r["applied"]), default=None)
    sample_note = "sample too small for statistically-backed changes"
    if best and best["applied"] >= 30:
        recs.append(f"{best['variant']} converts best ({best['conversion_pct']}%) - prefer for matching roles.")
    else:
        recs.append(f"Keep collecting outcomes - {sample_note}.")
    recs.append("Resume CONTENT is never auto-modified; recommendations only.")
    return {"generated_at": now(), "variants": rows, "recommendations": recs}

def reliability():
    ev = load(BASE/"events.jsonl", [])
    cnt = Counter()
    recent_stale = 0
    cut = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    for e in ev:
        k = e.get("event"); cnt[k] += 1
        if k == "restart" and str(e.get("ts","")) >= cut:
            recent_stale += 1
    corrupt = sum(1 for e in ev if e.get("event")=="state_corrupt")
    alerts = []
    if corrupt > 40: alerts.append(f"high progress-file corruption events ({corrupt}) - cosmetic but noisy")
    if recent_stale > 3: alerts.append(f"{recent_stale} restarts in last 2h - check model latency")
    fails = [j for j in load(BASE/"jobs.json",{}).get("jobs",[]) if j.get("status")=="failed"
             and "worker" not in str(j.get("error",""))]
    if fails: alerts.append(f"{len(fails)} genuine (non-infra) failures need review")
    return {"generated_at": now(),
            "counters": {"total_restarts": cnt.get("restart",0),
                          "crash_recoveries": cnt.get("crash_recovery",0),
                          "dedupe_events": cnt.get("dedupe",0),
                          "state_corrupt_events": corrupt,
                          "recent_2h_restarts": recent_stale},
            "alerts": alerts or ["none - all clear"]}

def performance(cp, jd):
    hist = [h for h in cp.get("history", []) if h.get("took_sec")]
    apps = []
    for h in hist:
        n = sum(h.get("tally", {}).values()) or 1
        apps.append(h["took_sec"]/n)
    subs = [j for j in jd["jobs"] if j.get("status") == "submitted"]
    days = max(1, len({str(j.get("updated_at"))[:10] for j in subs})) if subs else 1
    meas = {
        "batches_completed": len(hist),
        "avg_batch_time_s": round(statistics.mean([h["took_sec"] for h in hist]),1) if hist else None,
        "avg_application_time_s": round(statistics.mean(apps),1) if apps else None,
        "queue_throughput_submissions_per_day": round(len(subs)/days,2),
        "pause_between_batches_s": json.loads((BASE/"config.json").read_text(encoding="utf-8-sig")).get("pause_between_batches_sec"),
    }
    recs = []
    if meas["avg_application_time_s"] and meas["avg_application_time_s"] > 240:
        recs.append("Application time high: consider trimming reading pauses for easy-apply portals.")
    if hist:
        slowest = max(hist, key=lambda h: h["took_sec"])
        if slowest["took_sec"] > 900:
            recs.append(f"Slowest batch {slowest.get('batch_id')} took {slowest['took_sec']}s - review logs/{slowest.get('batch_id')}.out.log")
    recs.append("Throughput scales with verified endpoints - keep expanding universe.")
    return {"generated_at": now(), "measurements": meas,
            "optimization_recommendations": recs}


def write_dashboard(dash_data):
    html = """<!doctype html><html><head><meta charset="utf-8"><title>JobOps Executive</title>
<style>body{font-family:Segoe UI,Arial;background:#0f1420;color:#e6ebf5;margin:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.card{background:#182034;border-radius:10px;padding:16px}
.k{color:#8ea0bf;font-size:12px;text-transform:uppercase}.v{font-size:28px;font-weight:600}
.ok{color:#4ade80}.warn{color:#fbbf24}table{border-collapse:collapse;width:100%}
td,th{padding:6px 10px;border-bottom:1px solid #233047;text-align:left;font-size:14px}
h2{margin-top:26px}</style></head><body>
<h1>JobOps Executive Dashboard</h1><div id="gen" class="k"></div><div class="grid" id="g"></div>
<h2>Queue</h2><table id="q"></table><div class="k">Auto-refresh 30s &middot; serve folder via
<code>python -m http.server</code> for live fetch</div>
<script>
const F=j=>fetch(j,{cache:"no-store"}).then(r=>r.json());
async function tick(){
 try{
  const d=await F("dashboard.json"), l=await F("status_live.json");
  document.getElementById("gen").textContent="updated "+new Date().toLocaleTimeString();
  const items=[["Applications today",d.applications_today],["This week",d.applications_this_week],
   ["Interview kits",d.interview_kits_ready],["Assessments",d.assessments_pending],
   ["Offers",d.offers],["Response rate %",d.response_rate_pct],
   ["Watchdog",(l.watchdog||{}).state],["Heartbeat age",(l.watchdog||{}).heartbeat_age_sec+"s"],
   ["Restarts",(l.watchdog||{}).restart_count],["Current batch",(l.current_batch||"-")]];
  document.getElementById("g").innerHTML=items.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v?? "-"}</div></div>`).join("");
  const q=d.queue||{};document.getElementById("q").innerHTML=
   "<tr><th>pending</th><th>in_progress</th><th>submitted</th><th>review</th><th>failed</th></tr>"+
   `<tr><td>${q.pending||0}</td><td>${q.in_progress||0}</td><td class="ok">${q.submitted||0}</td><td>${q.review_required||0}</td><td>${(q.failed||0)+(q.failed_unconfirmed||0)}</td></tr>`;
 }catch(e){document.getElementById("gen").textContent="serve via http server for live updates";}
}
tick();setInterval(tick,30000);
</script></body></html>"""
    (BASE/"dashboard.html").write_text(html, encoding="utf-8")

def write_followup_dash(items):
    today = datetime.now(timezone.utc).date().isoformat()
    groups = {"overdue":[], "due_today":[], "upcoming":[]}
    for i in items:
        if i["due_date"] < today: groups["overdue"].append(i)
        elif i["due_date"] == today: groups["due_today"].append(i)
        else: groups["upcoming"].append(i)
    html = """<!doctype html><html><head><meta charset="utf-8"><title>Follow-ups</title>
<style>body{font-family:Segoe UI,Arial;background:#12101a;color:#eee;margin:24px}
h3{margin-top:26px}.card{background:#1d1930;padding:12px;border-left:4px solid #7c5cff;margin:10px 0;border-radius:8px}
.k{color:#9a8fd0;font-size:12px}</style></head><body><h1>Follow-up Dashboard (drafts only)</h1>"""
    for g in ("overdue","due_today","upcoming"):
        html += f"<h3>{g.upper()} ({len(groups[g])})</h3>"
        for i in groups[g]:
            html += f"""<div class="card"><b>{i['company']}</b> — day {i['day']} · due {i['due_date']}
<div class="k">{i['status']}</div><p>{i['draft']}</p></div>"""
    html += "</body></html>"
    (BASE/"followup_dashboard.html").write_text(html, encoding="utf-8")

def write_weekly_html(weekly_md_data, perf, resume_rows):
    rows = "".join(f"<tr><td>{p}</td><td>{d.get('verified_endpoints',0)}</td>"
                   f"<td>{d.get('jobs_in_queue',0)}</td><td>{d.get('submissions',0)}</td></tr>"
                   for p, d in perf.get("providers", {}).items())
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Weekly Exec</title>
<style>body{{font-family:Segoe UI,Arial;background:#101a14;color:#e8f5ec;margin:24px}}
td,th{{padding:6px 10px;border-bottom:1px solid #1e3328;text-align:left}}</style></head><body>
<h1>Weekly Executive Report</h1><p class=k>{now()}</p>
<ul><li>Applications this week: <b>{weekly_md_data['applications_this_week']}</b></li>
<li>Interview events: see email_events.json</li>
<li>Best-performing providers table below</li></ul>
<table><tr><th>Provider</th><th>Endpoints</th><th>In queue</th><th>Submissions</th></tr>{rows}</table>
<p>Recommendations: prioritize Tier-1 scans; complete manual LinkedIn batch;
resume content changes only after n>=30 per variant.</p></body></html>"""
    (BASE/"weekly_report.html").write_text(html, encoding="utf-8")


def main():
    jd = load(BASE/"jobs.json", {"jobs": []})
    ee = load(BASE/"email_events.json", {"events": []})
    oc = load(BASE/"outcomes.json", {"outcomes": []})
    intel = load(BASE/"hiring_intelligence.json", {"companies": []})
    cp = load(BASE/"checkpoint.json", {})

    analytics = build_funnel(jd, ee)
    ats = ats_performance(jd, oc, ee)
    analytics["ats_performance"] = ats
    lb = leaderboard(intel, ee, oc)
    ra = resume_analytics(oc)
    rel = reliability()
    perf = performance(cp, jd)

    (BASE/"analytics.json").write_text(json.dumps(analytics, indent=2), encoding="utf-8")
    (BASE/"company_leaderboard.json").write_text(json.dumps(lb, indent=2, ensure_ascii=False), encoding="utf-8")
    (BASE/"resume_analytics.json").write_text(json.dumps(ra, indent=2), encoding="utf-8")
    (BASE/"reliability.json").write_text(json.dumps(rel, indent=2), encoding="utf-8")
    (BASE/"performance_optimization.json").write_text(json.dumps(perf, indent=2), encoding="utf-8")
    provider_performance_report_update(perf, ats)

    fu = load(BASE/"followups.json", {"items": []})
    write_followup_dash(fu.get("items", []))
    write_dashboard(dict(load(BASE/"dashboard.json", {})))
    write_weekly_html({"applications_this_week": sum(
        1 for j in jd["jobs"] if j.get("status") == "submitted" and
        (j.get("updated_at") or "") >= (datetime.now(timezone.utc)-timedelta(days=7)).isoformat(timespec="seconds"))},
        load(BASE/"provider_performance_report.json", {}), None)

    print("funnel:", json.dumps(analytics["funnel"]))
    print("conversions:", json.dumps(analytics["conversions"]))
    print("alerts:", rel["alerts"])
    print("outputs: analytics.json company_leaderboard.json resume_analytics.json "
          "reliability.json performance_optimization.json dashboard.html "
          "followup_dashboard.html weekly_report.html")

def provider_performance_report_update(perf, ats):
    pp = BASE/"provider_performance_report.json"
    data = load(pp, {"providers": {}})
    for row in ats.get("providers", []):
        p = row["provider"]
        node = data["providers"].setdefault(p, {})
        node.update({k: v for k, v in row.items() if k != "provider"})
    data["generated_at"] = now()
    data["ranking_by_applications"] = [r["provider"] for r in ats.get("providers", [])]
    pp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    sys.exit(main())
