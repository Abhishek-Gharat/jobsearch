# RUN NEXT — BrowserOS job-application run

Audit date: 2026-09-03

> **UPDATE 2026-09-17 — read this before using sections 3 and 6 below.**
>
> `D:\newjobs\excel-rows.json` was found **corrupted** (half-written JSON: the
> array ended with a trailing `},` and no closing `]`), so `json.loads` failed for
> every consumer. It has been repaired and now parses.
>
> What survived: **5 rows — Q031, Q033, Q034, Q035, Q036** (3 PENDING_HUMAN,
> 1 SUBMITTED, 1 NOT_PROCESSED).
>
> **Q001–Q030 are gone.** The truncated file did not contain them and no other
> artifact records their URLs/fields, so they cannot be reconstructed without
> inventing data (forbidden by `starterprompt.md` §17). They are **not** available
> in `control-center/data/excel-rows.json` either — that store's `JobQueue` is a
> *different* run (2026-08-17, mostly Naukri) that reuses the same Q001–Q050 ids but
> maps them to different companies (e.g. Q004 there is "Yugasys Software", not
> "Frontline Data Solutions"). Merging the two would corrupt both.
>
> Consequently **section 3 and section 6 below are historical, not current.**
> Re-run discovery per section 7 to rebuild the queue.
>
> The write path is now guarded — always save through `queue_store.py`, never by
> writing the file directly (see `starterprompt.md` §15a).
>
> **MCP endpoint note (2026-09-17).** BrowserOS neo serves the same browser
> instance on more than one port and the port has changed over time: `9211` (what
> this doc used to say) is **dead**, `9210` and `9010` are **both live and
> identical** (same window id, same tabs, `browseros-neo` v0.0.50). Verify before
> running the pipeline:
>
> ```bash
> python -c "import bos; b=bos.BOS('probe'); print('ok', bos.PORT)"
> ```
>
> `bos.py` now defaults to 9010 and honours `BROWSEROS_PORT` / `BROWSEROS_HOST`.
> Note that the three outreach scripts (`whatsapp_outreach.py`,
> `recruiter_outreach.py`, `hiring_lead_hunter.py`) still hardcode `9210`; they
> currently work but will break when that listener goes away.
>
> Also: an MCP page is **owned by the session that created it**. A later session
> can `tabs list` and see it, but cannot snapshot, read or act on it — everything a
> flow needs must happen inside the one process that opened the tab.


## 1. Blocking prerequisite

**Open a NEW WorkBuddy conversation before running anything.**

The `browseros-neo` MCP was configured and trusted during the current session, but MCP
servers register at session start, so its 20 tools are not callable here. Config and trust
are already saved and persist — only a new chat is needed, no re-trust, no app restart.

Sanity check in the new chat: ask it to list your browser tabs. A real tab list = tools live.

## 2. Environment status (verified)

| Item | Status |
|---|---|
| BrowserOS neo MCP | connected — `http://127.0.0.1:9010/mcp` (port moves; 9210 also live, 9211 dead), v0.0.50, 20 tools |
| LinkedIn login | **live** — "Alex Morgan, Frontend Developer @ Hridayam Soft Solutions, Mumbai, Premium" |
| Naukri login | **live** — multiple prior `myapply/showAcp` confirmation tabs |
| Resume | `D:\newjobs\Resume.pdf` |
| Profile | `D:\newjobs\Job_Profile.TEMPLATE.md` |

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
Read D:\newjobs\starterprompt.md, D:\newjobs\Job_Profile.TEMPLATE.md and
D:\newjobs\Resume.pdf. Run job discovery for the LAST 7 DAYS, target 10 jobs,
from Naukri + LinkedIn + Wellfound only. Use BrowserOS neo for all browsing.

Save results to D:\newjobs\excel-rows.json and D:\newjobs\job-findings.md.
Do NOT apply to anything. When done, show me the ranked shortlist and stop for my review.
```

### Phase 2 — apply (paste only after you approve the shortlist)

```
Run D:\newjobs\exicutionrules.md against the 10 jobs just discovered in
D:\newjobs\excel-rows.json, sequentially, using BrowserOS neo.
Resume: D:\newjobs\Resume.pdf.
Follow the state machine exactly: OPEN -> SNAPSHOT -> ACT -> SNAPSHOT -> VERIFY -> LOG -> NEXT.
Max 90s per job, 3 recovery attempts per interaction, fresh snapshot after every
navigation, click and redirect. Stop for me on CAPTCHA/OTP and mark PENDING_HUMAN.
Append every non-completed job to D:\newjobs\failed-jobs.md with reason and URL.
Report submitted / pending human / failed / skipped / not processed counts.
```
