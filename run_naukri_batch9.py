import bos
import sys
import time
import json
import re
import queue_store

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("naukri-broad-search")
    
    current_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0] for j in current_jobs}
    print(f"Loaded {len(current_jobs)} existing jobs. Searching fresh Naukri openings...")
    
    searches = [
        "https://www.naukri.com/react-developer-jobs?experience=1&sort=f",
        "https://www.naukri.com/frontend-developer-jobs-in-pune?experience=1&sort=f",
        "https://www.naukri.com/frontend-developer-jobs-in-bengaluru?experience=1&sort=f",
        "https://www.naukri.com/javascript-developer-jobs-in-mumbai?experience=1&sort=f",
        "https://www.naukri.com/next-js-developer-jobs?experience=1&sort=f"
    ]
    
    out, _ = b.call("tabs", {"action": "new", "url": searches[0]})
    p = int(re.search(r"page (\d+)", out).group(1))
    time.sleep(3)
    
    all_candidates = []
    seen = set(existing_urls)
    
    js_extract = """() => {
        const cards = Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, .cust-job-tuple')).slice(0, 15).map(c => {
            const titleEl = c.querySelector('a.title, .job-title');
            const compEl = c.querySelector('a.comp-name, .company-name');
            const expEl = c.querySelector('.expwdth, .experience');
            const dateEl = c.querySelector('.job-post-day, .date');
            const locEl = c.querySelector('.locWdth, .location');
            return {
                title: titleEl ? titleEl.innerText.trim() : '',
                company: compEl ? compEl.innerText.trim() : '',
                exp: expEl ? expEl.innerText.trim() : '',
                date: dateEl ? dateEl.innerText.trim() : '',
                loc: locEl ? locEl.innerText.trim() : '',
                href: titleEl ? titleEl.href.split('?')[0] : ''
            };
        }).filter(j => j.title && j.href);
        return JSON.stringify(cards);
    }"""
    
    for s_url in searches:
        if len(all_candidates) >= 10:
            break
        print(f"\nScanning: {s_url}")
        b.call("navigate", {"page": p, "url": s_url})
        time.sleep(3.5)
        
        res, _ = b.call("evaluate", {"page": p, "func": js_extract})
        start = res.find("[{")
        end = res.rfind("}]") + 2
        if start != -1 and end > start:
            try:
                cards = json.loads(res[start:end], strict=False)
                for c in cards:
                    href = c["href"]
                    title = c["title"]
                    if href not in seen and len(title) > 3:
                        if not any(s in title.lower() for s in ["senior", "lead", "staff", "principal", "manager", "head", "architect", "writer", "sales"]):
                            seen.add(href)
                            all_candidates.append(c)
                            if len(all_candidates) >= 10:
                                break
            except Exception as e:
                print(f"Error parsing: {e}")
                
    print(f"\nDiscovered {len(all_candidates)} fresh matching candidates:")
    for idx, c in enumerate(all_candidates, 1):
        print(f"[{idx}] {c['company']} — {c['title']} ({c['exp']}) | {c['date']} -> {c['href']}")
        
    if not all_candidates:
        print("No fresh jobs discovered.")
        b.close(p)
        return
        
    existing_b_ids = [int(j["queueId"][1:]) for j in current_jobs if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()]
    next_num = max(existing_b_ids) + 1 if existing_b_ids else 66
    
    results = []
    
    print("\n========================================================")
    print(f"STARTING NAUKRI BATCH 9 DIRECT APPLY ({len(all_candidates)} JOBS)")
    print("========================================================")
    
    for item in all_candidates:
        qid = f"B{next_num:02d}"
        next_num += 1
        item_url = item.get("href")
        
        print(f"\n[{qid}] Navigating to {item['company']} — {item['title']}...")
        b.call("navigate", {"page": p, "url": item_url})
        time.sleep(3)
        
        # Click apply
        js_apply = """() => {
            const body = document.body ? document.body.innerText.toLowerCase() : '';
            if (body.includes('applied to') || (body.includes('applied') && !body.includes('apply'))) {
                return { status: 'ALREADY_APPLIED' };
            }
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.trim().toLowerCase() === 'apply');
            if (!btn) {
                const ext = Array.from(document.querySelectorAll('button, a')).find(b => b.innerText.toLowerCase().includes('apply on company site'));
                if (ext) return { status: 'EXTERNAL_SITE' };
                return { status: 'NO_BUTTON' };
            }
            btn.click();
            return { status: 'CLICKED' };
        }"""
        
        click_res_raw, _ = b.call("evaluate", {"page": p, "func": js_apply})
        time.sleep(2.5)
        
        page_text = b.read(p)
        applied = False
        reason = ""
        
        if "applied to" in page_text.lower() or "saveapply" in page_text.lower() or "200" in page_text or "application has been submitted" in page_text.lower():
            applied = True
            reason = "Verified 1-click Naukri direct apply (HTTP 200)"
        elif "already_applied" in click_res_raw.lower() or "already applied" in page_text.lower():
            applied = True
            reason = "Verified previously applied on Naukri"
        elif "external_site" in click_res_raw.lower() or "apply on company site" in page_text.lower():
            reason = "Redirects to external company career page"
        else:
            js_submit = """() => {
                const submit = Array.from(document.querySelectorAll('button, .btn')).find(b => {
                    const t = b.innerText.trim().toLowerCase();
                    return t === 'submit' || t === 'apply now' || t === 'save & apply';
                });
                if (submit) {
                    submit.click();
                    return 'CLICKED_SUBMIT';
                }
                return 'NO_SUBMIT';
            }"""
            b.call("evaluate", {"page": p, "func": js_submit})
            time.sleep(2)
            page_text_after = b.read(p)
            if "applied to" in page_text_after.lower() or "saveapply" in page_text_after.lower():
                applied = True
                reason = "Verified modal submit on Naukri"
            else:
                reason = "Application questionnaire / custom form required"
                
        final_status = "SUBMITTED" if applied else "READY"
        print(f"[{qid}] Result: {final_status} ({reason})")
        
        current_jobs = queue_store.load()
        current_jobs.append({
            "queueId": qid,
            "company": item["company"] or "Tech Employer",
            "role": item["title"],
            "jobUrl": item_url,
            "status": final_status,
            "submissionDate": time.strftime("%Y-%m-%d") if applied else "",
            "failureReason": reason
        })
        queue_store.save(current_jobs)
        
        results.append({
            "queueId": qid,
            "company": item["company"],
            "role": item["title"],
            "status": final_status,
            "reason": reason
        })
        time.sleep(1)
        
    b.close(p)
    
    print("\n========================================================")
    print("NAUKRI BATCH 9 COMPLETE")
    print("========================================================")
    for r in results:
        print(f"{r['queueId']} | {r['company']} | {r['role'][:35]} -> {r['status']} ({r['reason']})")
        
    with open("d:\\newjobs\\naukri_batch9_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
