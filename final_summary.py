import json, os

store_file = r"D:\newjobs\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

jq = store.get("JobQueue", [])
print(f"Total jobs in JobQueue: {len(jq)}")
print("\nAll 50 jobs:")
for i, j in enumerate(jq, 1):
    print(f"{i:2}. {j.get('company', 'N/A'):<35} | {j.get('role', 'N/A'):<45} | {j.get('experience_required', 'N/A'):<12} | {j.get('location', 'N/A'):<20} | {j.get('source', 'N/A')}")

# Count by platform
from collections import Counter
platforms = Counter(j.get("source", "Unknown") for j in jq)
print(f"\nCount by platform:")
for p, c in platforms.most_common():
    print(f"  {p}: {c}")

# Count by experience
exp_counts = Counter(j.get("experience_required", "Unknown") for j in jq)
print(f"\nExperience distribution:")
for exp, count in sorted(exp_counts.items(), key=lambda x: -x[1]):
    print(f"  {exp}: {count}")
