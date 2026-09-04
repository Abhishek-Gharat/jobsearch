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
        print(f"[{index}/{total}] POSTED: {job['company']} - {job['title']} ({job['experience']}) -> {result.get('ts', 'OK')}")
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
        
        for line in lines[1:30]:
            line = line.strip()
            if not line:
                continue
            if not experience:
                exp_match = re.search(r"(\d+[\-\d]*\s*Yrs|\d+\s*Yrs)", line)
                if exp_match:
                    experience = exp_match.group(1).strip()
            if not company and re.match(r"^\[.+?\]\(.+?\)", line):
                company_match = re.match(r"^\[(.+?)\]", line)
                if company_match:
                    company = company_match.group(1)
                    company = re.sub(r"\[\d+\.?\d*\]\(.*?\)", "", company).strip()
                    company = re.sub(r"\d+\s*Reviews", "", company).strip()
                    continue
            if not location and re.search(r"Pune|Mumbai|Bengaluru|Hyderabad|Noida|Gurugram|Delhi|Chennai|Kolkata|Remote|Navi Mumbai|Churchgate|Andheri", line):
                clean_line = re.sub(r"\d+[\-\d]*\s*Yrs", "", line).strip()
                loc_match = re.match(r"^([A-Za-z\s,\(\)\-]+?)(?:\s+[A-Z][a-z])", clean_line)
                if loc_match:
                    location = loc_match.group(1).strip()
                else:
                    parts_loc = clean_line.split(",")
                    location = parts_loc[0].strip() if parts_loc else clean_line
                location = re.sub(r"\d+[\-\d]*\s*Yrs.*", "", location).strip()
                continue
            if not posted_date:
                posted_match = re.search(r"(Just now|\d+ days? ago|\d+\+ weeks? ago|\d+ weeks? ago)", line)
                if posted_match:
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

def parse_linkedin_md(filepath):
    jobs = []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    matches = re.findall(r"- \[(.+?)\]\((.+?)\)\s+([^\n]+?)\s+([^\n]+)", content)
    for m in matches:
        title, url, company, location = m[0], m[1], m[2].strip(), m[3].strip()
        company = company.replace("Promoted", "").replace("Viewed", "").strip()
        if title and company and url and "jobs/view/" in url:
            jobs.append({
                "title": title,
                "company": company,
                "url": url,
                "location": location,
                "experience": "Not specified",
                "postedDate": "Recent",
                "platform": "LinkedIn",
                "workType": "Remote" if "Remote" in location else "On-site"
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
        # Deduplicate by company + role + normalized title
        key = (job["company"].lower(), job["title"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique

if __name__ == "__main__":
    all_jobs = []
    
    naukri_files = [
        (os.path.join(BASE_DIR, "read-1786967847506-42cdcec4-7cb1-40ab-8fda-755e03b5741a.md"), "Pune"),
        (os.path.join(BASE_DIR, "read-1786967903711-b97ab64b-5607-4c70-88ef-40b54348dec4.md"), "Mumbai"),
        (os.path.join(BASE_DIR, "read-1786967449947-9fa5b635-fd4f-4b03-98e4-73976005e5bb.md"), "Bangalore"),
        (os.path.join(BASE_DIR, "read-1786968021520-9671ffa2-87fc-4b79-9ebb-c795905aedd0.md"), "Bangalore"),
        (os.path.join(BASE_DIR, "read-1786968110547-255f1883-fe64-4f9e-b3a6-5c6536b299f0.md"), "Remote"),
        (os.path.join(BASE_DIR, "read-1786966755716-9609dfc7-455d-48e9-9b2a-ce53f17641c3.md"), "Pune"),
    ]
    
    for filepath, city in naukri_files:
        if os.path.exists(filepath):
            jobs = parse_naukri_md(filepath, city)
            all_jobs.extend(jobs)
            print(f"Parsed {len(jobs)} jobs from {os.path.basename(filepath)}")
    
    linkedin_file = os.path.join(BASE_DIR, "read-1786968161746-a997618e-d2e5-4a1c-8fb4-80469e532514.md")
    if os.path.exists(linkedin_file):
        jobs = parse_linkedin_md(linkedin_file)
        all_jobs.extend(jobs)
        print(f"Parsed {len(jobs)} jobs from LinkedIn")
    
    print(f"\nTotal parsed: {len(all_jobs)}")
    
    filtered = filter_jobs(all_jobs)
    print(f"After filtering: {len(filtered)}")
    unique = deduplicate(filtered)
    print(f"After deduplication: {len(unique)}")
    
    # Prioritize 0-2 and 1-3 years
    def sort_key(j):
        exp = j["experience"]
        score = j.get("matchScore", 0)
        if "0-" in exp or "0 Yrs" in exp:
            return (0, -score)
        elif "1-" in exp:
            return (1, -score)
        elif "2-" in exp:
            return (2, -score)
        else:
            return (3, -score)
    
    unique.sort(key=sort_key)
    final_jobs = unique[:50]
    print(f"Selected top {len(final_jobs)} jobs")
    
    # Show distribution
    exp_counts = {}
    for j in final_jobs:
        exp = j["experience"]
        exp_counts[exp] = exp_counts.get(exp, 0) + 1
    print(f"\nExperience distribution:")
    for exp, count in sorted(exp_counts.items(), key=lambda x: -x[1]):
        print(f"  {exp}: {count}")
    
    # Show platforms
    platforms = {}
    for j in final_jobs:
        p = j["platform"]
        platforms[p] = platforms.get(p, 0) + 1
    print(f"\nPlatform distribution:")
    for p, count in sorted(platforms.items(), key=lambda x: -x[1]):
        print(f"  {p}: {count}")
    
    success_count = 0
    for i, job in enumerate(final_jobs, 1):
        if post_job(job, i, len(final_jobs)):
            success_count += 1
    
    print(f"\nDone! Successfully posted {success_count}/{len(final_jobs)} jobs to JobQueue")
