#!/usr/bin/env python3
"""
Job Sniper — 100% Free Real-Time Job Alert Engine.

Polls free RSS feeds every 5 minutes and stores new job alerts
in job_alerts.json for the web dashboard.

Sources:
  A. Google Jobs RSS (unlimited, no API key)
  B. LinkedIn Jobs RSS (public feed)
  C. Reddit r/forhire, r/csMajors (hiring posts)

Dependencies: requests, feedparser (both 100% free)
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

# ======================================================================
# Paths
# ======================================================================
BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "sniper_config.json"
SEEN_PATH = BASE / "seen_jobs.json"
ALERTS_PATH = BASE / "job_alerts.json"
LOG_PATH = BASE / "sniper.log"

# ======================================================================
# Logging
# ======================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("job_sniper")

# ======================================================================
# Configuration
# ======================================================================

DEFAULT_CONFIG = {
    "keywords": [
        "Software Engineer",
        "Frontend Developer",
        "React Developer",
        "Full Stack Developer",
    ],
    "locations": ["Remote", "India"],
    "poll_interval_sec": 300,
    "max_age_minutes": 15,
    "sources": {
        "google_jobs": True,
        "linkedin": True,
        "reddit": True,
    },
    "reddit_subreddits": ["forhire", "csMajors"],
    "send_summary": False,
}


def load_config() -> dict:
    """Load sniper_config.json or create with defaults."""
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            # Merge with defaults for any missing keys
            merged = {**DEFAULT_CONFIG, **cfg}
            merged["sources"] = {**DEFAULT_CONFIG["sources"], **cfg.get("sources", {})}
            return merged
        except Exception as exc:
            logger.warning("Failed to load config, using defaults: %s", exc)
    # Write default config
    CONFIG_PATH.write_text(
        json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Created default config at %s", CONFIG_PATH)
    return dict(DEFAULT_CONFIG)


# ======================================================================
# State Management (dedup)
# ======================================================================

def load_seen() -> dict:
    """Load seen_jobs.json."""
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"jobs": {}}
    return {"jobs": {}}


def save_seen(state: dict) -> None:
    """Save seen_jobs.json atomically."""
    tmp = SEEN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    if SEEN_PATH.exists():
        SEEN_PATH.replace(tmp)
    else:
        tmp.rename(SEEN_PATH)


def job_key(title: str, link: str) -> str:
    """Generate a unique key for a job posting."""
    # Use link as primary key (most stable), fall back to title hash
    if link and link.startswith("http"):
        return hashlib.md5(link.encode()).hexdigest()
    return hashlib.md5(f"{title}".encode()).hexdigest()


def is_seen(state: dict, key: str) -> bool:
    """Check if a job has already been seen."""
    return key in state.get("jobs", {})


def mark_seen(state: dict, key: str, title: str, company: str, link: str) -> None:
    """Record a job as seen."""
    state.setdefault("jobs", {})[key] = {
        "title": title,
        "company": company,
        "link": link,
        "seen_at": datetime.now(timezone.utc).isoformat(),
    }
    # Prune entries older than 7 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    state["jobs"] = {
        k: v for k, v in state["jobs"].items()
        if v.get("seen_at", "") > cutoff
    }


# ======================================================================
# Alert Storage (job_alerts.json)
# ======================================================================

def load_alerts() -> dict:
    """Load job_alerts.json."""
    if ALERTS_PATH.exists():
        try:
            return json.loads(ALERTS_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"alerts": [], "stats": {"total": 0, "unread": 0}}
    return {"alerts": [], "stats": {"total": 0, "unread": 0}}


def save_alerts(state: dict) -> None:
    """Save job_alerts.json atomically."""
    tmp = ALERTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    if ALERTS_PATH.exists():
        ALERTS_PATH.replace(tmp)
    else:
        tmp.rename(ALERTS_PATH)


def prune_old_alerts(state: dict, max_age_hours: int = 24) -> int:
    """Remove alerts older than max_age_hours. Returns count removed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    before = len(state.get("alerts", []))
    state["alerts"] = [
        a for a in state.get("alerts", [])
        if a.get("created_at", "") > cutoff
    ]
    removed = before - len(state["alerts"])
    if removed > 0:
        _recalc_stats(state)
    return removed


