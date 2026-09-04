import urllib.request
import json
import re
import os
from datetime import datetime

SERVER_URL = "http://127.0.0.1:3000/log"
BASE_DIR = r"%USERPROFILE%\AppData\Local\BrowserClaw\Application\148.0.7988.97\.browseros\tool-output"

def post_job(job, index, total):
    payload = {
        "company": job["company"],
        "role": job.get("title", job.get("role", "Unknown")),
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
        "notes": job.get("notes", "")
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(SERVER_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode())
        print(f"[{index}/{total}] POSTED: {job['company']} - {job.get('title', job.get('role', 'Unknown'))} -> {result.get('ts', 'OK')}")
        return True
    except Exception as e:
        print(f"[{index}/{total}] FAILED: {job['company']} - {job.get('title', job.get('role', 'Unknown'))} -> {e}")
        return False

def parse_naukri_md(filepath, city_label):
    jobs = []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Split by ## headers which indicate job listings
    parts = re.split(r"\n## \[", content)
    for part in parts[1:]:
        lines = part.split("\n")
        if not lines:
            continue
        # First line has the title and URL
        header_line = lines[0]
        title_match = re.match(r"(.+?)\]\((.+?)\)", header_line)
        if not title_match:
            continue
        title = title_match.group(1).strip()
        url = title_match.group(2).strip()
        
        # Find company line (usually next non-empty line)
        company = ""
        experience = ""
        location = ""
        posted_date = ""
        
        for i, line in enumerate(lines[1:15], 1):
            line = line.strip()
            if not line:
                continue
            # Company line typically has [Company Name](url) pattern
            if re.match(r"^\[.+?\]\(.+?\)", line) and not company:
                company_match = re.match(r"^\[(.+?)\]", line)
                if company_match:
                    company = company_match.group(1)
                    # Remove rating suffixes
                    company = re.sub(r"\[\d+\.?\d*\]\(.*?\)", "", company).strip()
                    company = re.sub(r"\d+\s*Reviews", "", company).strip()
                    continue
            # Experience line: e.g., "0-5 Yrs", "2-4 Yrs", "1-3 Yrs", "0 Yrs"
            exp_match = re.search(r"(\d+[\-\d]*\s*Yrs|\d+\s*Yrs)", line)
            if exp_match and not experience:
                experience = exp_match.group(1).strip()
                continue
            # Location line: cities before the description
            if not location and re.search(r"Pune|Mumbai|Bengaluru|Hyderabad|Noida|Gurugram|Delhi|Chennai|Kolkata|Remote|Navi Mumbai|Churchgate|Andheri", line):
                # Extract just the location part (before the description)
                loc_match = re.match(r"^([A-Za-z\s,\(\)\-]+?)(?:\s+[A-Z][a-z])", line)
                if loc_match:
                    location = loc_match.group(1).strip()
                else:
                    location = line.split(" ")[0] + " " + line.split(" ")[1] if len(line.split(" ")) > 1 else line
                location = re.sub(r"\d+[\-\d]*\s*Yrs.*", "", location).strip()
                continue
            # Posted date: e.g., "1 week ago", "3+ weeks ago", "4 days ago", "Just now"
            posted_match = re.search(r"(Just now|\d+ days? ago|\d+\+ weeks? ago|\d+ weeks? ago)", line)
            if posted_match and not posted_date:
                posted_date = posted_match.group(1)
                continue
        
        if title and company and url:
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
    
    # LinkedIn format: - [Title](url) Company Location
    pattern = r"- \[(.+?)\]\((.+?)\)\s+([^\n]+?)\s+([^\n]+?)(?:\s+[\-\—]\s+|\s+)([\d+]+ hours? ago|[\d+]+ days? ago|[\d+]+ weeks? ago|Just now|Promoted|Viewed|Easy Apply|Remote|On-site|Hybrid|\d+ LPA|\$[\d]+[\-\$]*[\d]*\/hour|Contract|Full time|Part time|Internship)"
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
    senior_keywords = ["senior", "lead", "staff", "principal", "architect", "manager", "director", "head of", "vp ", "vice president", "sde3", "sde ii", "sde2", "sde3", "3+ years", "4+ years", "5+ years", "6+ years", "7+ years", "8+ years", "9+ years", "10+ years"]
    
    for job in jobs:
        exp = job["experience"].lower()
        title = job["title"].lower()
        
        # Check if it's a senior role
        is_senior = any(kw in title or kw in exp for kw in senior_keywords)
        
        # Extract min experience from string like "0-5 Yrs", "2-4 Yrs", "1-3 Yrs"
        min_exp = 0
        max_exp = 10
        exp_range = re.search(r"(\d+)[\-–](\d+)", exp)
        if exp_range:
            min_exp = int(exp_range.group(1))
            max_exp = int(exp_range.group(2))
        elif re.search(r"^\d+\s*yrs?$", exp.strip()):
            min_exp = int(re.search(r"(\d+)", exp).group(1))
            max_exp = min_exp
        
        # Accept if: 0-2 years, 1-3 years, or 2-4 years with strong match
        # Also accept if no experience specified and title doesn't say senior
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
    
    # Parse Naukri files
    naukri_files = [
        (os.path.join(BASE_DIR, "read-1786967847506-42cdcec4-7cb1-40ab-8fda-755e03b5741a.md"), "Pune"),
        (os.path.join(BASE_DIR, "read-1786967903711-b97ab64b-5607-4c70-88ef-40b54348dec4.md"), "Mumbai"),
        (os.path.join(BASE_DIR, "read-1786967449947-9fa5b635-fd4f-4b03-98e4-73976005e5bb.md"), "Bangalore"),
        (os.path.join(BASE_DIR, "read-1786968021520-9671ffa2-87fc-4b79-9ebb-c795905aedd0.md"), "Bangalore"),
    ]
    
    for filepath, city in naukri_files:
        if os.path.exists(filepath):
            jobs = parse_naukri_md(filepath, city)
            all_jobs.extend(jobs)
            print(f"Parsed {len(jobs)} jobs from {os.path.basename(filepath)}")
    
    # Parse Naukri remote page
    remote_file = os.path.join(BASE_DIR, "read-1786968110547-255f1883-fe64-4f9e-b3a6-5c6536b299f0.md")
    if os.path.exists(remote_file):
        jobs = parse_naukri_md(remote_file, "Remote")
        all_jobs.extend(jobs)
        print(f"Parsed {len(jobs)} jobs from remote Naukri")
    
    # Parse LinkedIn
    linkedin_file = os.path.join(BASE_DIR, "read-1786968161746-a997618e-d2e5-4a1c-8fb4-80469e532514.md")
    if os.path.exists(linkedin_file):
        jobs = parse_linkedin_md(linkedin_file)
        all_jobs.extend(jobs)
        print(f"Parsed {len(jobs)} jobs from LinkedIn")
    
    # Parse Wellfound
    wellfound_file = os.path.join(BASE_DIR, "read-178696???")
    # Wellfound only had 2 results and both were senior - skip for now
    
    print(f"\nTotal parsed: {len(all_jobs)}")
    
    # Filter and deduplicate
    filtered = filter_jobs(all_jobs)
    print(f"After filtering: {len(filtered)}")
    unique = deduplicate(filtered)
    print(f"After deduplication: {len(unique)}")
    
    # Sort by match score descending
    unique.sort(key=lambda x: x.get("matchScore", 0), reverse=True)
    
    # Take top 50
    final_jobs = unique[:50]
    print(f"Selected top {len(final_jobs)} jobs")
    
    # POST each job
    success_count = 0
    for i, job in enumerate(final_jobs, 1):
        if post_job(job, i, len(final_jobs)):
            success_count += 1
    
    print(f"\nDone! Successfully posted {success_count}/{len(final_jobs)} jobs")
