import urllib.request
import json
import re
import os

SERVER_URL = "http://127.0.0.1:3000/log"
BASE_DIR = r"%USERPROFILE%\AppData\Local\BrowserClaw\Application\148.0.7988.97\.browseros\tool-output"

def post_job(job, index, total):
    payload = {
        "company": job["company"],
        "role": job["title"],
        "platform": job["platform"],
        "status": "DISCOVERED",
        "event": "Job Found",
        "progress": f"{index}/{total}",
        "jobUrl": job["url"],
        "canonicalUrl": job["url"],
        "location": job["location"],
        "workType": job.get("workType", "On-site"),
        "experienceRequired": job["experience"],
        "matchScore": job.get("matchScore", 75),
        "matchReason": job.get("matchReason", "React/JS skills match, experience within range"),
        "applicationMethod": "External Form",
        "postedDate": job.get("postedDate", "Recent"),
        "notes": job.get("notes", ""),
        "sheet": "JobQueue",
        "dateFound": job.get("postedDate", "Recent")
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(SERVER_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode())
        print(f"[{index}/{total}] POSTED: {job['company']} - {job['title']} -> {result.get('ts', 'OK')}")
        return True
    except Exception as e:
        print(f"[{index}/{total}] FAILED: {job['company']} - {job['title']} -> {e}")
        return False

def parse_naukri_md(filepath, city_label):
    jobs = []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    parts = re.split(r"\n## \[", content)
    for part in parts[1:]:
        lines = part.split("\n")
        if not lines:
            continue
        header_line = lines[0]
        title_match = re.match(r"(.+?)\]\((.+?)\)", header_line)
        if not title_match:
            continue
        title = title_match.group(1).strip()
        url = title_match.group(2).strip()
        company = ""
        experience = ""
        location = ""
        posted_date = ""
        for line in lines[1:20]:
            line = line.strip()
            if not line:
                continue
            if re.match(r"^\[.+?\]\(.+?\)", line) and not company:
                company_match = re.match(r"^\[(.+?)\]", line)
                if company_match:
                    company = company_match.group(1)
                    company = re.sub(r"\[\d+\.?\d*\]\(.*?\)", "", company).strip()
                    company = re.sub(r"\d+\s*Reviews", "", company).strip()
                    continue
            exp_match = re.search(r"(\d+[\-\d]*\s*Yrs|\d+\s*Yrs)", line)
            if exp_match and not experience:
                experience = exp_match.group(1).strip()
                continue
            if not location and re.search(r"Pune|Mumbai|Bengaluru|Hyderabad|Noida|Gurugram|Delhi|Chennai|Kolkata|Remote|Navi Mumbai|Churchgate|Andheri", line):
                loc_match = re.match(r"^([A-Za-z\s,\(\)\-]+?)(?:\s+[A-Z][a-z])", line)
                if loc_match:
                    location = loc_match.group(1).strip()
                else:
                    parts_loc = line.split(",")
                    location = parts_loc[0].strip() if parts_loc else line
                location = re.sub(r"\d+[\-\d]*\s*Yrs.*", "", location).strip()
                continue
            posted_match = re.search(r"(Just now|\d+ days? ago|\d+\+ weeks? ago|\d+ weeks? ago)", line)
            if posted_match and not posted_date:
                posted_date = posted_match.group(1)
                continue
        if title and company and url and "naukri.com" in url:
            jobs.append({
                "title": title,
                "company": company,
                "url": url,
                "location": location or city_label,
                "experience": experience or "Not specified",
                "postedDate": posted_date or "Recent",
                "platform": "Naukri",
                "workType": "On-site"
            })
    return jobs

def filter_jobs(jobs):
    filtered = []
    senior_keywords = ["senior", "lead", "staff", "principal", "architect", "manager", "director", "head of", "vp ", "vice president", "sde3", "sde ii", "sde2", "sde3"]
    for job in jobs:
        exp = job["experience"].lower()
        title = job["title"].lower()
        is_senior = any(kw in title or kw in exp for kw in senior_keywords)
        min_exp = 0
        max_exp = 10
        exp_range = re.search(r"(\d+)[\-–](\d+)", exp)
        if exp_range:
            min_exp = int(exp_range.group(1))
            max_exp = int(exp_range.group(2))
        elif re.search(r"^\d+\s*yrs?$", exp.strip()):
            min_exp = int(re.search(r"(\d+)", exp).group(1))
            max_exp = min_exp
        if is_senior and min_exp > 2:
            continue
        if min_exp <= 2 or max_exp <= 3 or (not is_senior and min_exp == 0):
            job["matchScore"] = 85 if min_exp <= 2 else (70 if max_exp <= 4 else 60)
            filtered.append(job)
        elif max_exp <= 4 and not is_senior:
            job["matchScore"] = 65
            filtered.append(job)
    return filtered

def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = (job["company"].lower(), job["title"].lower(), job["url"])
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique

if __name__ == "__main__":
    all_jobs = []
    
    # Use the Pune file which had the most 0-2 year jobs
    pune_file = os.path.join(BASE_DIR, "read-1786967847506-42cdcec4-7cb1-40ab-8fda-755e03b5741a.md")
    if os.path.exists(pune_file):
        jobs = parse_naukri_md(pune_file, "Pune")
        all_jobs.extend(jobs)
    
    # Also parse the second Pune file (the earlier one with 0 Yrs jobs)
    pune_file2 = os.path.join(BASE_DIR, "read-1786966755716-9609dfc7-455d-48e9-9b2a-ce53f17641c3.md")
    if os.path.exists(pune_file2):
        jobs = parse_naukri_md(pune_file2, "Pune")
        all_jobs.extend(jobs)
    
    filtered = filter_jobs(all_jobs)
    unique = deduplicate(filtered)
    unique.sort(key=lambda x: x.get("matchScore", 0), reverse=True)
    
    # Get jobs that are 0-2 years or 1-3 years specifically
    good_jobs = [j for j in unique if "0-" in j["experience"] or "1-" in j["experience"] or "0 Yrs" in j["experience"]]
    print(f"Found {len(good_jobs)} jobs with 0-2 or 1-3 years experience")
    
    # Take top 5
    final_jobs = good_jobs[:5]
    print(f"Selected {len(final_jobs)} additional jobs")
    
    success_count = 0
    for i, job in enumerate(final_jobs, 46):
        if post_job(job, i, 50):
            success_count += 1
    
    print(f"\nDone! Posted {success_count} additional jobs")
