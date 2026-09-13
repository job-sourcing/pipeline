#!/usr/bin/env python3
"""Complete the Careerjet publisher wizard (3 steps) in a ZenRows browser — v2.

Key insight from diagnosis: selecting #country triggers an AJAX that
RE-RENDERS the address fields (country-specific formats). Order matters:
country → wait for address re-render → fill address fields → turnstile → Continue.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from cj_session import extract_api_key  # noqa: E402
from lib.state import load_state, save_state


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
CJ = "https://www.careerjet.com"
WIZARD_URL = f"{CJ}/partners/register/as-publisher"


def log(*a):
    print(" ".join(str(x) for x in a), flush=True)


async def wait_turnstile(page, timeout_s: int = 40) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            val = await page.evaluate(
                "() => { const i = document.querySelector('input[name=turnstile_token]');"
                " return i ? i.value : ''; }")
            if val:
                return val
        except Exception:
            pass
        await asyncio.sleep(1.5)
    return ""



async def safe_click(page, sel: str, timeout: int = 8000):
    """Click that tolerates navigation-wait timeouts (click still fires)."""
    try:
        await page.click(sel, timeout=timeout)
    except Exception as e:
        if "Timeout" in type(e).__name__ and "click action done" in str(e):
            return True  # click fired, navigation pending
        raise

async def visible_named_fields(page) -> list[dict]:
    return await page.evaluate(
        """() => Array.from(document.querySelectorAll(
              'form.validate input, form.validate textarea, form.validate select'))
            .filter(e => e.offsetParent !== null && e.name)
            .map(e => ({tag: e.tagName, type: e.type, name: e.name, id: e.id,
                        value: (e.value||'').slice(0,50), required: e.required}))""")


async def wait_for_valid(page, timeout_s: int = 15) -> list[dict]:
    """Wait until the form passes checkValidity; return invalid fields otherwise."""
    deadline = time.time() + timeout_s
    invalid = []
    while time.time() < deadline:
        invalid = await page.evaluate(
            """() => { const f = document.querySelector('form.validate');
                if (!f) return [{name: 'NO_FORM'}];
                return Array.from(f.querySelectorAll('input,select,textarea'))
                  .filter(e => !e.checkValidity())
                  .map(e => ({name: e.name, type: e.type, value: (e.value||'').slice(0,30),
                              visible: e.offsetParent !== null})); }""")
        if not invalid:
            return []
        await asyncio.sleep(1)
    return invalid


async def current_step(page) -> str:
    return await page.evaluate(
        """() => { const li = document.querySelector('.steps li.active, li.active');
            return li ? li.textContent.trim() : '?'; }""")


async def run() -> int:
    state = load_state(SERVICE)
    email, password = state["email"], state["password"]

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        log("connecting to ZenRows browser...")
        browser = await p.chromium.connect_over_cdp(
            f"wss://browser.zenrows.com?apikey={ZENROWS_KEY}")
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(f"{CJ}/login?redir=%2Fpartners%2Fregister%2Fas-publisher",
                            timeout=45000, wait_until="domcontentloaded")
            await page.fill('input#email', email)
            await page.fill('input#password', password)
            try:
                await page.click('button[type=submit]', timeout=10000)
            except Exception:
                log("login click navigation wait — proceeding")
            try:
                await page.wait_for_url("**/partners/**", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(2)
            if "/partners/register/as-publisher" not in page.url:
                await page.goto(WIZARD_URL, timeout=45000, wait_until="domcontentloaded")
                await asyncio.sleep(2)
            log(f"wizard loaded: {page.url}, step: {await current_step(page)}")

            # ─── STEP 1 ────────────────────────────────────────────────
            await page.check('#partner_type-individual', timeout=5000)
            await page.fill('#fname', "Alex")
            await page.fill('#lname', "Sourcer")
            # country FIRST → triggers address-field re-render
            await page.select_option('#country', "US")
            await asyncio.sleep(3.5)  # let the address AJAX re-render settle
            # now fill the (possibly re-rendered) address fields
            for sel, val in (('#address', "1 Market St"), ('#address2', ""),
                             ('#city', "San Francisco"), ('#postal_code', "94105")):
                try:
                    await page.fill(sel, val, timeout=4000)
                except Exception as e:
                    log(f"  fill {sel}: {type(e).__name__}")
            try:
                await page.select_option('#state', "CA", timeout=4000)
            except Exception as e:
                log(f"  state select: {type(e).__name__}")

            invalid = await wait_for_valid(page)
            log(f"step1 validity: {'OK' if not invalid else invalid}")
            token = await wait_turnstile(page)
            log(f"step1 turnstile: {len(token)}")
            await safe_click(page, 'button.btn-next')
            await asyncio.sleep(10)
            log(f"step now: {await current_step(page)} | url: {page.url}")
            state["wizard_step1_done"] = True
            save_state(SERVICE, state)

            # ─── STEP 2 ────────────────────────────────────────────────
            fields = await visible_named_fields(page)
            log("STEP2 fields:", [(f["name"], f["type"]) for f in fields])
            body = await page.evaluate("document.body.innerText")
            log("step2 visible text:", " ".join(body.split())[:600])

            # fill generically
            for f in fields:
                name = f["name"]
                if name in ("csrf_token", "turnstile_token"):
                    continue
                sel = f"form.validate [name={name}]"
                try:
                    if f["tag"] == "SELECT":
                        opts = await page.evaluate(
                            f"() => Array.from(document.querySelector('form.validate [name={name}]').options).map(o=>o.value).filter(Boolean)")
                        for pref in ("US", "United States", "en", "other", "Other", "jobs", "career", "technology"):
                            if pref in opts:
                                await page.select_option(sel, pref)
                                break
                        else:
                            if opts:
                                await page.select_option(sel, opts[0])
                    elif f["type"] == "checkbox":
                        await page.check(sel, timeout=2000)
                    elif f["type"] in ("text", "url", "email", "tel", "number", "", "textarea"):
                        val = ""
                        if "url" in name or "site" in name or "website" in name:
                            val = "https://github.com/strbtwc/job-sourcing-research"
                        elif "desc" in name or "about" in name or "comment" in name:
                            val = "Personal job-search market research."
                        if val:
                            await page.fill(sel, val, timeout=3000)
                except Exception as e:
                    log(f"  field {name}: {type(e).__name__}")

            invalid = await wait_for_valid(page)
            log(f"step2 validity: {'OK' if not invalid else invalid}")
            token = await wait_turnstile(page)
            log(f"step2 turnstile: {len(token)}")
            await safe_click(page, 'button.btn-next')
            await asyncio.sleep(10)
            log(f"step now: {await current_step(page)} | url: {page.url}")
            state["wizard_step2_done"] = True
            save_state(SERVICE, state)

            # ─── STEP 3 ────────────────────────────────────────────────
            fields = await visible_named_fields(page)
            log("STEP3 fields:", [(f["name"], f["type"]) for f in fields])
            body = await page.evaluate("document.body.innerText")
            log("step3 visible text:", " ".join(body.split())[:600])
            token = await wait_turnstile(page)
            log(f"step3 turnstile: {len(token)}")
            # final submit button may differ
            for sel in ("button.btn-next", "form.validate button[type=submit]",
                        "button:has-text('Confirm')", "button:has-text('Submit')",
                        "button:has-text('Finish')"):
                try:
                    await safe_click(page, sel, timeout=3000)
                    break
                except Exception:
                    continue
            await asyncio.sleep(10)

            body = await page.evaluate("document.body.innerText")
            log(f"FINAL url: {page.url}")
            log(f"final text: {' '.join(body.split())[:900]}")
            key, anchored = extract_api_key(body)
            state["wizard_final_url"] = page.url
            state["wizard_final_excerpt"] = body[:1500]
            if key:
                state["env_kv"] = {"CAREERJET_API_KEY_2": key}
                state["key_extraction_status"] = ("ok_from_wizard" if anchored
                                                  else "unanchored_verify")
                log(f"API KEY FOUND: {key[:6]}...{key[-4:]} "
                    f"(anchored={anchored})")
            save_state(SERVICE, state)
            return 0
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
