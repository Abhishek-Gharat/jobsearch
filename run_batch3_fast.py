import bos
import time
import sys
import json
import re
import queue_store
import fast_apply_engine

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("batch3-runner")
    page = 73
    
    print("Extracting active 'Apply on Wellfound' jobs from page 73...")
    
    # Scroll down 3 times to render full list
    for _ in range(3):
        b.call("act", {"page": page, "kind": "scroll", "direction": "down", "amount": 8})
        time.sleep(1)
        
    func = """() => {
        const cards = Array.from(document.querySelectorAll('a[href*="/jobs/"]')).map(a => {
            const href = a.href.split('?')[0];
            const text = a.innerText.trim().replace(/\\n/g, ' ');
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
    if not m:
        print("Could not extract jobs array")
        return
        
    cards = json.loads(m.group(1))
    
    # Filter against existing queue
    existing = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0] for j in existing}
    
    candidates = []
    seen = set(existing_urls)
    
    for c in cards:
        href = c["href"]
        role = c["role"]
        company = c["company"] or "Startup"
        if href not in seen and len(role) > 3:
            # Filter out senior roles
            if not any(s in role.lower() for s in ["senior", "lead", "staff", "principal", "manager", "head"]):
                seen.add(href)
                candidates.append({
                    "company": company,
                    "role": role,
                    "url": href
                })
                
    print(f"\nFiltered {len(candidates)} fresh jobs to apply to:")
    for idx, c in enumerate(candidates[:10], 1):
        print(f"[{idx}] {c['company']} — {c['role'][:50]} -> {c['url']}")
        
    target_batch = candidates[:10]
    if not target_batch:
        print("No fresh jobs found in current view")
        return
        
    # Queue them up as B12, B13, ...
    engine = fast_apply_engine.FastApplyEngine()
    current_jobs = queue_store.load()
    
    # Find next queue id index
    existing_b_ids = [int(j["queueId"][1:]) for j in current_jobs if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()]
    next_num = max(existing_b_ids) + 1 if existing_b_ids else 12
    
    results = []
    
    print("\n========================================================")
    print("STARTING AUTONOMOUS HIGH-SPEED ZERO-TOKEN BATCH APPLY")
    print("========================================================")
    
    for item in target_batch:
        qid = f"B{next_num:02d}"
        next_num += 1
        
        # Add to queue as READY
        current_jobs = queue_store.load()
        current_jobs.append({
            "queueId": qid,
            "company": item["company"],
            "role": item["role"],
            "jobUrl": item["url"],
            "status": "READY"
        })
        queue_store.save(current_jobs)
        
        # Apply using FastApplyEngine
        ok, reason = engine.apply_wellfound(qid, item["company"], item["role"], item["url"])
        results.append((qid, item["company"], item["role"], "SUBMITTED" if ok else "FAILED", reason))
        time.sleep(1)
        
    print("\n========================================================")
    print("BATCH EXECUTION COMPLETE SUMMARY")
    print("========================================================")
    for r in results:
        print(f"{r[0]} | {r[1]} | {r[2][:45]} -> {r[3]} ({r[4]})")

if __name__ == "__main__":
    main()
