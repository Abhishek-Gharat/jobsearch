import json, os

store_file = r"D:\newjobs\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

# Show current applications
apps = store.get("Applications", [])
print(f"Current Applications: {len(apps)}")
for a in apps:
    print(f"  {a.get('company')} | {a.get('role')} | {a.get('postedDate')} | {a.get('experience_required', 'N/A')}")
