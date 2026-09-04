# Job Search Automation Toolkit

Browser-assisted job discovery, application tracking, recruiter outreach, and Telegram reporting.

> **Setup is template-based.** This repo contains NO personal data. Copy every `*.example.*` / `*.TEMPLATE.*` file to its real name (those are git-ignored) and fill in your own details.

## 1. Your profile (required)

- Copy `Job_Profile.TEMPLATE.md` to `My_Job_Profile.md` and replace all placeholders (`Your Name`, `your.email@example.com`, `+91 98XXXXXXXX`, links, CTC, education, experience).
- Put your resume at `Resume.pdf` (git-ignored, never committed).
- `bos.py` resolves paths relative to the repo root; update `FIELD_VALUES` with your own details.

## 2. Telegram reports (optional)

```powershell
copy .telegram.example.json .telegram.json
python telegram_reports.py setup --token <TOKEN> --chat <CHAT_ID>
```

## 3. Recruiter outreach (optional)

```powershell
copy outreach\outreach_config.example.json outreach\outreach_config.json
```
Fill in Gmail address + app password (or set `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` env vars). Sends are manual-only by design.

## 4. Engine configs (optional)

```powershell
copy autoapply\config.example.json autoapply\config.json
copy autoapply\sniper_config.example.json autoapply\sniper_config.json
```

## 5. Control center (optional)

```powershell
cd control-center
copy .env.example .env
npm install
npm start
```

## Never commit

`My_Job_Profile.md`, `Resume.pdf`, `.env`, `.telegram.json`, `outreach/outreach_config.json`, `excel-rows.json`, `browser_profile/` — all covered by `.gitignore`.
