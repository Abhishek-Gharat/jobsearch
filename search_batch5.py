import find_batch_jobs
import sys
import json
import re
import queue_store

sys.stdout.reconfigure(encoding="utf-8")

existing = queue_store.load()
existing_urls = {j.get("jobUrl", "").split("?")[0] for j in existing}
print(f"Existing queue size: {len(existing_urls)}")

queries = [
    'site:wellfound.com/jobs "React" India',
    'site:wellfound.com/jobs "Frontend Developer" India',
    'site:wellfound.com/jobs "Full Stack" React India',
    'site:wellfound.com/jobs "Junior" Frontend',
    'site:wellfound.com/jobs "Next.js" Developer India',
    'site:wellfound.com/jobs "Frontend Engineer" Remote India'
]

discovered = []
seen = set(existing_urls)

for q in queries:
    res = find_batch_jobs.search(q, 15)
    print(f"Query: {q} -> found {len(res)} results")
    for title, url in res:
        clean_url = url.split("?")[0]
        # Match wellfound job pattern /jobs/\d+-
        if "/jobs/" in clean_url and re.search(r"/jobs/\d+", clean_url) and clean_url not in seen:
            # Filter out senior/lead
            if not any(s in title.lower() for s in ["senior", "lead", "staff", "principal", "manager", "head"]):
                seen.add(clean_url)
                # Parse company and role from title if possible (e.g. "Frontend Developer at Startup | Wellfound")
                parts = title.split(" at ")
                if len(parts) == 2:
                    role = parts[0].strip()
                    comp = parts[1].split("|")[0].split("-")[0].strip()
                else:
                    role = title.split("|")[0].strip()
                    comp = "Tech Startup"
                discovered.append({
                    "company": comp,
                    "role": role,
                    "url": clean_url
                })

print(f"\nTotal fresh, non-duplicate matching jobs found: {len(discovered)}")
for idx, d in enumerate(discovered[:15], 1):
    print(f"[{idx}] {d['company']} — {d['role']} -> {d['url']}")

with open("batch5_discovered.json", "w", encoding="utf-8") as f:
    json.dump(discovered, f, indent=2)
