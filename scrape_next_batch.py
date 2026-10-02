import bos
import time
import sys
import json
import re
import queue_store

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("scrape-batch")
    
    # Load existing URLs to avoid duplicates
    existing_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0] for j in existing_jobs}
    print(f"Existing jobs in queue: {len(existing_jobs)}")
    
    url = "https://wellfound.com/jobs?roles[]=Frontend+Engineer&roles[]=Full+Stack+Engineer"
    out, ok = b.call("tabs", {"action": "new", "url": url})
    m = re.search(r"page (\d+)", out)
    page = int(m.group(1)) if m else None
    print(f"Opened Wellfound search on page {page}")
    time.sleep(3)
    
    # Scroll multiple times to load several dozen jobs
    all_jobs = []
    seen_hrefs = set(existing_urls)
    
    for scroll in range(6):
        b.call("act", {"page": page, "kind": "scroll", "direction": "down", "amount": 8})
        time.sleep(1.5)
        
        func = """() => {
            const cards = Array.from(document.querySelectorAll('a[href*="/jobs/"]')).map(a => {
                const href = a.href.split('?')[0];
                const text = a.innerText.trim().replace(/\\n/g, ' ');
                // Find company name if present nearby
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
            try:
                cards = json.loads(m.group(1))
                for c in cards:
                    href = c["href"]
                    role = c["role"]
                    if href not in seen_hrefs and len(role) > 4:
                        if not any(senior in role.lower() for senior in ["senior", "lead", "staff", "principal", "manager", "architect"]):
                            seen_hrefs.add(href)
                            all_jobs.append(c)
            except Exception:
                pass
                
        if len(all_jobs) >= 10:
            break
            
    b.close(page)
    print(f"\nDiscovered {len(all_jobs)} fresh junior/mid jobs:")
    for idx, j in enumerate(all_jobs[:10], 1):
        print(f"[{idx}] {j['role'][:60]} -> {j['href']}")
        
    with open("d:\\newjobs\\batch3_discovered.json", "w", encoding="utf-8") as f:
        json.dump(all_jobs[:10], f, indent=2)
    print("Saved to d:\\newjobs\\batch3_discovered.json")

if __name__ == "__main__":
    main()
