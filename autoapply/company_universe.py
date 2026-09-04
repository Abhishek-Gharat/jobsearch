#!/usr/bin/env python3
"""Company Universe manager - persistent company DB + ATS detection + job discovery.

Commands:
  seed              create/incrementally extend company_universe.json with real companies
  verify [--limit N]  probe unknown companies against public ATS endpoints, assign provider
  discover          scan verified endpoints for fresh (<=7d when dated) matching jobs -> jobs.json
  stats             print universe statistics
"""
from __future__ import annotations
import argparse, json, re, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
UNIVERSE = BASE / "company_universe.json"
JOBS = BASE / "jobs.json"
UA = {"User-Agent": "Mozilla/5.0 (job-universe-manager)"}

TARGET_ROLES = re.compile(r"frontend|front-end|front end|react|javascript|web developer|full.?stack|mern|next\.?js|ui engineer|software engineer|\bsde\b|\bsde\s*i\b|developer", re.I)
BLOCK = re.compile(r"senior|\bsr\b|sr\.|staff|principal|\blead\b|architect|manager|head of|director|\bii\b|\biii\b|[3-9]\+?\s*(year|yr)", re.I)
LOC_OK = re.compile(r"india|remote|bengaluru|bangalore|mumbai|pune|hyderabad|delhi|noida|gurugram|gurgaon|chennai|punjab|mohali|chandigarh|ahmedabad|kolkata|indore|jaipur", re.I)
LOC_BAD = re.compile(r"united states|usa\b|new york|san francisco|seattle|austin|boston|chicago|london|uk\b|england|germany|france|netherlands|poland|spain|italy|sweden|canada|toronto|australia|singapore|japan|vietnam|thailand|philippines|poland|egypt|nigeria|kenya|south africa|brazil|mexico|argentina|colombia|dubai|uae\b|saudi|qatar|kuwait", re.I)

SEED_COMPANIES = [
    # Indian consumer/fintech/product
    "Zepto","Blinkit","Swiggy","Zomato","Dunzo","BigBasket","Otipy","CRED","Razorpay","PhonePe",
    "Paytm","BharatPe","Navi","Fi Money","Jupiter Money","Niyo","KreditBee","Lendingkart","MoneyView",
    "Upstox","Zerodha","Groww","Dhan","Angel One","smallcase","Wint Wealth","GoldenPi","Jiraaf",
    "Meesho","Nykaa","Purplle","Lenskart","FirstCry","Honasa Consumer","boAt Lifestyle","Noise",
    "Dream11","Mobile Premier League","Zupee","Games24x7","WinZO","Physics Wallah","Unacademy",
    "Vedantu","Scaler","upGrad","Eruditus","Great Learning","LEAD School","Classplus","Teachmint",
    "Toddle","Postman","Hasura","Chargebee","Freshworks","Facilio","Zenoti","Whatfix","MoEngage",
    "CleverTap","Netcore Cloud","WebEngage","Mindtickle","SpotDraft","Leegality","Sirion Labs",
    "Icertis","Gupshup","Haptik","Yellow.ai","Avaamo","Uniphore","Observe.AI","Atlan","Amagi",
    "Zeta","Juspay","Setu","M2P Fintech","Decentro","Signzy","Perfios","Skyflow","Zamp","Arthan",
    "HyperVerge","DevRev","BrowserStack","LambdaTest","Testsigma","Kissflow","Rocketlane","Zluri",
    "Spendflo","Keka","greytHR","Darwinbox","HROne","Plum Benefits","Onsurity","Loop Health",
    "Pristyn Care","PharmEasy","Tata 1mg","Apollo 247","Curefit","Leverage Edu","CarDekho","Spinny",
    "Cars24","Droom","Rapido","Porter","Sarvam AI","Krutrim","Gnani AI","Mad Street Den","Fractal",
    "Tiger Analytics","LatentView Analytics","Quantiphi","TheMathCompany","Tredence","Jio Platforms",
    "Airtel Digital","Tata Digital","Flipkart","Myntra","Walmart Global Tech India","Target India",
    "Tesco Bengaluru","Lowe's India","Optum India","Nagarro","SIXT R&D India","NielsenIQ","Sutherland",
    # Global with India engineering hubs
    "Microsoft","Google","Amazon","Adobe","Salesforce","ServiceNow","Nutanix","NetApp","Cisco",
    "Intel","AMD","NVIDIA","Qualcomm","Samsung R&D Institute India","SAP Labs","Oracle","IBM",
    "Goldman Sachs","JPMorgan Chase","Morgan Stanley","Wells Fargo","Bank of America","Mastercard",
    "Visa","American Express","PayPal","Uber","Atlassian","Grafana Labs","Elastic","Confluent",
    "Databricks","Snowflake","Cloudflare","Twilio","Shopify","Deel","Remote.com","Multiplier",
    "GitLab","Automattic","Canonical","Hotjar","Doist","Crunchyroll","Braze","Abnormal AI",
    # Already-known compliant boards (verified earlier)
    "Rajyug IT Solutions","Pentoz Technology","iCore Solutions","IndiaRush","Nichetech","KoiReader",
    "Endeavor IT Solutions","CTM360","EVERSANA","OGD Solutions","Two-Up Agency","Markeeters",
    "Smart Working Solutions","Drivetrain","Netomi","Wing Assistant","Plane Software","Bjak",
    "Collinear AI","Fermi AI","Bespoke Labs","Tolken","SigNoz","Tempo","Lingaro Group","Brevo",
]

