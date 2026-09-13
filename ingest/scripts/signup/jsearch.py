#!/usr/bin/env python3
"""RapidAPI signup + JSearch subscription automation.

Flow:
1. Navigate to rapidapi.com/auth/join, click "Sign Up" link to switch to
   signup mode (same SPA)
2. Fill username + email (redacted@priv.email) + password + confirm + agree
3. Submit — verification email sent
4. Poll v3-mail for verification email → click verify link in body
5. Login → search for "JSearch" → navigate to API page
6. Click "Subscribe Free" → wait for subscription
7. Go to /apps → Default Application → copy X-RapidAPI-Key

Env: JSEARCH_API_KEY (= X-RapidAPI-Key)
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from lib import email_unified as eu
from lib.state import gen_password, load_state, save_state
from lib.stealth_browser import launch, new_context, new_page, screenshot, close

SERVICE = "jsearch"
JOIN_URL = "https://rapidapi.com/auth/join"
LOGIN_URL = "https://rapidapi.com/auth/login"
JSEARCH_API_PAGE = "https://rapidapi.com/letmeapproveyou/api/jsearch2/pricing"  # may redirect
APPS_URL = "https://rapidapi.com/developer/apps"  # apps dashboard


def step_signup(state: dict) -> dict:
    """Switch to signup mode and submit the form."""
    if "email" not in state:
        state["email"] = eu.alias_for_service(SERVICE)  # redacted@priv.email
        state["password"] = gen_password()
        state["username"] = f"jobsearch{int(time.time())%100000:05d}"
        save_state(SERVICE, state)
        print(f"  [signup] using email={state['email']} username={state['username']}")

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(JOIN_URL, timeout=30000, wait_until="domcontentloaded")
        # Wait for SPA to render
        page.wait_for_selector('input#email', timeout=10000)
        # Click "Sign Up" link to switch modes
        try:
            page.click('a:has-text("Sign Up"), button:has-text("Sign Up")', timeout=5000)
            page.wait_for_selector('input#username', timeout=10000)
        except Exception as e:
            print(f"  [signup] Sign Up click failed: {e}")
        screenshot(page, "01_signup_form", SERVICE)

        # Fill the form
        page.fill('input#username', state["username"])
        page.fill('input#email', state["email"])
        page.fill('input#password', state["password"])
        page.fill('input#confirmPassword', state["password"])
        # Tick the agree-to-terms checkbox (the unnamed one — usually the first
        # unnamed checkbox after confirmPassword)
        try:
            page.check('input[type=checkbox]:not([name*="ot-group"]):not([id*="ot-group"]):not([name="vendor-search-handler"])', timeout=5000)
        except Exception as e:
            print(f"  [signup] terms checkbox: {e}")
        screenshot(page, "02_form_filled", SERVICE)

        email_start_ms = int(time.time() * 1000)
        # Submit
        try:
            page.click('button[type=submit]:has-text("Sign Up"), button[type=submit]:has-text("Register"), button[type=submit]:not(:has-text("Google")):not(:has-text("Github")):not(:has-text("Log in"))', timeout=5000)
        except Exception:
            # Fallback: any submit button that isn't an OAuth one
            page.click('button[type=submit]', timeout=5000)
        # Give the SPA time to navigate
        time.sleep(5)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "03_after_submit", SERVICE)

        state["signup_completed_url"] = page.url
        state["signup_completed_at"] = int(time.time())
        state["email_window_start_ms"] = email_start_ms
        body_text = page.evaluate("document.body.innerText")
        state["signup_response_excerpt"] = body_text[:1000]
        save_state(SERVICE, state)
        print(f"  [signup] post-submit URL: {page.url}")
        print(f"  [signup] body excerpt: {body_text[:300]}")
        return state
    finally:
        close(browser)


def step_verify_email(state: dict) -> dict:
    """Poll v3-mail for RapidAPI verification email → click verify link."""
    if state.get("verify_email_status") == "ok":
        print("  [verify] already done")
        return state

    since = state.get("email_window_start_ms")
    print(f"  [verify] polling v3-mail for RapidAPI email to {state['email']}...")
    msg = eu.wait_for_email(
        service=SERVICE,
        sender_contains="rapidapi",
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
    state["verify_email_received_at"] = msg.get("received_at")
    print(f"  [verify] subject: {msg['subject']}")

    body_html = msg.get("body_html", "") or ""
    body_text = msg.get("body_text", "") or ""

    import html as html_mod
    verify_link = eu.extract_verify_link(body_html, host_contains="rapidapi")
    if not verify_link:
        urls = re.findall(r"https?://\S+", body_text)
        verify_link = next((u for u in urls if "rapidapi.com" in u.lower() and any(
            kw in u.lower() for kw in ("verify", "confirm", "activate"))), None)

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
        time.sleep(3)  # let SPA render
        screenshot(page, "04_verify_landed", SERVICE)
        body_text = page.evaluate("document.body.innerText")
        state["verify_landed_url"] = page.url
        state["verify_landed_excerpt"] = body_text[:500]
        if any(kw in body_text.lower() for kw in ("verified", "confirmed", "success", "welcome", "logged in")):
            state["verify_email_status"] = "ok"
        else:
            state["verify_email_status"] = "landed"
        print(f"  [verify] landed URL: {page.url}; status={state['verify_email_status']}")
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def step_subscribe_jsearch(state: dict) -> dict:
    """Login → navigate to JSearch API page → click Subscribe Free."""
    if state.get("subscribe_status") == "ok":
        print("  [subscribe] already done")
        return state

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        # Login first
        page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_selector('input#email', timeout=10000)
        page.fill('input#email', state["email"])
        page.fill('input#password', state["password"])
        try:
            page.click('button[type=submit]:has-text("Log in"), button[type=submit]:not(:has-text("Google")):not(:has-text("Github")):not(:has-text("Sign Up"))', timeout=5000)
        except Exception:
            page.click('button[type=submit]', timeout=5000)
        time.sleep(5)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "05_after_login", SERVICE)
        state["post_login_url"] = page.url
        print(f"  [subscribe] post-login URL: {page.url}")

        # Search for JSearch API
        # Try direct URL first
        page.goto(JSEARCH_API_PAGE, timeout=30000, wait_until="domcontentloaded")
        time.sleep(5)
        screenshot(page, "06_jsearch_api_page", SERVICE)
        body_text = page.evaluate("document.body.innerText")
        print(f"  [subscribe] jsearch page URL: {page.url}")
        print(f"  [subscribe] body excerpt: {body_text[:300]}")

        # Look for "Subscribe" / "Free" buttons
        try:
            page.click('button:has-text("Subscribe"), a:has-text("Subscribe"), button:has-text("Free"), a:has-text("Free"), button:has-text("Basic"), a:has-text("Basic")', timeout=10000)
            time.sleep(3)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            screenshot(page, "07_after_subscribe_click", SERVICE)
            body_text = page.evaluate("document.body.innerText")
            # Confirm subscription if there's another confirmation
            try:
                page.click('button:has-text("Confirm"), button:has-text("Subscribe"), button:has-text("OK")', timeout=5000)
                time.sleep(2)
            except Exception:
                pass
            state["subscribe_status"] = "subscribed"
        except Exception as e:
            print(f"  [subscribe] no subscribe button: {e}")
            state["subscribe_status"] = f"no_subscribe_button: {e}"
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def step_extract_key(state: dict) -> dict:
    """Navigate to apps dashboard → copy X-RapidAPI-Key."""
    if state.get("env_kv", {}).get("JSEARCH_API_KEY"):
        print("  [keys] already extracted; skipping")
        return state

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        # Login
        page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_selector('input#email', timeout=10000)
        page.fill('input#email', state["email"])
        page.fill('input#password', state["password"])
        try:
            page.click('button[type=submit]:has-text("Log in"), button[type=submit]:not(:has-text("Google")):not(:has-text("Github")):not(:has-text("Sign Up"))', timeout=5000)
        except Exception:
            page.click('button[type=submit]', timeout=5000)
        time.sleep(5)

        # Navigate to apps dashboard
        page.goto(APPS_URL, timeout=30000, wait_until="domcontentloaded")
        time.sleep(5)
        screenshot(page, "08_apps_dashboard", SERVICE)
        body_text = page.evaluate("document.body.innerText")
        print(f"  [keys] apps URL: {page.url}")
        print(f"  [keys] body excerpt: {body_text[:400]}")

        # Look for X-RapidAPI-Key in the page
        # It's typically a 50-char hex/base64 string
        m = re.search(r"\b([a-f0-9]{50})\b", body_text)
        if not m:
            m = re.search(r"\b([A-Za-z0-9]{50})\b", body_text)
        if not m:
            # Try HTML
            html = page.content()
            m = re.search(r"\b([a-f0-9]{50})\b", html)
            if not m:
                m = re.search(r"\b([A-Za-z0-9]{50})\b", html)
        if m:
            state["env_kv"] = {"JSEARCH_API_KEY": m.group(1)}
            state["key_extraction_status"] = "ok"
            print(f"  [keys] extracted: {m.group(1)[:10]}...")
        else:
            # Try clicking into "Default Application" and looking for Authorization
            try:
                page.click('a:has-text("Default"), a:has-text("default"), a:has-text("Authorization"), button:has-text("Authorization")', timeout=5000)
                time.sleep(3)
                body_text = page.evaluate("document.body.innerText")
                m = re.search(r"\b([a-f0-9]{50})\b", body_text) or re.search(r"\b([A-Za-z0-9]{50})\b", body_text)
                if m:
                    state["env_kv"] = {"JSEARCH_API_KEY": m.group(1)}
                    state["key_extraction_status"] = "ok_after_nav"
                    print(f"  [keys] extracted after nav: {m.group(1)[:10]}...")
            except Exception as e:
                print(f"  [keys] nav failed: {e}")
            if not state.get("env_kv"):
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

    if state.get("subscribe_status") != "subscribed":
        state = step_subscribe_jsearch(state)
    else:
        print(f"  [skip] subscribe already done")

    if not state.get("env_kv") or not state["env_kv"].get("JSEARCH_API_KEY"):
        state = step_extract_key(state)
    else:
        print(f"  [skip] keys already extracted")

    env_kv = state.get("env_kv") or {}
    if env_kv.get("JSEARCH_API_KEY"):
        print(f"\n[SUCCESS] JSearch key captured: JSEARCH_API_KEY={env_kv['JSEARCH_API_KEY'][:8]}...")
        return 0
    else:
        print(f"\n[PARTIAL] state so far:")
        print(state)
        return 1


if __name__ == "__main__":
    sys.exit(main())
