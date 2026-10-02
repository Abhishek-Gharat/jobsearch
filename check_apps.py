import urllib.request, json, os

store_file = r"D:\newjobs\control-center\data\excel-rows.json"
if os.path.exists(store_file):
    with open(store_file, "r", encoding="utf-8") as f:
        store = json.load(f)
    apps = store.get("Applications", [])
    print(f"Total applications logged: {len(apps)}")
    print("\nFirst 10 applications:")
    for a in apps[:10]:
        print(f"  {a.get('company', 'N/A')} | {a.get('role', 'N/A')} | {a.get('status', 'N/A')} | {a.get('platform', 'N/A')}")
else:
    print("Store file not found")
