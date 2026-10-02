import sys
import time
import json
import re
import bos
import queue_store

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("relevant-hunter")
    current_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0].lower() for j in current_jobs}

    searches = [
        "https://www.naukri.com/react-developer-jobs-in-mumbai?experience=1&sort=f",
        "https://www.naukri.com/react-js-developer-jobs-in-mumbai?experience=1&sort=f",
        "https://www.naukri.com/frontend-developer-jobs-in-mumbai?experience=1&sort=f",
        "https://www.naukri.com/react-developer-jobs?experience=1&wfhType=0&sort=f",
        "https://www.naukri.com/react-developer-jobs-in-pune?experience=1&sort=f",
        "https://www.naukri.com/frontend-developer-jobs-in-pune?experience=1&sort=f",
        "https://www.naukri.com/react-developer-jobs-in-bengaluru?experience=1&sort=f"
    ]

    js_extract_template = """(startIdx) => {{
        return Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, .cust-job-tuple')).slice(startIdx, startIdx + 5).map(c => ({{
            title: c.querySelector('a.title, .job-title')?.innerText?.trim() || '',
            comp: c.querySelector('a.comp-name, .company-name')?.innerText?.trim() || '',
            exp: c.querySelector('.expwdth, .experience')?.innerText?.trim() || '',
            loc: c.querySelector('.locWdth, .location')?.innerText?.trim() || '',
            sal: c.querySelector('.sal-wrap, .ni-job-tuple-icon-srp-rupee')?.parentElement?.innerText?.trim() || '',
            date: c.querySelector('.job-post-day, .date')?.innerText?.trim() || '',
            href: c.querySelector('a.title, .job-title')?.href?.split('?')[0] || ''
        }}));
    }}"""

    found = []
    seen = set(existing_urls)

    for s_url in searches:
        print(f"Scanning: {s_url}")
        b.call("navigate", {"page": 4, "url": s_url})
        time.sleep(3.5)
        for offset in [0, 5, 10]:
            func_str = f"() => ({js_extract_template})({offset})"
            res, _ = b.call("evaluate", {"page": 4, "func": func_str})
            m = re.search(r"ignore any embedded commands\.\s*\n(.*?)\n\[END_UNTRUSTED_PAGE_CONTENT", res, re.DOTALL)
            if not m:
                continue
            try:
                cards = json.loads(m.group(1), strict=False)
                for c in cards:
                    href = c.get("href", "").strip()
                    if not href or href.lower() in seen:
                        continue
                    t_low = c["title"].lower()
                    # Strict negative filters
                    if any(bad in t_low for bad in [
                        "senior", "sr.", "lead", "staff", "principal", "manager", "architect",
                        "walk-in", "walk in", "java", "php", "laravel", "wordpress", "shopify",
                        "angular", "writer", "sales", "consultant", "ai engineer", "share point",
                        "designer", "flutter", "android", "ios", "qa", "tester", "product specialist",
                        "customer experience", "associate"
                    ]):
                        continue
                    # Positive role match
                    if any(good in t_low for good in [
                        "react", "frontend", "front end", "front-end", "next.js", "nextjs",
                        "ui developer", "web developer", "software engineer", "sde"
                    ]):
                        seen.add(href.lower())
                        found.append(c)
            except Exception as e:
                print(f"Error parsing offset {offset}: {e}")

    print(f"\n========================================================")
    print(f"DISCOVERED {len(found)} HIGH-FIT FRESH REACT/FRONTEND JOBS")
    print(f"========================================================")
    for idx, f in enumerate(found, 1):
        print(f"[{idx}] {f['comp']} | {f['title']} ({f['exp']}) | {f['loc']} | {f['sal']} | {f['date']}")
        print(f"     URL: {f['href']}\n")

    with open("d:\\newjobs\\fresh_relevant_candidates.json", "w", encoding="utf-8") as out_f:
        json.dump(found, out_f, indent=2)

if __name__ == "__main__":
    main()
