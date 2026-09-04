#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_reports.py — push every job-hunt report from <PROJECT_ROOT> to Telegram.

Stdlib only (urllib + json). No pip install, works on any Python 3.8+.

ONE-TIME SETUP
--------------
1. Telegram -> @BotFather -> /newbot -> follow prompts -> copy the HTTP token.
2. Open your new bot in Telegram and press Start (send it any message).
3. Get your chat id:
       python telegram_reports.py whoami --token <TOKEN>
   (or open https://api.telegram.org/bot<TOKEN>/getUpdates in a browser
    and read result[0].message.chat.id)
4. Save both:
       python telegram_reports.py setup --token <TOKEN> --chat <CHAT_ID>

USAGE
-----
    python telegram_reports.py status                 config + reachability check
    python telegram_reports.py list                   list every report available
    python telegram_reports.py test                   send a test message
    python telegram_reports.py digest                 the combined daily digest
    python telegram_reports.py send queue             one report by key
    python telegram_reports.py send queue --doc       ...and attach the raw file
    python telegram_reports.py send-all               every report, text only
    python telegram_reports.py send-all --doc         every report + file attachments
    python telegram_reports.py watch --interval 120   live push when a report changes

Credentials are read from <PROJECT_ROOT>\\.telegram.json, or from the environment
variables TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (env wins).
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
AUTO = ROOT / "autoapply"
CONFIG_PATH = ROOT / ".telegram.json"
LOG_PATH = ROOT / "telegram_reports.log"

API = "https://api.telegram.org"
MAX_CHARS = 3800        # Telegram hard limit is 4096; leave headroom
RETRIES = 3
SEND_GAP = 0.6          # seconds between messages, avoids 429s

# ----------------------------------------------------------------------
# Logging (stdout is utf-8 reconfigured so emoji survive on Windows)
# ----------------------------------------------------------------------
def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode())
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
def load_config() -> dict:
    cfg = {"bot_token": "", "chat_id": "", "enabled": True}
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            _log(f"WARN could not read {CONFIG_PATH.name}: {exc}")
    cfg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("bot_token", "")
    cfg["chat_id"] = str(os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("chat_id", ""))
    return cfg


def save_config(token: str | None = None, chat: str | None = None,
                enabled: bool | None = None) -> dict:
    cfg = load_config()
    # env vars would shadow the file; strip them from what we persist
    cfg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or (token if token is not None else cfg["bot_token"])
    cfg["chat_id"] = str(os.environ.get("TELEGRAM_CHAT_ID") or (chat if chat is not None else cfg["chat_id"]))
    if token is not None:
        cfg["bot_token"] = token
    if chat is not None:
        cfg["chat_id"] = str(chat)
    if enabled is not None:
        cfg["enabled"] = enabled
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return cfg


def is_configured(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("bot_token") and cfg.get("chat_id"))


# ----------------------------------------------------------------------
# Low-level API
# ----------------------------------------------------------------------
def _encode_multipart(fields: dict, file_spec: tuple) -> tuple[bytes, str]:
    """file_spec = (field_name, filename, bytes)"""
    boundary = "----TelegramReports" + uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode()
    fname_field, filename, content = file_spec
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    out += f"--{boundary}\r\n".encode()
    out += (
        f'Content-Disposition: form-data; name="{fname_field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def api(method: str, params: dict | None = None, file_spec: tuple | None = None,
        timeout: int = 60) -> dict | None:
    cfg = load_config()
    if not cfg.get("bot_token"):
        _log("ERROR no bot_token configured")
        return None
    url = f"{API}/bot{cfg['bot_token']}/{method}"

    payload = {k: v for k, v in (params or {}).items() if v is not None}
    if file_spec:
        body, ctype = _encode_multipart(
            {k: str(v) for k, v in payload.items()}, file_spec)
    else:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        ctype = "application/x-www-form-urlencoded"

    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": ctype}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return result.get("result")
            desc = result.get("description", "unknown")
            if result.get("error_code") == 429:
                wait = int((result.get("parameters") or {}).get("retry_after", 5))
                _log(f"rate limited, waiting {wait}s")
                time.sleep(wait + 1)
                continue
            _log(f"API error on {method}: {desc}")
            return None
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            _log(f"HTTP {exc.code} on {method}: {detail}")
            if exc.code == 429:
                time.sleep(5 * attempt)
                continue
            if 400 <= exc.code < 500:
                return None
        except Exception as exc:
            _log(f"{method} attempt {attempt} failed: {exc}")
        time.sleep(2 * attempt)
    return None


