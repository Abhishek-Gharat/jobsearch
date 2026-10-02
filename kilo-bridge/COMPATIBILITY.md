# Kilo Code Compatibility Report

**Extension:** `kilocode.kilo-code` **7.4.22** (win32-x64)
**VS Code:** 1.133.0
**Date:** 2026-08-17
**Method:** static inspection of `dist/extension.js` + `dist/webview.js` at
`C:\Users\ASUS\.vscode\extensions\kilocode.kilo-code-7.4.22-win32-x64`,
then **live execution** of every candidate command from the Kilo Bridge
Extension Development Host, with outcome verified against Kilo's session
database (`~/.local/share/kilo/kilo.db`).

## 1. Contributed API surface

The extension contributes views/webviews (chat sidebar, agent manager,
diff panels), settings, and 60+ commands. Full command list from
`registerCommand` in the bundle:

```
kilo-code.new.addToContext
kilo-code.new.agentManager.*            (worktree/tab/terminal/PR ops)
kilo-code.new.autocomplete.*
kilo-code.new.cycleAgentMode
kilo-code.new.cyclePreviousAgentMode
kilo-code.new.explainCode
kilo-code.new.fixCode
kilo-code.new.focusChatInput
kilo-code.new.generateCommitMessage
kilo-code.new.generateTerminalCommand
kilo-code.new.historyButtonClicked
kilo-code.new.improveCode
kilo-code.new.kiloClawOpen
kilo-code.new.marketplaceButtonClicked
kilo-code.new.openInTab
kilo-code.new.plusButtonClicked
kilo-code.new.profileButtonClicked
kilo-code.new.reload
kilo-code.new.settingsButtonClicked
kilo-code.new.sidebarTitle.*
kilo-code.new.terminalAddToContext
kilo-code.new.terminalExplainCommand
kilo-code.new.terminalFixCommand
kilo-code.new.toggleAutoApprove
kilo-code.new.toggleChatSearch
kilo-code.new.toggleMemory
kilo-code.new.toggleRemote
kilo-code.SidebarProvider.focus
```

## 2. Is there a command that accepts an arbitrary prompt?

**NO.** Verified by exhaustive search of every `registerCommand` call in the
bundle: no command takes a prompt/text parameter. The closest candidates
(`explainCode`, `fixCode`, `improveCode`, `addToContext`,
`generateTerminalCommand`, …) all **build their prompt internally** from
editor/terminal context. None accepts free text.

## 3. Live execution results (Extension Development Host, verified)

| Command | Result | Proof |
| --- | --- | --- |
| `kilo-code.SidebarProvider.focus` | PASS | executes, reveals chat; Kilo CLI backend spawns |
| `kilo-code.new.plusButtonClicked` | PASS | executes; webview handles it (newTaskRequest → new session view) |
| `kilo-code.new.focusChatInput` | PASS | executes; webview handler `focusInput` → `textarea.focus()` |
| `editor.action.clipboardPasteAction` | **NO-OP** | resolves `false` when no editor is open (command's `run()` returns false instead of throwing). Does NOT deliver text into the webview textarea. |
| `type` (`{text:"\n"}`) | **NO-OP** | requires `textInputFocus` (workbench textarea), which is false while the webview iframe holds focus. Resolves silently; no key event reaches the webview. |
| `webview.paste` | **NOT REGISTERED** | command does not exist in VS Code 1.133 (`/commands?q=webview.` returns only `workbench.action.webview.*`). |

## 4. Why UI-level delivery is required

Kilo's prompt submission is a **webview → extension** message
(`{type:"sendMessage", text, sessionID, ...}` → `handleSendMessage`), and
text injection into the input is `{type:"setChatBoxMessage"}` /
`{type:"appendChatBoxMessage"}` — both handled **only inside the webview
iframe** (`case "sendMessage"`, `handleImportAndSend`, `onBridgeMessage`
only handles `openFile`). A third-party extension cannot postMessage into
another extension's webview. Therefore delivery must happen at the UI
level: focus the chat textarea (works via command), then deliver a real
paste + Enter.

## 5. Delivered mechanism (Kilo Bridge)

1. `kilo-code.SidebarProvider.focus` — reveal chat
2. `kilo-code.new.plusButtonClicked` — new task
3. `kilo-code.new.focusChatInput` (x3, retried) — focus textarea
4. `vscode.env.clipboard.writeText(prompt)` — official clipboard API
5. Windows SendKeys `^v` then `{ENTER}` — real browser-native paste +
   submit into the focused webview textarea (AppActivate targets the
   window hosting the extension via `process.ppid` ancestor walk; **no
   screen coordinates**)
6. **Verification:** poll Kilo's session DB (`~/.local/share/kilo/kilo.db`,
   WAL first) until the exact prompt text appears as a persisted text part.
   `{ok:true}` is returned ONLY after confirmation; otherwise one full
   retry, then an honest failure.

### Verification evidence (kilo.db)

| Time (local) | Session | First text part |
| --- | --- | --- |
| 13:13:08 | `ses_ff153650effe` "OK Confirmation Request" | `Reply with OK only.` |
| 13:17:29 | `ses_ff14f73baffe` "OK confirmation request" | `Reply with OK only.` |
| 13:17:49 | `ses_ff14f1cffffe` "React job postings report" | `Browse job postings on the jobs portal. …` (task #3 via control center) |

## 6. Settings of interest

| Setting | Default | Note |
| --- | --- | --- |
| `kilo-code.new.browserAutomation.enabled` | `false` | Kilo UI automation; not required by the bridge |
| `kilo-code.new.autoApprove.enabled` | `false` | Manual tool approval; BrowserOS MCP actions may prompt — recommend enabling for unattended runs |

## 7. Failures found

- `editor.action.clipboardPasteAction` / `type` silently no-op on webview
  focus — resolved `false` masked as success by naive `executeCommand`
  wrappers. The bridge now treats `false` as failure.
- `webview.paste` does not exist in VS Code 1.133.
- First-send flakiness (webview busy mid-response) — solved by
  DB-verified delivery + one full retry.