import sys
import json
import time
import bos

sys.stdout.reconfigure(encoding="utf-8")

def main():
    b = bos.BOS("dbg")
    with open("d:\\newjobs\\unapplied_react_targets.json", "r", encoding="utf-8") as f:
        jobs = json.load(f)

    js = """() => {
        const text = document.body ? document.body.innerText.toLowerCase() : '';
        if (text.includes('already applied') || text.includes("you've already applied") || text.includes('applied to')) return 'ALREADY_APPLIED';
        const applyBtn = Array.from(document.querySelectorAll('button, a')).find(b => (b.innerText||'').trim().toLowerCase() === 'apply');
        if (applyBtn) return 'DIRECT_APPLY';
        const extBtn = Array.from(document.querySelectorAll('button, a')).find(b => (b.innerText||'').toLowerCase().includes('apply on company site'));
        if (extBtn) return 'EXTERNAL_SITE';
        return 'NO_BUTTON';
    }"""

    results = []
    print(f"Checking apply types for {len(jobs)} jobs...")
    for idx, j in enumerate(jobs, 1):
        u = j["href"]
        b.call("navigate", {"page": 4, "url": u})
        time.sleep(2.5)
        res, _ = b.call("evaluate", {"page": 4, "func": js})
        status = "UNKNOWN"
        for s in ["ALREADY_APPLIED", "DIRECT_APPLY", "EXTERNAL_SITE", "NO_BUTTON"]:
            if s in res:
                status = s
                break
        print(f"[{idx}] {j['comp']} | {j['title']} -> {status}")
        results.append({**j, "apply_type": status})

    with open("d:\\newjobs\\react_targets_with_apply_types.json", "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2)

if __name__ == "__main__":
    main()
