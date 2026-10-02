import bos
import time
import sys
import json
import re
import queue_store
import fast_apply_engine

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("batch4-runner")
    b.call("name_session", {
        "name": "batch4-apply",
        "category": "job-application",
        "summary": "Autonomous discovery and application worker for Batch 4"
    })
    
    existing = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0] for j in existing}
    print(f"Loaded {len(existing)} existing jobs from queue.")
    
    # 1. Open Wellfound search with Remote & India friendly roles
    search_url = "https://wellfound.com/jobs?roles[]=Frontend+Engineer&roles[]=Full+Stack+Engineer&remote=true"
    print(f"Opening Wellfound: {search_url}")
    out, ok = b.call("tabs", {"action": "new", "url": search_url})
    m = re.search(r"page (\d+)", out)
    if not m:
        print("Failed to open Wellfound tab")
        return
    page = int(m.group(1))
    time.sleep(3.5)
    
    # Scroll down multiple times to render rich feed
    print("Scrolling feed to discover openings...")
    for _ in range(5):
        b.call("act", {"page": page, "kind": "scroll", "direction": "down", "amount": 8})
        time.sleep(1.2)
        
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
    b.close(page)
    
    clean = re.sub(r'\[\/?UNTRUSTED_PAGE_CONTENT[^\]]*\]', '', res).strip()
    m_json = re.search(r'(\[\s*\{.*\}\s*\])', clean, re.DOTALL)
    if not m_json:
        print("Could not extract jobs from Wellfound DOM.")
        return
        
    cards = json.loads(m_json.group(1))
    print(f"Discovered raw cards: {len(cards)}")
    
    candidates = []
    seen = set(existing_urls)
    
    for c in cards:
        href = c["href"]
        role = c["role"]
        company = c["company"] or "Startup"
        if href not in seen and len(role) > 3:
            # Filter out senior / staff / lead / principal / manager
            if not any(s in role.lower() for s in ["senior", "lead", "staff", "principal", "manager", "head", "director", "architect"]):
                seen.add(href)
                candidates.append({
                    "company": company,
                    "role": role,
                    "url": href
                })
                
    print(f"\nFiltered {len(candidates)} fresh matching jobs:")
    for idx, c in enumerate(candidates[:10], 1):
        print(f"[{idx}] {c['company']} — {c['role'][:50]} -> {c['url']}")
        
    target_batch = candidates[:10]
    if not target_batch:
        print("No fresh jobs discovered. Trying broader query...")
        return
        
    # Queue IDs starting after max existing
    current_jobs = queue_store.load()
    existing_b_ids = [int(j["queueId"][1:]) for j in current_jobs if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()]
    next_num = max(existing_b_ids) + 1 if existing_b_ids else 20
    
    engine = fast_apply_engine.FastApplyEngine()
    results = []
    
    print("\n========================================================")
    print(f"STARTING BATCH 4 AUTONOMOUS APPLY ({len(target_batch)} JOBS)")
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
        
        # Fast autonomous apply
        ok, reason = engine.apply_wellfound(qid, item["company"], item["role"], item["url"])
        status = "SUBMITTED" if ok else "READY"
        results.append({
            "queueId": qid,
            "company": item["company"],
            "role": item["role"],
            "url": item["url"],
            "status": status,
            "reason": reason
        })
        time.sleep(1)
        
    print("\n========================================================")
    print("BATCH 4 EXECUTION COMPLETE")
    print("========================================================")
    for r in results:
        print(f"{r['queueId']} | {r['company']} | {r['role'][:40]} -> {r['status']} ({r['reason']})")
        
    with open("d:\\newjobs\\batch4_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
