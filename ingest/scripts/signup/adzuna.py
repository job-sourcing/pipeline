#!/usr/bin/env python3
"""Adzuna developer signup automation — v2 with v3-mail body access.

Flow:
1. Visit developer.adzuna.com/signup
2. Fill form (the admin alias — resolved from USAJOBS_USER_AGENT in the
   environment; forwarded to v3-mail.priv.email storage)
3. Submit — Adzuna sends "API account confirmation" email with verify link
4. Poll v3-mail admin API for the email → extract verify link from HTML body
5. Visit the verify link → account activated
6. Login → /applications → see APP_ID + API_KEY

Env: ADZUNA_APP_ID + ADZUNA_API_KEY
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from lib import email_unified as eu
from lib.state import gen_password, load_state, save_state, update_state
from lib.stealth_browser import launch, new_context, new_page, screenshot, close

SERVICE = "adzuna"
SIGNUP_URL = "https://developer.adzuna.com/signup"
LOGIN_URL = "https://developer.adzuna.com/login"
APPS_URL = "https://developer.adzuna.com/applications"


def step_signup(state: dict) -> dict:
    """Submit the Adzuna signup form with the admin alias (USAJOBS_USER_AGENT)."""
    if "email" not in state:
        state["email"] = eu.alias_for_service(SERVICE)  # USAJOBS_USER_AGENT alias
        state["password"] = gen_password()
        state["username"] = f"jobsearch{int(time.time())%100000:05d}"
        state["org_name"] = "Personal Job Search"
        state["org_website"] = "https://github.com/strbtwc/job-sourcing-research"
        save_state(SERVICE, state)
        print(f"  [signup] using email={state['email']}")

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(SIGNUP_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "01_signup_form", SERVICE)

        page.fill('input[name="account[user][username]"]', state["username"])
        page.fill('input[name="account[user][email]"]', state["email"])
        page.fill('input[name="account[user][password]"]', state["password"])
        page.fill('input[name="account[user][password_confirmation]"]', state["password"])
        page.fill('input[name="account[org_name]"]', state["org_name"])
        page.fill('input[name="account[org_website]"]', state["org_website"])

        page.select_option('#account_org_application_id', "Personal or academic research")
        page.select_option('#account_avg_visitors_id', "0-5000")
        page.select_option('#account_primary_market_id', "North America")
        try:
            opts = page.eval_on_selector_all(
                '#account_primary_industry_id option',
                'els => els.map(e => ({value: e.value, text: e.textContent.trim()}))'
            )
            non_empty = [o for o in opts if o["value"]]
            tech = next((o for o in non_empty if "tech" in o["text"].lower() or "it" == o["text"].lower()), None)
            chosen = tech or (non_empty[0] if non_empty else None)
            if chosen:
                page.select_option('#account_primary_industry_id', chosen["value"])
        except Exception as e:
            print(f"  [industry] {e}")

        try:
            page.check('input[type=checkbox][name*="terms"], input[type=checkbox][name*="toc"]')
        except Exception:
            pass

        screenshot(page, "02_signup_filled", SERVICE)

        email_start_ms = int(time.time() * 1000)
        page.click('input[type=submit][name=commit], button[type=submit]')
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "03_after_submit", SERVICE)
        state["signup_completed_url"] = page.url
        state["signup_completed_at"] = int(time.time())
        state["email_window_start_ms"] = email_start_ms
        save_state(SERVICE, state)
        print(f"  [signup] post-submit URL: {page.url}")
        body_text = page.evaluate("document.body.innerText")
        state["signup_response_excerpt"] = body_text[:500]
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def step_verify_email(state: dict) -> dict:
    """Poll v3-mail for Adzuna confirmation email → click verify link."""
    if state.get("verify_email_status") == "ok":
        print("  [verify] already done")
        return state

    since = state.get("email_window_start_ms")
    print(f"  [verify] polling v3-mail for Adzuna email to {state['email']}...")
    msg = eu.wait_for_email(
        service=SERVICE,
        sender_contains="adzuna",
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

    # Look for a verify link to developer.adzuna.com
    verify_link = eu.extract_verify_link(body_html, host_contains="adzuna")
    if not verify_link:
        # Maybe it's in the text body (some services put link in text too)
        # Try regex on text
        urls = re.findall(r"https?://\S+", body_text)
        verify_link = next((u for u in urls if "adzuna" in u.lower() and any(
            kw in u.lower() for kw in ("confirm", "verify", "activate", "click", "signup"))), None)

    if not verify_link:
        print(f"  [verify] no verify link found in body")
        state["verify_email_status"] = "no_link_in_body"
        state["verify_email_body_excerpt"] = body_text[:800]
        save_state(SERVICE, state)
        return state

    import html as html_mod
    verify_link = html_mod.unescape(verify_link)
    print(f"  [verify] visiting confirmation URL: {verify_link[:100]}")
    state["verify_url"] = verify_link

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(verify_link, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "04_verify_landed", SERVICE)
        # If there's a "Confirm" button (some flows require a second click), click it
        try:
            page.click('button[type=submit], input[type=submit]', timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            screenshot(page, "05_after_confirm", SERVICE)
        except Exception:
            pass
        body_text = page.evaluate("document.body.innerText")
        state["verify_landed_url"] = page.url
        state["verify_landed_excerpt"] = body_text[:500]
        # Check for success markers
        success = any(kw in body_text.lower() for kw in
                     ("confirmed", "verified", "success", "activated", "welcome"))
        state["verify_email_status"] = "ok" if success else "landed"
        print(f"  [verify] landed URL: {page.url}; success={success}")
        save_state(SERVICE, state)
        return state
    finally:
        close(browser)


def step_login_and_extract_keys(state: dict) -> dict:
    """Login to Adzuna dashboard and pull APP_ID + API_KEY."""
    if state.get("env_kv", {}).get("ADZUNA_APP_ID"):
        print("  [keys] already extracted; skipping")
        return state

    browser = launch()
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "06_login", SERVICE)
        page.fill('input[name="username"], input#session_username', state["email"])
        page.fill('input[name="password"], input#session_password', state["password"])
        page.click('input[type=submit][name=commit], button[type=submit]')
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        screenshot(page, "07_after_login", SERVICE)
        state["post_login_url"] = page.url
        print(f"  [keys] post-login URL: {page.url}")

        page.goto(APPS_URL, timeout=30000, wait_until="domcontentloaded")
        screenshot(page, "08_apps_page", SERVICE)
        body_text = page.evaluate("document.body.innerText")
        state["apps_page_excerpt"] = body_text[:1000]
        print(f"  [keys] apps page excerpt: {body_text[:400]}")

        # Try regex patterns — Adzuna dashboard shows ID + Key
        app_ids = re.findall(r"\b(\d{6,12})\b", body_text)
        app_keys = re.findall(r"\b([a-f0-9]{32})\b", body_text)
        env_kv: dict[str, str] = {}
        if app_ids:
            env_kv["ADZUNA_APP_ID"] = app_ids[0]
        if app_keys:
            env_kv["ADZUNA_API_KEY"] = app_keys[0]

        if not env_kv:
            # Try to click a "New Application" or "Create" button if the dashboard
            # requires explicit app creation
            try:
                page.click('a:has-text("New Application"), a:has-text("Create"), button:has-text("Create"), a:has-text("add"), a:has-text("Add"), a:has-text("new")', timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                # Fill app name + submit if form appears
                try:
                    page.fill('input[name*="name"], input[type=text]', "Job Search Aggregator")
                    page.click('input[type=submit], button[type=submit]', timeout=5000)
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    pass
                screenshot(page, "09_after_create", SERVICE)
                body_text = page.evaluate("document.body.innerText")
                app_ids = re.findall(r"\b(\d{6,12})\b", body_text)
                app_keys = re.findall(r"\b([a-f0-9]{32})\b", body_text)
                if app_ids:
                    env_kv["ADZUNA_APP_ID"] = app_ids[0]
                if app_keys:
                    env_kv["ADZUNA_API_KEY"] = app_keys[0]
            except Exception as e:
                print(f"  [keys] no create-app flow: {e}")

        if env_kv:
            state["env_kv"] = env_kv
            state["key_extraction_status"] = "ok"
            print(f"  [keys] extracted: {env_kv}")
        else:
            state["key_extraction_status"] = "not_found"
            print(f"  [keys] no keys found in dashboard")

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
        print(f"  [skip] signup already done at {state['signup_completed_url']}")

    if state.get("verify_email_status") != "ok":
        state = step_verify_email(state)
    else:
        print(f"  [skip] email verification already done")

    if not state.get("env_kv") or not state["env_kv"].get("ADZUNA_APP_ID"):
        state = step_login_and_extract_keys(state)
    else:
        print(f"  [skip] keys already extracted")

    env_kv = state.get("env_kv") or {}
    if env_kv.get("ADZUNA_APP_ID"):
        print(f"\n[SUCCESS] Adzuna keys captured:")
        print(f"  ADZUNA_APP_ID  = {env_kv['ADZUNA_APP_ID']}")
        print(f"  ADZUNA_API_KEY = {env_kv.get('ADZUNA_API_KEY', '')[:8]}...")
        return 0
    else:
        print(f"\n[PARTIAL] state so far:")
        print(state)
        return 1


if __name__ == "__main__":
    sys.exit(main())
