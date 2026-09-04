#!/usr/bin/env python3
"""Expand company_universe.json from curated public GitHub lists. Incremental."""
import json, re, sys, urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
UNIVERSE = BASE / "company_universe.json"
UA = {"User-Agent": "Mozilla/5.0"}

SOURCES = [
    # (url, parser, note)
    ("https://raw.githubusercontent.com/softvar/awesome-startups/master/countries/india.md",
     "md_links_names_only", "startupranking-top100"),
    ("https://raw.githubusercontent.com/SR-Sunny-Raj/Companies/main/README.md",
     "md_links_with_urls", "product-companies-378"),
]

ATS_FINGERPRINTS = [
    ("lever", r"jobs\.lever\.co|lever\.co"),
    ("greenhouse", r"greenhouse\.io"),
    ("ashby", r"ashbyhq\.com"),
    ("smartrecruiters", r"smartrecruiters\.com"),
    ("workable", r"workable\.com|apply\.workable"),
    ("recruitee", r"recruitee\.com"),
    ("teamtailor", r"teamtailor\.com"),
    ("personio", r"personio"),
    ("jobvite", r"jobvite"),
    ("comeet", r"comeet"),
    ("rippling", r"rippling"),
    ("pinpoint", r"pinpointhq"),
    ("workday", r"myworkdayjobs|wd\d+\.myworkday"),
    ("darwinbox", r"darwinbox"),
    ("recruiterbox", r"recruiterbox"),
    ("freshteam", r"freshteam"),
    ("breezy", r"breezy\.hr"),
    ("zohorecruit", r"zohorecruit"),
    ("hirexp", r"hirexp"),
    ("skillate", r"skillate"),
    ("eightfold", r"eightfold"),
    ("param_ai", r"param\.ai"),
    ("instahyre", r"instahyre"),
    ("angel_co", r"angel\.co|wellfound"),
    ("linkedin_jobs", r"linkedin\.com/jobs"),
    ("naukri", r"naukri\.com"),
]

def detect_ats(url):
    for prov, pat in ATS_FINGERPRINTS:
        if re.search(pat, url or "", re.I):
            return prov
    return "unknown"

def slugify(n): return re.sub(r"[^a-z0-9]", "", n.lower())
def slug_dash(n): return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")

def norm_name(n): return re.sub(r"[^a-z0-9]", "", (n or "").lower())

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")

def parse_md_links(text, mode):
    """Yield (name, career_url_or_'') skipping repo chrome links."""
    out = []
    for m in re.finditer(r"\[([^\]]{2,60})\]\((https?://[^)\s]+)\)", text):
        name, url = m.group(1).strip(), m.group(2)
        low = url.lower()
        if any(s in low for s in ("github.com", "shields.io", "contrib.rocks",
                                  "twitter.com", "startupranking.com",
                                  "laobi.icu", "netlify.app")):
            if mode == "md_links_names_only":
                # ranking links: use link TEXT as company name, no career url
                nm = re.sub(r"[^\w&.\-' ]", "", name).strip()
                if 2 <= len(nm) <= 40 and not nm.lower().startswith("http"):
                    out.append((nm, ""))
            continue
        if mode == "md_links_with_urls":
            nm = name.replace("&#39;", "'").replace("&amp;", "&").strip()
            if 2 <= len(nm) <= 45:
                out.append((nm, url))
    return out


def main():
    u = json.loads(UNIVERSE.read_text(encoding="utf-8-sig"))
    by_norm = {norm_name(c["name"]): c for c in u["companies"]}
    added, dup = [], 0
    for url, mode, tag in SOURCES:
        try:
            text = fetch(url)
        except Exception as e:
            print(f"[skip] {tag}: {e}")
            continue
        pairs = parse_md_links(text, mode)
        print(f"{tag}: parsed {len(pairs)} links")
        for name, career in pairs:
            k = norm_name(name)
            if not k or k in by_norm:
                dup += 1
                continue
            ats = detect_ats(career) if career else "unknown"
            entry = {
                "name": name,
                "career_page": career,
                "ats": ats if ats != "unknown" else "unknown",
                "endpoint": None,
                "slug_guesses": [slugify(name), slug_dash(name)],
                "country": "India",
                "india_hiring": True,
                "remote_hiring": None,
                "last_scan": None,
                "last_job_count": None,
                "status": "unverified",
                "source_tag": tag,
            }
            if ats != "unknown":
                entry["ats_hint_url"] = career
                entry["career_page"] = ""
            u["companies"].append(entry)
            by_norm[k] = entry
            added.append(name)
    save_path = UNIVERSE
    save_path.write_text(json.dumps(u, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"added={len(added)} duplicates_skipped={dup}")
    print(f"universe_size={len(u['companies'])}")
    from collections import Counter
    print(dict(Counter(c["ats"] for c in u["companies"])))


if __name__ == "__main__":
    main()
