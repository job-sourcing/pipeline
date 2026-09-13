"""Stealth Playwright context factory.

- Headless chromium (sandbox disabled — container constraint)
- playwright-stealth applied to mask HeadlessChrome fingerprint
- Persistent user-data dir per service so cookies survive between sessions
  (login → dashboard → API key extraction can be split across runs)
- Reasonable viewport + locale to look like a normal user
- Optional HTTP proxy for IP-reputation-blocked sites (USAJOBS, etc.)
"""
from __future__ import annotations

import os

from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

# playwright-stealth v1 exposes stealth_sync(); v2 (2024+) moved to
# Stealth().apply_stealth_sync(page). Support both so fresh sandboxes that
# pip-install the latest version keep working.
try:
    from playwright_stealth import stealth_sync  # v1 API
except ImportError:  # v2 API
    try:
        from playwright_stealth import Stealth as _Stealth

        _stealth_v2 = _Stealth()

        def stealth_sync(page):  # type: ignore[misc]
            _stealth_v2.apply_stealth_sync(page)
    except ImportError:  # not installed — no-op, stealth headers still set
        def stealth_sync(page):  # type: ignore[misc]
            return None

# Repo-relative (2026-08-27 consolidation): ingest/data/signup_artifacts/
# (lib/ -> parents[3] = ingest/). Override via SIGNUP_ARTIFACTS_DIR env if needed.
ARTIFACTS = Path(os.environ.get("SIGNUP_ARTIFACTS_DIR") or
                 Path(__file__).resolve().parents[3] / "data" / "signup_artifacts")


def launch(headless: bool = True, proxy: Optional[str] = None) -> Browser:
    """Launch a stealth chromium. Container needs --no-sandbox.

    Args:
        proxy: optional "http://ip:port" — used when the target site WAF
               blocks our IP (e.g., USAJOBS, government sites).
    """
    pw = sync_playwright().start()
    launch_kwargs = dict(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-features=WebUSB,WebBluetooth",
        ],
    )
    if proxy:
        launch_kwargs["proxy"] = {
            "server": proxy,
            # username/password left blank — free proxies usually don't need auth
        }
    browser = pw.chromium.launch(**launch_kwargs)
    # Stash the playwright handle on the browser so caller can stop it
    browser._pw_handle = pw  # type: ignore[attr-defined]
    return browser


def new_context(browser: Browser, storage_state: Optional[Path] = None) -> BrowserContext:
    ctx = browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="en-US",
        timezone_id="America/Los_Angeles",
        storage_state=str(storage_state) if storage_state else None,
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    # Set realistic extra headers
    ctx.set_extra_http_headers({
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Chromium";v="120", "Not?A_Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
    })
    return ctx


def new_page(ctx: BrowserContext) -> Page:
    """New page with stealth applied (hides webdriver flag etc.)."""
    page = ctx.new_page()
    stealth_sync(page)
    return page


def screenshot(page: Page, name: str, service: str) -> Path:
    """Save a screenshot to the artifacts dir. Returns the path."""
    out_dir = ARTIFACTS / service
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = __import__("time").strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{name}-{ts}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def close(browser: Browser) -> None:
    """Close browser + stop playwright handle."""
    try:
        browser.close()
    finally:
        pw = getattr(browser, "_pw_handle", None)
        if pw:
            pw.stop()


if __name__ == "__main__":
    # Smoke test: visit bot.sannysoft.com and check the stealth detection
    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    page.goto("https://bot.sannysoft.com/", timeout=30000, wait_until="domcontentloaded")
    title = page.title()
    # Take screenshot for inspection
    path = screenshot(page, "stealth_test", "_selftest")
    # Check key indicator
    try:
        ua = page.evaluate("navigator.userAgent")
        webdriver = page.evaluate("navigator.webdriver")
    except Exception:
        ua = "?"
        webdriver = "?"
    print(f"title={title!r}")
    print(f"UA={ua}")
    print(f"webdriver={webdriver}")
    print(f"shot={path}")
    close(browser)
