# AutoApply v2 — Overnight Fault-Tolerant Application System

OpenCode CLI (`opencode run`, model `opencode/x-preview-f-free`) + BrowserOS MCP + OX Alpha vision.
Built for **unattended overnight execution** of the job queue with strict evidence-based accounting.

## Guarantees

1. Every processed job ends in EXACTLY ONE terminal state:
   `submitted | skipped | failed | failed_unconfirmed | review_required`
2. Max recoverable loss at any moment: ~60 seconds (results written per-job immediately,
   checkpoint + progress flushed every poll <=10s, watchdog acts within 120s).
3. Exactly-N cap honored: `max_jobs_per_run: 200`. Stops after 200 processed OR when
   pending queue empties; if fewer than 200 exist the final report states the exact number.
4. No duplicate processing: integrity verification scans all results; double-submits are
   flagged as INTEGRITY FAIL in `final_report.json`.

## Live dashboard (any time)

```
python supervisor.py --status
```
Shows: Applied / Pending / Failed / Failed-unconfirmed / Review-required / Skipped /
Current Job (id+step+url) / Current Batch / ETA / Watchdog state / Heartbeat age /
Restart count / MCP status / Runtime. Also mirrored continuously to `status_live.json`.
Tail activity live: `powershell -Command "Get-Content heartbeat.log -Wait -Tail 5"`

## Dynamic batching (Phase 4)

Portal classes size their own batches (config `batch_size_by_portal`):
LinkedIn Easy Apply = 8 with 120s inter-batch pacing; Greenhouse/Lever/Ashby = 12;
Workday/Taleo/SmartRecruiters/Workable/iCIMS/Recruiterflow/Zoho/custom ATS = 5 with 45s pacing.
Every batch = a FRESH short-lived `opencode run` worker. A hang costs one batch only:
watchdog kills the tree after 120s without heartbeat, restarts up to 2x, then honestly
fails the stragglers. Batch ceiling scales with batch size (600s + 240s/job).

## Recovery matrix (Phases 3 & 7)

| Failure | Automatic response |
|---|---|
| Page stuck >30s | worker reloads once |
| Still stuck at budget (150s/job) | skipped(hang), next job |
| Popup/modal/chat widget | auto-closed |
| CAPTCHA checkbox-type | screenshot -> click -> verify, max 30s |
| CAPTCHA hard types | review_required -> review_queue.json |
| Login wall / OTP / MFA | review_required -> review_queue.json |
| MCP disconnect | wait 10s, retry, reconnect, resume; 2nd failure -> mcp_down batch abort |
| Browser crash mid-job | reopen tab, re-navigate, continue same job |
| Worker freeze / no heartbeat | supervisor taskkill /T /F + restart batch from checkpoint |
| OpenCode exit != 0 or no done-flag | counted as restart, batch retried |
| Supervisor killed | resume.bat requeues orphan in_progress jobs, continues |
| OS sleep | supervisor holds ES_SYSTEM_REQUIRED awake flag while running |

## Submission evidence rules (Phase 6)

Counted `submitted` ONLY on: "Application Submitted" / "Thank you for applying" /
application ID / confirmation page-dialog / visible confirmation email.
NEVER counted: "Application Saved" / "Draft Saved" / "Continue Later" ->
those become `failed_unconfirmed` (retried once, then final).

## Files

jobs.json (queue; statuses above) · checkpoint.json (v2: last_completed stats,
current_job {url,step}, worker_id, restart_count, history) · failed_jobs.json (final
failures registry) · review_queue.json (CAPTCHA/login-wall jobs awaiting human) ·
heartbeat.log (liveness) · events.jsonl (full audit: started/resumed/submitted/skipped/
failed/review/restart+reason/batch lifecycle) · results/*.jsonl + *.progress.json +
*.done · batches/*.json (immutable manifests) · logs/ (console + per-worker stdout)
· status_live.json (dashboard mirror) · final_report.json (end-of-run report).

## Morning procedure

1. `python supervisor.py --status`
2. Open `final_report.json`: totals, restart history, runtime, integrity verdict,
   guarantee line ("every processed job has a recorded final state ...").
3. Work `review_queue.json` items manually (CAPTCHAs/login walls - forms often already filled).
4. Requeue anything you disagree with: edit its status back to "pending".

## Notes

- Worker model is pinned to free tier `opencode/x-preview-f-free` (verified PONG + MCP).
  Change via `opencode_model` in config.json; ALWAYS smoke-test first:
  `opencode run --model <id> "Reply with exactly one word: PONG"`
- Keep the laptop plugged in; screen may sleep, system stays awake by design.
- To stop mid-night: close the minimized `autoapply-supervisor` window or
  `taskkill /IM python.exe` — resume.bat continues safely afterwards.
