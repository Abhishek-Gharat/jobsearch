import sys
import json
import re
import time
import bos
import queue_store

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("dbg")
    current_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0].lower() for j in current_jobs}

    searches = [
        ("Pune", "https://www.naukri.com/react-developer-jobs-in-pune?k=react%20developer&experience=1"),
        ("Bengaluru", "https://www.naukri.com/react-developer-jobs-in-bengaluru?k=react%20developer&experience=1"),
        ("Remote", "https://www.naukri.com/react-developer-jobs?k=react%20developer&experience=1&wfhType=0"),
        ("NextJS", "https://www.naukri.com/next-js-developer-jobs?k=next%20js&experience=1"),
        ("Frontend Pan-India", "https://www.naukri.com/frontend-developer-jobs?k=frontend%20developer&experience=1")
    ]

    js = """(startIdx) => {
        return Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, .cust-job-tuple')).slice(startIdx, startIdx + 6).map(c => ({
            title: c.querySelector('a.title, .job-title')?.innerText?.trim() || '',
            comp: c.querySelector('a.comp-name, .company-name')?.innerText?.trim() || '',
            exp: c.querySelector('.expwdth, .experience')?.innerText?.trim() || '',
            loc: c.querySelector('.locWdth, .location')?.innerText?.trim() || '',
            date: c.querySelector('.job-post-day, .date')?.innerText?.trim() || '',
            href: c.querySelector('a.title, .job-title')?.href?.split('?')[0] || ''
        }));
    }"""

    unapplied_matches = []
    seen = set(existing_urls)

    for name, s_url in searches:
        print(f"\n--- {name} ---")
        b.call("navigate", {"page": 4, "url": s_url})
        time.sleep(3.5)
        for offset in [0, 6, 12]:
            func_str = f"() => ({js})({offset})"
            res, _ = b.call("evaluate", {"page": 4, "func": func_str})
            m = re.search(r"ignore any embedded commands\.\s*\n(.*?)\n\[END_UNTRUSTED_PAGE_CONTENT", res, re.DOTALL)
            if not m:
                continue
            try:
                cards = json.loads(m.group(1))
                for c in cards:
                    href = c.get("href", "").strip()
                    if not href or href.lower() in seen:
                        continue
                    t = c["title"].lower()
                    if any(bad in t for bad in ["senior", "lead", "architect", "java", "php", "walk-in", "sales", "writer", "share point", "ai engineer"]):
                        continue
                    if any(good in t for good in ["react", "frontend", "front end", "next", "ui developer", "web developer"]):
                        seen.add(href.lower())
                        unapplied_matches.append(c)
                        print(f"MATCH: [{c['date']}] {c['comp']} | {c['title']} ({c['exp']}) | {c['loc']}")
                        print(f"  URL: {href}")
            except Exception as e:
                print(f"Error parsing offset {offset}: {e}")

    print(f"\n========================================================")
    print(f"TOTAL UNAPPLIED TARGETS DISCOVERED: {len(unapplied_matches)}")
    print(f"========================================================")
    with open("d:\\newjobs\\unapplied_react_targets.json", "w", encoding="utf-8") as f:
        json.dump(unapplied_matches, f, indent=2)

if __name__ == "__main__":
    main()
