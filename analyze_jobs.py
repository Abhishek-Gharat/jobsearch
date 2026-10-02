import json, os, re

store_file = r"D:\newjobs\control-center\data\excel-rows.json"
with open(store_file, "r", encoding="utf-8") as f:
    store = json.load(f)

apps = store.get("Applications", [])
job_events = [a for a in apps if a.get("event") == "Job Found"]
print(f"Total Job Found events: {len(job_events)}")

# Show experience distribution
from collections import Counter
exp_dist = Counter()
for j in job_events:
    exp = j.get("experience_required", j.get("postedDate", "Unknown"))
    exp_dist[exp] += 1

print("\nExperience distribution:")
for exp, count in sorted(exp_dist.items(), key=lambda x: -x[1]):
    print(f"  {exp}: {count}")

# Show jobs with low experience
print("\nJobs with 0-3 years or less:")
for j in job_events:
    exp = j.get("experience_required", "")
    if re.search(r"0[\-\s]?[0-3]|1[\-\s]?[0-3]", str(exp)):
        print(f"  {j['company']} - {j['role']} ({exp})")
