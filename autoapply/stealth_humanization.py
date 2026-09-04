#!/usr/bin/env python3
"""
Stealth Humanization Module for AutoApply v2.

Provides three anti-bot-detection countermeasures:
  1. Humanized typing with Gaussian delays and typo simulation
  2. Proxy injection and IP validation
  3. Browser profile warm-up routine

Designed to work alongside BrowserOS MCP via the supervisor/worker architecture.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("stealth_humanization")

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
PROFILE_DIR = BASE.parent / "browser_profile"


# ======================================================================
# Fix 1: Humanized Typing Wrapper
# ======================================================================

# Common QWERTY neighbors for realistic typos
_NEIGHBORS = {
    "a": "sqwz", "b": "vngh", "c": "xdfv", "d": "sfcer", "e": "wrsdf",
    "f": "dgrtv", "g": "fhtyb", "h": "gjyun", "i": "uojk", "j": "hkuim",
    "k": "jloi", "l": "kop", "m": "njk", "n": "bhjm", "o": "iplk",
    "p": "ol", "q": "wa", "r": "edft", "s": "awedx", "t": "rfgy",
    "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu",
    "z": "asx",
}


def _gaussian_delay(mean: float = 0.08, std_dev: float = 0.03,
                    min_val: float = 0.02, max_val: float = 0.25) -> float:
    """Sample a Gaussian-distributed delay clamped to [min_val, max_val]."""
    delay = random.gauss(mean, std_dev)
    return max(min_val, min(max_val, delay))


def _pick_typo(char: str) -> str:
    """Return a plausible adjacent-key typo for the given character."""
    lower = char.lower()
    neighbors = _NEIGHBORS.get(lower, "")
    if not neighbors:
        return char
    typo = random.choice(neighbors)
    return typo.upper() if char.isupper() else typo


def humanized_type(text: str, mean_delay: float = 0.08, std_dev: float = 0.03,
                   typo_chance: float = 0.02, pause_chance: float = 0.08) -> list[dict]:
    """
    Generate a sequence of keyboard events simulating human typing.

    Returns a list of action dicts that can be replayed via MCP:
      {"kind": "press", "key": "<char>"}       - regular keystroke
      {"kind": "press", "key": "Backspace"}     - typo correction
      {"kind": "wait",  "ms": <int>}            - inter-keystroke delay

    Parameters
    ----------
    text : str
        The text to type.
    mean_delay : float
        Mean seconds between keystrokes (Gaussian).
    std_dev : float
        Standard deviation for the delay distribution.
    typo_chance : float
        Probability (0-1) of inserting a typo + backspace before each char.
    pause_chance : float
        Probability (0-1) of inserting a longer "thinking" pause (0.3-0.8s).
    """
    events: list[dict] = []
    for i, char in enumerate(text):
        # Occasional longer pause (simulates thinking / reading)
        if i > 0 and random.random() < pause_chance:
            think_ms = int(random.gauss(500, 150))
            think_ms = max(200, min(800, think_ms))
            events.append({"kind": "wait", "ms": think_ms})

        # Occasional typo
        if random.random() < typo_chance and char.isalnum():
            typo = _pick_typo(char)
            delay_ms = int(_gaussian_delay(mean_delay, std_dev) * 1000)
            events.append({"kind": "wait", "ms": delay_ms})
            events.append({"kind": "press", "key": typo})
            # Pause before noticing typo
            time.sleep(0.12)
            events.append({"kind": "wait", "ms": int(random.gauss(150, 40))})
            events.append({"kind": "press", "key": "Backspace"})
            # Pause after correction
            events.append({"kind": "wait", "ms": int(random.gauss(120, 30))})

        # Normal keystroke
        delay_ms = int(_gaussian_delay(mean_delay, std_dev) * 1000)
        events.append({"kind": "wait", "ms": delay_ms})
        if char == " ":
            events.append({"kind": "press", "key": " "})
        else:
            events.append({"kind": "type", "text": char})

    return events


def estimate_typing_duration(text: str, mean_delay: float = 0.08,
                             std_dev: float = 0.03, typo_chance: float = 0.02,
                             pause_chance: float = 0.08) -> float:
    """Estimate total seconds to type `text` with the given parameters."""
    base_time = len(text) * mean_delay
    typo_extra = len(text) * typo_chance * 0.30  # ~150ms backspace + 150ms correction
    pause_extra = len(text) * pause_chance * 0.50  # ~500ms thinking pauses
    return base_time + typo_extra + pause_extra


def format_mcp_actions(events: list[dict]) -> str:
    """
    Convert event list to a human-readable MCP instruction block.
    Used in worker prompts to guide the LLM agent.
    """
    lines = []
    for ev in events:
        if ev["kind"] == "wait":
            lines.append(f"  wait {ev['ms']}ms")
        elif ev["kind"] == "press":
            lines.append(f"  press {ev['key']}")
        elif ev["kind"] == "type":
            lines.append(f"  type '{ev['text']}'")
    return "\n".join(lines)


# ======================================================================
# Fix 2: Proxy Injection & Validation
# ======================================================================

def load_proxy_config() -> dict:
    """Load proxy settings from config.json."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        return cfg.get("proxy", {})
    except Exception as exc:
        logger.warning("Failed to load config.json for proxy: %s", exc)
        return {}


