#!/usr/bin/env python3
"""Findwork.dev signup automation — uses v3-mail admin API for body access.

Flow:
1. POST /accounts/signup/ with billing@priv.email + password
2. v3-mail admin API polls for verification email → click verify link in body
3. Login → /developers/ → API token visible

Env: FINDWORK_API_KEY
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from lib import email_unified as eu
from lib.state import gen_password, load_state, save_state
from lib.stealth_browser import launch, new_context, new_page, screenshot, close

SERVICE = "findwork"
SIGNUP_URL = "https://findwork.dev/accounts/signup/?next=/developers/"
LOGIN_URL = "https://findwork.dev/accounts/login/?next=/developers/"
DEVELOPERS_URL = "https://findwork.dev/developers/"


def step_signup(state: dict) -> dict:
    """Submit the Findwork signup form with billing@priv.email."""
    if "email" not in state:
        state["email"] = eu.alias_for_service(SERVICE)  # billing@priv.email
        state["password"] = gen_password()
        save_state(SERVICE, state)
        print(f"  [signup] using email={state['email']}")

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(SIGNUP_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "01_signup_form", SERVICE)

        # Findwork uses Django allauth
        page.fill('input[name="email"], input#id_email', state["email"])
        page.fill('input[name="password1"], input#id_password1', state["password"])
        try:
            page.fill('input[name="password2"], input#id_password2', state["password"])
        except Exception:
            pass
        screenshot(page, "02_signup_filled", SERVICE)

        email_start_ms = int(time.time() * 1000)
        page.click('button[type=submit], input[type=submit]')
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "03_after_submit", SERVICE)
        state["signup_completed_url"] = page.url
        state["signup_completed_at"] = int(time.time())
        state["email_window_start_ms"] = email_start_ms
        body_text = page.evaluate("document.body.innerText")
        state["signup_response_excerpt"] = body_text[:500]
        save_state(SERVICE, state)
        print(f"  [signup] post-submit URL: {page.url}")
        return state
    finally:
        close(browser)


def step_verify_email(state: dict) -> dict:
    """Poll v3-mail for Findwork verification email → click verify link."""
    if state.get("verify_email_status") == "ok":
        print("  [verify] already done")
        return state

    since = state.get("email_window_start_ms")
    print(f"  [verify] polling v3-mail for Findwork email to {state['email']}...")
    msg = eu.wait_for_email(
        service=SERVICE,
        sender_contains="findwork",
        need_body=True,
        timeout_s=180,
        poll_interval_s=10,
        since_ts_ms=since,
    )
    if not msg:
        state["verify_email_status"] = "timeout"
        save_state(SERVICE, state)
        return state

    state["verify_email_subject"] = msg["subject"]
    state["verify_email_message_id"] = msg["message_id"]
    state["verify_email_received_at"] = msg.get("received_at")
    print(f"  [verify] subject: {msg['subject']}")

    body_html = msg.get("body_html", "") or ""
    body_text = msg.get("body_text", "") or ""

    # Look for verify link to findwork.dev
    import re, html as html_mod
    verify_link = eu.extract_verify_link(body_html, host_contains="findwork")
    if not verify_link:
        urls = re.findall(r"https?://\S+", body_text)
        verify_link = next((u for u in urls if "findwork" in u.lower() and any(
            kw in u.lower() for kw in ("confirm", "verify", "activate"))), None)

    if not verify_link:
        print(f"  [verify] no verify link found in body")
        state["verify_email_status"] = "no_link_in_body"
        state["verify_email_body_excerpt"] = body_text[:800]
        save_state(SERVICE, state)
        return state

    verify_link = html_mod.unescape(verify_link)
    print(f"  [verify] visiting: {verify_link[:100]}")
    state["verify_url"] = verify_link

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(verify_link, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "04_verify_landed", SERVICE)
        # Django allauth confirm-email typically requires another submit
        try:
            page.click('button[type=submit], input[type=submit]', timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            screenshot(page, "05_after_confirm", SERVICE)
        except Exception:
            pass
        body_text = page.evaluate("document.body.innerText")
        state["verify_landed_url"] = page.url
        state["verify_landed_excerpt"] = body_text[:500]
        if any(kw in body_text.lower() for kw in ("confirmed", "verified", "success", "activated")):
            state["verify_email_status"] = "ok"
        else:
            state["verify_email_status"] = "landed"
        print(f"  [verify] landed URL: {page.url}; status={state['verify_email_status']}")
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def step_login_and_extract_keys(state: dict) -> dict:
    """Login to Findwork and pull the API token from /developers/."""
    if state.get("env_kv", {}).get("FINDWORK_API_KEY"):
        print("  [keys] already extracted; skipping")
        return state

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "06_login", SERVICE)
        page.fill('input[name="login"], input#id_login', state["email"])
        page.fill('input[name="password"], input#id_password', state["password"])
        page.click('button[type=submit], input[type=submit]')
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "07_after_login", SERVICE)
        state["post_login_url"] = page.url
        print(f"  [keys] post-login URL: {page.url}")

        page.goto(DEVELOPERS_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "08_developers", SERVICE)
        body_text = page.evaluate("document.body.innerText")
        state["developers_page_excerpt"] = body_text[:800]
        print(f"  [keys] developers excerpt: {body_text[:300]}")

        # Findwork's dashboard typically shows the token inline. Pattern:
        # "Your API token: xxx" OR a <code>...</code> block
        import re
        # Try multiple patterns
        m = re.search(r"(?:api[- ]?token|token)[\s:]+([A-Za-z0-9_\-]{20,})", body_text, re.IGNORECASE)
        if m:
            state["env_kv"] = {"FINDWORK_API_KEY": m.group(1)}
            state["key_extraction_status"] = "ok"
            save_state(SERVICE, state)
            return state

        # Findwork hides the token behind ••••• bullets but stores the real
        # value in a <span data-value="..."> attribute. Grab all data-value
        # values that look like tokens.
        try:
            data_vals = page.eval_on_selector_all(
                '[data-value]',
                'els => els.map(e => e.getAttribute("data-value")).filter(v => v && v.length >= 20)'
            )
            if data_vals:
                # Pick the longest-looking hex token
                data_vals.sort(key=len, reverse=True)
                state["env_kv"] = {"FINDWORK_API_KEY": data_vals[0]}
                state["key_extraction_status"] = "ok_from_data_value"
                save_state(SERVICE, state)
                return state
        except Exception:
            pass

        # Look for input fields containing a token
        try:
            token_val = page.eval_on_selector_all(
                'input[name*="token"], input[readonly], input.code, code',
                'els => els.map(e => e.value || e.textContent).filter(x => x && x.length > 15)'
            )
            if token_val:
                state["env_kv"] = {"FINDWORK_API_KEY": token_val[0]}
                state["key_extraction_status"] = "ok_from_input"
                save_state(SERVICE, state)
                return state
        except Exception:
            pass

        # Try clicking "Generate" / "Create" / "Reset" button
        try:
            page.click('a:has-text("Generate"), button:has-text("Generate"), a:has-text("Create"), button:has-text("Create"), a:has-text("Reset"), button:has-text("Reset")', timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            screenshot(page, "09_after_generate", SERVICE)
            body_text = page.evaluate("document.body.innerText")
            m = re.search(r"(?:api[- ]?token|token)[\s:]+([A-Za-z0-9_\-]{20,})", body_text, re.IGNORECASE)
            if m:
                state["env_kv"] = {"FINDWORK_API_KEY": m.group(1)}
                state["key_extraction_status"] = "ok_after_generate"
                save_state(SERVICE, state)
                return state
        except Exception as e:
            print(f"  [keys] no generate button: {e}")

        state["key_extraction_status"] = "not_found"
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def main() -> int:
    state = load_state(SERVICE)
    if not state:
        print(f"=== {SERVICE} fresh signup ===")
    else:
        print(f"=== {SERVICE} resume: status={state.get('key_extraction_status')} ===")

    if not state.get("signup_completed_url"):
        state = step_signup(state)
    else:
        print(f"  [skip] signup already done")

    if state.get("verify_email_status") != "ok":
        state = step_verify_email(state)
    else:
        print(f"  [skip] email verification already done")

    if not state.get("env_kv") or not state["env_kv"].get("FINDWORK_API_KEY"):
        state = step_login_and_extract_keys(state)
    else:
        print(f"  [skip] keys already extracted")

    env_kv = state.get("env_kv") or {}
    if env_kv.get("FINDWORK_API_KEY"):
        print(f"\n[SUCCESS] Findwork key captured: FINDWORK_API_KEY={env_kv['FINDWORK_API_KEY'][:8]}...")
        return 0
    else:
        print(f"\n[PARTIAL] state so far:")
        print(state)
        return 1


if __name__ == "__main__":
    sys.exit(main())
