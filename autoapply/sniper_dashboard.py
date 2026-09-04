#!/usr/bin/env python3
"""
Job Sniper Dashboard — lightweight web UI for viewing job alerts.

Run:
  python sniper_dashboard.py              # default port 8080
  python sniper_dashboard.py --port 9090  # custom port
  python sniper_dashboard.py --open       # auto-open browser

Zero dependencies — uses only Python stdlib (http.server, json, pathlib).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent
SEEN_PATH = BASE / "seen_jobs.json"
CONFIG_PATH = BASE / "sniper_config.json"
ALERTS_PATH = BASE / "job_alerts.json"
LOG_PATH = BASE / "sniper.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sniper_dashboard")


# ======================================================================
# Data Loading
# ======================================================================

def load_seen() -> dict:
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"jobs": {}}


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def load_alerts() -> dict:
    try:
        return json.loads(ALERTS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"alerts": [], "stats": {"total": 0, "unread": 0}}


def load_log(lines: int = 100) -> list[str]:
    try:
        all_lines = LOG_PATH.read_text(encoding="utf-8-sig").splitlines()
        return all_lines[-lines:]
    except Exception:
        return []


def get_stats() -> dict:
    seen = load_seen()
    config = load_config()
    jobs = seen.get("jobs", {})

    # Parse log for scan stats
    log_lines = load_log(200)
    scan_count = sum(1 for l in log_lines if "Scan cycle started" in l)
    alert_count = sum(1 for l in log_lines if "NEW:" in l)

    # Count by source
    sources = {}
    for j in jobs.values():
        src = "Unknown"
        # Try to extract source from link
        link = j.get("link", "")
        if "google" in link:
            src = "Google Jobs"
        elif "linkedin" in link:
            src = "LinkedIn"
        elif "reddit" in link:
            src = "Reddit"
        sources[src] = sources.get(src, 0) + 1

    return {
        "total_jobs": len(jobs),
        "scan_count": scan_count,
        "alert_count": alert_count,
        "sources": sources,
        "config": config,
        "telegram_configured": bool(os.environ.get("TELEGRAM_CONFIGURED")),
    }


# ======================================================================
# HTML Dashboard
# ======================================================================

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>Job Sniper Dashboard</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e1e4ed;
    --muted: #8b8fa3;
    --accent: #6c5ce7;
    --green: #00b894;
    --red: #e17055;
    --yellow: #fdcb6e;
    --blue: #74b9ff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .header {
    background: linear-gradient(135deg, #1a1d27 0%, #2d1f4e 100%);
    border-bottom: 1px solid var(--border);
    padding: 20px 30px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .header h1 {
    font-size: 24px;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .header h1 span { color: var(--accent); }
  .header .subtitle { color: var(--muted); font-size: 13px; margin-top: 4px; }
  .refresh-info {
    color: var(--muted);
    font-size: 12px;
    text-align: right;
  }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }

  /* Stats Row */
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }
  .stat-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }
  .stat-card .value {
    font-size: 32px;
    font-weight: 700;
    color: var(--accent);
  }
  .stat-card .label {
    font-size: 13px;
    color: var(--muted);
    margin-top: 4px;
  }
  .stat-card.green .value { color: var(--green); }
  .stat-card.yellow .value { color: var(--yellow); }
  .stat-card.blue .value { color: var(--blue); }

  /* Section Headers */
  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }
  .section-header h2 {
    font-size: 18px;
    font-weight: 600;
  }
  .badge {
    background: var(--accent);
    color: white;
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 20px;
    font-weight: 600;
  }

  /* Job Cards */
  .jobs-grid {
    display: grid;
    gap: 12px;
    margin-bottom: 32px;
  }
  .job-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 20px;
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 12px;
    align-items: start;
    transition: border-color 0.2s;
  }
  .job-card:hover { border-color: var(--accent); }
  .job-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--text);
    text-decoration: none;
  }
  .job-title:hover { color: var(--accent); }
  .job-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 6px;
    font-size: 13px;
    color: var(--muted);
  }
  .job-meta span { display: flex; align-items: center; gap: 4px; }
  .job-right {
    text-align: right;
    white-space: nowrap;
  }
  .job-time {
    font-size: 12px;
    color: var(--green);
    font-weight: 500;
  }
  .job-source {
    font-size: 11px;
    color: var(--muted);
    margin-top: 4px;
  }
  .source-tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }
  .source-google { background: #1a472a; color: #34d399; }
  .source-linkedin { background: #1a2744; color: #60a5fa; }
  .source-reddit { background: #3d1a1a; color: #f87171; }
  .source-unknown { background: #2a2d3a; color: var(--muted); }

  /* Config Panel */
  .config-panel {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 24px;
  }
  .config-panel h3 {
    font-size: 15px;
    margin-bottom: 12px;
    color: var(--muted);
  }
  .config-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
  }
  .config-item {
    font-size: 13px;
  }
  .config-item .key { color: var(--muted); }
  .config-item .val { color: var(--text); font-weight: 500; }

  /* Log Panel */
  .log-panel {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 24px;
  }
  .log-panel h3 {
    font-size: 15px;
    margin-bottom: 12px;
    color: var(--muted);
  }
  .log-content {
    background: #0d0f14;
    border-radius: 8px;
    padding: 14px;
    font-family: 'Cascadia Code', 'Fira Code', monospace;
    font-size: 12px;
    line-height: 1.6;
    max-height: 300px;
    overflow-y: auto;
    color: #a0a4b8;
  }
  .log-line { white-space: pre-wrap; word-break: break-all; }
  .log-line.info { color: #60a5fa; }
  .log-line.warn { color: #fbbf24; }
  .log-line.error { color: #f87171; }
  .log-line.new { color: #34d399; font-weight: 600; }

  /* Empty State */
  .empty {
    text-align: center;
    padding: 60px 20px;
    color: var(--muted);
  }
  .empty .icon { font-size: 48px; margin-bottom: 16px; }
  .empty p { font-size: 14px; line-height: 1.6; }
  .empty code {
    background: var(--card);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 13px;
  }

  /* Footer */
  .footer {
    text-align: center;
    padding: 20px;
    color: var(--muted);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 20px;
  }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1><span>&#127919;</span> Job Sniper</h1>
    <div class="subtitle">Real-time job alert dashboard</div>
  </div>
  <div class="refresh-info">
    Auto-refresh: 30s<br>
    <span id="timestamp">__TIMESTAMP__</span>
  </div>
</div>

<div class="container">

  <!-- Stats -->
  <div class="stats">
    <div class="stat-card">
      <div class="value">__TOTAL_JOBS__</div>
      <div class="label">Jobs Tracked</div>
    </div>
    <div class="stat-card green">
      <div class="value">__ALERT_COUNT__</div>
      <div class="label">Alerts Sent</div>
    </div>
    <div class="stat-card yellow">
      <div class="value">__SCAN_COUNT__</div>
      <div class="label">Scans Run</div>
    </div>
    <div class="stat-card blue">
      <div class="value">__SOURCES_ACTIVE__</div>
      <div class="label">Active Sources</div>
    </div>
  </div>

  <!-- Config -->
  <div class="config-panel">
    <h3>&#9881; Configuration</h3>
    <div class="config-grid">
      <div class="config-item">
        <span class="key">Keywords:</span><br>
        <span class="val">__KEYWORDS__</span>
      </div>
      <div class="config-item">
        <span class="key">Locations:</span><br>
        <span class="val">__LOCATIONS__</span>
      </div>
      <div class="config-item">
        <span class="key">Poll Interval:</span><br>
        <span class="val">__POLL_INTERVAL__</span>
      </div>
      <div class="config-item">
        <span class="key">Max Age:</span><br>
        <span class="val">__MAX_AGE__</span>
      </div>
      <div class="config-item">
        <span class="key">Telegram:</span><br>
        <span class="val">__TELEGRAM_STATUS__</span>
      </div>
      <div class="config-item">
        <span class="key">Sources:</span><br>
        <span class="val">__SOURCES_LIST__</span>
      </div>
    </div>
  </div>

  <!-- Job Alerts -->
  <div class="section-header">
    <h2>&#128276; Job Alerts</h2>
    <span class="badge">__TOTAL_JOBS__ total</span>
  </div>

  __JOBS_HTML__

  <!-- Log -->
  <div class="log-panel">
    <h3>&#128196; Recent Activity</h3>
    <div class="log-content">
__LOG_HTML__
    </div>
  </div>

</div>

<div class="footer">
  Job Sniper Dashboard &mdash; Auto-refreshes every 30 seconds &mdash;
  Data from <code>seen_jobs.json</code> &amp; <code>sniper.log</code>
</div>

</body>
</html>"""


