import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import queue_store

NEW = [
    {
        "company": "Creative Hands HR",
        "role": "Junior Developer",
        "jobUrl": "https://www.naukri.com/job-listings-junior-developer-creative-hands-hr-hyderabad-chennai-bengaluru-0-to-2-years-280926000987",
        "location": "Hyderabad, Bengaluru, Chennai",
        "experienceRequired": "0-2 Yrs",
        "postedDate": "Few hours ago",
        "matchScore": 88,
        "matchReason": "Posted <24h, 0-2 yrs entry band, software application development + code review fit",
        "applicationMethod": "Naukri Apply",
    },
    {
        "company": "Keyideas Infotech",
        "role": "Software Developer (Trainee - Paid, Fulltime)",
        "jobUrl": "https://www.naukri.com/job-listings-hiring-software-developer-trainee-paid-fulltime-role-keyideas-infotech-gurugram-0-to-1-years-210126019271",
        "location": "Remote (Gurugram hiring office)",
        "experienceRequired": "0-1 Yrs",
        "postedDate": "Just now",
        "matchScore": 84,
        "matchReason": "Remote, 4-5 LPA, React/NodeJS/MongoDB in stack; requires public coding profile (GitHub ready)",
        "applicationMethod": "Naukri Apply",
    },
    {
        "company": "Meroxio IT Solutions Pvt. Ltd.",
        "role": "Shopify Web Designer & Front-End Developer",
        "jobUrl": "https://www.naukri.com/job-listings-frontend-web-developer-meroxio-it-solutions-noida-0-to-1-years-270926001642",
        "location": "Noida (Sector 63)",
        "experienceRequired": "0-1 Yrs",
        "postedDate": "1 day ago",
        "matchScore": 80,
        "matchReason": "Entry-level frontend, Liquid/HTML/CSS/JS + Figma-to-code, e-commerce focus",
        "applicationMethod": "Naukri Apply",
    },
    {
        "company": "Kahan Technologies",
        "role": "Technical Developer",
        "jobUrl": "https://www.naukri.com/job-listings-technical-developer-kahan-technologies-bengaluru-0-to-5-years-280926001652",
        "location": "Bengaluru",
        "experienceRequired": "0-5 Yrs",
        "postedDate": "Just now",
        "matchScore": 74,
        "matchReason": "Fresh posting, wide experience band accepts 1 yr candidate",
        "applicationMethod": "Naukri Apply",
    },
    {
        "company": "Leading Software Company (Mohan Jothi)",
        "role": "Software Developer",
        "jobUrl": "https://www.naukri.com/job-listings-software-developer-mohana-jothi-ambattur-0-to-1-years-280926002244",
        "location": "Ambattur, Chennai",
        "experienceRequired": "0-1 Yrs",
        "postedDate": "Just now",
        "matchScore": 76,
        "matchReason": "0-1 yrs fresher-friendly dev role, posted minutes ago",
        "applicationMethod": "Naukri Apply",
    },
    {
        "company": "Web Spiders",
        "role": "AI Automation Engineer",
        "jobUrl": "https://www.naukri.com/job-listings-ai-automation-engineer-web-spiders-jaipur-0-to-3-years-280926006185",
        "location": "Jaipur",
        "experienceRequired": "0-3 Yrs",
        "postedDate": "Just now",
        "matchScore": 68,
        "matchReason": "Python/automation leaning; secondary fit only",
        "applicationMethod": "Naukri Apply",
    },
    {
        "company": "JobTrade",
        "role": "Full-stack Developer",
        "jobUrl": "https://www.linkedin.com/jobs/view/4471325045/",
        "location": "Noida, Uttar Pradesh (On-site)",
        "experienceRequired": "Entry/Associate",
        "postedDate": "4 hours ago",
        "matchScore": 82,
        "matchReason": "LinkedIn f_TPR=r86400 hit, non-promoted, entry level, React/Node stack",
        "applicationMethod": "LinkedIn Easy Apply",
    },
    {
        "company": "Sahara Cyber Tech",
        "role": "Full-Stack Developer",
        "jobUrl": "https://www.linkedin.com/jobs/view/4470804668/",
        "location": "Jamnagar, Gujarat (On-site)",
        "experienceRequired": "Entry/Associate",
        "postedDate": "13 hours ago",
        "matchScore": 78,
        "matchReason": "LinkedIn past-24h hit, full-stack React/Node fit",
        "applicationMethod": "LinkedIn Easy Apply",
    },
    {
        "company": "Confidential (Naukri)",
        "role": "React Native Developer",
        "jobUrl": "https://www.naukri.com/job-listings-react-native-developer-confidential-noida-1-to-2-years-270926003233",
        "location": "Remote (Noida)",
        "experienceRequired": "1-2 Yrs",
        "postedDate": "1 day ago",
        "matchScore": 80,
        "matchReason": "Remote React role matching 1.2 yrs experience; verify employer on detail page",
        "applicationMethod": "Naukri Apply",
    },
]


def main() -> int:
    rows = queue_store.load(quiet=True)
    seen = {(r.get("jobUrl") or "").split("?")[0].strip().lower() for r in rows}
    b_ids = [
        int(r["queueId"][1:])
        for r in rows
        if str(r.get("queueId", "")).startswith("B") and str(r["queueId"])[1:].isdigit()
    ]
    nxt = max(b_ids) + 1 if b_ids else 110
    added = 0
    for job in NEW:
        key = job["jobUrl"].split("?")[0].lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "queueId": f"B{nxt:03d}",
                "company": job["company"],
                "role": job["role"],
                "jobUrl": job["jobUrl"],
                "canonicalUrl": job["jobUrl"],
                "location": job["location"],
                "experienceRequired": job["experienceRequired"],
                "postedDate": job["postedDate"],
                "status": "READY",
                "matchScore": job["matchScore"],
                "matchReason": job["matchReason"],
                "applicationMethod": job["applicationMethod"],
                "submissionDate": "",
                "failureReason": "",
            }
        )
        print(f"  + [{f'B{nxt:03d}'}] {job['company']} - {job['role']} ({job['postedDate']})")
        nxt += 1
        added += 1
    if added:
        queue_store.save(rows, queue_store.QUEUE)
    print(f"Added {added}; queue now {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
