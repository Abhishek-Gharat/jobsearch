import bos
import time
import sys
import json
import re

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("discover-fast")
    page = 60
    print("Using page:", page)
    time.sleep(4)
    
    # Scroll down 4 times to populate virtualized job cards
    for s in range(4):
        b.call("act", {"page": page, "kind": "scroll", "direction": "down", "amount": 6})
        time.sleep(1.5)
        
    func = """() => {
        const links = Array.from(document.querySelectorAll('a[href*="/jobs/"]')).map(a => {
            const href = a.href.split('?')[0];
            const text = a.innerText.trim().replace(/\\n/g, ' ');
            return { href, text };
        }).filter(x => /\\/jobs\\/\\d+-[a-z0-9-]+/.test(x.href));
        
        const seen = new Set();
        const results = [];
        for (const item of links) {
            if (!seen.has(item.href) && item.text.length > 5 && !item.text.includes('Senior') && !item.text.includes('Lead') && !item.text.includes('Staff')) {
                seen.add(item.href);
                results.push(item);
            }
        }
        return JSON.stringify(results);
    }"""
    res, _ = b.call("evaluate", {"page": page, "func": func})
    
    # Remove UNTRUSTED_PAGE_CONTENT markers
    clean_res = re.sub(r'\[\/?UNTRUSTED_PAGE_CONTENT[^\]]*\]', '', res).strip()
    m = re.search(r'(\[\s*\{.*\}\s*\])', clean_res, re.DOTALL)
    if m:
        items = json.loads(m.group(1))
        print(f"\nFound {len(items)} matching 0-2 yr Frontend/Fullstack jobs:")
        for idx, it in enumerate(items, 1):
            print(f"[{idx}] {it['text'][:80]} -> {it['href']}")
            
        with open("d:\\newjobs\\fast_discovered_jobs.json", "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
        print("Saved to d:\\newjobs\\fast_discovered_jobs.json")
    else:
        print("No JSON array extracted from:", res[:500])

if __name__ == "__main__":
    main()
