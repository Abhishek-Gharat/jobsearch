import urllib.request, json, os
from datetime import datetime

# Read the excel store to see what jobs were saved
store_file = r"<PROJECT_ROOT>\control-center\data\excel-rows.json"
if os.path.exists(store_file):
    with open(store_file, "r", encoding="utf-8") as f:
        store = json.load(f)
    jobs = store.get("JobQueue", [])
    print(f"Total jobs in queue: {len(jobs)}")
    print("\nFirst 10 jobs:")
    for j in jobs[:10]:
        print(f"  {j.get('company', 'N/A')} | {j.get('role', 'N/A')} | {j.get('location', 'N/A')} | {j.get('experience_required', 'N/A')} | {j.get('source', 'N/A')}")
    print("\nLast 10 jobs:")
    for j in jobs[-10:]:
        print(f"  {j.get('company', 'N/A')} | {j.get('role', 'N/A')} | {j.get('location', 'N/A')} | {j.get('experience_required', 'N/A')} | {j.get('source', 'N/A')}")
else:
    print("Store file not found")
