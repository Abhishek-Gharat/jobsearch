import bos
import time
import sys
import json
import re

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("extract-jobs")
    page = 58
    
    # Wait for React to render search results
    time.sleep(2)
    
    func = """() => {
        const links = Array.from(document.querySelectorAll('a[href*="/jobs/"]')).map(a => {
            const href = a.href;
            const text = a.innerText.trim().replace(/\\n/g, ' ');
            return { href, text };
        }).filter(x => /\\/jobs\\/\\d+-[a-z0-9-]+/.test(x.href));
        
        // deduplicate by href
        const seen = new Set();
        const deduped = [];
        for (const item of links) {
            const cleanUrl = item.href.split('?')[0];
            if (!seen.has(cleanUrl) && item.text.length > 3) {
                seen.add(cleanUrl);
                deduped.push({ href: cleanUrl, text: item.text });
            }
        }
        return JSON.stringify(deduped);
    }"""
    
    res, ok = b.call("evaluate", {"page": page, "func": func})
    try:
        # clean unneeded untrusted wrapper if present
        m = re.search(r"(\[.*\])", res, re.DOTALL)
        raw_json = m.group(1) if m else res
        items = json.loads(raw_json)
        print(f"Extracted {len(items)} matching jobs from Wellfound:")
        for idx, item in enumerate(items[:20], 1):
            print(f"[{idx}] {item['text']} -> {item['href']}")
    except Exception as e:
        print("Parsing error:", e)
        print("Raw response:", res[:1000])

if __name__ == "__main__":
    main()
