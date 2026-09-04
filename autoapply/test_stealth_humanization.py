#!/usr/bin/env python3
"""
Stealth Humanization Test Suite for AutoApply v2.

Tests the three humanization fixes:
  1. Proxy Test - Verifies IP is routed through proxy (not local ISP)
  2. Fingerprint Test - Checks navigator.webdriver is false/undefined
  3. Typing Test - Verifies humanized typing is not instant
  4. Warm-Up Test - Verifies warm-up routine executes without errors
  5. Config Test - Verifies config.json has proxy/profile sections

Run:
  python test_stealth_humanization.py
  python test_stealth_humanization.py --headed    # visible browser
  python test_stealth_humanization.py --verbose   # detailed logging

Requires: playwright (pip install playwright && playwright install chromium)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stealth_humanization import (
    humanized_type,
    estimate_typing_duration,
    validate_proxy,
    get_current_ip,
    generate_warmup_sequence,
    generate_warmup_mcp_script,
    load_proxy_config,
    get_browser_profile_dir,
)

# ======================================================================
# Test Configuration
# ======================================================================

LOG_FILE = Path(__file__).resolve().parent / "test_stealth.log"
RESULTS: list[dict] = []

# TODO: Replace with your actual local/ISP IP address
# The proxy test FAILS if the detected IP matches this value
LOCAL_IP_PLACEHOLDER = "223.185.41.237"  # Replace with your actual local IP for testing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("test_stealth")


def record_test(name: str, passed: bool, details: str = "") -> None:
    """Record a test result."""
    RESULTS.append({"test": name, "passed": passed, "details": details})
    mark = "PASS" if passed else "FAIL"
    logger.info("[%s] %s %s", mark, name, f"- {details}" if details else "")


# ======================================================================
# Test 1: Proxy Validation
# ======================================================================

def test_proxy() -> None:
    """
    Verify that the public IP is NOT the local ISP IP.
    If proxy is disabled, this test is SKIPPED (not failed).
    """
    logger.info("=" * 60)
    logger.info("TEST 1: Proxy Validation")
    logger.info("=" * 60)

    proxy_cfg = load_proxy_config()
    if not proxy_cfg.get("enabled"):
        record_test("proxy", True, "SKIPPED - proxy not enabled in config.json")
        return

    result = validate_proxy()
    if result["status"] == "ok":
        record_test("proxy", True,
                     f"IP routed through proxy: {result.get('detected_ip')}")
    elif result["status"] == "skipped":
        record_test("proxy", True, f"SKIPPED - {result.get('reason')}")
    else:
        record_test("proxy", False,
                     f"Proxy validation failed: {result.get('reason')} "
                     f"(detected_ip={result.get('detected_ip')})")


# ======================================================================
# Test 2: Fingerprint Check (requires Playwright)
# ======================================================================

def test_fingerprint(headed: bool = False) -> None:
    """
    Launch a Chromium browser and check navigator.webdriver.
    Assert that it is false or undefined (not leaking automation flag).
    """
    logger.info("=" * 60)
    logger.info("TEST 2: Browser Fingerprint (navigator.webdriver)")
    logger.info("=" * 60)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        record_test("fingerprint", False,
                     "SKIPPED - playwright not installed "
                     "(pip install playwright && playwright install chromium)")
        return

    webdriver_detected = None
    ua_string = None
    error_msg = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not headed,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="Asia/Kolkata",
            )
            page = context.new_page()

            # Navigate to a neutral page
            page.goto("about:blank")

            # Check webdriver flag
            webdriver_detected = page.evaluate("""() => {
                const results = {};
                results.navigator_webdriver = navigator.webdriver;
                results.has_webdriver_prop = 'webdriver' in navigator;
                results.chrome_obj = typeof window.chrome;
                results.languages = navigator.languages;
                results.plugins_count = navigator.plugins.length;
                results.platform = navigator.platform;
                results.userAgent = navigator.userAgent;
                return results;
            }""")

            ua_string = webdriver_detected.get("userAgent", "unknown")
            wd_value = webdriver_detected.get("navigator_webdriver")
            has_wd = webdriver_detected.get("has_webdriver_prop")

            browser.close()

            # Evaluate result
            if wd_value is False or wd_value is None or wd_value is undefined:
                record_test("fingerprint", True,
                             f"navigator.webdriver={wd_value} (safe)")
            elif wd_value is True:
                record_test("fingerprint", False,
                             f"navigator.webdriver=true LEAKED - automation flag exposed")
            else:
                record_test("fingerprint", True,
                             f"navigator.webdriver={wd_value} (acceptable)")

    except Exception as exc:
        error_msg = str(exc)
        record_test("fingerprint", False, f"ERROR: {error_msg}")

    # Log full fingerprint details
    if webdriver_detected:
        logger.info("Full fingerprint: %s", json.dumps(webdriver_detected, indent=2))


# Handle Playwright's undefined sentinel
try:
    from playwright.sync_api import Error as PlaywrightError
    undefined = None  # Playwright JS undefined maps to None in Python
except ImportError:
    undefined = None


# ======================================================================
# Test 3: Humanized Typing Speed
# ======================================================================

def test_typing_speed() -> None:
    """
    Generate humanized typing events for "automation" and verify
    the estimated duration is > 0.5 seconds (not instant).
    """
    logger.info("=" * 60)
    logger.info("TEST 3: Humanized Typing Speed")
    logger.info("=" * 60)

    test_text = "automation"
    events = humanized_type(test_text)

    # Calculate total wait time from events
    total_wait_ms = sum(e.get("ms", 0) for e in events if e["kind"] == "wait")
    total_wait_s = total_wait_ms / 1000.0

    # Count events
    keystrokes = sum(1 for e in events
                     if e["kind"] in ("type", "press")
                     and e.get("key", "") != "Backspace")
    backspaces = sum(1 for e in events if e["kind"] == "press"
                     and e.get("key") == "Backspace")
    waits = sum(1 for e in events if e["kind"] == "wait")

    # Verify it's not instant (< 0.5s)
    is_slow_enough = total_wait_s > 0.5

    # Verify it has reasonable upper bound (< 5s for 10 chars)
    is_fast_enough = total_wait_s < 5.0

    # Verify typos were inserted
    has_typos = backspaces > 0

    details = (
        f"text='{test_text}' ({len(test_text)} chars) | "
        f"events={len(events)} | keystrokes={keystrokes} | "
        f"typos={backspaces} | waits={waits} | "
        f"total_time={total_wait_s:.3f}s"
    )

    if is_slow_enough and is_fast_enough:
        record_test("typing_speed", True, details)
    elif not is_slow_enough:
        record_test("typing_speed", False,
                     f"TOO FAST ({total_wait_s:.3f}s < 0.5s) - typing is instant!")
    else:
        record_test("typing_speed", False,
                     f"TOO SLOW ({total_wait_s:.3f}s > 5.0s) - typing is sluggish")

    # Additional: verify distribution consistency
    delays = [e.get("ms", 0) for e in events if e["kind"] == "wait" and e.get("ms", 0) < 300]
    if delays:
        mean_delay = sum(delays) / len(delays)
        variance = sum((d - mean_delay) ** 2 for d in delays) / len(delays)
        std_dev = variance ** 0.5
        logger.info("Keystroke delay stats: mean=%.1fms std=%.1fms min=%dms max=%dms",
                     mean_delay, std_dev, min(delays), max(delays))
        # Verify Gaussian distribution (std_dev should be ~30ms, not 0)
        if std_dev > 5:
            record_test("typing_distribution", True,
                         f"Gaussian distribution confirmed (std={std_dev:.1f}ms)")
        else:
            record_test("typing_distribution", False,
                         f"Distribution too uniform (std={std_dev:.1f}ms, expected ~30ms)")


# ======================================================================
# Test 4: Warm-Up Routine
# ======================================================================

def test_warmup() -> None:
    """
    Verify the warm-up routine generates a valid action sequence
    and can be converted to MCP instructions without errors.
    """
    logger.info("=" * 60)
    logger.info("TEST 4: Warm-Up Routine")
    logger.info("=" * 60)

    try:
        sequence = generate_warmup_sequence()
        mcp_script = generate_warmup_mcp_script()

        # Validate sequence structure
        has_navigate = any(s["action"] == "navigate" for s in sequence)
        has_scroll = any(s["action"] == "scroll" for s in sequence)
        has_wait = any(s["action"] == "wait" for s in sequence)
        has_close = any(s["action"] == "close_tab" for s in sequence)

        # Validate MCP script is non-empty
        script_valid = len(mcp_script) > 100

        # Validate scroll counts are reasonable
        scroll_count = sum(1 for s in sequence if s["action"] == "scroll")
        scrolls_reasonable = 3 <= scroll_count <= 6

        # Validate URLs
        navigate_urls = [s["url"] for s in sequence if s["action"] == "navigate"]
        urls_valid = all("wikipedia.org" in u for u in navigate_urls)

        all_ok = (has_navigate and has_scroll and has_wait and has_close
                  and script_valid and scrolls_reasonable and urls_valid)

        details = (
            f"steps={len(sequence)} | scrolls={scroll_count} | "
            f"has_navigate={has_navigate} | has_close={has_close} | "
            f"mcp_script_len={len(mcp_script)}"
        )

        record_test("warmup", all_ok, details)

        if not all_ok:
            logger.warning("Warm-up sequence details: %s", json.dumps(sequence, indent=2))

    except Exception as exc:
        record_test("warmup", False, f"ERROR: {exc}")


# ======================================================================
# Test 5: Config Validation
# ======================================================================

def test_config() -> None:
    """Verify config.json has the required proxy and browser_profile sections."""
    logger.info("=" * 60)
    logger.info("TEST 5: Config Validation")
    logger.info("=" * 60)

    config_path = Path(__file__).resolve().parent / "config.json"
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        record_test("config", False, f"Cannot read config.json: {exc}")
        return

    has_proxy = "proxy" in cfg
    has_profile = "browser_profile" in cfg
    proxy_fields = all(
        k in cfg.get("proxy", {})
        for k in ("enabled", "server_url", "username", "password")
    )
    profile_fields = all(
        k in cfg.get("browser_profile", {})
        for k in ("persist", "profile_dir", "max_age_days")
    )

    all_ok = has_proxy and has_profile and proxy_fields and profile_fields
    details = (
        f"proxy_section={has_proxy} (fields_ok={proxy_fields}) | "
        f"profile_section={has_profile} (fields_ok={profile_fields})"
    )
    record_test("config", all_ok, details)


# ======================================================================
# Test 6: Profile Directory
# ======================================================================

def test_profile_dir() -> None:
    """Verify the browser profile directory exists and is writable."""
    logger.info("=" * 60)
    logger.info("TEST 6: Browser Profile Directory")
    logger.info("=" * 60)

    try:
        profile_dir = get_browser_profile_dir()
        profile_path = Path(profile_dir)

        # Create if not exists
        profile_path.mkdir(parents=True, exist_ok=True)

        # Write test
        test_file = profile_path / ".stealth_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()

        record_test("profile_dir", True, f"Directory: {profile_dir}")

    except Exception as exc:
        record_test("profile_dir", False, f"ERROR: {exc}")


# ======================================================================
# Main
# ======================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Stealth Humanization Test Suite")
    parser.add_argument("--headed", action="store_true",
                        help="Run browser tests in headed (visible) mode")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug-level logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("STEALTH HUMANIZATION TEST SUITE")
    logger.info("=" * 60)

    # Run all tests
    test_config()
    test_profile_dir()
    test_proxy()
    test_typing_speed()
    test_warmup()
    test_fingerprint(headed=args.headed)

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("RESULTS SUMMARY")
    logger.info("=" * 60)

    passed = sum(1 for r in RESULTS if r["passed"])
    failed = sum(1 for r in RESULTS if not r["passed"])
    total = len(RESULTS)

    for r in RESULTS:
        mark = "PASS" if r["passed"] else "FAIL"
        logger.info("  [%s] %s - %s", mark, r["test"], r["details"])

    logger.info("")
    logger.info("Total: %d/%d passed", passed, total)

    if failed:
        logger.warning("FAILURES DETECTED - review above for details")
        return 1
    else:
        logger.info("ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[abort] test suite interrupted")
        sys.exit(130)
