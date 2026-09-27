#!/usr/bin/env python3
"""Careerjet publisher account registration via Playwright stealth browser.

The plain-HTTP POST /register returned a generic "An error occurred" —
likely JS-based anti-bot. This path uses the repo's stealth browser
(the one that successfully activated Adzuna + Findwork).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from lib import email_unified as eu
from lib.state import load_state, save_state
from lib.stealth_browser import launch, new_context, new_page, screenshot, close

SERVICE = "careerjet"
REGISTER_URL = "https://www.careerjet.com/register?redir=%2Fpartners%2Fregister%2Fas-publisher"


def main() -> int:
    state = load_state(SERVICE)
    if not state.get("email"):
        state["email"] = eu.alias_for_service(SERVICE)
        state["password"] = state.get("password") or __import__("lib.state", fromlist=["gen_password"]).gen_password()
        save_state(SERVICE, state)
    print(f"account: {state['email']}")

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(REGISTER_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "11_pw_register_form", SERVICE)
        print(f"URL: {page.url}")

        page.fill('input#email, input[name=email]', state["email"])
        page.fill('input#password, input[name=password]', state["password"])
        screenshot(page, "12_pw_filled", SERVICE)

        t0 = int(time.time() * 1000)
        try:
            page.click('button:has-text("Create"), input[type=submit]', timeout=5000)
        except Exception:
            page.click('button[type=submit], input[type=submit]', timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        time.sleep(2)
        screenshot(page, "13_pw_after_submit", SERVICE)

        body = page.evaluate("document.body.innerText")
        print(f"post-submit URL: {page.url}")
        print(f"body excerpt: {body[:700]}")
        state.update({
            "pw_register_final_url": page.url,
            "pw_register_excerpt": body[:800],
            "email_window_start_ms": t0,
            # Explicit success signal only (the success redirect contains
            # "/register", so a URL check misjudges — CodeRabbit R1).
            "register_done": "An error occurred" not in body,
        })
        save_state(SERVICE, state)
    finally:
        close(browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
