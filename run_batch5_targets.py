import bos
import time
import sys
import json
import queue_store
import fast_apply_engine

sys.stdout.reconfigure(encoding="utf-8")

def main():
    with open("d:\\newjobs\\batch5_targets.json", "r", encoding="utf-8") as f:
        targets = json.load(f)
        
    print(f"Loaded {len(targets)} targets from batch5_targets.json")
    
    current_jobs = queue_store.load()
    existing_urls = {j.get("jobUrl", "").split("?")[0] for j in current_jobs}
    
    # Filter out any duplicate URLs
    fresh = [t for t in targets if t["url"].split("?")[0] not in existing_urls]
    batch = fresh[:10]
    
    print(f"Executing Batch 5 on {len(batch)} jobs:")
    for idx, b in enumerate(batch, 1):
        print(f"[{idx}] {b['company']} — {b['role']} -> {b['url']}")
        
    existing_b_ids = [int(j["queueId"][1:]) for j in current_jobs if j.get("queueId", "").startswith("B") and j["queueId"][1:].isdigit()]
    next_num = max(existing_b_ids) + 1 if existing_b_ids else 28
    
    engine = fast_apply_engine.FastApplyEngine()
    results = []
    
    print("\n========================================================")
    print(f"STARTING BATCH 5 AUTONOMOUS APPLY ({len(batch)} JOBS)")
    print("========================================================")
    
    for item in batch:
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
    print("BATCH 5 EXECUTION COMPLETE")
    print("========================================================")
    for r in results:
        print(f"{r['queueId']} | {r['company']} | {r['role'][:40]} -> {r['status']} ({r['reason']})")
        
    with open("d:\\newjobs\\batch5_final_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
