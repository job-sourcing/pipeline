#!/usr/bin/env python3
"""Careerjet publisher signup automation.

Careerjet is reachable from our IP (no WAF block). Flow:
1. Visit /register?redir=/partners/register/as-publisher
2. Fill email (the shop alias — resolved from CAREERJET_PARTNER_EMAIL in
   the environment) + password
3. Submit — account created, redirect to /partners/register/as-publisher
4. Fill publisher registration form (name, website, etc.)
5. Submit — API key shown on response page OR emailed

Env: CAREERJET_API_KEY (HTTP Basic auth, key as username, empty password)
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

SERVICE = "careerjet"
REGISTER_URL = "https://www.careerjet.com/register?redir=%2Fpartners%2Fregister%2Fas-publisher"
PARTNERS_URL = "https://www.careerjet.com/partners/register/as-publisher"
LOGIN_URL = "https://www.careerjet.com/login?redir=%2Fpartners%2Fregister%2Fas-publisher"


def step_signup(state: dict) -> dict:
    """Submit Careerjet's main signup form."""
    if "email" not in state:
        state["email"] = eu.alias_for_service(SERVICE)  # CAREERJET_PARTNER_EMAIL alias
        state["password"] = gen_password()
        save_state(SERVICE, state)
        print(f"  [signup] using email={state['email']}")

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(REGISTER_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "01_signup_form", SERVICE)
        print(f"  [signup] URL: {page.url}")

        # Careerjet register form: email + password (single field, choose pw)
        page.fill('input#email, input[name=email]', state["email"])
        page.fill('input#password, input[name=password]', state["password"])
        # Check agree-to-terms checkbox if present
        try:
            page.check('input[type=checkbox]', timeout=3000)
        except Exception:
            pass
        screenshot(page, "02_form_filled", SERVICE)

        email_start_ms = int(time.time() * 1000)
        # Submit — find the "Create an account" button
        try:
            page.click('button:has-text("Create"), button[type=submit]:has-text("Create"), input[type=submit]', timeout=5000)
        except Exception:
            page.click('button[type=submit], input[type=submit]', timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "03_after_submit", SERVICE)

        body_text = page.evaluate("document.body.innerText")
        state["signup_completed_url"] = page.url
        state["signup_completed_at"] = int(time.time())
        state["email_window_start_ms"] = email_start_ms
        state["signup_response_excerpt"] = body_text[:1500]
        save_state(SERVICE, state)
        print(f"  [signup] post-submit URL: {page.url}")
        print(f"  [signup] body excerpt: {body_text[:400]}")
        return state
    finally:
        close(browser)


