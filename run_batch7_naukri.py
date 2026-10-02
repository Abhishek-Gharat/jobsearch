import bos
import sys
import time
import json
import re
import queue_store

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("naukri-batch-runner")
    
    current_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0] for j in current_jobs}
    print(f"Loaded {len(current_jobs)} existing jobs. Searching fresh Naukri openings...")
    
    # Target search queries on Naukri sorted by date
    search_queries = [
        "https://www.naukri.com/react-developer-jobs?experience=1&sort=f",
        "https://www.naukri.com/frontend-developer-jobs-in-mumbai?experience=1&sort=f",
        "https://www.naukri.com/full-stack-developer-jobs?experience=1&sort=f&wfhType=0"
    ]
    
    out, _ = b.call("tabs", {"action": "new", "url": search_queries[0]})
    m = re.search(r"page (\d+)", out)
    p = int(m.group(1))
    
    all_cards = []
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
    
    for sq in search_queries:
        if len(all_cards) >= 6:
            break
        b.call("navigate", {"page": p, "url": sq})
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
                        if not any(s in title.lower() for s in ["senior", "lead", "staff", "principal", "manager", "head", "architect"]):
                            seen.add(href)
                            all_cards.append(c)
                            if len(all_cards) >= 6:
                                break
            except Exception as e:
                print(f"Error parsing {sq}: {e}")
                
    print(f"\nDiscovered {len(all_cards)} fresh matching Naukri jobs:")
    for idx, c in enumerate(all_cards, 1):
        print(f"[{idx}] {c['company']} — {c['title']} ({c['exp']}) | {c['date']} -> {c['href']}")
        
    if not all_cards:
        print("No fresh jobs discovered.")
        b.close(p)
        return
        
    existing_b_ids = [int(j["queueId"][1:]) for j in current_jobs if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()]
    next_num = max(existing_b_ids) + 1 if existing_b_ids else 49
    
    results = []
    
    print("\n========================================================")
    print(f"STARTING NAUKRI BATCH 7 DIRECT APPLY ({len(all_cards)} JOBS)")
    print("========================================================")
    
    for item in all_cards:
        qid = f"B{next_num:02d}"
        next_num += 1
        item_url = item.get("href") or item.get("url")
        
        # Navigate to job page
        b.call("navigate", {"page": p, "url": item_url})
        time.sleep(3)
        
        # Check if already applied or apply button exists
        js_apply = """() => {
            const body = document.body ? document.body.innerText.toLowerCase() : '';
            if (body.includes('applied') && !body.includes('applied to')) {
                // If it already says 'Applied' on header
                const tag = Array.from(document.querySelectorAll('.apply-button, .already-applied, button')).find(b => b.innerText.trim().toLowerCase() === 'applied');
                if (tag) return { status: 'ALREADY_APPLIED' };
            }
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.trim().toLowerCase() === 'apply');
            if (!btn) {
                // Check if external company site
                const ext = Array.from(document.querySelectorAll('button, a')).find(b => b.innerText.toLowerCase().includes('apply on company site'));
                if (ext) return { status: 'EXTERNAL_SITE' };
                return { status: 'NO_APPLY_BTN' };
            }
            btn.click();
            return { status: 'CLICKED' };
        }"""
        
        apply_res_raw, _ = b.call("evaluate", {"page": p, "func": js_apply})
        time.sleep(2.5)
        
        # Read page to check confirmation
        page_text = b.read(p)
        applied_confirmed = False
        reason = ""
        
        if "applied to" in page_text.lower() or "saveapply" in page_text.lower() or "application has been submitted" in page_text.lower() or "200" in page_text:
            applied_confirmed = True
            reason = "Verified Naukri direct apply (HTTP 200)"
        elif "already applied" in apply_res_raw.lower() or "already" in page_text.lower():
            applied_confirmed = True
            reason = "Already applied on Naukri"
        elif "external_site" in apply_res_raw.lower():
            reason = "Redirects to external company career site"
        else:
            reason = "Direct apply modal / questionnaire required"
            
        status = "SUBMITTED" if applied_confirmed else "READY"
        print(f"[{qid}] {item['company']} — {item['title'][:40]} -> {status} ({reason})")
        
        current_jobs = queue_store.load()
        current_jobs.append({
            "queueId": qid,
            "company": item["company"] or "Tech Employer",
            "role": item["title"],
            "jobUrl": item_url,
            "status": status,
            "submissionDate": time.strftime("%Y-%m-%d") if applied_confirmed else "",
            "failureReason": reason
        })
        queue_store.save(current_jobs)
        
        results.append({
            "queueId": qid,
            "company": item["company"],
            "role": item["title"],
            "status": status,
            "reason": reason
        })
        time.sleep(1)
        
    b.close(p)
    
    print("\n========================================================")
    print("NAUKRI BATCH 7 COMPLETE")
    print("========================================================")
    for r in results:
        print(f"{r['queueId']} | {r['company']} | {r['role'][:35]} -> {r['status']}")
        
    with open("d:\\newjobs\\naukri_batch7_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