def _recalc_stats(state: dict) -> None:
    """Recalculate alert stats."""
    alerts = state.get("alerts", [])
    state["stats"] = {
        "total": len(alerts),
        "unread": sum(1 for a in alerts if not a.get("is_read", False)),
    }


def add_alert(state: dict, job: dict) -> str | None:
    """
    Add a new job alert to the state.

    Returns the alert_id if added, None if duplicate.
    """
    # Check for duplicate by link
    link = job.get("link", "")
    for existing in state.get("alerts", []):
        if existing.get("job_url") == link:
            return None

    alert_id = str(uuid.uuid4())[:8]
    now_iso = datetime.now(timezone.utc).isoformat()

    # Determine source category from source string
    source_raw = job.get("source", "")
    if "google" in source_raw.lower():
        source = "google_jobs"
    elif "linkedin" in source_raw.lower():
        source = "linkedin"
    elif "reddit" in source_raw.lower():
        source = "reddit"
    else:
        source = "other"

    alert = {
        "alert_id": alert_id,
        "job_title": job.get("title", "Untitled"),
        "company": job.get("company", "Unknown"),
        "location": job.get("location", "Not specified"),
        "job_url": link,
        "source": source,
        "posted_time": job.get("posted_time", ""),
        "is_read": False,
        "created_at": now_iso,
    }

    state.setdefault("alerts", []).insert(0, alert)  # newest first
    _recalc_stats(state)
    return alert_id


# ======================================================================
# Time Utilities
# ======================================================================

def parse_pub_date(date_str: str) -> datetime | None:
    """Parse RSS pubDate into a timezone-aware datetime."""
    if not date_str:
        return None
    try:
        # Try RFC 2822 format (standard for RSS)
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    try:
        # Try ISO format
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    try:
        # Try common formats
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                     "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def is_fresh(pub_date: datetime | None, max_age_minutes: int) -> bool:
    """Check if a job was posted within the allowed time window."""
    if pub_date is None:
        return False  # Unknown age = skip (conservative)
    now = datetime.now(timezone.utc)
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    age = (now - pub_date).total_seconds() / 60
    return age <= max_age_minutes