def step_verify_email(state: dict) -> dict:
    """Careerjet may require email verification."""
    if state.get("verify_email_status") == "ok":
        print("  [verify] already done")
        return state

    since = state.get("email_window_start_ms")
    print(f"  [verify] polling v3-mail for Careerjet email to {state['email']}...")
    msg = eu.wait_for_email(
        service=SERVICE,
        sender_contains="careerjet",
        need_body=True,
        timeout_s=120,
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
    verify_link = eu.extract_verify_link(body_html, host_contains="careerjet")
    if not verify_link:
        urls = re.findall(r"https?://\S+", body_text)
        verify_link = next((u for u in urls if "careerjet" in u.lower() and any(
            kw in u.lower() for kw in ("verify", "confirm", "activate", "click"))), None)

    if not verify_link:
        # Maybe the email just contains the API key directly
        m = re.search(r"\b([a-f0-9]{32})\b", body_text + body_html)
        if m:
            state["env_kv"] = {"CAREERJET_API_KEY": m.group(1)}
            state["key_extraction_status"] = "ok_from_email_body"
            state["verify_email_status"] = "ok"
            print(f"  [verify] API key found in email body: {m.group(1)[:8]}...")
            save_state(SERVICE, state)
            return state
        # No verify link AND no key — surface the body for inspection
        print(f"  [verify] no verify link or key in email body")
        state["verify_email_status"] = "no_link_or_key"
        state["verify_email_body_excerpt"] = body_text[:1000]
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
        body_text = page.evaluate("document.body.innerText")
        state["verify_landed_url"] = page.url
        state["verify_landed_excerpt"] = body_text[:500]
        if any(kw in body_text.lower() for kw in ("verified", "confirmed", "success", "welcome")):
            state["verify_email_status"] = "ok"
        else:
            state["verify_email_status"] = "landed"
        print(f"  [verify] landed URL: {page.url}; status={state['verify_email_status']}")
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def step_publisher_register(state: dict) -> dict:
    """After account is created + verified, visit the publisher registration
    page and fill it to get the API key."""
    if state.get("publisher_register_status") == "ok":
        print("  [publisher] already done")
        return state

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        # Login first
        page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        page.fill('input#email, input[name=email]', state["email"])
        page.fill('input#password, input[name=password]', state["password"])
        try:
            page.click('button[type=submit]:has-text("Sign in"), button[type=submit]', timeout=5000)
        except Exception:
            page.click('button[type=submit], input[type=submit]', timeout=5000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "05_after_login", SERVICE)
        print(f"  [publisher] post-login URL: {page.url}")

        # Navigate to publisher registration
        page.goto(PARTNERS_URL, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        screenshot(page, "06_publisher_form", SERVICE)
        body_text = page.evaluate("document.body.innerText")
        print(f"  [publisher] form URL: {page.url}")
        print(f"  [publisher] body excerpt: {body_text[:400]}")

        # Get form fields
        forms = page.evaluate("""Array.from(document.querySelectorAll('form')).map(f => ({
          action: f.action, method: f.method,
          fields: Array.from(f.elements).map(e => ({tag: e.tagName, type: e.type, name: e.name, id: e.id, placeholder: e.placeholder})).filter(e => e.name || e.id)
        }))""")
        print(f"  [publisher] forms: {forms}")

        # Fill the publisher form fields
        # Common Careerjet publisher fields: site URL, site name, description, etc.
        for selector, value in [
            ('input[name*="site" i]', "https://github.com/strbtwc/job-sourcing-research"),
            ('input[name*="name" i]', "Personal Job Search"),
            ('input[name*="url" i]', "https://github.com/strbtwc/job-sourcing-research"),
            ('input[name*="description" i]', "Personal job search aggregation"),
            ('textarea[name*="description" i]', "Personal job search aggregation and analysis for tech-sector market research."),
            ('input[name*="company" i]', "Personal Research"),
        ]:
            try:
                page.fill(selector, value)
            except Exception:
                pass

        # Check any agreement checkbox
        try:
            page.check('input[type=checkbox]', timeout=2000)
        except Exception:
            pass

        screenshot(page, "07_publisher_filled", SERVICE)
        email_start_ms = int(time.time() * 1000)
        # Submit
        try:
            page.click('button[type=submit], input[type=submit]', timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  [publisher] submit click failed: {e}")
        screenshot(page, "08_after_submit", SERVICE)
        body_text = page.evaluate("document.body.innerText")
        state["publisher_register_url"] = page.url
        state["publisher_register_response_excerpt"] = body_text[:1500]
        state["email_window_start_ms"] = email_start_ms
        # Look for the API key in the response
        m = re.search(r"\b([a-f0-9]{32})\b", body_text)
        if m:
            state["env_kv"] = {"CAREERJET_API_KEY": m.group(1)}
            state["key_extraction_status"] = "ok_from_publisher_response"
            state["publisher_register_status"] = "ok"
            print(f"  [publisher] API key in response: {m.group(1)[:8]}...")
        else:
            state["publisher_register_status"] = "submitted_no_key_in_response"
            print(f"  [publisher] no key in response; check email")
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def step_extract_key_from_email(state: dict) -> dict:
    """If the publisher key was emailed, fetch it from v3-mail."""
    if state.get("env_kv", {}).get("CAREERJET_API_KEY"):
        print("  [keys] already extracted; skipping")
        return state

    print("  [keys] polling v3-mail for API key email...")
    since = state.get("email_window_start_ms")
    msg = eu.wait_for_email(
        service=SERVICE,
        sender_contains="careerjet",
        need_body=True,
        timeout_s=120,
        poll_interval_s=10,
        since_ts_ms=since,
    )
    if not msg:
        state["key_extraction_status"] = "no_email"
        save_state(SERVICE, state)
        return state
    body_text = msg.get("body_text", "") or ""
    body_html = msg.get("body_html", "") or ""
    m = re.search(r"\b([a-f0-9]{32})\b", body_text + body_html)
    if m:
        state["env_kv"] = {"CAREERJET_API_KEY": m.group(1)}
        state["key_extraction_status"] = "ok_from_email"
        print(f"  [keys] extracted from email: {m.group(1)[:8]}...")
    else:
        state["key_extraction_status"] = "no_key_in_email"
        state["verify_email_body_excerpt"] = body_text[:1000]
    save_state(SERVICE, state)
    return state


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

    if state.get("publisher_register_status") != "ok":
        state = step_publisher_register(state)
    else:
        print(f"  [skip] publisher register already done")

    if not state.get("env_kv") or not state["env_kv"].get("CAREERJET_API_KEY"):
        state = step_extract_key_from_email(state)
    else:
        print(f"  [skip] keys already extracted")

    env_kv = state.get("env_kv") or {}
    if env_kv.get("CAREERJET_API_KEY"):
        print(f"\n[SUCCESS] Careerjet key captured: CAREERJET_API_KEY={env_kv['CAREERJET_API_KEY'][:8]}...")
        return 0
    else:
        print(f"\n[PARTIAL] state so far:")
        print(state)
        return 1


if __name__ == "__main__":
    sys.exit(main())
