# RUN NEXT — BrowserOS job-application run

Audit date: 2026-09-03

## 1. Blocking prerequisite

**Open a NEW WorkBuddy conversation before running anything.**

The `browseros-neo` MCP was configured and trusted during the current session, but MCP
servers register at session start, so its 20 tools are not callable here. Config and trust
are already saved and persist — only a new chat is needed, no re-trust, no app restart.

Sanity check in the new chat: ask it to list your browser tabs. A real tab list = tools live.

## 2. Environment status (verified)

| Item | Status |
|---|---|
| BrowserOS neo MCP | connected, `http://127.0.0.1:9211/mcp`, v0.0.50, 20 tools |
| LinkedIn login | **live** — "Your Name, Frontend Developer @ Example Company A, Mumbai, Premium" |
| Naukri login | **live** — multiple prior `myapply/showAcp` confirmation tabs |
| Resume | `<PROJECT_ROOT>\Resume.pdf` |
| Profile | `<PROJECT_ROOT>\Job_Profile.TEMPLATE.md` |

## 3. Queue status — `excel-rows.json` (30 jobs)

| Status | Count | Notes |
|---|---|---|
| SUBMITTED | 6 | done |
| FAILED | 4 | Q001, Q003 (micro1.ai phone input), Q005 (no Apply button), Q006 (CVViZ upload) |
| PENDING_HUMAN | 1 | **Q030 Machstatz — CAPTCHA. All fields filled + resume attached.** |
| **unprocessed** | **19** | all LinkedIn, posted 2026-08-16/17 |

`pipeline-state.json` is empty — the pipeline was reset, so no in-flight state to recover.

## 4. Key finding: the backlog is probably dead

All 19 pending jobs were posted **2026-08-16/17** — about 17 days old. Spot-checked 3:

- Q004 Frontline Data Solutions — **still live**
- Q020 UnknwnAI Ltd — **"no longer accepting applications"**
- Q022 Synthires — **"no longer accepting applications"**

Do not burn a full run on the old queue before validating it. Expect heavy attrition.

## 5. Recommended path

**Step 1 (2 min, do now, no tools needed)** — finish Q030 Machstatz by hand. It is sitting on a
CAPTCHA with everything else already filled and the resume attached. Find that tab in BrowserOS.

**Step 2 (new conversation) — DECIDED.** Run fresh discovery per `starterprompt.md`:
**last 7 days, target 10 jobs**, from Naukri + LinkedIn + Wellfound only.

Treated as an end-to-end pipeline trial before scaling up. Note that `starterprompt.md` forbids
applying during discovery — the discovery agent only does
FIND → VALIDATE → MATCH → DEDUPLICATE → RANK → SAVE. It writes to `excel-rows.json` and
`job-findings.md`, then stops. Applying is a separate phase driven by `exicutionrules.md`.

**Step 3** — run the surviving queue through `exicutionrules.md` sequentially:
`OPEN → SNAPSHOT → ACT → SNAPSHOT → VERIFY → LOG → NEXT`. Max 90s per job, 3 recovery
attempts per interaction, fresh snapshot after every navigation/click/redirect.

**Step 4** — all non-completed jobs get appended to `failed-jobs.md` with reason + URL. Never
drop a job from the master queue.

## 6. The 19 unprocessed jobs

| ID | Company | Role | Method |
|---|---|---|---|
| Q004 | Frontline Data Solutions | React/Next.JS Front-End Developer | Company Website |
| Q007 | Wake Up Whistle | Frontend Developer Intern | LinkedIn / Company |
| Q008 | ArGo Intern | Frontend Developer Intern | LinkedIn / Company |
| Q010 | DIMIYA Tech | Solutions Architect — Web, React | LinkedIn Apply |
| Q011 | MediNex Workforce | Full Stack Developer Intern | LinkedIn / Company |
| Q012 | Vortenza Systems | Front-End Development Intern | LinkedIn / Company |
| Q013 | MediNex Workforce | Web Developer Intern | LinkedIn / Company |
| Q014 | Hire Feed | UI Engineer (Remote) | LinkedIn / Company |
| Q015 | Hire Feed | Full-Stack Developer (Remote) | Company Website |
| Q016 | Zenithbyte | Full Stack Web Developer Intern | LinkedIn / Company |
| Q017 | Vortenza Systems | Web Development Intern | LinkedIn / Company |
| Q018 | MediNex Workforce | Front End Developer Intern | LinkedIn / Company |
| Q019 | GoodSpace AI | Software Engineer | LinkedIn / Company |
| Q020 | UnknwnAI Ltd | Full Stack Developer | LinkedIn Apply |
| Q021 | Hirely | Software Engineer — Remote Contract | LinkedIn / Company |
| Q022 | Synthires | Software Engineer (Remote) | LinkedIn Apply |
| Q023 | Quik Hire Staffing | Software Engineer (Remote) | LinkedIn Apply |
| Q024 | MediNex Workforce | HTML/CSS Developer Intern | LinkedIn / Company |
| Q025 | Quik Hire Staffing | Node.js Developer (Remote) | LinkedIn / Company |

## 7. Paste-ready prompts for the new conversation

### Phase 1 — discovery (paste this first)

```
Read <PROJECT_ROOT>\starterprompt.md, <PROJECT_ROOT>\Job_Profile.TEMPLATE.md and
<PROJECT_ROOT>\Resume.pdf. Run job discovery for the LAST 7 DAYS, target 10 jobs,
from Naukri + LinkedIn + Wellfound only. Use BrowserOS neo for all browsing.

Save results to <PROJECT_ROOT>\excel-rows.json and <PROJECT_ROOT>\job-findings.md.
Do NOT apply to anything. When done, show me the ranked shortlist and stop for my review.
```

### Phase 2 — apply (paste only after you approve the shortlist)

```
Run <PROJECT_ROOT>\exicutionrules.md against the 10 jobs just discovered in
<PROJECT_ROOT>\excel-rows.json, sequentially, using BrowserOS neo.
Resume: <PROJECT_ROOT>\Resume.pdf.
Follow the state machine exactly: OPEN -> SNAPSHOT -> ACT -> SNAPSHOT -> VERIFY -> LOG -> NEXT.
Max 90s per job, 3 recovery attempts per interaction, fresh snapshot after every
navigation, click and redirect. Stop for me on CAPTCHA/OTP and mark PENDING_HUMAN.
Append every non-completed job to <PROJECT_ROOT>\failed-jobs.md with reason and URL.
Report submitted / pending human / failed / skipped / not processed counts.
```