def format_age(pub_date: datetime | None) -> str:
    """Format a datetime as a human-readable age string."""
    if pub_date is None:
        return "unknown time"
    now = datetime.now(timezone.utc)
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    age_sec = (now - pub_date).total_seconds()
    if age_sec < 60:
        return "just now"
    elif age_sec < 3600:
        mins = int(age_sec / 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif age_sec < 86400:
        hours = int(age_sec / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(age_sec / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"


# ======================================================================
# Source A: Google Jobs RSS
# ======================================================================

def build_google_jobs_url(keyword: str, location: str = "") -> str:
    """
    Construct a Google Jobs search URL that returns RSS.

    Google Jobs RSS is a free, unlimited feed — no API key needed.
    """
    query = keyword
    if location and location.lower() != "remote":
        query += f" {location}"
    elif location and location.lower() == "remote":
        query += " remote"

    encoded = quote_plus(query)
    # Google Jobs RSS endpoint
    return (
        f"https://www.google.com/search?q={encoded}"
        f"&ibp=htl;jobs&format=rss"
    )


def fetch_google_jobs(keyword: str, location: str, max_age: int) -> list[dict]:
    """Fetch jobs from Google Jobs RSS feed."""
    url = build_google_jobs_url(keyword, location)
    jobs = []

    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.debug("Google Jobs RSS parse error for '%s': %s", keyword,
                         feed.bozo_exception)
            return jobs

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            pub_date = parse_pub_date(entry.get("published", ""))

            if not title or not link:
                continue

            if not is_fresh(pub_date, max_age):
                continue

            # Extract company from title (Google Jobs format: "Title - Company")
            company = "Unknown"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                title = parts[0].strip()
                company = parts[1].strip()

            jobs.append({
                "title": title,
                "company": company,
                "location": location or "Not specified",
                "link": link,
                "posted_time": format_age(pub_date),
                "source": "Google Jobs",
                "pub_date": pub_date,
            })

    except Exception as exc:
        logger.warning("Google Jobs fetch failed for '%s': %s", keyword, exc)

    return jobs


# ======================================================================
# Source B: LinkedIn Jobs RSS
# ======================================================================

def build_linkedin_url(keyword: str, location: str = "") -> str:
    """Construct LinkedIn Jobs RSS URL."""
    params = {
        "keywords": keyword,
        "f_TPR": "r604800",  # Past week
    }
    if location and location.lower() != "remote":
        params["location"] = location

    query = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
    return f"https://www.linkedin.com/jobs/search/rss?{query}"


def fetch_linkedin_jobs(keyword: str, location: str, max_age: int) -> list[dict]:
    """Fetch jobs from LinkedIn Jobs RSS feed."""
    url = build_linkedin_url(keyword, location)
    jobs = []

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.debug("LinkedIn RSS returned HTTP %d", resp.status_code)
            return jobs

        feed = feedparser.parse(resp.text)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            pub_date = parse_pub_date(entry.get("published", ""))
            summary = entry.get("summary", "")

            if not title or not link:
                continue

            if not is_fresh(pub_date, max_age):
                continue

            # Extract company from summary or title
            company = "Unknown"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                title = parts[0].strip()
                company = parts[1].strip()

            # Try to extract location from summary
            loc = location or "Not specified"
            if summary:
                loc_match = re.search(r"Location:\s*([^<\n]+)", summary)
                if loc_match:
                    loc = loc_match.group(1).strip()

            jobs.append({
                "title": title,
                "company": company,
                "location": loc,
                "link": link,
                "posted_time": format_age(pub_date),
                "source": "LinkedIn",
                "pub_date": pub_date,
            })

    except Exception as exc:
        logger.warning("LinkedIn RSS fetch failed for '%s': %s", keyword, exc)

    return jobs


# ======================================================================
# Source C: Reddit Hiring Posts
# ======================================================================

def fetch_reddit_jobs(subreddit: str, keywords: list[str],
                      max_age: int) -> list[dict]:
    """Fetch hiring posts from a subreddit RSS feed."""
    url = f"https://www.reddit.com/r/{subreddit}.rss"
    jobs = []

    try:
        headers = {
            "User-Agent": "JobSniper/1.0 (free RSS parser)"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.debug("Reddit RSS r/%s returned HTTP %d", subreddit,
                         resp.status_code)
            return jobs

        feed = feedparser.parse(resp.text)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            pub_date = parse_pub_date(entry.get("published", ""))

            if not title or not link:
                continue

            # Filter for hiring-related posts
            title_lower = title.lower()
            is_hiring = any(term in title_lower
                           for term in ["hiring", "looking for", "[for hire]",
                                        "we're hiring", "we are hiring"])
            if not is_hiring:
                continue

            if not is_fresh(pub_date, max_age):
                continue

            # Check if any target keywords match
            has_keyword = any(kw.lower() in title_lower for kw in keywords)
            if not has_keyword and keywords:
                continue

            # Extract company from title (often "[Company] Title" or "Title - Company")
            company = "Reddit User"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                if len(parts[1]) < 60:
                    company = parts[1].strip()
                    title = parts[0].strip()

            jobs.append({
                "title": title,
                "company": company,
                "location": "Remote (Reddit)",
                "link": link,
                "posted_time": format_age(pub_date),
                "source": f"Reddit r/{subreddit}",
                "pub_date": pub_date,
            })

    except Exception as exc:
        logger.warning("Reddit r/%s fetch failed: %s", subreddit, exc)

    return jobs


# ======================================================================
# Main Sniper Loop
# ======================================================================

def scan_all_sources(config: dict) -> list[dict]:
    """Scan all enabled sources and return fresh jobs."""
    all_jobs = []
    max_age = config.get("max_age_minutes", 15)
    sources = config.get("sources", {})
    keywords = config.get("keywords", [])
    locations = config.get("locations", ["Remote"])

    # Source A: Google Jobs
    if sources.get("google_jobs"):
        for kw in keywords:
            for loc in locations:
                jobs = fetch_google_jobs(kw, loc, max_age)
                all_jobs.extend(jobs)
                logger.debug("Google '%s' in '%s': %d fresh jobs", kw, loc, len(jobs))

    # Source B: LinkedIn
    if sources.get("linkedin"):
        for kw in keywords:
            for loc in locations:
                jobs = fetch_linkedin_jobs(kw, loc, max_age)
                all_jobs.extend(jobs)
                logger.debug("LinkedIn '%s' in '%s': %d fresh jobs", kw, loc, len(jobs))

    # Source C: Reddit
    if sources.get("reddit"):
        subreddits = config.get("reddit_subreddits", ["forhire"])
        for sub in subreddits:
            jobs = fetch_reddit_jobs(sub, keywords, max_age)
            all_jobs.extend(jobs)
            logger.debug("Reddit r/%s: %d fresh jobs", sub, len(jobs))

    return all_jobs


def run_cycle(config: dict, state: dict, alerts_state: dict) -> int:
    """
    Run one polling cycle.

    Returns the number of new alerts saved.
    """
    logger.info("--- Scan cycle started ---")
    fresh_jobs = scan_all_sources(config)
    logger.info("Found %d fresh jobs across all sources", len(fresh_jobs))

    new_alerts = 0
    for job in fresh_jobs:
        key = job_key(job["title"], job["link"])
        if is_seen(state, key):
            logger.debug("Already seen: %s", job["title"][:60])
            continue

        # New job — save to alerts
        logger.info("NEW: %s @ %s (%s) [%s]",
                     job["title"][:50], job["company"], job["posted_time"],
                     job["source"])

        alert_id = add_alert(alerts_state, job)
        if alert_id:
            new_alerts += 1
            logger.info("  Alert saved: %s", alert_id)

        mark_seen(state, key, job["title"], job["company"], job["link"])

    # Prune old alerts (older than 24h)
    pruned = prune_old_alerts(alerts_state)

    # Save state after processing all jobs
    save_seen(state)
    save_alerts(alerts_state)
    logger.info("--- Scan cycle done: %d new alerts, %d pruned ---",
                new_alerts, pruned)
    return new_alerts


def main_loop() -> None:
    """Main polling loop — runs forever."""
    config = load_config()
    state = load_seen()
    alerts_state = load_alerts()
    interval = config.get("poll_interval_sec", 300)

    logger.info("=" * 60)
    logger.info("Job Sniper starting...")
    logger.info("Keywords: %s", config.get("keywords"))
    logger.info("Locations: %s", config.get("locations"))
    logger.info("Sources: %s", {k: v for k, v in config.get("sources", {}).items() if v})
    logger.info("Poll interval: %ds", interval)
    logger.info("Max age: %d minutes", config.get("max_age_minutes", 15))
    logger.info("=" * 60)

    # Initial scan
    total_alerts = 0
    try:
        total_alerts += run_cycle(config, state, alerts_state)
    except Exception as exc:
        logger.error("Initial scan failed: %s", exc)

    # Polling loop
    while True:
        logger.info("Sleeping %ds until next scan...", interval)
        time.sleep(interval)

        try:
            # Reload config in case it was edited
            config = load_config()
            interval = config.get("poll_interval_sec", 300)

            alerts = run_cycle(config, state, alerts_state)
            total_alerts += alerts

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            break
        except Exception as exc:
            logger.error("Scan cycle failed: %s", exc, exc_info=True)
            # Don't crash — wait for next cycle
            time.sleep(10)

    logger.info("Job Sniper stopped. Total alerts saved: %d", total_alerts)


# ======================================================================
# CLI
# ======================================================================

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Job Sniper — free real-time job alerts")
    ap.add_argument("--once", action="store_true",
                    help="Run a single scan cycle and exit")
    ap.add_argument("--status", action="store_true",
                    help="Show config and seen jobs count")
    args = ap.parse_args()

    if args.status:
        config = load_config()
        state = load_seen()
        alerts = load_alerts()
        print("=== Job Sniper Status ===")
        print(f"Keywords: {config.get('keywords')}")
        print(f"Locations: {config.get('locations')}")
        print(f"Sources: {config.get('sources')}")
        print(f"Poll interval: {config.get('poll_interval_sec')}s")
        print(f"Max age: {config.get('max_age_minutes')} minutes")
        print(f"Seen jobs: {len(state.get('jobs', {}))}")
        print(f"Total alerts: {alerts.get('stats', {}).get('total', 0)}")
        print(f"Unread alerts: {alerts.get('stats', {}).get('unread', 0)}")
        print(f"Config file: {CONFIG_PATH}")
        print(f"Seen file: {SEEN_PATH}")
        print(f"Alerts file: {ALERTS_PATH}")
        print(f"Log file: {LOG_PATH}")
        return 0

    if args.once:
        config = load_config()
        state = load_seen()
        alerts_state = load_alerts()
        alerts = run_cycle(config, state, alerts_state)
        print(f"Scan complete. {alerts} new alerts saved.")
        return 0

    main_loop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[abort] Job Sniper stopped")
        sys.exit(130)