def get_current_ip(timeout: int = 10) -> str | None:
    """
    Fetch the public IP from api.ipify.org.
    Returns the IP string or None on failure.
    """
    try:
        req = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data.get("ip")
    except Exception as exc:
        logger.error("Failed to fetch public IP: %s", exc)
        return None


def validate_proxy() -> dict:
    """
    Validate that the proxy is active by comparing current IP against local IP.
    Returns a result dict with status details.
    """
    proxy_cfg = load_proxy_config()
    if not proxy_cfg.get("enabled"):
        return {"status": "skipped", "reason": "proxy not enabled in config.json"}

    local_ip = get_current_ip()
    if local_ip is None:
        return {"status": "error", "reason": "could not fetch public IP"}

    # TODO: Replace with your actual local/ISP IP address
    # When proxy is working, the IP should NOT match this value
    LOCAL_IP_PLACEHOLDER = "0.0.0.0"  # <-- FILL IN YOUR REAL LOCAL IP

    if local_ip == LOCAL_IP_PLACEHOLDER:
        return {
            "status": "error",
            "reason": "public IP matches local ISP IP - proxy may not be active",
            "detected_ip": local_ip,
            "expected_different_from": LOCAL_IP_PLACEHOLDER,
        }

    return {
        "status": "ok",
        "detected_ip": local_ip,
        "proxy_server": proxy_cfg.get("server_url", "unknown"),
    }


def build_chromium_proxy_args() -> list[str]:
    """
    Build Chrome/Chromium command-line flags for proxy routing.
    Returns a list of args to append to the browser launch command.

    Usage with BrowserOS MCP:
      If BrowserOS accepts extra Chrome args, pass these via its config.
      If not, use a local proxy chain (e.g., proxychains on Linux, Proxifier on Windows).
    """
    proxy_cfg = load_proxy_config()
    if not proxy_cfg.get("enabled") or not proxy_cfg.get("server_url"):
        return []

    server = proxy_cfg["server_url"]
    username = proxy_cfg.get("username", "")
    password = proxy_cfg.get("password", "")

    args = [f"--proxy-server={server}"]

    # Chrome doesn't support inline auth in --proxy-server.
    # Use --proxy-server + --proxy-auth (or handle via PAC file).
    if username and password:
        # Option A: Use --proxy-auth (not supported on all Chrome versions)
        # Option B: Set up local PAC or use extension-based auth
        # For BrowserOS, the safest approach is to configure the proxy
        # at the OS/network level rather than via Chrome flags.
        logger.info("Proxy auth detected - configure at OS level for Chrome")

    return args


