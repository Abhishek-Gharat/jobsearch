# Kilo Bridge

A VS Code extension that lets the **Job Control Center** (`D:\newjobs\control-center`)
trigger **Kilo Code** tasks automatically — no AutoHotkey, no screen
coordinates, no mouse clicks.

```
Telegram (iPhone)
        ↓
Job Control Center (Node.js)
        ↓
Task Queue
        ↓ HTTP POST http://127.0.0.1:3011/run
Kilo Bridge (this extension)
        ↓ VS Code Extension API + verified prompt delivery
Kilo Code
        ↓ BrowserOS MCP
Job automation
        ↓ Telegram status updates + Excel logging
```

## How it works

The bridge runs a local HTTP server inside VS Code. On `POST /run` it
delivers the prompt to Kilo Code and **verifies delivery against Kilo's
session database before reporting success**:

1. `kilo-code.SidebarProvider.focus` — reveal the Kilo chat view
2. `kilo-code.new.plusButtonClicked` — start a **New Task**
3. `kilo-code.new.focusChatInput` (retried) — focus the chat textarea
4. `vscode.env.clipboard.writeText(prompt)` — official clipboard API
5. Windows SendKeys `^v` + `{ENTER}` — a real paste + Enter into the
   focused webview textarea (browser-native; **no screen coordinates** —
   the window is targeted by process parent chain, not position)
6. **Verification** — poll `~/.local/share/kilo/kilo.db` (WAL first)
   until the exact prompt text appears as a persisted chat message. If it
   does not appear, the whole delivery is retried once, then reported as
   an honest failure.

Why SendKeys and not pure commands: Kilo has **no command that accepts an
arbitrary prompt** (verified against the bundle — see `COMPATIBILITY.md`),
and VS Code's paste/type commands silently no-op when the webview holds
focus. This is the documented last-resort in the design brief: VS Code
command execution → VS Code UI automation → keyboard simulation.

## Install (VSIX - normal VS Code, not a dev host)

```powershell
# one-time build
npx -y @vscode/vsce package --allow-missing-repository

# install into the normal VS Code installation
code --install-extension D:\newjobs\kilo-bridge\kilo-bridge-1.0.0.vsix --force
```

Then **reload** VS Code (`Developer: Reload Window`). The bridge activates
on VS Code startup (`onStartupFinished`) and listens on `127.0.0.1:3011`
automatically - no dev host needed.

Single instance: only the first window to bind port 3011 serves HTTP;
every other window logs `EADDRINUSE` and disables its server (expected).

Logs: `D:\newjobs\kilo-bridge\logs\bridge.log` (rotated at 1 MB, kept as
`bridge.log.old`), plus the `Kilo Bridge` output channel and `GET /debug`.

## API

### `POST http://127.0.0.1:3011/run`

```json
{ "prompt": "Find 5 React jobs in Pune posted this week." }
```

Success (only after the prompt is confirmed in Kilo's session DB):

```json
{ "ok": true, "verified": true }
```

Failure:

```json
{ "ok": false, "reason": "prompt was pasted but not confirmed in Kilo (webview busy?) after 15000ms" }
```

The launch is retried once on failure.

### `POST http://127.0.0.1:3011/event`

Status mirror from the Control Center (shown in the bridge output channel
and status bar):

```json
{ "event": "Searching Jobs", "company": "-", "taskId": 4 }
```

### `GET http://127.0.0.1:3011/healthz`

```json
{ "ok": true, "kilo": true, "bridge": true }
```

`kilo` is `true` when the Kilo Code extension is loaded and its chat
commands are registered. `bridge` is `true` when this instance serves HTTP.

### `GET http://127.0.0.1:3011/status`

```json
{
  "ok": true, "service": "kilo-bridge", "kiloDetected": true,
  "running": false,
  "lastRun": { "at": "...", "ok": true, "verified": true, "reason": "", "prompt": "..." },
  "lastEvent": "#4 Searching Jobs", "eventCount": 12, "uptimeSec": 3600
}
```

### `GET http://127.0.0.1:3011/health` · `GET /debug` · `GET /commands?q=...`

Health / last-300 log lines / registered-command lookup.

## Verify

```powershell
Invoke-RestMethod -Method GET http://127.0.0.1:3011/health
Invoke-RestMethod -Method GET http://127.0.0.1:3011/status
Invoke-RestMethod -Method POST -ContentType "application/json" `
  -Body '{"prompt":"Reply with OK only."}' http://127.0.0.1:3011/run
```

`ok:true` means the prompt is **confirmed in Kilo** (chat visible in the
Kilo sidebar; session persisted). See `VALIDATION.md` for the full
end-to-end PASS/FAIL runbook.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `kilobridge.host` / `port` | `127.0.0.1` / `3011` | Bridge address |
| `kilobridge.sendMode` | `sendkeys` | `sendkeys` = verified SendKeys delivery (reliable). `commands` = paste/type commands (may silently no-op; kept for experimentation) |
| `kilobridge.settleMs` | `2000` | Wait for the Kilo chat view to render after New Task |
| `kilobridge.verifyMs` | `15000` | How long to poll Kilo's session DB for the prompt before failing |
| `kilobridge.focusViewCommand` / `newTaskCommand` / `focusInputCommand` | Kilo defaults | Kilo commands used for focus/new-task |
| `kilobridge.pasteCommands` | `["editor.action.clipboardPasteAction","webview.paste"]` | Paste ladder for `commands` mode |
| `kilobridge.eventMirror` | `true` | Mirror Control Center events in bridge log/status bar |

## Requirements

- VS Code with **Kilo Code** (`kilocode.kilo-code`) installed and enabled
- Control Center running: `node server.js` in `D:\newjobs\control-center`
- Kilo configured with the **BrowserOS MCP** server (already done)
- Kilo signed in with a working model provider (shared account config)

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `kiloDetected:false` | Kilo Code not installed/enabled in that window |
| `{ok:false}` with paste/sendKeys step failed | Check `GET /debug` for the exact step; verify no other window owns 3011 |
| `{ok:false}` "not confirmed in Kilo" | Kilo webview busy or focus stolen; the bridge already retried once — retry `/run` |
| Keys land in the wrong window | Only possible on non-Windows or multi-monitor focus edge cases; `sendKeys` targets the hosting window via `process.ppid` ancestor walk |
| Kilo asks for tool approval | Enable `kilo-code.new.autoApprove.enabled` for unattended runs |

## Files

- `extension.js` — the bridge (HTTP server + delivery + verification)
- `COMPATIBILITY.md` — Kilo command/API audit with live test results
- `VALIDATION.md` — end-to-end validation runbook with PASS/FAIL evidence
- `.vscode\launch.json` / `.vscode\tasks.json` — F5 debugging