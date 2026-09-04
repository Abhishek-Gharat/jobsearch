#!/usr/bin/env python3
"""Harvest fresh React/frontend jobs from public ATS board APIs into jobs.json."""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
JOBS = BASE / "jobs.json"

TARGET_ROLES = re.compile(
    r"frontend|front-end|front end|react|javascript|web developer|full.?stack|mern|next\.?js|ui engineer|software engineer|sde|developer",
    re.I,
)
BLOCK_SENIOR = re.compile(
    r"senior|\bsr\b|sr\.|staff|principal|\blead\b|architect|manager|head of|director|engineering manager|ii\b|iii\b|[3-9]\+?\s*year",
    re.I,
)
LOC_OK = re.compile(r"india|remote|bengaluru|bangalore|mumbai|pune|hyderabad|delhi|noida|gurgaon|gurugram|chennai|punjab|mohali", re.I)
LOC_BAD = re.compile(r"united states|usa\b|new york|san francisco|london|uk\b|europe|canada|australia|singapore|vietnam|poland|egypt|nigeria", re.I)

GREENHOUSE = [
    "chargebee", "freshworks", "browserstack", "wingify", "moengage", "clevertap",
    "whatfix", "meesho", "zepto", "groww", "zetwerk", "urbancompany", "razorpay",
    "swiggy", "dunzo", "postman", "postmanlabs", "smallcase", "sliceit", "jarapp",
    "cred", "gojek", "mindtickle", "sprinklr", "crunchyroll", "atlan", "dhiwise",
    "netomi", "drivetrain", "abnormalsecurity", "braze",
]
LEVER = [
    "cred", "dunzo", "zepto", "smallcase", "hasura", "spotdraft", "zetwerk",
    "jarapp", "groww", "postman", "slice", "toplyne", "skyflow", "netomi",
    "drivetrain", "getwingapp", "smart-working-solutions", "brevo", "lingarogroup",
]
ASHBY = [
    "bespokelabs", "collinear-ai", "plane", "bjakcareer", "skyflow", "zamp",
    "arthan", "fermi ai", "tolken", "signoz", "tempo",
]
SMARTRECRUITERS = [
    "SIXT", "Nagarro1", "NielsenIQ", "Sutherland", "RajyugITSolutionsPvtLtd",
    "PentozTechnology", "ICore3", "IndiaRush", "Nichetech1",
    "KoiReaderTechnologies", "EndeavorItSolutionsPvtLtd1", "CTM360", "EVERSANA1",
    "OGDSolutions1", "Jupitorconsulting", "Markeeterscom",
]

def get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 job-harvester"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None

