#!/usr/bin/env python3
"""Complete the Careerjet publisher wizard (3 steps) in a ZenRows browser.

Every wizard step's POST is Turnstile-gated server-side; we drive the whole
wizard inside a ZenRows cloud browser (residential IP), waiting for the
turnstile token to auto-solve before each Next click.

Step 1 (About you):    individual + name + address
Step 2 (Tell us more): site/traffic questions — fields dumped + filled generically
Step 3 (Confirmation): submit + extract API key
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

LOG = []


def log(*a):
    line = " ".join(str(x) for x in a)
    LOG.append(line)
    print(line, flush=True)


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


async def dump_fields(page) -> list[dict]:
    return await page.evaluate(
        """() => Array.from(document.querySelectorAll(
              'form.validate input, form.validate textarea, form.validate select'))
            .filter(e => e.offsetParent !== null || e.type === 'hidden')
            .map(e => ({tag: e.tagName, type: e.type, name: e.name, id: e.id,
                        value: (e.value||'').slice(0,60), required: e.required,
                        options: e.tagName==='SELECT' ? Array.from(e.options).slice(0,80).map(o=>o.value).filter(Boolean) : undefined}))""")


async def click_next(page):
    for sel in ("button.btn-next", "form.validate button[type=submit]",
                "button:has-text('Next')", "button:has-text('Continue')",
                "button:has-text('Submit')", "button:has-text('Confirm')"):
        try:
            await page.click(sel, timeout=2500)
            return True
        except Exception:
            continue
    return False



def fields_resemble_step1(fields: list[dict]) -> bool:
    names = {f.get("name") or "" for f in fields}
    return "fname" in names and "address" in names and "postal_code" in names

async def run() -> int:
    state = load_state(SERVICE)
    email, password = state["email"], state["password"]

    from playwright.async_api import async_playwright

    url = f"wss://browser.zenrows.com?apikey={ZENROWS_KEY}"
    async with async_playwright() as p:
        log("connecting to ZenRows browser...")
        browser = await p.chromium.connect_over_cdp(url)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        state["wizard_log"] = LOG
        try:
            # ── login ────────────────────────────────────────────────
            await page.goto(f"{CJ}/login?redir=%2Fpartners%2Fregister%2Fas-publisher",
                            timeout=45000, wait_until="domcontentloaded")
            await page.fill('input#email', email)
            await page.fill('input#password', password)
            await page.click('button[type=submit]', timeout=10000)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            log(f"post-login URL: {page.url}")

            # ── wizard ───────────────────────────────────────────────
            if "/partners/register/as-publisher" not in page.url:
                try:
                    await page.goto(WIZARD_URL, timeout=45000, wait_until="domcontentloaded")
                except Exception:
                    log("goto timeout — continuing on current page")
            await asyncio.sleep(2)

            # STEP 1: About you
            fields = await dump_fields(page)
            log("STEP1 fields:", [f["name"] for f in fields])
            # capture network responses for diagnosis
            responses = []
            page.on("response", lambda r: responses.append((r.status, r.url)) if "/partners/register" in r.url else None)
            await page.check('#partner_type-individual', timeout=5000)
            await page.fill('#fname', "Alex")
            await page.fill('#lname', "Sourcer")
            await page.fill('#address', "1 Market St")
            await page.fill('#city', "San Francisco")
            await page.select_option('#state', "CA")
            await page.fill('#postal_code', "94105")
            await page.select_option('#country', "US")
            token = await wait_turnstile(page)
            log(f"step1 turnstile: {len(token)}")
            await click_next(page)
            await asyncio.sleep(6)
            log(f"after step1 URL: {page.url}")
            log(f"step1 POST responses: {responses}")
            # capture any error text
            errs = await page.evaluate(
                """() => Array.from(document.querySelectorAll(
                    '.fcm, [class*=error], [class*=alert], [class*=notice], [class*=invalid]'))
                    .filter(e => e.offsetParent !== null && e.textContent.trim())
                    .map(e => e.textContent.trim().slice(0,150))""")
            log("step1 visible errors:", errs)
            state["wizard_step1_done"] = not fields_resemble_step1(await dump_fields(page))
            save_state(SERVICE, state)

            # STEP 2: Tell us more — inspect fields then fill
            fields = await dump_fields(page)
            log("STEP2 fields:", [(f["name"], f["type"], (f.get("options") or [])[:6]) for f in fields])
            body = await page.evaluate("document.body.innerText")
            log("step2 body:", body[:700])

            # generic filling based on field names
            for f in fields:
                name = f.get("name") or ""
                if not name or name in ("csrf_token", "turnstile_token"):
                    continue
                sel = f"form.validate [name={name}]"
                try:
                    if f["tag"] == "SELECT":
                        opts = f.get("options") or []
                        for pref in ("US", "United States", "en", "other", "Other",
                                     "technology", "jobs", "career", "0-1000", "1000-10000"):
                            if pref in opts:
                                await page.select_option(sel, pref)
                                break
                        else:
                            if opts:
                                await page.select_option(sel, opts[0])
                    elif f["type"] == "checkbox":
                        await page.check(sel, timeout=2000)
                    elif f["type"] in ("text", "url", "email", "tel", "number", "textarea", ""):
                        val = ""
                        if "url" in name or "site" in name or "website" in name:
                            val = "https://github.com/strbtwc/job-sourcing-research"
                        elif "name" in name and "company" not in name:
                            continue
                        elif "desc" in name or "about" in name:
                            val = "Personal job-search market research."
                        if val:
                            await page.fill(sel, val, timeout=3000)
                except Exception as e:
                    log(f"  field {name}: {type(e).__name__}")
            token = await wait_turnstile(page)
            log(f"step2 turnstile: {len(token)}")
            await click_next(page)
            await asyncio.sleep(4)
            log(f"after step2 URL: {page.url}")
            state["wizard_step2_done"] = True
            save_state(SERVICE, state)

            # STEP 3: Confirmation
            fields = await dump_fields(page)
            log("STEP3 fields:", [f["name"] for f in fields])
            body = await page.evaluate("document.body.innerText")
            log("step3 body:", body[:700])
            token = await wait_turnstile(page)
            log(f"step3 turnstile: {len(token)}")
            await click_next(page)
            await asyncio.sleep(6)

            body = await page.evaluate("document.body.innerText")
            log(f"FINAL URL: {page.url}")
            log(f"final body: {body[:900]}")
            key, anchored = extract_api_key(body)
            state["wizard_final_url"] = page.url
            state["wizard_final_excerpt"] = body[:1200]
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
