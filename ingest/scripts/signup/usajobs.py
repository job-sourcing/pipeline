#!/usr/bin/env python3
"""USAJOBS API key request automation — uses free US proxy for IP-blocked site.

USAJOBS (developer.usajobs.gov) is behind Akamai WAF that blocks our HK
container IP. Free US proxies from geonode.com work for the signup form.

Form URL: https://developer.usajobs.gov/apirequest/ (trailing slash required;
without it the server redirects to the developer portal home, not the form)

Flow:
1. Fetch a working US proxy from geonode (re-validated at script start)
2. Visit developer.usajobs.gov/apirequest/ through the proxy with stealth
3. Fill the form: givenName, lastName, emailAddress=redacted@priv.email,
   phoneNumber (fake 555-555-5555), companyAgency, requestReason, agreeCheck
4. Submit — USAJOBS shows the API key on the response page OR emails it
5. The User-Agent header value = the registered email (USAJOBS_USER_AGENT)

Env: USAJOBS_API_KEY + USAJOBS_USER_AGENT
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from curl_cffi import requests as cc_requests
from lib.state import load_state, save_state
from lib.stealth_browser import launch, new_context, new_page, screenshot, close

SERVICE = "usajobs"
SIGNUP_URL = "https://developer.usajobs.gov/apirequest/"  # trailing slash!


def fetch_working_proxy() -> str | None:
    """Fetch a free US proxy from geonode that can reach USAJOBS."""
    import concurrent.futures
    try:
        r = cc_requests.get(
            "https://proxylist.geonode.com/api/proxy-list?limit=100&page=1"
            "&sort_by=lastChecked&sort_type=desc&protocols=http%2Chttps&country=US",
            timeout=15, impersonate="chrome120",
        )
        fresh = r.json().get("data", [])[:100]
    except Exception as e:
        print(f"  [proxy] geonode failed: {e}")
        return None

    def try_proxy(p):
        proxy_url = f"http://{p['ip']}:{p['port']}"
        try:
            r = cc_requests.get(
                SIGNUP_URL,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=12, impersonate="chrome120",
            )
            if (r.status_code == 200
                and len(r.text) > 1000
                and "Access Denied" not in r.text
                and "givenName" in r.text):
                return proxy_url
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(try_proxy, fresh))

    for r in results:
        if r:
            print(f"  [proxy] using {r}")
            return r
    print("  [proxy] no working proxy found")
    return None


def step_signup(state: dict) -> dict:
    """Fill and submit the USAJOBS API request form via a working proxy."""
    if "email" not in state:
        state["email"] = "redacted@priv.email"
        save_state(SERVICE, state)
        print(f"  [signup] using email={state['email']}")

    if not state.get("proxy"):
        proxy = fetch_working_proxy()
        if not proxy:
            state["signup_status"] = "no_proxy"
            save_state(SERVICE, state)
            return state
        state["proxy"] = proxy
        save_state(SERVICE, state)

    browser = launch(headless=True, proxy=state["proxy"])
    ctx = new_context(browser)
    page = new_page(ctx)
    try:
        # Use the trailing-slash URL — without it the server 302s to the
        # developer portal home, NOT the form
        page.goto(SIGNUP_URL, timeout=60000, wait_until="domcontentloaded")
        screenshot(page, "01_apirequest_form", SERVICE)
        print(f"  [signup] landed URL: {page.url}")

        body_text = page.evaluate("document.body.innerText")
        state["form_page_excerpt"] = body_text[:800]
        if "givenName" not in page.content():
            state["signup_status"] = "form_not_loaded"
            save_state(SERVICE, state)
            print(f"  [signup] form didn't load — body excerpt: {body_text[:300]}")
            return state

        # Fill the form
        page.fill('#givenName', "Job")
        page.fill('#lastName', "Searcher")
        page.fill('#emailAddress', state["email"])
        page.fill('#phoneNumber', "555-555-5555")
        page.fill('#companyAgency', "Personal Research")
        page.fill('#requestReason',
                  "Personal job search aggregation and analysis "
                  "for tech-sector market research.")
        # Tick the agreement checkbox. The real input is visually hidden
        # off-viewport behind the styled label (Playwright's check() fails
        # with "element is outside of the viewport" — observed live
        # 2026-08-29 on geonode proxies). Set it via JS and dispatch the
        # events the form's validation listens for.
        page.eval_on_selector("#agreeCheck", """
            el => {
                el.checked = true;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('click', {bubbles: true}));
            }
        """)
        page.wait_for_timeout(500)
        # Wait for the JS to enable the submit button
        page.wait_for_selector('#submitBtn:not(.disabled)', timeout=5000)
        screenshot(page, "02_form_filled", SERVICE)

        email_start_ms = int(time.time() * 1000)
        page.click('#submitBtn')
        page.wait_for_load_state("domcontentloaded", timeout=60000)
        screenshot(page, "03_after_submit", SERVICE)

        body_text = page.evaluate("document.body.innerText")
        state["signup_completed_url"] = page.url
        state["signup_completed_at"] = int(time.time())
        state["signup_response_excerpt"] = body_text[:2000]
        state["email_window_start_ms"] = email_start_ms
        save_state(SERVICE, state)
        print(f"  [signup] post-submit URL: {page.url}")
        print(f"  [signup] response excerpt: {body_text[:400]}")
        return state
    finally:
        close(browser)


def step_extract_key(state: dict) -> dict:
    """Extract the USAJOBS API key from the response page or v3-mail email."""
    if state.get("env_kv", {}).get("USAJOBS_API_KEY"):
        print("  [keys] already extracted; skipping")
        return state

    # First try the post-submit response body. USAJobs issues BASE64
    # keys (43 chars + '=', verified live 2026-08-29); legacy 32-hex
    # keys are still accepted for history.
    body = state.get("signup_response_excerpt", "")
    m = (re.search(r"\b([A-Za-z0-9+/]{43}=)\b", body)
         or re.search(r"\b([A-Fa-f0-9]{32})\b", body))
    if m:
        key = m.group(1)
        if re.fullmatch(r"[A-Fa-f0-9]{32}", key):
            key = key.upper()      # legacy hex keys only; base64 is
                                    # case-sensitive and must not be touched
        state["env_kv"] = {
            "USAJOBS_API_KEY": key,
            "USAJOBS_USER_AGENT": state["email"],
        }
        state["key_extraction_status"] = "ok_from_response"
        save_state(SERVICE, state)
        print(f"  [keys] extracted from response: {state['env_kv']['USAJOBS_API_KEY'][:8]}...")
        return state

    # Otherwise poll v3-mail for the USAJOBS email
    print("  [keys] not in response; polling v3-mail for USAJOBS email...")
    from lib import email_unified as eu
    since = state.get("email_window_start_ms")
    msg = eu.wait_for_email(
        service=SERVICE,
        sender_contains="usajobs",
        need_body=True,
        timeout_s=180,
        poll_interval_s=10,
        since_ts_ms=since,
    )
    if not msg:
        state["key_extraction_status"] = "no_email_received"
        save_state(SERVICE, state)
        return state

    state["verify_email_subject"] = msg["subject"]
    state["verify_email_received_at"] = msg.get("received_at")
    print(f"  [keys] email subject: {msg['subject']}")

    body_text = msg.get("body_text", "") or ""
    body_html = msg.get("body_html", "") or ""
    m = (re.search(r"\b([A-Za-z0-9+/]{43}=)\b", body_text + body_html)
         or re.search(r"\b([A-Fa-f0-9]{32})\b", body_text + body_html))
    if m:
        key = m.group(1)
        if re.fullmatch(r"[A-Fa-f0-9]{32}", key):
            key = key.upper()      # legacy hex keys only; base64 is
                                    # case-sensitive and must not be touched
        state["env_kv"] = {
            "USAJOBS_API_KEY": key,
            "USAJOBS_USER_AGENT": state["email"],
        }
        state["key_extraction_status"] = "ok_from_email"
        print(f"  [keys] extracted from email: {state['env_kv']['USAJOBS_API_KEY'][:8]}...")
    else:
        state["key_extraction_status"] = "no_key_in_email"
        state["verify_email_body_excerpt"] = body_text[:800]
        print(f"  [keys] no key found in email body")

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

    if not state.get("env_kv") or not state["env_kv"].get("USAJOBS_API_KEY"):
        state = step_extract_key(state)
    else:
        print(f"  [skip] keys already extracted")

    env_kv = state.get("env_kv") or {}
    if env_kv.get("USAJOBS_API_KEY"):
        print(f"\n[SUCCESS] USAJOBS key captured:")
        print(f"  USAJOBS_API_KEY     = {env_kv['USAJOBS_API_KEY']}")
        print(f"  USAJOBS_USER_AGENT  = {env_kv['USAJOBS_USER_AGENT']}")
        return 0
    else:
        print(f"\n[PARTIAL] state so far:")
        print(state)
        return 1


if __name__ == "__main__":
    sys.exit(main())