def clean_loc(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def loc_ok(loc: str) -> bool:
    l = clean_loc(loc)
    if LOC_BAD.search(l):
        return False
    return bool(LOC_OK.search(l))

def jd_allows(content: str) -> tuple[bool, str]:
    """Return (ok, reason) using JD text when available."""
    c = content or ""
    head = c[:3000]
    if re.search(r"[3-9]\+?\s*(?:years|yrs|yr)\b", head, re.I):
        return False, "jd_requires_3plus_years"
    return True, ""

def days_since(dt_str_or_ms) -> float | None:
    try:
        if isinstance(dt_str_or_ms, (int, float)):
            return (time.time() * 1000 - dt_str_or_ms) / 86400000
        dt = datetime.fromisoformat(str(dt_str_or_ms).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400000
    except Exception:
        return None


def main():
    jobs_data = json.loads(JOBS.read_text(encoding="utf-8-sig"))
    have_urls = {j.get("url", "").split("?")[0].rstrip("/").lower() for j in jobs_data["jobs"]}
    have_ct = {(j.get("company", "").lower(), j.get("title", "").lower()) for j in jobs_data["jobs"]}
    next_n = sum(1 for j in jobs_data["jobs"] if str(j.get("id", "")).startswith("A")) + 1
    added, scanned = [], 0
    stats_by_portal = {}

    def consider(company, title, url, portal, location, age_days, extra=None):
        nonlocal next_n, scanned
        scanned += 1
        t = (title or "").strip()
        if not TARGET_ROLES.search(t) or BLOCK_SENIOR.search(t):
            return
        if not loc_ok(location or ""):
            return
        key = url.split("?")[0].rstrip("/").lower()
        ck = (company.lower(), t.lower())
        if key in have_urls or ck in have_ct:
            return
        entry = {
            "id": f"A{next_n:03d}", "company": company, "title": t, "url": url,
            "portal": portal, "location": clean_loc(location), "match_score": 80,
            "status": "pending", "attempts": 0, "error": None,
            "evidence": {"source": f"harvest:{portal}",
                         "posted_age_days": round(age_days, 1) if age_days is not None else None},
            "updated_at": None,
        }
        if extra:
            entry["evidence"].update(extra)
        jobs_data["jobs"].append(entry)
        have_urls.add(key)
        have_ct.add(ck)
        stats_by_portal[portal] = stats_by_portal.get(portal, 0) + 1
        next_n += 1
        added.append(f"{entry['id']} {company} | {t[:48]} | {clean_loc(location)[:36]} | age={entry['evidence']['posted_age_days']}")

    # --- Greenhouse ---
    for slug in GREENHOUSE:
        data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        if not data or not data.get("jobs"):
            continue
        for jb in data["jobs"]:
            loc = jb.get("location", {}).get("name", "") if isinstance(jb.get("location"), dict) else ""
            if not loc_ok(loc):
                continue
            ok, why = jd_allows(jb.get("content") or "")
            if not ok:
                continue
            age = days_since(jb.get("first_published") or jb.get("updated_at"))
            consider(slug.capitalize(), jb.get("title", ""),
                     f"https://boards.greenhouse.io/{slug}/jobs/{jb.get('id')}",
                     "greenhouse", loc, age)
        time.sleep(0.2)

    # --- Lever ---
    for slug in LEVER:
        data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if not isinstance(data, list):
            continue
        for jb in data:
            cat = jb.get("categories") or {}
            loc = cat.get("location", "")
            if not loc_ok(loc):
                continue
            ok, _ = jd_allows((jb.get("descriptionPlain") or "")[:3000])
            if not ok:
                continue
            age = days_since(jb.get("createdAt"))
            consider(slug.capitalize(), jb.get("text", ""),
                     f"https://jobs.lever.co/{slug}/{jb.get('id')}",
                     "lever", loc, age)
        time.sleep(0.2)

    # --- Ashby ---
    for slug in ASHBY:
        data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(slug)}")
        if not data or not data.get("jobs"):
            continue
        for jb in data["jobs"]:
            loc = jb.get("location") or ""
            if not loc_ok(loc):
                continue
            ok, _ = jd_allows((jb.get("descriptionPlain") or ""))
            if not ok:
                continue
            age = days_since(jb.get("publishedAt") or jb.get("updatedAt"))
            consider(data.get("company", {}).get("name", slug).capitalize(), jb.get("title", ""),
                     jb.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{jb.get('id')}",
                     "ashby", loc, age)
        time.sleep(0.2)

    # --- SmartRecruiters ---
    for comp in SMARTRECRUITERS:
        offset = 0
        while True:
            data = get_json(f"https://api.smartrecruiters.com/v1/companies/{comp}/postings?limit=50&offset={offset}")
            if not data or not data.get("content"):
                break
            for jb in data["content"]:
                loc = ((jb.get("location") or {}).get("city", "") + ", " +
                       (jb.get("location") or {}).get("region", "") + ", " +
                       (jb.get("location") or {}).get("country", "")).strip(", ")
                if not loc_ok(loc):
                    continue
                name = jb.get("name", "")
                if BLOCK_SENIOR.search(name) or not TARGET_ROLES.search(name):
                    continue
                rel = jb.get("releasedDate")
                age = days_since(rel.split(".")[0] if isinstance(rel, str) else None)
                consider(comp, name,
                         f"https://jobs.smartrecruiters.com/{comp}/{jb.get('id')}",
                         "smartrecruiters", loc, age,
                         {"department": (jb.get("department") or {}).get("label", "")})
            if len(data["content"]) < 50:
                break
            offset += 50
            if offset > 150:
                break
        time.sleep(0.2)

    JOBS.write_text(json.dumps(jobs_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"scanned_postings={scanned}")
    print(f"ADDED={len(added)}")
    print(json.dumps(stats_by_portal))
    for a in added[:60]:
        print(" ", a)


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
