import json, os

store_file = r"D:\newjobs\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

apps = store.get("Applications", [])
job_events = [a for a in apps if a.get("event") == "Job Found"]
print(f"Total Job Found events: {len(job_events)}")

# Check for exact duplicates
seen = set()
dupes = 0
for j in job_events:
    key = (j.get("company", ""), j.get("role", ""))
    if key in seen:
        dupes += 1
        print(f"  DUPLICATE: {j.get('company')} - {j.get('role')}")
    seen.add(key)

print(f"\nExact duplicates (same company+role): {dupes}")
print(f"Unique jobs: {len(seen)}")

# Show all unique jobs
print("\nAll unique jobs:")
for i, (company, role) in enumerate(sorted(seen), 1):
    print(f"  {i}. {company} - {role}")
