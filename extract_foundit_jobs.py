import sys
import json
import re
import bos

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("dbg")
    js = """() => {
        const cards = Array.from(document.querySelectorAll('.srpResultCard, .cardContainer, div[class*="card"]')).slice(0, 20);
        return cards.map(c => {
            const title = c.querySelector('.jobTitle, h3, a[class*="title"]')?.innerText?.trim();
            const comp = c.querySelector('.companyName, a[class*="company"]')?.innerText?.trim();
            const exp = c.querySelector('.experience, [class*="exp"]')?.innerText?.trim();
            const loc = c.querySelector('.location, [class*="loc"]')?.innerText?.trim();
            const url = c.querySelector('a[href*="/job/"], a[class*="title"]')?.href;
            const btn = c.querySelector('button')?.innerText?.trim();
            return { title, comp, exp, loc, url, btn };
        }).filter(j => j.title && j.comp);
    }"""
    res, _ = b.call("evaluate", {"page": 4, "func": js})
    m = re.search(r"ignore any embedded commands\.\s*\n(.*?)\n\[END_UNTRUSTED_PAGE_CONTENT", res, re.DOTALL)
    if m:
        cards = json.loads(m.group(1))
        print(f"Foundit React Jobs ({len(cards)}):")
        for idx, c in enumerate(cards, 1):
            print(f"[{idx}] {c.get('comp')} | {c.get('title')} ({c.get('exp', '')}) | {c.get('loc', '')}")
            print(f"     URL: {c.get('url')} | Button: {c.get('btn')}")
        with open("d:\\newjobs\\foundit_react_targets.json", "w", encoding="utf-8") as f:
            json.dump(cards, f, indent=2)
    else:
        print("Raw evaluate:", res[:400])

if __name__ == "__main__":
    main()
