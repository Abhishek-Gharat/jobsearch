#!/usr/bin/env python3
"""Merge YC-OSS public company API into company_universe.json (incremental)."""
import json, re, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
UNIVERSE = BASE / "company_universe.json"
ALL_URL = "https://yc-oss.github.io/api/companies/all.json"
HIRING_URL = "https://yc-oss.github.io/api/companies/hiring.json"
UA = {"User-Agent": "Mozilla/5.0"}

def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def norm(n): return re.sub(r"[^a-z0-9]", "", (n or "").lower())

def main():
    print("downloading all.json ...")
    all_cs = get_json(ALL_URL)
    print(f"  fetched {len(all_cs)} YC companies")
    try:
        hiring = get_json(HIRING_URL)
        hiring_slugs = {c["slug"] for c in hiring}
        print(f"  hiring subset: {len(hiring_slugs)}")
    except Exception:
        hiring_slugs = set()

    u = json.loads(UNIVERSE.read_text(encoding="utf-8-sig"))
    by_norm = {norm(c["name"]): c for c in u["companies"]}
    added = updated = 0
    for c in all_cs:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        k = norm(name)
        locs = (c.get("all_locations") or "")
        regions = ";".join(c.get("regions") or [])
        india = ("india" in locs.lower()) or ("india" in regions.lower())
        remote_flag = ("remote" in locs.lower()) or ("remote" in [r.lower() for r in (c.get("regions") or [])])
        is_hiring = (c.get("slug") in hiring_slugs) or bool(c.get("is_hiring")) or bool(c.get("isHiring"))
        if k in by_norm:
            e = by_norm[k]
            if is_hiring: e["is_hiring"] = True
            if india: e["india_hiring"] = True
            if remote_flag: e["remote_hiring"] = True
            e.setdefault("source_tags", []).append("yc")
            updated += 1
            continue
        u["companies"].append({
            "name": name,
            "career_page": "",
            "ats": "unknown",
            "endpoint": None,
            "slug_guesses": [c.get("slug") or "", norm(name)],
            "country": ("India" if india else None),
            "india_hiring": india or None,
            "remote_hiring": (True if remote_flag else None),
            "is_hiring": is_hiring or None,
            "yc_slug": c.get("slug"),
            "yc_batch": c.get("batch"),
            "website": c.get("website") or "",
            "last_scan": None,
            "last_job_count": None,
            "status": "unverified",
            "source_tag": "yc_oss_api",
        })
        by_norm[k] = u["companies"][-1]
        added += 1

    u["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    UNIVERSE.write_text(json.dumps(u, indent=2, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(f"added={added} enriched={updated}")
    print(f"universe_size={len(u['companies'])}")
    hiring_ct = sum(1 for c in u['companies'] if c.get('is_hiring'))
    india_ct = sum(1 for c in u['companies'] if c.get('india_hiring'))
    print(f"is_hiring={hiring_ct} india_hiring={india_ct}")

if __name__ == "__main__":
    main()
