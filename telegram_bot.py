#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_bot.py — control the <PROJECT_ROOT> job repository from Telegram.

Two-way link. `telegram_reports.py` pushes reports OUT; this bot takes commands IN.

Uses long polling (getUpdates). No webhook, no public IP, no port forwarding,
no HTTPS certificate required — it works behind NAT on your laptop as-is.

RUN
    python telegram_bot.py

Then message your bot:
    /help               command list
    /digest             combined daily digest
    /list               all 21 reports
    /send queue         one report
    /send tracker --doc one report + attach the raw file
    /sendall            every report
    /status             pipeline + bot health
    /watch on           push a report the moment its file changes
    /pause  /resume     mute / unmute pushes
    /logs               last 20 log lines

Commands also work without the leading slash ("digest", "send queue").

SECURITY
    Only the chat_id in .telegram.json is accepted. Every other sender is
    ignored and logged. Anyone who learns your bot's @username cannot use it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import telegram_reports as TR  # noqa: E402

ROOT = TR.ROOT
STATE_PATH = ROOT / ".telegram_bot_state.json"
LOCK_PATH = ROOT / ".telegram_bot.lock"
POLL_TIMEOUT = 30          # Telegram long-poll seconds
CMD_COOLDOWN = 1.5         # min seconds between accepted commands

HELP = """🤖 <b>Job Hunt Bot</b>

<b>Reports</b>
/digest — combined daily summary
/list — all 21 reports + timestamps
/send &lt;key&gt; — one report
/send &lt;key&gt; --doc — one report + attach raw file
/sendall — every report
/sendall --doc — every report + all files

<b>Quick keys</b>
<code>queue</code> <code>live</code> <code>dashboard</code> <code>failed</code>
<code>outcomes</code> <code>analytics</code> <code>provider</code> <code>discovery</code>
<code>runnext</code> <code>tracker</code> <code>weekly</code> <code>portal</code>

<b>Control</b>
/status — pipeline + bot health
/watch on|off|&lt;secs&gt; — live push on file change
/pause — mute pushes
/resume — unmute
/logs — last 20 log lines
/ping — latency check

Commands work with or without the leading <code>/</code>.
Bot is <b>read-only by design</b> — it never submits job applications.
"""


# ----------------------------------------------------------------------
# State
# ----------------------------------------------------------------------
def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"offset": 0, "started": 0}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


