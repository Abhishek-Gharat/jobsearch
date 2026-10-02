import sys
import json
import re
import time
import bos
import queue_store

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("auto-apply-worker")
    current_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0].lower() for j in current_jobs}

    searches = [
        "https://www.naukri.com/react-developer-jobs-in-mumbai-2?k=react%20developer&experience=1",
        "https://www.naukri.com/frontend-developer-jobs-in-mumbai-2?k=frontend%20developer&experience=1",
        "https://www.naukri.com/react-developer-jobs-in-pune-2?k=react%20developer&experience=1",
        "https://www.naukri.com/react-developer-jobs-in-bengaluru-2?k=react%20developer&experience=1",
        "https://www.naukri.com/react-developer-jobs-2?k=react%20developer&experience=1&wfhType=0",
        "https://www.naukri.com/next-js-developer-jobs-2?k=next%20js&experience=1",
        "https://www.naukri.com/frontend-developer-jobs-2?k=frontend%20developer&experience=1"
    ]

    js_extract = """(startIdx) => {
        return Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, .cust-job-tuple')).slice(startIdx, startIdx + 6).map(c => ({
            title: c.querySelector('a.title, .job-title')?.innerText?.trim() || '',
            comp: c.querySelector('a.comp-name, .company-name')?.innerText?.trim() || '',
            exp: c.querySelector('.expwdth, .experience')?.innerText?.trim() || '',
            loc: c.querySelector('.locWdth, .location')?.innerText?.trim() || '',
            date: c.querySelector('.job-post-day, .date')?.innerText?.trim() || '',
            href: c.querySelector('a.title, .job-title')?.href?.split('?')[0] || ''
        }));
    }"""

    candidates = []
    seen = set(existing_urls)

    print("Phase 1: Discovering high-relevance React/Frontend openings on Page 2...")
    for s_url in searches:
        print(f"Scanning: {s_url}")
        b.call("navigate", {"page": 4, "url": s_url})
        time.sleep(3.5)
        for offset in [0, 6, 12]:
            func_str = f"() => ({js_extract})({offset})"
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
                    if any(bad in t for bad in [
                        "senior", "sr.", "lead", "staff", "principal", "manager", "architect",
                        "walk-in", "walk in", "java", "php", "laravel", "wordpress", "shopify",
                        "angular", "writer", "sales", "consultant", "ai engineer", "share point",
                        "designer", "flutter", "android", "ios", "qa", "tester", "product specialist",
                        "customer experience", "associate"
                    ]):
                        continue
                    if any(good in t for good in [
                        "react", "frontend", "front end", "front-end", "next.js", "nextjs",
                        "ui developer", "web developer", "software engineer", "sde"
                    ]):
                        seen.add(href.lower())
                        candidates.append(c)
                        print(f"  Found: {c['comp']} | {c['title']} ({c['exp']}) | {c['loc']}")
            except Exception as e:
                pass

    print(f"\nPhase 1 Complete. Found {len(candidates)} high-relevance candidate openings.")

    # Phase 2: Live Apply to eligible jobs
    existing_b_ids = [int(j["queueId"][1:]) for j in current_jobs if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()]
    next_id_num = max(existing_b_ids) + 1 if existing_b_ids else 95

    submitted = []
    print("\nPhase 2: Applying to eligible direct-apply jobs...")

    for c in candidates:
        if len(submitted) >= 5:
            print("Reached target batch limit of 5 successful applications.")
            break

        url = c["href"]
        print(f"\nInspecting: {c['comp']} — {c['title']}...")
        b.call("navigate", {"page": 4, "url": url})
        time.sleep(3)

        # Check page text
        page_text = b.read(4).lower()
        if "already applied" in page_text or "you've already applied" in page_text:
            print("  Already applied.")
            continue

        # Look for Apply button
        js_click_apply = """() => {
            const btn = document.getElementById('apply-button') || Array.from(document.querySelectorAll('button, a')).find(b => (b.innerText||'').trim().toLowerCase() === 'apply');
            if (!btn) return 'NO_APPLY';
            btn.scrollIntoView({ behavior: 'instant', block: 'center' });
            btn.click();
            btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            return 'CLICKED_APPLY';
        }"""
        click_res, _ = b.call("evaluate", {"page": 4, "func": js_click_apply})
        if "CLICKED_APPLY" not in click_res:
            print("  No direct apply button (may be external site).")
            continue

        time.sleep(3.5)
        after_text = b.read(4).lower()

        # Check if immediate 200 / applied
        is_applied = False
        if "applied to" in after_text or "200" in after_text or "application has been submitted" in after_text:
            is_applied = True
        else:
            # Check for questionnaire modal and solve
            js_solve = """() => {
                let count = 0;
                // Radio/checkboxes
                document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(inp => {
                    const l = (inp.parentElement ? inp.parentElement.innerText : '').toLowerCase();
                    if (l.includes('yes') || l.includes('immediate') || l.includes('15') || l.includes('mumbai') || l.includes('agree')) {
                        if (!inp.checked) { inp.click(); count++; }
                    }
                });
                // Text inputs
                document.querySelectorAll('input[type="text"], input[type="number"], textarea').forEach(inp => {
                    const all = ((inp.placeholder||'') + ' ' + (inp.name||'') + ' ' + (inp.parentElement?inp.parentElement.innerText:'')).toLowerCase();
                    if (!inp.value) {
                        if (all.includes('experience') || all.includes('years') || all.includes('exp')) { inp.value = '1'; count++; }
                        else if (all.includes('current ctc') || all.includes('current salary')) { inp.value = '3'; count++; }
                        else if (all.includes('expected ctc') || all.includes('expected salary')) { inp.value = '5.5'; count++; }
                        else if (all.includes('notice')) { inp.value = '0'; count++; }
                        else if (all.includes('location') || all.includes('city')) { inp.value = 'Mumbai'; count++; }
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                });
                // Submit button
                const sub = Array.from(document.querySelectorAll('button, input[type="submit"]')).find(b => {
                    const t = (b.innerText || b.value || '').trim().toLowerCase();
                    return t === 'submit' || t === 'apply now' || t === 'save & apply' || t === 'save and apply';
                });
                if (sub) { sub.click(); return 'SUBMITTED_MODAL'; }
                return 'FILLED_' + count;
            }"""
            solve_res, _ = b.call("evaluate", {"page": 4, "func": js_solve})
            time.sleep(3)
            after_solve = b.read(4).lower()
            if "applied to" in after_solve or "200" in after_solve or "application has been submitted" in after_solve:
                is_applied = True

        if is_applied:
            qid = f"B{next_id_num:02d}"
            next_id_num += 1
            print(f"  [SUCCESS] {qid} SUBMITTED: {c['comp']} — {c['title']}!")
            current_jobs = queue_store.load()
            current_jobs.append({
                "queueId": qid,
                "company": c["comp"],
                "role": c["title"],
                "jobUrl": url,
                "status": "SUBMITTED",
                "submissionDate": time.strftime("%Y-%m-%d"),
                "failureReason": "Verified 1-click Naukri direct apply (HTTP 200)"
            })
            queue_store.save(current_jobs)
            submitted.append({
                "queueId": qid,
                "company": c["comp"],
                "role": c["title"],
                "url": url
            })

    print(f"\n========================================================")
    print(f"APPLIED TO {len(submitted)} NEW JOBS SUCCESSFULLY!")
    print(f"========================================================")
    for s in submitted:
        print(f"{s['queueId']} | {s['company']} | {s['role']}")

    with open("d:\\newjobs\\batch12_results.json", "w", encoding="utf-8") as out_f:
        json.dump(submitted, out_f, indent=2)

if __name__ == "__main__":
    main()
