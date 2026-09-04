import json, os

store_file = r"<PROJECT_ROOT>\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

jq = store.get("JobQueue", [])
print(f"Current JobQueue: {len(jq)}")
for j in jq:
    print(f"  {j.get('company')} | {j.get('role')} | {j.get('experience_required')} | {j.get('location')}")
