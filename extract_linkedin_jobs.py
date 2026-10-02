import sys
import json
import re
import bos

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("dbg")
    js = """() => {
        const items = Array.from(document.querySelectorAll('.jobs-search-results__list-item, .job-card-container, div[data-job-id]')).slice(0, 10);
        return items.map(el => {
            const titleEl = el.querySelector('.job-card-list__title--link, .job-card-container__link, a.job-card-list__title');
            const compEl = el.querySelector('.artdeco-entity-lockup__subtitle, .job-card-container__primary-description');
            const locEl = el.querySelector('.artdeco-entity-lockup__caption, .job-card-container__metadata-wrapper');
            return {
                title: titleEl ? titleEl.innerText.trim() : '',
                comp: compEl ? compEl.innerText.trim() : '',
                loc: locEl ? locEl.innerText.trim() : '',
                href: titleEl ? titleEl.href.split('?')[0] : ''
            };
        }).filter(j => j.title && j.href);
    }"""
    res, _ = b.call("evaluate", {"page": 1, "func": js})
    m = re.search(r"ignore any embedded commands\.\s*\n(.*?)\n\[END_UNTRUSTED_PAGE_CONTENT", res, re.DOTALL)
    if m:
        cards = json.loads(m.group(1))
        print(f"LinkedIn Easy Apply Cards ({len(cards)}):")
        for idx, c in enumerate(cards, 1):
            print(f"[{idx}] {c['comp']} | {c['title']} | {c['loc']}")
            print(f"     {c['href']}")
        with open("d:\\newjobs\\linkedin_easy_apply_targets.json", "w", encoding="utf-8") as f:
            json.dump(cards, f, indent=2)
    else:
        print("Failed to parse evaluate output:", res[:300])

if __name__ == "__main__":
    main()
