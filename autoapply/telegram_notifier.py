#!/usr/bin/env python3
"""
Telegram Notification Module for Job Sniper.

Sends formatted job alerts to a Telegram chat using the standard
Bot HTTP API. Zero paid dependencies — uses only `requests`.

Setup:
  1. Talk to @BotFather on Telegram -> /newbot -> copy the token
  2. Send any message to your new bot, then visit:
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
     to find your chat_id
  3. Fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID below
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger("job_sniper.telegram")

TELEGRAM_API = "https://api.telegram.org"
REQUEST_TIMEOUT = 15  # seconds

# Credentials are shared with the root-level reporter:
#   <PROJECT_ROOT>\telegram_reports.py  (config: <PROJECT_ROOT>\.telegram.json)
# Configure once with:
#   python telegram_reports.py setup --token <TOKEN> --chat <CHAT_ID>
_CONFIG_PATH = Path(__file__).resolve().parent.parent / ".telegram.json"


def _load_credentials() -> tuple[str, str]:
    """Read shared credentials from .telegram.json (env vars win)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        token = token or data.get("bot_token", "")
        chat = chat or str(data.get("chat_id", ""))
    except Exception:
        pass
    return token, chat


def is_configured() -> bool:
    """Check if Telegram credentials are set."""
    token, chat = _load_credentials()
    return bool(token and chat)


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a raw text message to the configured Telegram chat.

    Returns True on success, False on failure.
    """
    token, chat_id = _load_credentials()
    if not (token and chat_id):
        logger.warning("Telegram not configured — message skipped")
        return False

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                logger.info("Telegram message sent (msg_id=%s)",
                            data.get("result", {}).get("message_id"))
                return True
            else:
                logger.error("Telegram API error: %s", data.get("description"))
                return False
        else:
            logger.error("Telegram HTTP %d: %s", resp.status_code, resp.text[:200])
            return False
    except requests.exceptions.Timeout:
        logger.error("Telegram request timed out")
        return False
    except requests.exceptions.ConnectionError:
        logger.error("Telegram connection failed (no internet?)")
        return False
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def send_job_alert(
    job_title: str,
    company: str,
    location: str,
    link: str,
    posted_time: str | None = None,
    source: str = "RSS",
) -> bool:
    """
    Send a formatted job alert to Telegram.

    Parameters
    ----------
    job_title : str
        The job posting title.
    company : str
        Company name.
    location : str
        Job location (e.g., "Remote", "Bangalore, India").
    link : str
        Direct URL to the job posting.
    posted_time : str, optional
        Human-readable posted time (e.g., "2 minutes ago").
    source : str
        Where the job was found (e.g., "Google Jobs", "LinkedIn", "Reddit").
    """
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Build a clean HTML message
    lines = [
        "🔔 <b>New Job Alert!</b>",
        "",
        f"💼 <b>{_escape_html(job_title)}</b>",
        f"🏢 {_escape_html(company)}",
        f"📍 {_escape_html(location)}",
    ]

    if posted_time:
        lines.append(f"🕐 Posted: {_escape_html(posted_time)}")

    lines.extend([
        f"🔍 Source: {_escape_html(source)}",
        f"⏰ Detected: {now}",
        "",
        f"🔗 <a href=\"{_escape_html(link)}\">Apply Now</a>",
    ])

    text = "\n".join(lines)
    return send_message(text)


def send_startup_message(sources: list[str], keywords: list[str]) -> bool:
    """Send a notification that the sniper has started."""
    source_list = ", ".join(sources) if sources else "none"
    keyword_list = ", ".join(keywords[:5]) if keywords else "none"
    if len(keywords) > 5:
        keyword_list += f" (+{len(keywords) - 5} more)"

    text = (
        "🎯 <b>Job Sniper Activated</b>\n\n"
        f"📡 Sources: {_escape_html(source_list)}\n"
        f"🔑 Keywords: {_escape_html(keyword_list)}\n"
        "⏱ Polling: every 5 minutes\n\n"
        f"⏰ Started at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    return send_message(text)


def send_error_alert(error_msg: str) -> bool:
    """Send an error notification (e.g., all sources failing)."""
    text = (
        "⚠️ <b>Job Sniper Error</b>\n\n"
        f"<code>{_escape_html(error_msg[:500])}</code>\n\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    return send_message(text)


def send_summary(jobs_found: int, new_alerts: int) -> bool:
    """Send a polling cycle summary (optional, can be noisy)."""
    text = (
        f"📊 Scan complete: {jobs_found} jobs scanned, "
        f"{new_alerts} new alerts sent."
    )
    return send_message(text)


def _escape_html(text: str) -> str:
    """Escape special characters for Telegram HTML parse mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ======================================================================
# Quick test
# ======================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not is_configured():
        print("Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        print("See docstring in this file for instructions.")
    else:
        print("Sending test message...")
        ok = send_job_alert(
            job_title="Senior React Developer",
            company="Example Corp",
            location="Remote",
            link="https://example.com/jobs/123",
            posted_time="3 minutes ago",
            source="Test",
        )
        print(f"Result: {'SUCCESS' if ok else 'FAILED'}")
