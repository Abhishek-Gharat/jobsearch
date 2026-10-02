import bos
import sys
import time
import json

sys.stdout.reconfigure(encoding="utf-8")
b = bos.BOS("naukri-fresh")
p = 159

url = "https://www.naukri.com/react-developer-jobs-in-mumbai?experience=1&sort=f"
b.call("navigate", {"page": p, "url": url})
time.sleep(3.5)

js_extract = """() => {
    const cards = Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, .cust-job-tuple')).slice(0, 10).map(c => {
        const titleEl = c.querySelector('a.title, .job-title');
        const compEl = c.querySelector('a.comp-name, .company-name');
        const expEl = c.querySelector('.expwdth, .experience');
        const dateEl = c.querySelector('.job-post-day, .date');
        return {
            title: titleEl ? titleEl.innerText.trim() : '',
            company: compEl ? compEl.innerText.trim() : '',
            exp: expEl ? expEl.innerText.trim() : '',
            date: dateEl ? dateEl.innerText.trim() : '',
            href: titleEl ? titleEl.href.split('?')[0] : ''
        };
    }).filter(j => j.title && j.href);
    return JSON.stringify(cards);
}"""

res, _ = b.call("evaluate", {"page": p, "func": js_extract})
start = res.find("[{")
end = res.rfind("}]") + 2
if start != -1 and end > start:
    cards = json.loads(res[start:end], strict=False)
    print(f"Found {len(cards)} FRESH jobs sorted by date:")
    for idx, c in enumerate(cards, 1):
        print(f"[{idx}] [{c['date']}] {c['company']} — {c['title']} ({c['exp']})")
        print(f"     URL: {c['href']}")
    with open("d:/newjobs/fresh_naukri_jobs.json", "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=2)
else:
    print("Could not parse json")
