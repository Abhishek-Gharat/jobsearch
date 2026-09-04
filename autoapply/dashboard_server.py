#!/usr/bin/env python3
"""
Dashboard Server — serves the job alerts dashboard and API.

Run:
  python dashboard_server.py              # default port 5000
  python dashboard_server.py --port 9090  # custom port
  python dashboard_server.py --open       # auto-open browser

Zero dependencies — uses only Python stdlib.
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
ALERTS_PATH = BASE / "job_alerts.json"
SEEN_PATH = BASE / "seen_jobs.json"
CONFIG_PATH = BASE / "sniper_config.json"
LOG_PATH = BASE / "sniper.log"
ALERTS_HTML = BASE / "alerts_dashboard.html"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dashboard_server")


# ======================================================================
# Data Loading
# ======================================================================

def load_alerts() -> dict:
    if ALERTS_PATH.exists():
        try:
            return json.loads(ALERTS_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"alerts": [], "stats": {"total": 0, "unread": 0}}
    return {"alerts": [], "stats": {"total": 0, "unread": 0}}


def save_alerts(state: dict) -> None:
    tmp = ALERTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    if ALERTS_PATH.exists():
        ALERTS_PATH.replace(tmp)
    else:
        tmp.rename(ALERTS_PATH)


def load_seen() -> dict:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"jobs": {}}
    return {"jobs": {}}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


def load_log(lines: int = 100) -> list[str]:
    try:
        all_lines = LOG_PATH.read_text(encoding="utf-8-sig").splitlines()
        return all_lines[-lines:]
    except Exception:
        return []


def get_overview_stats() -> dict:
    seen = load_seen()
    config = load_config()
    alerts = load_alerts()
    jobs = seen.get("jobs", {})

    log_lines = load_log(200)
    scan_count = sum(1 for l in log_lines if "Scan cycle started" in l)

    active_sources = [k for k, v in config.get("sources", {}).items() if v]

    return {
        "total_jobs": len(jobs),
        "total_alerts": alerts.get("stats", {}).get("total", 0),
        "unread_alerts": alerts.get("stats", {}).get("unread", 0),
        "scan_count": scan_count,
        "active_sources": len(active_sources),
        "sources": active_sources,
    }


# ======================================================================
# HTTP Handler
# ======================================================================

class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/":
            self._serve_redirect("/")
        elif path == "/dashboard" or path == "/overview":
            self._serve_redirect("/")
        elif path == "/alerts":
            self._serve_html_file(ALERTS_HTML)
        elif path == "/api/alerts":
            self._serve_json(load_alerts())
        elif path == "/api/stats":
            self._serve_json(get_overview_stats())
        elif path == "/api/config":
            self._serve_json(load_config())
        elif path == "/api/log":
            self._serve_json({"lines": load_log(100)})
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/alerts/read":
            self._handle_mark_read()
        else:
            self.send_error(404)

    def _handle_mark_read(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body) if body else {}
            alert_ids = data.get("alert_ids", [])

            if not alert_ids:
                self._send_json({"error": "alert_ids required"}, 400)
                return

            alerts_state = load_alerts()
            count = 0
            for alert in alerts_state.get("alerts", []):
                if alert.get("alert_id") in alert_ids:
                    alert["is_read"] = True
                    count += 1

            _recalc_stats(alerts_state)
            save_alerts(alerts_state)

            self._send_json({"marked_read": count})
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _serve_redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _serve_html_file(self, filepath: Path):
        try:
            html = filepath.read_text(encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        except FileNotFoundError:
            self.send_error(404)

    def _serve_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def _recalc_stats(state: dict) -> None:
    alerts = state.get("alerts", [])
    state["stats"] = {
        "total": len(alerts),
        "unread": sum(1 for a in alerts if not a.get("is_read", False)),
    }


# ======================================================================
# Server
# ======================================================================

def run_server(port: int = 5000, open_browser: bool = False):
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    url = f"http://127.0.0.1:{port}"

    logger.info("=" * 50)
    logger.info("Job Sniper Dashboard Server")
    logger.info("=" * 50)
    logger.info("Main dashboard : %s/", url)
    logger.info("Alerts page   : %s/alerts", url)
    logger.info("API endpoint  : %s/api/alerts", url)
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 50)

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"{url}/alerts")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped")
        server.server_close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Job Sniper Dashboard Server")
    ap.add_argument("--port", type=int, default=5000,
                    help="Port to listen on (default: 5000)")
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
