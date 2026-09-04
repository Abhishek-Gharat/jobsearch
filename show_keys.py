import json, os

store_file = r"<PROJECT_ROOT>\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

apps = store.get("Applications", [])
print(f"Current Applications: {len(apps)}")
for a in apps[:5]:
    print(f"  Keys: {list(a.keys())}")
    print(f"  Data: {a}")
    print()
