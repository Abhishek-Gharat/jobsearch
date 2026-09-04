import json, os

store_file = r"<PROJECT_ROOT>\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

# Keep only the Agent Started event in Applications
store["Applications"] = [a for a in store.get("Applications", []) if a.get("event") == "Agent Started"]
store["JobQueue"] = []

with open(store_file, "w", encoding="utf-8") as f:
    json.dump(store, f, indent=2)

print("Cleared Applications and JobQueue")
print(f"Applications: {len(store['Applications'])}")
print(f"JobQueue: {len(store['JobQueue'])}")