def format_job_card(key: str, job: dict) -> str:
    """Format a single job as an HTML card."""
    title = job.get("title", "Untitled")
    company = job.get("company", "Unknown")
    link = job.get("link", "#")
    seen_at = job.get("seen_at", "")

    # Determine source from link
    link_lower = link.lower()
    if "google" in link_lower:
        source_class = "source-google"
        source_name = "Google Jobs"
    elif "linkedin" in link_lower:
        source_class = "source-linkedin"
        source_name = "LinkedIn"
    elif "reddit" in link_lower:
        source_class = "source-reddit"
        source_name = "Reddit"
    else:
        source_class = "source-unknown"
        source_name = "RSS"

    # Format time
    time_str = ""
    if seen_at:
        try:
            dt = datetime.fromisoformat(seen_at)
            time_str = dt.strftime("%b %d, %H:%M UTC")
        except Exception:
            time_str = seen_at[:16]

    return f"""
    <div class="job-card">
      <div>
        <a href="{link}" target="_blank" class="job-title">{_esc(title)}</a>
        <div class="job-meta">
          <span>&#127970; {_esc(company)}</span>
        </div>
      </div>
      <div class="job-right">
        <div class="job-time">{time_str}</div>
        <div class="job-source">
          <span class="source-tag {source_class}">{source_name}</span>
        </div>
      </div>
    </div>"""


