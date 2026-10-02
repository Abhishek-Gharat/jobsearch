import urllib.request
import urllib.parse
import re
import html
import json
import time

DDG_URL = "https://html.duckduckgo.com/html/?q="

def search(q, max_results=15):
    results = []
    try:
        req = urllib.request.Request(
            DDG_URL + urllib.parse.quote(q),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", "replace")
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', content, re.S):
            raw = m.group(1)
            title = re.sub(r"<.*?>", "", m.group(2)).strip()
            title = html.unescape(title)
            if "uddg=" in raw:
                raw = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse("https:" + raw).query).get("uddg", [""])[0])
            if raw.startswith("http") and "duckduckgo" not in raw:
                results.append((title, raw))
            if len(results) >= max_results:
                break
    except Exception as e:
        print(f"Search error for {q}: {e}")
    return results

if __name__ == "__main__":
    queries = [
        '"React Developer" site:linkedin.com/jobs/view India',
        '"Frontend Developer" site:linkedin.com/jobs/view ("0-1 years" OR "1-2 years" OR "fresher" OR "junior" OR "remote")',
        '"React Developer" site:naukri.com/job-listings',
        '"Frontend Developer" site:wellfound.com/jobs',
        '"React Developer" "0-2 years" India site:linkedin.com/jobs/view',
        '"Junior Frontend Developer" India site:linkedin.com/jobs/view'
    ]
    all_found = []
    for q in queries:
        print(f"\n--- Query: {q} ---")
        res = search(q, 10)
        for title, url in res:
            print(f"{title} -> {url}")
            all_found.append({"title": title, "url": url})
        time.sleep(1)
    
    with open("scraped_discovery_jobs.json", "w", encoding="utf-8") as f:
        json.dump(all_found, f, indent=2)
    print(f"\nTotal collected: {len(all_found)}")
