#!/usr/bin/env python3
"""Careerjet registration via Playwright — waits for Cloudflare Turnstile.

The register form embeds a Turnstile widget (managed mode, auto-solves in a
real-looking browser). We wait for the injected turnstile_token hidden input
to be populated, then submit the form.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from lib import email_unified as eu
from lib.state import gen_password, load_state, save_state
from lib.stealth_browser import launch, new_context, new_page, screenshot, close

SERVICE = "careerjet"
REGISTER_URL = "https://www.careerjet.com/register?redir=%2Fpartners%2Fregister%2Fas-publisher"


def wait_turnstile(page, timeout_s: int = 45) -> str:
    """Wait for Turnstile to populate its response input. Returns token ('' if timeout)."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            val = page.evaluate(
                "() => { const i = document.querySelector('input[name=turnstile_token]');"
                " return i ? i.value : null; }")
            if val:
                return val
        except Exception:
            pass
        # also check the global turnstile response via iframe checkbox state
        try:
            solved = page.evaluate(
                "() => { const f = document.querySelector('iframe[src*=turnstile]');"
                " return f ? f.getAttribute('data-') : null; }")
        except Exception:
            solved = None
        if solved != last:
            last = solved
        time.sleep(1.5)
    return ""


def main() -> int:
    state = load_state(SERVICE)
    if not state.get("email"):
        state["email"] = eu.alias_for_service(SERVICE)
        state["password"] = gen_password()
        save_state(SERVICE, state)
    print(f"account: {state['email']}")

    headless = os.environ.get("CJ_HEADLESS", "1") == "1"
    browser = launch(headless=headless)
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(REGISTER_URL, timeout=30000, wait_until="domcontentloaded")
        print(f"URL: {page.url}")

        # fill credentials first (Turnstile runs in parallel)
        page.fill('input#email, input[name=email]', state["email"])
        page.fill('input#password, input[name=password]', state["password"])
        screenshot(page, "21_filled", SERVICE)

        print("waiting for turnstile token...")
        token = wait_turnstile(page, timeout_s=45)
        print(f"turnstile token: {'OK len=' + str(len(token)) if token else 'TIMEOUT'}")
        state["turnstile_solved"] = bool(token)
        save_state(SERVICE, state)
        if not token:
            close(browser)
            print("ABORT: Turnstile did not auto-solve headless — "
                  "use 07_register_zenrows.py (ZenRows Browser Sessions).")
            sys.exit(3)

        t0 = int(time.time() * 1000)
        page.click('button:has-text("Create"), input[type=submit]', timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        time.sleep(3)
        screenshot(page, "22_after_submit", SERVICE)

        body = page.evaluate("document.body.innerText")
        print(f"post-submit URL: {page.url}")
        print(f"body excerpt: {body[:600]}")
        state.update({
            "pw2_register_final_url": page.url,
            "pw2_register_excerpt": body[:800],
            "email_window_start_ms": t0,
            "register_done": "An error occurred" not in body and "/register" not in page.url,
        })
        save_state(SERVICE, state)
        print(f"\nregister_done: {state['register_done']}")
    finally:
        close(browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
