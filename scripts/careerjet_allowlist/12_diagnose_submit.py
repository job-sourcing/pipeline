#!/usr/bin/env python3
"""Diagnose wizard step-1 validation: fill, checkValidity, find invalid fields,
then click Continue and capture ALL network traffic."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from lib.state import load_state


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


async def run() -> int:
    state = load_state(SERVICE)
    email, password = state["email"], state["password"]

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            f"wss://browser.zenrows.com?apikey={ZENROWS_KEY}")
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(f"{CJ}/login?redir=%2Fpartners%2Fregister%2Fas-publisher",
                            timeout=45000, wait_until="domcontentloaded")
            await page.fill('input#email', email)
            await page.fill('input#password', password)
            await page.click('button[type=submit]', timeout=10000)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            if "/partners/register/as-publisher" not in page.url:
                await page.goto(f"{CJ}/partners/register/as-publisher", timeout=45000)
                await asyncio.sleep(2)

            # fill step 1
            await page.check('#partner_type-individual', timeout=5000)
            await page.fill('#fname', "Alex")
            await page.fill('#lname', "Sourcer")
            await page.fill('#address', "1 Market St")
            await page.fill('#city', "San Francisco")
            await page.select_option('#state', "CA")
            await page.fill('#postal_code', "94105")
            await page.select_option('#country', "US")
            await asyncio.sleep(3)  # let address AJAX settle

            validity = await page.evaluate("""() => {
                const f = document.querySelector('form.validate');
                if (!f) return {checkValidity: false, invalid: [{name: 'NO_FORM'}]};
                const out = {checkValidity: f.checkValidity(), invalid: []};
                f.querySelectorAll('input,select,textarea').forEach(e => {
                    if (!e.checkValidity()) {
                        out.invalid.push({name: e.name, type: e.type, value: (e.value||'').slice(0,40),
                                          required: e.required, pattern: e.pattern,
                                          visible: e.offsetParent !== null, disabled: e.disabled,
                                          validity: Object.entries(e.validity).filter(([k,v])=>v).map(([k])=>k)});
                    }
                });
                return out;
            }""")
            print(json.dumps(validity, indent=1))

            # capture all requests on submit
            traffic = []
            page.on("request", lambda r: traffic.append(("REQ", r.method, r.url[:110])))
            page.on("response", lambda r: traffic.append(("RESP", r.status, r.url[:110])))
            page.on("console", lambda m: traffic.append(("CONSOLE", m.type, (m.text or "")[:110])))

            await page.click('button.btn-next', timeout=8000)
            await asyncio.sleep(8)
            print("\n=== TRAFFIC ===")
            for t in traffic:
                print(t)
            print("\nURL now:", page.url)
            stepnav = await page.evaluate(
                "() => Array.from(document.querySelectorAll('li')).filter(li=>/about you|tell us|confirmation/i.test(li.textContent)).map(li => li.textContent.trim() + ' [' + li.className + ']')")
            print("stepnav:", stepnav)
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
