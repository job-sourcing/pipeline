#!/usr/bin/env python3
"""Careerjet registration via ZenRows Browser Sessions (residential IP + real Chrome).

Only the /register form is Turnstile-gated; we run that one step inside a
ZenRows cloud browser (Playwright over CDP). Account creation is server-side,
so afterwards we can log in from the container (login form has no Turnstile).

State: ingest/data/signup_artifacts/careerjet/state.json (same as signup lib).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from lib import email_unified as eu
from lib.state import gen_password, load_state, save_state


def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = HERE.parents[1] / "ingest" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


SERVICE = "careerjet"
# S8-A scrub: env-only credentials — os.environ first, ingest/.env fallback.
ZENROWS_KEY = (os.environ.get("ZENROWS_API_KEY")
               or _env_file_value("ZENROWS_API_KEY"))
REGISTER_URL = "https://www.careerjet.com/register?redir=%2Fpartners%2Fregister%2Fas-publisher"


async def run() -> int:
    state = load_state(SERVICE)
    if not state.get("email"):
        state["email"] = eu.alias_for_service(SERVICE)
        state["password"] = gen_password()
        save_state(SERVICE, state)
    email, password = state["email"], state["password"]
    print(f"account: {email}")

    from playwright.async_api import async_playwright

    connection_url = f"wss://browser.zenrows.com?apikey={ZENROWS_KEY}"
    async with async_playwright() as p:
        print("connecting to ZenRows browser...")
        browser = await p.chromium.connect_over_cdp(connection_url)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(REGISTER_URL, timeout=45000, wait_until="domcontentloaded")
            print(f"loaded: {page.url}")

            await page.fill('input#email, input[name=email]', email)
            await page.fill('input#password, input[name=password]', password)
            print("credentials filled; waiting for turnstile...")

            # wait for turnstile token to populate (managed mode on a clean
            # residential IP usually auto-solves in 3-15s)
            token = ""
            for _ in range(30):
                token = await page.evaluate(
                    "() => { const i = document.querySelector('input[name=turnstile_token]');"
                    " return i ? i.value : ''; }")
                if token:
                    break
                await asyncio.sleep(1.5)
            print(f"turnstile token: {'OK len=' + str(len(token)) if token else 'TIMEOUT'}")

            t0 = int(time.time() * 1000)
            await page.click('.register-form button[type=submit]', timeout=10000)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            body = await page.evaluate("document.body.innerText")
            print(f"post-submit URL: {page.url}")
            print(f"body excerpt: {body[:400]}")

            state.update({
                "zr_register_final_url": page.url,
                "zr_register_excerpt": body[:800],
                "zr_turnstile_solved": bool(token),
                "email_window_start_ms": t0,
                # Success = no error banner (the redirect target itself
                # contains "/register" — URL check misjudges; CodeRabbit R1).
                "register_done": "An error occurred" not in body,
            })
            save_state(SERVICE, state)
            print(f"\nregister_done: {state['register_done']}")
            return 0 if state["register_done"] else 1
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
