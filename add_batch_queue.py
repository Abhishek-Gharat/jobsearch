import json
from datetime import datetime
import queue_store

new_jobs = [
    {
        "queueId": "B01",
        "company": "Unico Connect",
        "role": "Frontend Developer (React)",
        "location": "Mumbai, India",
        "workType": "On-site / Hybrid",
        "experience": "1-2 years",
        "jobUrl": "https://unicoconnect.com/careers/frontend-react-developer",
        "canonicalUrl": "https://unicoconnect.com/careers/frontend-react-developer",
        "platform": "Company Careers",
        "applicationMethod": "Email Application",
        "contactEmail": "careers@unicoconnect.com",
        "matchScore": 95,
        "matchReason": "React, Next.js, TypeScript production skills match junior requirements",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Direct email application to careers@unicoconnect.com. Junior role in Mumbai."
    },
    {
        "queueId": "B02",
        "company": "Ubikon Technologies",
        "role": "Full Stack Developer (Node.js + React)",
        "location": "Indore / Remote",
        "workType": "Remote / Hybrid",
        "experience": "1-3 years",
        "jobUrl": "https://ubikon.in/careers/full-stack-developer",
        "canonicalUrl": "https://ubikon.in/careers/full-stack-developer/apply",
        "platform": "Company Careers",
        "applicationMethod": "Direct Form",
        "matchScore": 92,
        "matchReason": "Next.js, React, Node.js, Express, PostgreSQL/MongoDB stack alignment",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "4-step application form active on ubikon.in/careers/full-stack-developer/apply"
    },
    {
        "queueId": "B03",
        "company": "FactWise",
        "role": "Frontend Developer",
        "location": "Mumbai, India",
        "workType": "In-Office",
        "experience": "2 years",
        "jobUrl": "https://wellfound.com/jobs/3939364-frontend-developer",
        "canonicalUrl": "https://wellfound.com/jobs/3939364-frontend-developer",
        "platform": "Wellfound",
        "applicationMethod": "Wellfound Direct Apply",
        "matchScore": 90,
        "matchReason": "React with TypeScript architecture, UI design, fast feedback loop",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Active Wellfound listing. ₹6L - ₹20L salary bracket."
    },
    {
        "queueId": "B04",
        "company": "India Tech Engine",
        "role": "Full Stack Developer (React.js + Node.js)",
        "location": "Mumbai / Remote",
        "workType": "Remote / Hybrid",
        "experience": "2 years",
        "jobUrl": "https://wellfound.com/jobs/1092868-mern-full-stack-developer",
        "canonicalUrl": "https://wellfound.com/jobs/1092868-mern-full-stack-developer",
        "platform": "Wellfound",
        "applicationMethod": "Wellfound Direct Apply",
        "matchScore": 92,
        "matchReason": "React.js, Next.js, Node.js, Express, MongoDB/PostgreSQL match profile projects",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Active Wellfound listing. Competitive compensation ($25k-$35k)."
    },
    {
        "queueId": "B05",
        "company": "Thalmaar",
        "role": "Full Stack AI Engineer - India",
        "location": "Pune / Mumbai / Remote",
        "workType": "Remote / Hybrid",
        "experience": "2+ years / Project portfolio",
        "jobUrl": "https://wellfound.com/jobs/4572904-full-stack-ai-engineer-india",
        "canonicalUrl": "https://wellfound.com/jobs/4572904-full-stack-ai-engineer-india",
        "platform": "Wellfound",
        "applicationMethod": "Wellfound Direct Apply",
        "matchScore": 88,
        "matchReason": "React.js, Next.js, PostgreSQL, modern AI-assisted engineering workflows",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Active Wellfound listing. Welcomes strong project/internship experience. ₹5L-₹10L."
    },
    {
        "queueId": "B06",
        "company": "Stylabs Technologies",
        "role": "Full Stack Developer",
        "location": "Mumbai / Remote",
        "workType": "Remote / Hybrid",
        "experience": "2 years",
        "jobUrl": "https://wellfound.com/jobs/3081258-full-stack-developer",
        "canonicalUrl": "https://wellfound.com/jobs/3081258-full-stack-developer",
        "platform": "Wellfound",
        "applicationMethod": "Wellfound Direct Apply",
        "matchScore": 90,
        "matchReason": "React, Next.js, Node.js, PostgreSQL, MongoDB, AI pair programming workflows",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Active Wellfound listing. ₹6L - ₹13L compensation."
    },
    {
        "queueId": "B07",
        "company": "Webdura Technologies",
        "role": "Junior Frontend Developer",
        "location": "Kochi / Remote",
        "workType": "Remote / On-site",
        "experience": "0-1 years",
        "jobUrl": "https://infopark.in/companies/job-search/",
        "canonicalUrl": "https://webdura.in/careers",
        "platform": "Infopark / Company",
        "applicationMethod": "Email Application",
        "contactEmail": "careers@webdura.in",
        "matchScore": 95,
        "matchReason": "0-1 years exp, React, JavaScript, HTML/CSS, Redux, TypeScript, REST API",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Email application to careers@webdura.in / hr01@webdura.tech. HR phone: +91 87147 46555."
    },
    {
        "queueId": "B08",
        "company": "AlignMinds Technologies",
        "role": "Full Stack Developer (React & Python FastAPI)",
        "location": "Kochi / Remote",
        "workType": "Remote / On-site",
        "experience": "1-2 years",
        "jobUrl": "https://infopark.in/companies/job-search/",
        "canonicalUrl": "https://alignminds.com",
        "platform": "Infopark / Company",
        "applicationMethod": "Email Application",
        "contactEmail": "careers@alignminds.com",
        "matchScore": 85,
        "matchReason": "1-2 years exp, React.js frontend, REST APIs, Git, rapid product delivery",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Email application to careers@alignminds.com."
    },
    {
        "queueId": "B09",
        "company": "Dot In Technologies",
        "role": "Junior React.js Developer",
        "location": "Cherthala / Remote",
        "workType": "Remote / On-site",
        "experience": "0-1 years",
        "jobUrl": "https://infopark.in/companies/job-search/",
        "canonicalUrl": "https://infopark.in",
        "platform": "Infopark / Company",
        "applicationMethod": "Email Application",
        "contactEmail": "dotintechnologies@gmail.com",
        "matchScore": 92,
        "matchReason": "0-1 years entry level React.js, Redux, RESTful APIs, Git, Next.js plus",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "Send resume to dotintechnologies@gmail.com with subject: 'Application for Junior React.js Developer'."
    },
    {
        "queueId": "B10",
        "company": "Nymbl",
        "role": "Frontend Developer (React & Next.js)",
        "location": "Kochi / Remote",
        "workType": "Remote / On-site",
        "experience": "1-2 years",
        "jobUrl": "https://infopark.in/companies/job-search/",
        "canonicalUrl": "https://infopark.in",
        "platform": "Infopark / Company",
        "applicationMethod": "Email Application",
        "contactEmail": "krishnapriya@benymbl.co",
        "matchScore": 95,
        "matchReason": "React 18, Next.js (App Router/SSR), TypeScript, Tailwind CSS, Ant Design",
        "status": "READY",
        "dateFound": datetime.now().strftime("%Y-%m-%d"),
        "notes": "HR Lead Krishnapriya (+91 77366 98555). Job code CHKP-KOCHI/2026/FE/React&Next."
    }
]

existing_jobs = queue_store.load(queue_store.QUEUE, quiet=False)
existing_ids = {j.get("queueId") for j in existing_jobs}

to_add = [j for j in new_jobs if j["queueId"] not in existing_ids]

if to_add:
    updated_queue = existing_jobs + to_add
    queue_store.save(updated_queue, queue_store.QUEUE)
    print(f"Successfully added {len(to_add)} new jobs to {queue_store.QUEUE}")
else:
    print("All jobs already present in queue.")
