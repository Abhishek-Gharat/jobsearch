import json, os

store_file = r"<PROJECT_ROOT>\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

# Remove test entry
store["Applications"] = [a for a in store.get("Applications", []) if a.get("company") != "Test"]

# Remove duplicates - keep first occurrence of each company+role
seen = set()
cleaned = []
for a in store.get("Applications", []):
    key = (a.get("company", ""), a.get("role", ""))
    if key not in seen:
        seen.add(key)
        cleaned.append(a)
store["Applications"] = cleaned

# Also clean JobQueue
seen_jq = set()
cleaned_jq = []
for a in store.get("JobQueue", []):
    key = (a.get("company", ""), a.get("role", ""))
    if key not in seen_jq:
        seen_jq.add(key)
        cleaned_jq.append(a)
store["JobQueue"] = cleaned_jq

with open(store_file, "w", encoding="utf-8") as f:
    json.dump(store, f, indent=2)

print(f"Cleaned Applications: {len(store['Applications'])}")
print(f"Cleaned JobQueue: {len(store['JobQueue'])}")
