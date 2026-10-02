import bos
import time
import sys
import json
import re
import queue_store
import fast_apply_engine

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("batch9-runner")
    b.call("name_session", {
        "name": "batch9-fast-apply",
        "category": "job-application",
        "summary": "Autonomous discovery and application sweep for Batch 9"
    })
    
    current_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0] for j in current_jobs}
    print(f"Loaded {len(current_jobs)} existing jobs. Searching fresh openings for Batch 9...")
    
    # Target high-yield queries on Wellfound
    target_urls = [
        "https://wellfound.com/role/l/software-engineer/india",
        "https://wellfound.com/jobs?roles[]=Frontend+Engineer&roles[]=Full+Stack+Engineer&query=TypeScript&remote=true",
        "https://wellfound.com/jobs?roles[]=Frontend+Engineer&roles[]=Full+Stack+Engineer&query=Next.js",
        "https://wellfound.com/role/r/software-engineer"
    ]
    
    out, _ = b.call("tabs", {"action": "new", "url": target_urls[0]})
    p = int(re.search(r"page (\d+)", out).group(1))
    time.sleep(3.5)
    
    all_candidates = []
    seen = set(existing_urls)
    
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
    
    for t_url in target_urls:
        if len(all_candidates) >= 10:
            break
        print(f"\nScanning: {t_url}")
        b.call("navigate", {"page": p, "url": t_url})
        time.sleep(3.5)
        
        # Scroll 3 times to trigger lazy load
        for _ in range(3):
            b.call("act", {"page": p, "kind": "scroll", "direction": "down", "amount": 8})
            time.sleep(1)
            
        res, _ = b.call("evaluate", {"page": p, "func": func})
        
        # Parse cards
        start = res.find("[{")
        end = res.rfind("}]") + 2
        if start != -1 and end > start:
            try:
                cards = json.loads(res[start:end], strict=False)
                print(f"Scraped {len(cards)} raw cards from {t_url}")
                for c in cards:
                    href = c["href"]
                    role = c["role"]
                    company = c["company"] or "Startup"
                    if href not in seen and len(role) > 3:
                        # Exclude senior roles
                        if not any(s in role.lower() for s in ["senior", "lead", "staff", "principal", "manager", "head", "architect", "director"]):
                            # Prefer software / frontend / web / fullstack
                            if any(k in role.lower() for k in ["frontend", "developer", "engineer", "full stack", "react", "software", "web"]):
                                seen.add(href)
                                all_candidates.append({
                                    "company": company,
                                    "role": role,
                                    "url": href
                                })
                                if len(all_candidates) >= 10:
                                    break
            except Exception as e:
                print(f"Parse error: {e}")
                
    b.close(p)
    
    print(f"\nDiscovered {len(all_candidates)} fresh matching candidates for Batch 9:")
    for idx, c in enumerate(all_candidates, 1):
        print(f"[{idx}] {c['company']} — {c['role'][:50]} -> {c['url']}")
        
    if not all_candidates:
        print("No fresh jobs discovered.")
        return
        
    existing_b_ids = [int(j["queueId"][1:]) for j in current_jobs if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()]
    next_num = max(existing_b_ids) + 1 if existing_b_ids else 65
    
    engine = fast_apply_engine.FastApplyEngine()
    results = []
    
    print("\n========================================================")
    print(f"STARTING BATCH 9 AUTONOMOUS APPLY ({len(all_candidates)} JOBS)")
    print("========================================================")
    
    for item in all_candidates:
        qid = f"B{next_num:02d}"
        next_num += 1
        
        # Save as READY first
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
        final_status = "SUBMITTED" if ok else "READY"
        results.append({
            "queueId": qid,
            "company": item["company"],
            "role": item["role"],
            "url": item["url"],
            "status": final_status,
            "reason": reason
        })
        time.sleep(1)
        
    print("\n========================================================")
    print("BATCH 9 EXECUTION COMPLETE")
    print("========================================================")
    for r in results:
        print(f"{r['queueId']} | {r['company']} | {r['role'][:40]} -> {r['status']} ({r['reason']})")
        
    with open("d:\\newjobs\\batch9_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