# ----------------------------------------------------------------------
# Single-instance lock
# ----------------------------------------------------------------------
def acquire_lock() -> bool:
    if LOCK_PATH.exists():
        try:
            pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or 0)
        except Exception:
            pid = 0
        if pid and _pid_alive(pid):
            return False
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID exists on this machine."""
    try:
        if os.name == "nt":
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10).stdout or ""
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------
# Background watcher
# ----------------------------------------------------------------------
class Watcher(threading.Thread):
    """Pushes a report to Telegram whenever its source file changes."""

    def __init__(self, interval: int = 90):
        super().__init__(daemon=True)
        self.interval = max(15, interval)
        self._stop = threading.Event()
        self.hits = 0

    def stop(self):
        self._stop.set()

    def run(self):
        watched = {k: p for k, (_, p, _) in TR.REPORTS.items() if p.exists()}
        seen = {}
        for k, p in watched.items():
            try:
                st = p.stat()
                seen[k] = (st.st_mtime, st.st_size)
            except OSError:
                pass
        TR._log(f"watcher active: {len(watched)} files, {self.interval}s")
        while not self._stop.is_set():
            if self._stop.wait(self.interval):
                break
            for k, p in list(watched.items()):
                try:
                    st = p.stat()
                except OSError:
                    continue
                cur = (st.st_mtime, st.st_size)
                if seen.get(k) != cur:
                    seen[k] = cur
                    time.sleep(2)                     # debounce the writer
                    got = TR.render(k)
                    if got:
                        self.hits += 1
                        TR._log(f"watcher -> change in {k}")
                        TR.send("🔄 <b>auto-update</b> · <code>" + TR.esc(k) + "</code>")
                        TR.send(got[0], silent=True)
                        time.sleep(TR.SEND_GAP)


# ----------------------------------------------------------------------
# Command handling
# ----------------------------------------------------------------------
class Bot:
    def __init__(self, allowed_chat: str):
        self.allowed = str(allowed_chat)
        self.watcher: Watcher | None = None
        self.last_cmd = 0.0
        self.count = 0

    # -- helpers ---------------------------------------------------
    def typing(self):
        try:
            TR.api("sendChatAction", {"chat_id": self.allowed, "action": "typing"}, timeout=10)
        except Exception:
            pass

    def reply(self, text: str, silent: bool = False):
        return TR.send(text, silent=silent, chat_id=self.allowed)

    # -- dispatch --------------------------------------------------
    def handle(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        now = time.time()
        if now - self.last_cmd < CMD_COOLDOWN:
            time.sleep(CMD_COOLDOWN - (now - self.last_cmd))
        self.last_cmd = time.time()
        self.count += 1

        parts = text.split()
        cmd = parts[0].lstrip("/").lower()
        args = parts[1:]

        table = {
            "start": self.c_help, "help": self.c_help, "?": self.c_help,
            "digest": self.c_digest, "d": self.c_digest,
            "list": self.c_list, "reports": self.c_list,
            "send": self.c_send, "get": self.c_send,
            "sendall": self.c_sendall, "all": self.c_sendall,
            "status": self.c_status, "health": self.c_status,
            "watch": self.c_watch,
            "pause": self.c_pause, "mute": self.c_pause,
            "resume": self.c_resume, "unmute": self.c_resume,
            "logs": self.c_logs, "log": self.c_logs,
            "ping": self.c_ping,
        }

        # bare report key -> send that report  ("queue", "live", ...)
        if cmd in TR.REPORTS:
            return self.c_send([cmd] + args)

        fn = table.get(cmd)
        if not fn:
            self.reply(f"❓ Unknown command <code>{TR.esc(cmd)}</code>\n\n"
                       f"Send <code>/help</code> for the command list, or "
                       f"<code>/list</code> to see report keys.")
            return
        try:
            fn(args)
        except Exception as exc:
            TR._log(f"command '{cmd}' crashed: {exc}")
            self.reply(f"💥 <code>{TR.esc(cmd)}</code> failed: <code>{TR.esc(str(exc)[:300])}</code>")

    # -- commands ---------------------------------------------------
    def c_help(self, _a=None):
        self.reply(HELP)

    def c_ping(self, _a=None):
        t0 = time.time()
        me = TR.api("getMe")
        ms = int((time.time() - t0) * 1000)
        name = f"@{me.get('username')}" if me else "unreachable"
        self.reply(f"🏓 pong · {TR.esc(name)} · {ms}ms · {self.count} commands served")

    def c_digest(self, _a=None):
        self.typing()
        n = self.reply(TR.build_digest())
        if not n:
            self.reply("⚠️ digest failed to send — check the log.")

    def c_list(self, _a=None):
        lines = [f"📚 <b>{len(TR.REPORTS)} reports</b>", ""]
        for key, (title, path, _) in TR.REPORTS.items():
            mark = "✓" if path.exists() else "✗"
            fresh = _age(path)
            lines.append(f"{mark} <code>{TR.esc(key)}</code> — {TR.esc(title)} <i>({TR.esc(fresh)})</i>")
        lines.append("")
        lines.append("<i>/send &lt;key&gt; — or just type the key</i>")
        self.reply("\n".join(lines))

    def c_send(self, args):
        if not args:
            self.reply("Usage: <code>/send &lt;key&gt; [--doc]</code>\n"
                       "Keys: <code>/list</code>")
            return
        key = args[0].lower()
        doc = "--doc" in [a.lower() for a in args[1:]]
        if key == "all":
            return self.c_sendall(args[1:])
        if key not in TR.REPORTS:
            close = [k for k in TR.REPORTS if k.startswith(key[:2])]
            hint = f"\n\nDid you mean: {' '.join('<code>'+k+'</code>' for k in close[:4])}" if close else ""
            self.reply(f"❌ No report <code>{TR.esc(key)}</code>.{hint}")
            return
        self.typing()
        got = TR.render(key)
        if not got:
            self.reply("❌ render failed")
            return
        text, path = got
        n = self.reply(text)
        if doc and path.exists():
            time.sleep(TR.SEND_GAP)
            ok = TR.send_file(path, caption=TR.REPORTS[key][0], chat_id=self.allowed)
            if not ok:
                self.reply("⚠️ text sent, but the file attachment failed.")
        if not n:
            self.reply("⚠️ nothing was delivered — check the log.")

    def c_sendall(self, args):
        doc = "--doc" in [a.lower() for a in args]
        keys = list(TR.REPORTS.keys())
        self.reply(f"📤 Sending {len(keys)} reports{'  (+ files)' if doc else ''}…")
        self.typing()
        ok = 0
        for i, key in enumerate(keys, 1):
            got = TR.render(key)
            if not got:
                continue
            text, path = got
            if self.reply(text, silent=True):
                ok += 1
            if doc and path.exists():
                time.sleep(TR.SEND_GAP)
                TR.send_file(path, caption=TR.REPORTS[key][0], chat_id=self.allowed)
            time.sleep(TR.SEND_GAP)
        self.reply(f"✅ done — <b>{ok}/{len(keys)}</b> delivered.")

    def c_status(self, _a=None):
        cfg = TR.load_config()
        live = TR.read_json(TR.AUTO / "status_live.json") or {}
        rows = TR.read_json(ROOT / "excel-rows.json") or []
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1

        w = "🟢 on" if self.watcher and self.watcher.is_alive() else "⚪ off"
        push = "🔇 paused" if not cfg.get("enabled", True) else "🔊 active"
        wd = live.get("watchdog", {}) or {}

        out = ["🩺 <b>Status</b>", "",
               f"bot      : online · {self.count} commands served",
               f"pushes   : {push}",
               f"watcher  : {w}" + (f" · {self.watcher.interval}s · {self.watcher.hits} hits"
                                    if self.watcher and self.watcher.is_alive() else ""),
               f"reports  : {len(TR.REPORTS)} registered", "",
               f"pipeline : <code>{TR.esc(live.get('phase','n/a'))}</code>",
               f"watchdog : {TR.esc(wd.get('state','n/a'))} · restarts {TR.esc(wd.get('restart_count',0))}",
               f"queue    : {len(rows)} tracked"]
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            out.append(f"   {TR.esc(k)}: {v}")
        self.reply("\n".join(out))

    def c_watch(self, args):
        arg = (args[0].lower() if args else "on")
        if arg in ("off", "stop", "0"):
            if self.watcher and self.watcher.is_alive():
                hits = self.watcher.hits
                self.watcher.stop()
                self.watcher = None
                self.reply(f"⏹ watcher stopped · {hits} updates pushed.")
            else:
                self.reply("Watcher is not running.")
            return
        if arg == "on":
            interval = 90
        else:
            try:
                interval = int(arg)
            except ValueError:
                self.reply("Usage: <code>/watch on</code> · <code>/watch off</code> · "
                           "<code>/watch 60</code>")
                return
        if self.watcher and self.watcher.is_alive():
            self.watcher.stop()
            self.watcher = None
        self.watcher = Watcher(interval)
        self.watcher.start()
        files = sum(1 for _, p, _ in TR.REPORTS.values() if p.exists())
        self.reply(f"👁 watcher on — polling {files} files every {interval}s.\n"
                   f"Any report that changes gets pushed here.\n"
                   f"<i>/watch off to stop</i>")

    def c_pause(self, _a=None):
        TR.save_config(enabled=False)
        self.reply("🔇 Paused. Reports will not be pushed until <code>/resume</code>.")

    def c_resume(self, _a=None):
        TR.save_config(enabled=True)
        self.reply("🔊 Resumed.")

    def c_logs(self, args):
        try:
            n = int(args[0]) if args else 20
        except ValueError:
            n = 20
        n = max(5, min(60, n))
        p = TR.LOG_PATH
        if not p.exists():
            self.reply("No log file yet.")
            return
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        self.reply(f"📜 <b>last {len(lines)} log lines</b>\n\n"
                   f"<code>{TR.esc(chr(10).join(l[-110:] for l in lines))}</code>")


def _age(path: Path) -> str:
    try:
        secs = time.time() - path.stat().st_mtime
    except OSError:
        return "missing"
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{int(secs/60)}m ago"
    if secs < 172800:
        return f"{int(secs/3600)}h ago"
    return f"{int(secs/86400)}d ago"


# ----------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = TR.load_config()
    if not TR.is_configured(cfg):
        print("Telegram not configured.")
        print("  python telegram_reports.py setup --token <TOKEN> --chat <CHAT_ID>")
        return 1

    if not acquire_lock():
        print("Another telegram_bot.py is already running (lock held).")
        print(f"If that is wrong, delete {LOCK_PATH}")
        return 1

    me = TR.api("getMe")
    if not me:
        print("Bot token rejected by Telegram. Re-run: python telegram_reports.py status")
        release_lock()
        return 1

    bot = Bot(cfg["chat_id"])
    state = load_state()

    print("=" * 58)
    print("  TELEGRAM BOT — listening")
    print("=" * 58)
    print(f"  bot        : @{me.get('username')}")
    print(f"  allowed    : chat_id {bot.allowed} (all others ignored)")
    print(f"  reports    : {len(TR.REPORTS)}")
    print(f"  poll       : {POLL_TIMEOUT}s long-poll")
    print(f"  log        : {TR.LOG_PATH}")
    print("\n  Send /help to your bot. Ctrl+C to stop.\n")

    TR._log(f"bot online @{me.get('username')} chat={bot.allowed}")
    TR.send("🟢 <b>Bot online</b> — send <code>/help</code> for commands.",
            chat_id=bot.allowed)

    stop = False
    while not stop:
        try:
            ups = TR.api("getUpdates", {
                "offset": state.get("offset", 0),
                "timeout": POLL_TIMEOUT,
                "allowed_updates": json.dumps(["message"]),
            }, timeout=POLL_TIMEOUT + 15)
        except KeyboardInterrupt:
            stop = True
            break
        except Exception as exc:
            TR._log(f"poll error: {exc}")
            time.sleep(5)
            continue

        for upd in ups or []:
            uid = upd.get("update_id", 0)
            if uid >= state.get("offset", 0):
                state["offset"] = uid + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            chat = msg.get("chat") or {}
            cid = str(chat.get("id", ""))
            if cid != bot.allowed:
                who = chat.get("username") or chat.get("first_name") or cid
                TR._log(f"IGNORED command from unauthorized chat {cid} ({who})")
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                TR.send("I only understand text commands. Send <code>/help</code>.",
                        chat_id=bot.allowed)
                continue
            TR._log(f"cmd from {cid}: {text[:80]}")
            try:
                bot.handle(text)
            except KeyboardInterrupt:
                stop = True
                break
            except Exception as exc:
                TR._log(f"handler error: {exc}")
        save_state(state)

    save_state(state)
    if bot.watcher and bot.watcher.is_alive():
        bot.watcher.stop()
    TR.send("🔴 <b>Bot offline</b>", chat_id=bot.allowed)
    TR._log("bot offline")
    release_lock()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        try:
            release_lock()
        except Exception:
            pass
        sys.exit(130)
