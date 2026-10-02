import bos
import sys
import json
import re
import time
import queue_store

sys.stdout.reconfigure(encoding="utf-8")
b = bos.BOS("wellfound-inspect")

# Close duplicate 117 if exists
# Find active wellfound page
tabs_out, _ = b.call("tabs", {"action": "list"})
pages = [int(m.group(1)) for m in re.finditer(r"\[(\d+)\]\s+https://wellfound\.com/jobs", tabs_out)]
print("Wellfound pages:", pages)

page = pages[-1] if pages else None
if not page:
    out, _ = b.call("tabs", {"action": "new", "url": "https://wellfound.com/jobs"})
    page = int(re.search(r"page (\d+)", out).group(1))
    time.sleep(3)

print(f"Using page {page}")

# Check existing queue
existing = queue_store.load()
existing_urls = {j.get("jobUrl", "").split("?")[0] for j in existing}
print(f"Existing queue size: {len(existing_urls)}")

# Scroll 10 times to load lazy content
for i in range(10):
    b.call("act", {"page": page, "kind": "scroll", "direction": "down", "amount": 10})
    time.sleep(1)

func = """() => {
    const cards = Array.from(document.querySelectorAll('a[href*="/jobs/"]')).map(a => {
        const href = a.href.split('?')[0];
        const text = a.innerText.trim().replace(/\\n/g, ' ');
        let p = a.parentElement;
        let company = "";
        while (p && p.tagName !== 'BODY') {
            const h = p.querySelector('h2, h3, a[href*="/startups/"]');
            if (h) { company = h.innerText.trim(); break; }
            p = p.parentElement;
        }
        return { href, role: text, company };
    }).filter(x => /\\/jobs\\/\\d+-[a-z0-9-]+/.test(x.href));
    return JSON.stringify(cards);
}"""

res, _ = b.call("evaluate", {"page": page, "func": func})
clean = re.sub(r'\[\/?UNTRUSTED_PAGE_CONTENT[^\]]*\]', '', res).strip()
m = re.search(r'(\[\s*\{.*\}\s*\])', clean, re.DOTALL)
if m:
    cards = json.loads(m.group(1))
    print(f"Total cards on page: {len(cards)}")
    fresh = []
    seen = set(existing_urls)
    for c in cards:
        if c["href"] not in seen:
            seen.add(c["href"])
            fresh.append(c)
    print(f"Fresh cards not in queue: {len(fresh)}")
    for f in fresh[:15]:
        print(f"-> {f['company']} | {f['role'][:60]} | {f['href']}")