# ----------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------
def esc(text) -> str:
    """Escape for Telegram HTML parse mode."""
    return html.escape(str(text), quote=False)


def chunk(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split text on line boundaries so no chunk exceeds the limit."""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:          # one pathological long line
            if buf:
                parts.append(buf.rstrip())
                buf = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf.rstrip())
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf.strip():
        parts.append(buf.rstrip())
    return parts


def send(text: str, silent: bool = False, chat_id: str | None = None) -> int:
    """Send HTML text, auto-splitting long messages. Returns chunks delivered."""
    cfg = load_config()
    if not is_configured(cfg):
        _log("ERROR Telegram not configured — run: python telegram_reports.py setup --token .. --chat ..")
        return 0
    if not cfg.get("enabled", True):
        _log("paused (enabled=false) — message dropped")
        return 0

    target = str(chat_id or cfg["chat_id"])
    sent = 0
    pieces = chunk(text)
    for i, piece in enumerate(pieces, 1):
        suffix = f"\n<i>({i}/{len(pieces)})</i>" if len(pieces) > 1 else ""
        ok = api("sendMessage", {
            "chat_id": target,
            "text": piece + suffix,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        })
        if ok is None:
            _log(f"chunk {i}/{len(pieces)} FAILED")
        else:
            sent += 1
        if i < len(pieces):
            time.sleep(SEND_GAP)
    return sent


def send_file(path: Path, caption: str = "", chat_id: str | None = None) -> bool:
    """Upload a file as a Telegram document."""
    cfg = load_config()
    if not is_configured(cfg):
        return False
    target = str(chat_id or cfg["chat_id"])
    path = Path(path)
    if not path.exists():
        _log(f"ERROR file missing: {path}")
        return False
    data = path.read_bytes()
    if len(data) > 45 * 1024 * 1024:
        _log(f"ERROR {path.name} too large for Telegram (45MB cap)")
        return False
    res = api("sendDocument", {"chat_id": target, "caption": caption[:1024],
                               "parse_mode": "HTML"},
              file_spec=("document", path.name, data), timeout=180)
    return res is not None


# ----------------------------------------------------------------------
# File helpers
# ----------------------------------------------------------------------
def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception as exc:
        _log(f"WARN cannot parse {path}: {exc}")
        return None


def read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except Exception as exc:
        _log(f"WARN cannot read {path}: {exc}")
    return ""


def stamp(path: Path) -> str:
    try:
        mtime = datetime.fromtimestamp(Path(path).stat().st_mtime)
        return mtime.strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "unknown"


def pct(a, b) -> str:
    return f"{(a / b * 100):.1f}%" if b else "n/a"


def bar(value: int, total: int, width: int = 12) -> str:
    if not total:
        return "░" * width
    filled = max(0, min(width, round(value / total * width)))
    return "█" * filled + "░" * (width - filled)


# ----------------------------------------------------------------------
# Renderers — one per report
# ----------------------------------------------------------------------
def r_queue(_p=None) -> str:
    rows = read_json(ROOT / "excel-rows.json") or []
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "UNKNOWN")] = counts.get(r.get("status", "UNKNOWN"), 0) + 1
    total = len(rows) or 1
    order = ["SUBMITTED", "PENDING_HUMAN", "FAILED", "SKIPPED", "PENDING", "UNKNOWN"]
    keys = [k for k in order if k in counts] + [k for k in counts if k not in order]

    out = ["📋 <b>Job Queue</b>  ·  <code>excel-rows.json</code>",
           f"🕒 file updated {stamp(ROOT / 'excel-rows.json')}", ""]
    for k in keys:
        out.append(f"{bar(counts[k], len(rows))}  {counts[k]:>3}  {esc(k)}")
    out.append(f"\nTotal tracked: <b>{len(rows)}</b>")

    human = [r for r in rows if r.get("status") == "PENDING_HUMAN"]
    if human:
        out.append("\n🛑 <b>Needs you</b>")
        for r in human[:5]:
            out.append(f"• {esc(r.get('company','?'))} — {esc(r.get('role','?'))}")
            why = (r.get("failureReason") or r.get("notes") or "")[:120]
            if why:
                out.append(f"   <i>{esc(why)}</i>")
        if len(human) > 5:
            out.append(f"   …+{len(human)-5} more")

    failed = [r for r in rows if r.get("status") == "FAILED"]
    if failed:
        out.append(f"\n❌ <b>Failed</b> ({len(failed)})")
        for r in failed[:5]:
            out.append(f"• {esc(r.get('company','?'))} — {esc(r.get('role','?'))}")
        if len(failed) > 5:
            out.append(f"   …+{len(failed)-5} more")

    pending = [r for r in rows if r.get("status") in (None, "", "PENDING", "UNPROCESSED")]
    if pending:
        out.append(f"\n⏳ <b>Not processed</b> ({len(pending)})")
        for r in pending[:8]:
            out.append(f"• {esc(r.get('queueId','?'))} {esc(r.get('company','?'))} — {esc(r.get('role','?'))}")
        if len(pending) > 8:
            out.append(f"   …+{len(pending)-8} more")
    out.append(f"\n<i>submitted share {pct(counts.get('SUBMITTED',0), len(rows))}</i>")
    return "\n".join(out)


def r_live(_p=None) -> str:
    p = AUTO / "status_live.json"
    d = read_json(p)
    if not d:
        return "⚠️ <b>Pipeline status unavailable</b>\n\n<code>status_live.json</code> missing or unreadable."
    c = d.get("counts", {})
    wd = d.get("watchdog", {})
    cj = d.get("current_job", {}) or {}
    total = sum(c.values()) or 1

    out = ["🛰 <b>Pipeline — live status</b>",
           f"🕒 updated {esc(d.get('updated_at','?'))}",
           f"phase: <code>{esc(d.get('phase','?'))}</code>", ""]
    for k in ["submitted", "in_progress", "pending", "review_required", "failed", "skipped"]:
        if k in c:
            out.append(f"{bar(c[k], total)}  {c[k]:>3}  {esc(k)}")

    out.append(f"\n🎯 current job: <code>{esc(cj.get('id') or '—')}</code> · step <code>{esc(cj.get('step') or '—')}</code>")
    out.append(f"👷 worker: <code>{esc(d.get('worker_id','—'))}</code>   ⏱ ETA: {esc(d.get('eta_minutes','—'))} min")

    state = wd.get("state", "?")
    icon = {"idle": "😴", "healthy": "✅", "stale": "⚠️"}.get(state, "❔")
    out.append(f"\n{icon} watchdog: <b>{esc(state)}</b> · hb age {esc(wd.get('heartbeat_age_sec','—'))}s "
               f"(timeout {esc(wd.get('heartbeat_timeout_sec','—'))}s)")
    out.append(f"🔁 restarts: {esc(wd.get('restart_count',0))}"
               + (f" · last: <i>{esc(wd.get('last_restart_reason'))}</i>" if wd.get("last_restart_reason") else ""))
    return "\n".join(out)


def r_dashboard(_p=None) -> str:
    p = AUTO / "dashboard.json"
    d = read_json(p)
    if not d:
        return "⚠️ <b>Dashboard unavailable</b>"
    q = d.get("queue", {})
    out = ["📊 <b>Dashboard snapshot</b>",
           f"🕒 generated {esc(str(d.get('generated_at','?'))[:19])}", "",
           f"📤 today: <b>{esc(d.get('applications_today',0))}</b>   "
           f"📅 week: <b>{esc(d.get('applications_this_week',0))}</b>",
           f"💬 response rate: <b>{esc(d.get('response_rate_pct',0))}%</b>   "
           f"🎁 offers: <b>{esc(d.get('offers',0))}</b>",
           f"📝 assessments pending: {esc(d.get('assessments_pending',0))}   "
           f"🎒 interview kits: {esc(d.get('interview_kits_ready',0))}", ""]
    if q:
        total = sum(v for v in q.values() if isinstance(v, int)) or 1
        for k, v in q.items():
            if isinstance(v, int):
                out.append(f"{bar(v,total)}  {v:>3}  {esc(k)}")
    tops = d.get("top_companies_by_score") or []
    if tops:
        out.append("\n🏆 <b>Top companies by score</b>")
        for i, t in enumerate(tops[:8], 1):
            out.append(f"{i}. {esc(t.get('company'))} — <b>{esc(t.get('score'))}</b>")
    dh = d.get("discovery_health") or {}
    if dh:
        out.append(f"\n🌐 universe: {esc(dh.get('universe_size','—'))} companies · "
                   f"{esc(dh.get('verified_endpoints','—'))} verified endpoints")
    return "\n".join(out)


def r_failed(_p=None) -> str:
    p = AUTO / "failed_jobs.json"
    d = read_json(p)
    if not d:
        return "⚠️ <b>Failed-jobs report unavailable</b>"
    items = d.get("items", [])
    out = ["❌ <b>Failed jobs</b>",
           f"🕒 updated {esc(d.get('updated_at','?'))}",
           f"count: <b>{len(items)}</b>", ""]
    by_portal: dict[str, int] = {}
    for it in items:
        by_portal[it.get("portal", "?")] = by_portal.get(it.get("portal", "?"), 0) + 1
    if by_portal:
        out.append("by portal:")
        for k, v in sorted(by_portal.items(), key=lambda x: -x[1])[:8]:
            out.append(f"  • {esc(k)}: {v}")
        out.append("")
    for it in items[:10]:
        out.append(f"• <b>{esc(it.get('company','?'))}</b> — {esc(str(it.get('title','?'))[:60])}")
        out.append(f"   <code>{esc(it.get('portal','?'))}</code> · <a href=\"{esc(it.get('url',''))}\">open</a>")
    if len(items) > 10:
        out.append(f"\n…+{len(items)-10} more (send with --doc for the full list)")
    return "\n".join(out)


def r_outcomes(_p=None) -> str:
    p = AUTO / "outcomes.json"
    d = read_json(p)
    if not d:
        return "⚠️ <b>Outcomes unavailable</b>"
    outs = d.get("outcomes", [])
    by_state: dict[str, int] = {}
    for o in outs:
        by_state[o.get("state", "?")] = by_state.get(o.get("state", "?"), 0) + 1
    total = len(outs) or 1
    out = ["🎯 <b>Application outcomes</b>",
           f"🕒 {esc(str(d.get('generated_at','?'))[:19])}",
           f"total recorded: <b>{len(outs)}</b>", ""]
    for k, v in sorted(by_state.items(), key=lambda x: -x[1]):
        out.append(f"{bar(v,total)}  {v:>3}  {esc(k)}")
    recent = [o for o in outs if o.get("state") == "submitted"][-8:]
    if recent:
        out.append("\n✅ <b>Recent submissions</b>")
        for o in reversed(recent):
            out.append(f"• {esc(o.get('company','?'))} — {esc(str(o.get('role','?'))[:55])}")
    return "\n".join(out)


def r_analytics(_p=None) -> str:
    d = read_json(AUTO / "analytics.json")
    if not d:
        return "⚠️ <b>Analytics unavailable</b>"
    f = d.get("funnel", {})
    c = d.get("conversions", {})
    out = ["📈 <b>Funnel analytics</b>",
           f"🕒 {esc(str(d.get('generated_at','?'))[:19])}", "",
           f"discovered  {esc(f.get('discovered',0))}",
           f"queued      {esc(f.get('queued',0))}",
           f"applied     {esc(f.get('applied',0))}",
           f"interview   {esc(f.get('interview',0))}",
           f"assessment  {esc(f.get('assessment',0))}",
           f"offer       {esc(f.get('offer',0))}", "",
           f"queued→applied   <b>{esc(c.get('queued_to_applied_pct','—'))}%</b>",
           f"applied→response <b>{esc(c.get('applied_to_response_pct','—'))}%</b>",
           f"applied→interview <b>{esc(c.get('applied_to_interview_pct','—'))}%</b>"]
    provs = (d.get("ats_performance") or {}).get("providers") or []
    if provs:
        out.append("\n🏁 <b>By ATS provider</b>")
        for p in sorted(provs, key=lambda x: -(x.get("submissions") or 0))[:8]:
            out.append(f"• {esc(p.get('provider'))}: {esc(p.get('submissions',0))} submitted / "
                       f"{esc(p.get('applications',0))} attempted / {esc(p.get('failures',0))} failed")
    return "\n".join(out)


def r_provider(_p=None) -> str:
    d = read_json(AUTO / "provider_performance_report.json")
    if not d:
        return "⚠️ <b>Provider report unavailable</b>"
    provs = d.get("providers", {})
    out = ["🔌 <b>ATS provider performance</b>",
           f"🕒 {esc(str(d.get('generated_at','?'))[:19])}", ""]
    rows = sorted(provs.items(), key=lambda kv: -(kv[1].get("submissions") or 0))
    for name, v in rows[:12]:
        out.append(f"• <b>{esc(name)}</b> — {esc(v.get('submissions',0))} submitted, "
                   f"{esc(v.get('jobs_in_queue',0))} queued, "
                   f"{esc(v.get('verified_endpoints',0))} endpoints")
    if len(rows) > 12:
        out.append(f"\n…+{len(rows)-12} more providers")
    return "\n".join(out)


def r_discovery(_p=None) -> str:
    d = read_json(AUTO / "weekly_discovery_report.json")
    if not d:
        return "⚠️ <b>Discovery report unavailable</b>"
    out = ["🔎 <b>Weekly discovery</b>",
           f"🕒 {esc(str(d.get('generated_at','?'))[:19])} · window {esc(d.get('window','?'))}",
           f"jobs discovered: <b>{esc(d.get('jobs_discovered_total',0))}</b>", ""]
    bs = d.get("by_source") or {}
    if bs:
        out.append("<b>by source</b>")
        for k, v in sorted(bs.items(), key=lambda x: -x[1])[:8]:
            out.append(f"  • {esc(k)}: {v}")
    bp = d.get("by_portal") or {}
    if bp:
        out.append("\n<b>by portal</b>")
        for k, v in sorted(bp.items(), key=lambda x: -x[1])[:8]:
            out.append(f"  • {esc(k)}: {v}")
    tc = d.get("top_companies") or {}
    if tc:
        out.append("\n🏢 <b>top companies</b>")
        for k, v in list(sorted(tc.items(), key=lambda x: -x[1]))[:8]:
            out.append(f"  • {esc(k)}: {v}")
    return "\n".join(out)


def _md_brief(path: Path, title: str, preview: int = 900) -> str:
    """Headings outline + a short preview of a markdown report."""
    text = read_text(path)
    if not text.strip():
        return f"⚠️ <b>{esc(title)}</b>\n\nfile is empty: <code>{esc(path.name)}</code>"
    heads = [ln.strip("# ").strip() for ln in text.splitlines()
             if ln.startswith("#") and len(ln.strip()) > 2]
    out = [f"📄 <b>{esc(title)}</b>",
           f"🕒 updated {stamp(path)} · {len(text):,} chars", ""]
    if heads:
        out.append("<b>Contents</b>")
        for h in heads[:12]:
            out.append(f"  • {esc(h[:80])}")
        if len(heads) > 12:
            out.append(f"  …+{len(heads)-12} more headings")
        out.append("")
    body = "\n".join(ln for ln in text.splitlines() if ln.strip())[:preview]
    out.append("<b>Preview</b>")
    out.append(f"<code>{esc(body)}</code>")
    if len(text) > preview:
        out.append(f"\n<i>…truncated. Use --doc for the complete file.</i>")
    return "\n".join(out)


def _generic_json(path: Path, title: str) -> str:
    d = read_json(path)
    if d is None:
        return f"⚠️ <b>{esc(title)}</b>\n\ncannot read <code>{esc(path.name)}</code>"
    pretty = json.dumps(d, indent=1, ensure_ascii=False)[:2600]
    out = [f"📦 <b>{esc(title)}</b>", f"🕒 updated {stamp(path)}", "",
           f"<code>{esc(pretty)}</code>"]
    if len(pretty) >= 2600:
        out.append("\n<i>…truncated. Use --doc for the full JSON.</i>")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Report registry:  key -> (title, path, renderer)
# ----------------------------------------------------------------------
REPORTS: dict[str, tuple] = {
    # --- live / operational -------------------------------------------
    "queue":      ("Job Queue",                  ROOT / "excel-rows.json",                       r_queue),
    "live":       ("Pipeline Live Status",       AUTO / "status_live.json",                      r_live),
    "dashboard":  ("Dashboard Snapshot",         AUTO / "dashboard.json",                        r_dashboard),
    "failed":     ("Failed Jobs",                AUTO / "failed_jobs.json",                      r_failed),
    "outcomes":   ("Application Outcomes",       AUTO / "outcomes.json",                         r_outcomes),
    "analytics":  ("Funnel Analytics",           AUTO / "analytics.json",                        r_analytics),
    "provider":   ("ATS Provider Performance",   AUTO / "provider_performance_report.json",      r_provider),
    "discovery":  ("Weekly Discovery",           AUTO / "weekly_discovery_report.json",          r_discovery),
    "final":      ("Final Run Report",           AUTO / "final_report.json",                     None),
    "pipeline":   ("Pipeline State",             ROOT / "pipeline-state.json",                   None),
    # --- markdown reports ---------------------------------------------
    "runnext":    ("RUN NEXT",                   ROOT / "RUN-NEXT.md",                           None),
    "tracker":    ("Job Application Tracker",    ROOT / "job-application-tracker.md",            None),
    "findings":   ("Job Findings",               ROOT / "job-findings.md",                       None),
    "weekly":     ("Weekly Intelligence",        AUTO / "weekly_report.md",                      None),
    "morning":    ("Morning Brief",              AUTO / "morning_brief.md",                      None),
    "experiment": ("Weekly Experiments",         AUTO / "weekly_experiment_report.md",           None),
    "duplicates": ("Duplicate Audit",            AUTO / "duplicate_audit.md",                    None),
    "gmail":      ("Gmail Duplicate Report",     AUTO / "gmail_duplicate_report.md",             None),
    "portal":     ("Full Portal Report",         ROOT / "frontend_job_hunt_full_portal_report.md", None),
    "remote":     ("Remote Companies Tracking",  ROOT / "remote-companies-tracking.md",          None),
    "behavior":   ("Behavior Report",            ROOT / "behavior-report.md",                    None),
}


def render(key: str) -> tuple[str, Path] | None:
    """Return (message_text, source_path) for a report key."""
    if key not in REPORTS:
        return None
    title, path, fn = REPORTS[key]
    if not path.exists():
        return f"⚠️ <b>{esc(title)}</b>\n\nfile not found: <code>{esc(str(path))}</code>", path
    if fn:
        return fn(path), path
    if path.suffix.lower() == ".json":
        return _generic_json(path, title), path
    return _md_brief(path, title), path


# ----------------------------------------------------------------------
# Digest
# ----------------------------------------------------------------------
def build_digest() -> str:
    now = datetime.now().strftime("%a %d %b %Y, %H:%M")
    rows = read_json(ROOT / "excel-rows.json") or []
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "UNKNOWN")] = counts.get(r.get("status", "UNKNOWN"), 0) + 1
    live = read_json(AUTO / "status_live.json") or {}
    dash = read_json(AUTO / "dashboard.json") or {}
    fails = read_json(AUTO / "failed_jobs.json") or {}
    disc = read_json(AUTO / "weekly_discovery_report.json") or {}

    total = len(rows)
    submitted = counts.get("SUBMITTED", 0)
    human = counts.get("PENDING_HUMAN", 0)
    failed = counts.get("FAILED", 0)
    lp = counts.get("PENDING") or counts.get("") or 0
    unproc = sum(1 for r in rows if r.get("status") in (None, "", "PENDING", "UNPROCESSED"))

    out = [f"🧭 <b>JOB HUNT DIGEST</b> · {esc(now)}", "━" * 26, "",
           f"📋 <b>Queue</b> — {total} tracked",
           f"   ✅ submitted      {submitted}",
           f"   ⏳ unprocessed    {unproc}",
           f"   ❌ failed         {failed}",
           f"   🛑 needs you      {human}", ""]

    if live:
        wd = live.get("watchdog", {}) or {}
        out.append(f"🛰 <b>Pipeline</b> — phase <code>{esc(live.get('phase','?'))}</code>")
        lc = live.get("counts", {}) or {}
        if lc:
            out.append("   " + "  ".join(f"{k}:{v}" for k, v in list(lc.items())[:5]))
        out.append(f"   watchdog: {esc(wd.get('state','?'))} · restarts {esc(wd.get('restart_count',0))}")
        out.append("")

    if dash:
        out.append(f"📊 <b>Velocity</b> — today {esc(dash.get('applications_today',0))} · "
                   f"week {esc(dash.get('applications_this_week',0))} · "
                   f"responses {esc(dash.get('response_rate_pct',0))}% · "
                   f"offers {esc(dash.get('offers',0))}")
        out.append("")

    if disc:
        out.append(f"🔎 <b>Discovery ({esc(disc.get('window','7d'))})</b> — "
                   f"{esc(disc.get('jobs_discovered_total',0))} new jobs")
        tc = list((disc.get("top_companies") or {}).items())[:3]
        if tc:
            out.append("   " + ", ".join(f"{esc(k)} ({v})" for k, v in tc))
        out.append("")

    blockers = [r for r in rows if r.get("status") == "PENDING_HUMAN"]
    if blockers:
        out.append("🛑 <b>Action required</b>")
        for r in blockers[:4]:
            out.append(f"   • {esc(r.get('company','?'))} — {esc(str(r.get('role','?'))[:50])}")
            out.append(f"     <a href=\"{esc(r.get('jobUrl') or r.get('canonicalUrl') or '')}\">open job</a>")
        if len(blockers) > 4:
            out.append(f"   …+{len(blockers)-4} more")
        out.append("")

    n_failed = len(fails.get("items", []))
    if n_failed:
        out.append(f"❌ <b>Failures</b> — {n_failed} logged in <code>failed_jobs.json</code>")

    tops = (dash.get("top_companies_by_score") or [])[:5]
    if tops:
        out.append("\n🏆 <b>Top targets</b>")
        for i, t in enumerate(tops, 1):
            out.append(f"   {i}. {esc(t.get('company'))} — {esc(t.get('score'))}")

    out.append("\n" + "━" * 26)
    out.append("<i>send a report: </i><code>/send queue</code><i> · full: </i><code>/send-all</code>")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_status(_a=None) -> int:
    cfg = load_config()
    print("=" * 54)
    print("  TELEGRAM REPORTS — STATUS")
    print("=" * 54)
    print(f"  config file : {CONFIG_PATH}")
    print(f"  token set   : {'yes (' + cfg['bot_token'][:8] + '…)' if cfg['bot_token'] else 'NO'}")
    print(f"  chat id     : {cfg['chat_id'] or 'NOT SET'}")
    print(f"  enabled     : {cfg.get('enabled', True)}")
    if not is_configured(cfg):
        print("\n  -> not configured. Run:")
        print("     python telegram_reports.py setup --token <TOKEN> --chat <CHAT_ID>")
        return 1
    me = api("getMe")
    if not me:
        print("\n  -> token present but API call failed (bad token or no network).")
        return 1
    print(f"\n  bot         : @{me.get('username')} ({me.get('first_name')})")
    # verify the chat is reachable
    info = api("getChat", {"chat_id": cfg["chat_id"]})
    if info:
        title = info.get("title") or info.get("first_name") or info.get("username") or "?"
        print(f"  chat        : {title}  (type={info.get('type')}, id={info.get('id')})")
        print("\n  ✅ READY — reports can be delivered.")
    else:
        print(f"\n  ⚠️  chat {cfg['chat_id']} unreachable.")
        print("     Open the bot in Telegram and press Start, then re-run this.")
        print("     Lost your chat id?  python telegram_reports.py whoami")
        return 1
    print(f"\n  reports available: {len(REPORTS)}")
    return 0


def cmd_whoami(_a=None) -> int:
    cfg = load_config()
    token = _a.token if getattr(_a, "token", None) else cfg["bot_token"]
    if not token:
        print("No token. Pass --token <TOKEN> or run setup first.")
        return 1
    url = f"{API}/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        print(f"Failed: {exc}")
        return 1
    if not data.get("ok"):
        print(f"API error: {data.get('description')}")
        return 1
    results = data.get("result") or []
    if not results:
        print("No updates yet.")
        print("-> Open your bot in Telegram and send it any message (e.g. /start),")
        print("   then run this command again.")
        return 1
    seen = {}
    for upd in results:
        chat = None
        for key in ("message", "edited_message", "my_chat_member", "channel_post"):
            if key in upd and isinstance(upd[key], dict) and "chat" in upd[key]:
                chat = upd[key]["chat"]
                break
        if chat:
            seen[chat["id"]] = chat.get("title") or chat.get("first_name") or chat.get("username") or "?"
    print("Chats that have talked to your bot:")
    for cid, name in seen.items():
        print(f"  {cid}   {name}")
    print("\nSave one with:")
    print(f"  python telegram_reports.py setup --chat {list(seen)[0]}")
    return 0


def cmd_list(_a=None) -> int:
    print(f"{'KEY':<12} {'TITLE':<32} {'UPDATED':<18} FILE")
    print("-" * 100)
    for key, (title, path, _) in REPORTS.items():
        mark = "✓" if path.exists() else "✗"
        print(f"{key:<12} {title:<32} {stamp(path):<18} {mark} {path.name}")
    print("-" * 100)
    print(f"{len(REPORTS)} reports.  python telegram_reports.py send <key>")
    return 0


def cmd_test(_a=None) -> int:
    if not is_configured():
        print("Not configured. Run: python telegram_reports.py setup --token <TOKEN> --chat <CHAT_ID>")
        return 1
    n = send("✅ <b>telegram_reports.py connected</b>\n\n"
             "Your job-hunt reports will land in this chat.\n\n"
             "<i>Commands</i>\n"
             "  <code>digest</code> — combined daily summary\n"
             "  <code>send &lt;key&gt;</code> — one report\n"
             "  <code>send-all</code> — every report\n\n"
             f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("sent" if n else "FAILED")
    return 0 if n else 1


def cmd_digest(_a=None) -> int:
    n = send(build_digest())
    print(f"digest sent ({n} message(s))" if n else "digest FAILED")
    return 0 if n else 1


def cmd_send(a) -> int:
    if a.key == "all":
        return cmd_send_all(a)
    got = render(a.key)
    if not got:
        print(f"Unknown report '{a.key}'. Run: python telegram_reports.py list")
        return 1
    text, path = got
    n = send(text)
    if a.doc and path.exists():
        time.sleep(SEND_GAP)
        send_file(path, caption=REPORTS[a.key][0])
    print(f"{a.key}: {'sent' if n else 'FAILED'}")
    return 0 if n else 1


def cmd_send_all(a) -> int:
    keys = list(REPORTS.keys())
    ok = 0
    for i, key in enumerate(keys, 1):
        got = render(key)
        if not got:
            continue
        text, path = got
        n = send(text, silent=True)
        if a.doc and path.exists():
            time.sleep(SEND_GAP)
            send_file(path, caption=REPORTS[key][0])
        ok += 1 if n else 0
        print(f"  [{i}/{len(keys)}] {key}: {'ok' if n else 'FAILED'}")
        time.sleep(SEND_GAP)
    print(f"\ndone — {ok}/{len(keys)} delivered")
    return 0 if ok else 1


def cmd_send_digest_then_all(a) -> int:
    cmd_digest()
    time.sleep(1)
    return cmd_send_all(a)


def cmd_watch(a) -> int:
    interval = max(15, int(a.interval))
    if not is_configured():
        print("Not configured.")
        return 1
    watched = {k: p for k, (_, p, _) in REPORTS.items() if p.exists()}
    state = {}
    for k, p in watched.items():
        try:
            st = p.stat()
            state[k] = (st.st_mtime, st.st_size)
        except OSError:
            pass
    _log(f"watching {len(watched)} reports, interval {interval}s — Ctrl+C to stop")
    send("👀 <b>Report watcher started</b>\n\n"
         f"Monitoring {len(watched)} files every {interval}s.\n"
         "Any report that changes gets pushed here automatically.")
    try:
        while True:
            time.sleep(interval)
            for k, p in list(watched.items()):
                try:
                    st = p.stat()
                except OSError:
                    continue
                cur = (st.st_mtime, st.st_size)
                if state.get(k) != cur:
                    state[k] = cur
                    time.sleep(2)          # debounce: let the writer finish
                    got = render(k)
                    if got:
                        _log(f"change detected -> {k}")
                        send(got[0])
                        time.sleep(SEND_GAP)
    except KeyboardInterrupt:
        _log("watcher stopped")
        send("🛑 <b>Report watcher stopped</b>")
    return 0


def cmd_setup(a) -> int:
    if a.token or a.chat:
        cfg = save_config(token=a.token, chat=a.chat)
        print(f"Saved to {CONFIG_PATH}")
    else:
        cfg = load_config()
        print("Interactive setup not available in this environment.")
        print("Use:")
        print("  python telegram_reports.py setup --token <TOKEN>")
        print("  python telegram_reports.py setup --chat <CHAT_ID>")
        return 1
    if is_configured(cfg):
        print("\nVerifying…")
        return cmd_status()
    print("\nStill missing token or chat id.")
    return 1


def cmd_pause(a) -> int:
    save_config(enabled=False)
    print("Paused — messages will be dropped until you run: python telegram_reports.py resume")
    return 0


def cmd_resume(a) -> int:
    save_config(enabled=True)
    print("Resumed.")
    return 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(
        prog="telegram_reports.py",
        description="Push job-hunt reports from <PROJECT_ROOT> to Telegram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python telegram_reports.py setup --token 123:ABC --chat 987654\n"
               "  python telegram_reports.py digest\n"
               "  python telegram_reports.py send queue --doc\n"
               "  python telegram_reports.py watch --interval 120\n")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", help="check config + bot/chat reachability")
    sub.add_parser("list", help="list all available reports")
    sub.add_parser("test", help="send a test message")
    sub.add_parser("digest", help="send the combined digest")

    sp_who = sub.add_parser("whoami", help="discover your chat id")
    sp_who.add_argument("--token", help="bot token (uses saved one if omitted)")

    sp_send = sub.add_parser("send", help="send one report (or 'all')")
    sp_send.add_argument("key", help="report key from `list`, or 'all'")
    sp_send.add_argument("--doc", action="store_true", help="also attach the raw file")

    sp_all = sub.add_parser("send-all", help="send every report")
    sp_all.add_argument("--doc", action="store_true", help="also attach every raw file")

    sp_watch = sub.add_parser("watch", help="push reports automatically when they change")
    sp_watch.add_argument("--interval", default=120, help="poll seconds (default 120, min 15)")

    sp_setup = sub.add_parser("setup", help="save bot token / chat id")
    sp_setup.add_argument("--token")
    sp_setup.add_argument("--chat")

    sub.add_parser("pause", help="temporarily stop sending")
    sub.add_parser("resume", help="resume sending")

    a = p.parse_args()
    if not a.cmd:
        p.print_help()
        return 0

    handlers = {
        "status": cmd_status, "list": cmd_list, "test": cmd_test,
        "digest": cmd_digest, "send": cmd_send, "send-all": cmd_send_all,
        "watch": cmd_watch, "setup": cmd_setup, "whoami": cmd_whoami,
        "pause": cmd_pause, "resume": cmd_resume,
    }
    return handlers[a.cmd](a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