def format_log_line(line: str) -> str:
    """Format a log line with color coding."""
    escaped = _esc(line)
    if "NEW:" in line:
        return f'<div class="log-line new">{escaped}</div>'
    elif "[WARNING]" in line:
        return f'<div class="log-line warn">{escaped}</div>'
    elif "[ERROR]" in line:
        return f'<div class="log-line error">{escaped}</div>'
    elif "[INFO]" in line:
        return f'<div class="log-line info">{escaped}</div>'
    return f'<div class="log-line">{escaped}</div>'


def _esc(text: str) -> str:
    """HTML-escape text."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def render_dashboard() -> str:
    """Render the full dashboard HTML with current data."""
    stats = get_stats()
    seen = load_seen()
    config = load_config()
    log_lines = load_log(50)

    # Jobs HTML
    jobs = seen.get("jobs", {})
    if jobs:
        # Sort by seen_at descending
        sorted_jobs = sorted(jobs.items(),
                            key=lambda x: x[1].get("seen_at", ""),
                            reverse=True)
        jobs_html = '<div class="jobs-grid">'
        for key, job in sorted_jobs:
            jobs_html += format_job_card(key, job)
        jobs_html += "</div>"
    else:
        jobs_html = """
        <div class="empty">
          <div class="icon">&#128270;</div>
          <p>No jobs tracked yet.<br>
          Start the sniper with:<br>
          <code>python job_sniper.py</code></p>
        </div>"""

    # Log HTML
    log_html = "\n".join(format_log_line(l) for l in log_lines) or \
               '<div class="log-line">No log entries yet.</div>'

    # Config values
    keywords = ", ".join(config.get("keywords", [])[:5])
    if len(config.get("keywords", [])) > 5:
        keywords += f" (+{len(config['keywords']) - 5} more)"
    locations = ", ".join(config.get("locations", []))
    active_sources = [k for k, v in config.get("sources", {}).items() if v]
    sources_list = ", ".join(active_sources) or "none"

    # Fill template
    html = DASHBOARD_HTML
    html = html.replace("__TIMESTAMP__", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    html = html.replace("__TOTAL_JOBS__", str(stats["total_jobs"]))
    html = html.replace("__ALERT_COUNT__", str(stats["alert_count"]))
    html = html.replace("__SCAN_COUNT__", str(stats["scan_count"]))
    html = html.replace("__SOURCES_ACTIVE__", str(len(active_sources)))
    html = html.replace("__KEYWORDS__", _esc(keywords))
    html = html.replace("__LOCATIONS__", _esc(locations))
    html = html.replace("__POLL_INTERVAL__", f"{config.get('poll_interval_sec', 300)}s")
    html = html.replace("__MAX_AGE__", f"{config.get('max_age_minutes', 15)} min")
    html = html.replace("__TELEGRAM_STATUS__",
                         "Configured" if os.environ.get("TELEGRAM_CONFIGURED") else "Not set")
    html = html.replace("__SOURCES_LIST__", _esc(sources_list))
    html = html.replace("__JOBS_HTML__", jobs_html)
    html = html.replace("__LOG_HTML__", log_html)

    return html


# ======================================================================
# HTTP Server
# ======================================================================

class DashboardHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for the dashboard."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/dashboard":
            self._serve_dashboard()
        elif path == "/api/jobs":
            self._serve_json(load_seen())
        elif path == "/api/stats":
            self._serve_json(get_stats())
        elif path == "/api/log":
            self._serve_json({"lines": load_log(100)})
        elif path == "/api/config":
            self._serve_json(load_config())
        else:
            self.send_error(404)

    def _serve_dashboard(self):
        html = render_dashboard()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        """Suppress default access logging."""
        pass


def run_server(port: int = 8080, open_browser: bool = False):
    """Start the dashboard server."""
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    url = f"http://127.0.0.1:{port}"

    logger.info("=" * 50)
    logger.info("Job Sniper Dashboard")
    logger.info("=" * 50)
    logger.info("URL: %s", url)
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 50)

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard stopped")
        server.server_close()


# ======================================================================
# CLI
# ======================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Job Sniper Dashboard")
    ap.add_argument("--port", type=int, default=8080,
                    help="Port to listen on (default: 8080)")
    ap.add_argument("--open", action="store_true",
                    help="Auto-open browser on start")
    args = ap.parse_args()

    run_server(port=args.port, open_browser=args.open)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[abort] stopped")
        sys.exit(130)