def get_browser_profile_dir() -> str:
    """Return the persistent browser profile directory path."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return str(PROFILE_DIR)


# ======================================================================
# Fix 3: Warm-Up Routine
# ======================================================================

WARMUP_URL = "https://en.wikipedia.org/wiki/Random_article"
WARMUP_SCROLL_COUNT_MIN = 3
WARMUP_SCROLL_COUNT_MAX = 5
WARMUP_SCROLL_DELAY_MEAN = 1.5
WARMUP_SCROLL_DELAY_STD = 0.4
WARMUP_SETTLE_MEAN = 4.0
WARMUP_SETTLE_STD = 1.0


def generate_warmup_sequence() -> list[dict]:
    """
    Generate a sequence of MCP actions for the warm-up routine.

    Returns a list of action dicts:
      {"action": "navigate", "url": "<url>"}
      {"action": "scroll", "pixels": <int>, "wait_ms": <int>}
      {"action": "wait", "ms": <int>}
      {"action": "close_tab"}
    """
    scroll_count = random.randint(WARMUP_SCROLL_COUNT_MIN, WARMUP_SCROLL_COUNT_MAX)
    sequence = []

    # 1. Open neutral page
    sequence.append({"action": "navigate", "url": WARMUP_URL})

    # 2. Wait for page load
    load_wait = int(random.gauss(4000, 800))
    load_wait = max(2000, min(6000, load_wait))
    sequence.append({"action": "wait", "ms": load_wait})

    # 3. Scroll in random increments
    for i in range(scroll_count):
        scroll_px = random.randint(200, 600)
        if random.random() < 0.3:
            scroll_px = -scroll_px  # occasional scroll up
        delay = int(random.gauss(WARMUP_SCROLL_DELAY_MEAN * 1000,
                                 WARMUP_SCROLL_DELAY_STD * 1000))
        delay = max(800, min(3000, delay))
        sequence.append({"action": "scroll", "pixels": scroll_px, "wait_ms": delay})

    # 4. Settle before closing
    settle = int(random.gauss(WARMUP_SETTLE_MEAN * 1000,
                              WARMUP_SETTLE_STD * 1000))
    settle = max(2000, min(7000, settle))
    sequence.append({"action": "wait", "ms": settle})

    # 5. Close warm-up tab
    sequence.append({"action": "close_tab"})

    return sequence


def generate_warmup_mcp_script() -> str:
    """
    Generate a human-readable script for the LLM worker to execute
    the warm-up routine via BrowserOS MCP tools.
    """
    seq = generate_warmup_sequence()
    lines = [
        "## WARM-UP ROUTINE (execute before navigating to job sites)",
        "Open a new tab to a neutral page, scroll naturally, then close it.",
        "This establishes a realistic browsing session fingerprint.",
        "",
    ]
    for step in seq:
        act = step["action"]
        if act == "navigate":
            lines.append(f"1. Open new tab -> navigate to: {step['url']}")
        elif act == "wait":
            lines.append(f"   Wait {step['ms']}ms for page rendering")
        elif act == "scroll":
            direction = "down" if step["pixels"] > 0 else "up"
            lines.append(f"   Scroll {direction} {abs(step['pixels'])}px, "
                         f"then wait {step['wait_ms']}ms")
        elif act == "close_tab":
            lines.append("2. Close this warm-up tab")
    lines.append("")
    lines.append("After warm-up, open a NEW tab and navigate to the target job site.")
    return "\n".join(lines)


# ======================================================================
# Config Update Helper
# ======================================================================

def update_config_with_proxy(server_url: str = "", username: str = "",
                              password: str = "", enabled: bool = False) -> None:
    """Add or update the proxy section in config.json."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        cfg = {}

    cfg["proxy"] = {
        "enabled": enabled,
        "server_url": server_url,
        "username": username,
        "password": password,
    }

    # Also add browser profile settings if not present
    if "browser_profile" not in cfg:
        cfg["browser_profile"] = {
            "persist": True,
            "profile_dir": str(PROFILE_DIR),
            "max_age_days": 30,
        }

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Updated config.json with proxy and profile settings")


def update_config_with_browser_profile(enabled: bool = True) -> None:
    """Add or update the browser_profile section in config.json."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        cfg = {}

    cfg["browser_profile"] = {
        "persist": enabled,
        "profile_dir": str(PROFILE_DIR),
        "max_age_days": 30,
    }

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Updated config.json with browser profile settings")


# ======================================================================
# Standalone CLI for quick testing
# ======================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 60)
    print("STEALTH HUMANIZATION MODULE - Diagnostic")
    print("=" * 60)

    # 1. Proxy check
    print("\n--- Proxy Status ---")
    proxy_result = validate_proxy()
    print(f"  Status: {proxy_result['status']}")
    for k, v in proxy_result.items():
        if k != "status":
            print(f"  {k}: {v}")

    # 2. IP check
    print("\n--- Current Public IP ---")
    ip = get_current_ip()
    print(f"  IP: {ip or 'UNAVAILABLE'}")

    # 3. Typing simulation
    print("\n--- Typing Simulation ---")
    test_text = "Your Name"
    events = humanized_type(test_text)
    typed_chars = sum(1 for e in events if e["kind"] in ("type", "press") and e.get("key", "") != "Backspace")
    typos = sum(1 for e in events if e["kind"] == "press" and e.get("key") == "Backspace")
    total_wait_ms = sum(e["ms"] for e in events if e["kind"] == "wait")
    print(f"  Text: '{test_text}' ({len(test_text)} chars)")
    print(f"  Events generated: {len(events)}")
    print(f"  Typos inserted: {typos}")
    print(f"  Total wait time: {total_wait_ms}ms ({total_wait_ms/1000:.2f}s)")

    # 4. Warm-up sequence
    print("\n--- Warm-Up Sequence ---")
    warmup = generate_warmup_sequence()
    print(f"  Steps: {len(warmup)}")
    for i, step in enumerate(warmup):
        print(f"  [{i+1}] {step['action']}: {step}")

    # 5. Profile directory
    print(f"\n--- Browser Profile ---")
    print(f"  Directory: {get_browser_profile_dir()}")
    print(f"  Exists: {PROFILE_DIR.exists()}")

    print("\n" + "=" * 60)
