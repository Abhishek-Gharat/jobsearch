# Telegram → Job Hunt Reports

`telegram_reports.py` pushes every report in `D:\newjobs` to a Telegram chat.
Stdlib only — no pip install. Works with any Python 3.8+.
 opencode -s ses_f74bda74fffew0HdMmlRbNcTB7
---

## Part 1 — Get your credentials (2 min, one time)

### 1. Create the bot
1. Open Telegram, search **@BotFather**, press Start.
2. Send `/newbot`
3. Pick a display name → `Job Hunt Reports`
4. Pick a username → must end in `bot`, e.g. `my_jobhunt_bot`
5. BotFather replies with an **HTTP API token** that looks like:
   `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
   Copy it. That's your `--token`.

### 2. Activate the chat
Open your new bot in Telegram (search the username you just made) and **press Start** —
or send it any message. Telegram won't deliver anything until you do this.

### 3. Get your chat ID
```bash
python telegram_reports.py whoami --token 7123456789:AAFxxxx...
```
It prints the chats that have talked to your bot. Copy the numeric ID (e.g. `987654321`).
That's your `--chat`.

> If `whoami` says "No updates yet", you skipped step 2 — message the bot first.

### 4. Save both
```bash
python telegram_reports.py setup --token 7123456789:AAFxxxx... --chat 987654321
```
Saved to `D:\newjobs\.telegram.json` (chmod 600). Env vars
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` override it if you'd rather not store on disk.

### 5. Verify
```bash
python telegram_reports.py status
python telegram_reports.py test
```
You should get a "connected ✅" message in Telegram.

## Part 1b — Two-way control (optional)

`telegram_bot.py` lets you **drive the repo from your phone**. It uses long
polling, so there is no webhook, no public IP, no port forwarding and no
HTTPS certificate — it works behind NAT on your laptop as-is.

```bash
python telegram_bot.py
```

Leave it running. Then message your bot:

| Command | What it does |
|---|---|
| `/help` | Command list |
| `/digest` | Combined daily summary |
| `/list` | All 21 reports with freshness ("12d ago") |
| `/send queue` | One report — or just type `queue` |
| `/send tracker --doc` | One report + attach the raw file |
| `/send all` | Every report |
| `/status` | Pipeline + bot health |
| `/watch on` / `/watch 60` / `/watch off` | Push a report the instant its file changes |
| `/pause` `/resume` | Mute / unmute pushes |
| `/logs` | Last 20 log lines |
| `/ping` | Latency + command count |

**Read-only by design** — the bot never submits job applications. Reporting and
monitoring are safe to run from a phone; firing an auto-apply run unattended is
not (CAPTCHA walls, wrong submissions, no undo). Say the word if you want that
wired up behind a confirm step.

**Security:** only the `chat_id` in `.telegram.json` is accepted. Every other
sender is silently ignored and logged. Someone who finds your bot's @username
cannot use it. A PID lock stops a second instance from fighting over polling.

---

## Part 2 — Commands

| Command | What it does |
|---|---|
| `python telegram_reports.py status` | Config + bot/chat reachability check |
| `python telegram_reports.py list` | Show all 21 reports with last-updated time |
| `python telegram_reports.py test` | Send a test message |
| `python telegram_reports.py digest` | **The combined daily digest** (queue + pipeline + velocity + blockers) |
| `python telegram_reports.py send queue` | One report by key |
| `python telegram_reports.py send tracker --doc` | One report + attach the raw file |
| `python telegram_reports.py send-all` | Every report, text only |
| `python telegram_reports.py send-all --doc` | Every report + all raw files attached |
| `python telegram_reports.py watch --interval 120` | Live: push a report the moment its file changes |
| `python telegram_reports.py pause` / `resume` | Mute / unmute without losing config |

---

## Part 3 — The 21 reports

**Live / operational** (rendered from JSON into readable summaries)

| Key | Source | Contents |
|---|---|---|
| `queue` | `excel-rows.json` | Status breakdown, needs-you items, failures, backlog |
| `live` | `autoapply/status_live.json` | Pipeline phase, worker, ETA, watchdog health |
| `dashboard` | `autoapply/dashboard.json` | Velocity, response rate, top companies |
| `failed` | `autoapply/failed_jobs.json` | Failed jobs grouped by ATS portal |
| `outcomes` | `autoapply/outcomes.json` | Every application outcome by state |
| `analytics` | `autoapply/analytics.json` | Funnel + conversion rates |
| `provider` | `autoapply/provider_performance_report.json` | Which ATS boards actually convert |
| `discovery` | `autoapply/weekly_discovery_report.json` | New jobs by source/portal/company |
| `final` | `autoapply/final_report.json` | Last run report |
| `pipeline` | `pipeline-state.json` | In-flight pipeline state |

**Markdown reports** (heading outline + preview; `--doc` attaches the full file)

| Key | Source |
|---|---|
| `runnext` | `RUN-NEXT.md` |
| `tracker` | `job-application-tracker.md` |
| `findings` | `job-findings.md` |
| `weekly` | `autoapply/weekly_report.md` |
| `morning` | `autoapply/morning_brief.md` |
| `experiment` | `autoapply/weekly_experiment_report.md` |
| `duplicates` | `autoapply/duplicate_audit.md` |
| `gmail` | `autoapply/gmail_duplicate_report.md` |
| `portal` | `frontend_job_hunt_full_portal_report.md` |
| `remote` | `remote-companies-tracking.md` |
| `behavior` | `behavior-report.md` |

---

## Part 4 — Notes

- **Long messages** auto-split at 3,800 chars and numbered `1/3`, `2/3`. Telegram's hard limit is 4,096.
- **Rate limits** are handled — 429s wait `retry_after` and retry; sends are spaced 0.6s apart.
- **Failures are non-fatal** — a bad report never blocks the others.
- **Logs** go to `D:\newjobs\telegram_reports.log`.
- **Shared credentials** — `autoapply/telegram_notifier.py` (the job sniper's live alerts)
  now reads the same `.telegram.json`, so you configure Telegram once for the whole project.
- **Secrets** — `.telegram.json` holds your bot token. Don't commit it.

## Part 5 — Automate it

Daily digest every morning at 09:00:
```
Automation: recurring, FREQ=DAILY;BYHOUR=9;BYMINUTE=0
Prompt: Run `python D:\newjobs\telegram_reports.py digest`
```

Weekday evening wrap-up with every report attached:
```
Automation: recurring, FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=21;BYMINUTE=0
Prompt: Run `python D:\newjobs\telegram_reports.py send-all --doc`
```

Live push while the pipeline runs:
```bash
python telegram_reports.py watch --interval 120
```