PROBE_ORDER = [
    ("lever", lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json"),
    ("greenhouse", lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs?limit=1"),
    ("ashby", lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(s)}"),
    ("smartrecruiters", lambda s: f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1"),
    ("recruitee", lambda s: f"https://{s}.recruitee.com/api/offers/"),
]

def slugify(name): return re.sub(r"[^a-z0-9]", "", name.lower())
def slug_dash(name): return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

def get_json(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def load_universe():
    if UNIVERSE.exists():
        return json.loads(UNIVERSE.read_text(encoding="utf-8-sig"))
    return {"version": 1, "updated_at": None, "target_size": 1000,
            "providers_supported": ["greenhouse","lever","ashby","smartrecruiters","workable",
                                     "bamboohr","recruitee","teamtailor","personio","jobvite",
                                     "comeet","rippling","pinpoint","workday"],
            "companies": []}


def save_universe(u):
    u["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    UNIVERSE.write_text(json.dumps(u, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_seed():
    u = load_universe()
    have = {c["name"].lower() for c in u["companies"]}
    added = 0
    for name in SEED_COMPANIES:
        if name.lower() in have:
            continue
        u["companies"].append({
            "name": name, "career_page": "", "ats": "unknown",
            "endpoint": None, "slug_guesses": [slugify(name), slug_dash(name)],
            "country": None, "india_hiring": None, "remote_hiring": None,
            "last_scan": None, "last_job_count": None, "status": "unverified",
        })
        added += 1
    save_universe(u)
    print(f"seeded {added} new companies; universe={len(u['companies'])}/{u['target_size']}")


def _probe(c):
    """Try each provider for this company. Return updated dict."""
    guesses = c.get("slug_guesses") or [slugify(c["name"])]
    for prov, maker in PROBE_ORDER:
        for g in guesses:
            if not g:
                continue
            url = maker(g)
            data = get_json(url)
            if data is None:
                continue
            count = None
            if isinstance(data, dict):
                if "jobs" in data: count = len(data["jobs"])
                elif "content" in data: count = len(data["content"])
                elif isinstance(data.get("data"), list): count = len(data["data"])
                elif "offers" in data: count = len(data["offers"])
            elif isinstance(data, list):
                count = len(data)
            if count is None or count <= 0:
                # SR API returns 200+empty for any slug - only real postings count
                continue
            c.update({"ats": prov,
                      "endpoint": url.split("?")[0],
                      "last_scan": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "last_job_count": count,
                      "status": "verified"})
            c["slug_used"] = g
            return c
    c.update({"status": "no_public_endpoint",
              "last_scan": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return c


def cmd_verify(limit, flt=None):
    u = load_universe()
    todo = [c for c in u["companies"] if c.get("status") == "unverified"]
    if flt == "hiring_india":
        prio = [c for c in todo if c.get("is_hiring") and
                (c.get("india_hiring") or c.get("remote_hiring"))]
        rest = [c for c in todo if c not in prio]
        todo = prio + rest[:max(0, (limit or 0) - len(prio))] if limit else prio + rest
    if limit:
        todo = todo[:limit]
    if not todo:
        print("nothing to verify"); return
    done = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(_probe, c): c for c in todo}
        for fut in as_completed(futs):
            c = fut.result()
            done += 1
            if done % 25 == 0:
                print(f"  verified {done}/{len(todo)}...")
    # write back by name
    upd = {c["name"]: c for c in todo}
    for i, c in enumerate(u["companies"]):
        if c["name"] in upd:
            u["companies"][i] = upd[c["name"]]
    save_universe(u)
    from collections import Counter
    st = Counter(c.get("status") for c in u["companies"])
    print(f"verified {done}; universe={len(u['companies'])}; status={dict(st)}")
    ats = Counter(c.get("ats") for c in u["companies"] if c.get("status") == "verified")
    print(f"ats_breakdown={dict(ats)}")


def _fetch_jobs(c):
    prov, url = c.get("ats"), c.get("endpoint") or ""
    if not prov or not url:
        return []
    try:
        data = get_json(url, timeout=18)
    except Exception:
        return []
    out = []
    def add(title, loc, jurl, age):
        out.append({"title": title or "", "location": loc or "", "url": jurl or "", "age_days": age})
    if prov == "lever" and isinstance(data, list):
        for jb in data:
            cat = jb.get("categories") or {}
            age = None
            if jb.get("createdAt"):
                age = (time.time() * 1000 - jb["createdAt"]) / 86400000
            add(jb.get("text"), cat.get("location"), f"https://jobs.lever.co/{c.get('slug_used')}/{jb.get('id')}", age)
    elif prov == "greenhouse" and isinstance(data, dict):
        for jb in data.get("jobs", []):
            age = None
            ts = jb.get("first_published") or jb.get("updated_at")
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).total_seconds() / 86400000
            except Exception:
                pass
            add(jb.get("title"), (jb.get("location") or {}).get("name"),
                f"https://boards.greenhouse.io/{c.get('slug_used')}/jobs/{jb.get('id')}", age)
    elif prov == "ashby" and isinstance(data, dict):
        for jb in data.get("jobs", []):
            age = None
            add(jb.get("title"), jb.get("location"), jb.get("jobUrl"), age)
    elif prov == "smartrecruiters" and isinstance(data, dict):
        for jb in data.get("content", []):
            l = jb.get("location") or {}
            loc = ", ".join(filter(None, [l.get("city"), l.get("region"), l.get("country")]))
            age = None
            rel = jb.get("releasedDate")
            try:
                dt = datetime.fromisoformat(str(rel).split(".")[0])
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).total_seconds() / 86400000
            except Exception:
                pass
            add(jb.get("name"), loc, f"https://jobs.smartrecruiters.com/{c['name']}/{jb.get('id')}", age)
    return out


def cmd_discover(days=7):
    u = load_universe()
    jd = json.loads(JOBS.read_text(encoding="utf-8-sig"))
    have_urls = {j.get("url", "").split("?")[0].rstrip("/").lower() for j in jd["jobs"]}
    have_ct = {(j.get("company", "").lower(), j.get("title", "").lower()) for j in jd["jobs"]}
    next_n = sum(1 for j in jd["jobs"] if str(j.get("id", "")).startswith("A")) + 1
    verified = [c for c in u["companies"] if c.get("status") == "verified"]
    scanned = added = 0
    log = []

    def worker(c):
        jobs = _fetch_jobs(c)
        res = []
        for jb in jobs:
            t, loc, url, age = jb["title"], jb["location"], jb["url"], jb["age_days"]
            scanned_local = 1
            if not TARGET_ROLES.search(t) or BLOCK.search(t):
                continue
            lclean = (loc or "")
            if LOC_BAD.search(lclean) or not LOC_OK.search(lclean):
                continue
            if age is not None and age > days:
                continue
            key = url.split("?")[0].rstrip("/").lower()
            ck = (c["name"].lower(), t.strip().lower())
            with lock:
                if key in have_urls or ck in have_ct:
                    continue
                have_urls.add(key); have_ct.add(ck)
                nonlocal_dummy = 1
            res.append((c, jb, url, key, age))
        return res

    import threading
    lock = threading.Lock()
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(worker, c) for c in verified]
        for f in as_completed(futs):
            scanned += 1
            results.extend(f.result())

    for c, jb, url, key, age in results:
        entry = {
            "id": f"A{next_n:03d}", "company": c["name"], "title": jb["title"].strip(),
            "url": url, "portal": c.get("ats"), "location": jb["location"],
            "match_score": 80, "status": "pending", "attempts": 0, "error": None,
            "evidence": {"source": f"universe:{c.get('ats')}:{c.get('slug_used')}",
                          "posted_age_days": round(age, 1) if age is not None else None},
            "updated_at": None,
        }
        jd["jobs"].append(entry)
        next_n += 1
        added += 1
        log.append(f"A{next_n-1:03d} {c['name'][:20]} | {jb['title'][:42]} | {jb['location'][:30]} | age={entry['evidence']['posted_age_days']}")
        if len(log) <= 40:
            print(" ", log[-1])

    JOBS.write_text(json.dumps(jd, indent=2, ensure_ascii=False), encoding="utf-8")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for c in verified:
        c["last_scan"] = now_iso
    save_universe(u)
    print(f"\ncompanies_scanned={scanned} NEW_JOBS={added}")


def cmd_stats():
    u = load_universe()
    from collections import Counter
    cs = u["companies"]
    print(json.dumps({
        "universe_size": len(cs),
        "target": u.get("target_size"),
        "verified_endpoints": sum(1 for c in cs if c.get("status") == "verified"),
        "no_public_endpoint": sum(1 for c in cs if c.get("status") == "no_public_endpoint"),
        "pending_verification": sum(1 for c in cs if c.get("status") == "unverified"),
        "ats_breakdown": dict(Counter(c.get("ats") for c in cs if c.get("ats") != "unknown")),
    }, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["seed", "verify", "discover", "stats"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--filter", default=None, help="hiring_india = prioritize actively-hiring India/remote companies")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.command == "seed": cmd_seed()
    elif a.command == "verify": cmd_verify(a.limit, a.filter)
    elif a.command == "discover": cmd_discover(a.days)
    elif a.command == "stats": cmd_stats()
