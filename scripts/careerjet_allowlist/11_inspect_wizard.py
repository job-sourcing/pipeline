#!/usr/bin/env python3
"""Inspect the Careerjet publisher wizard DOM structure (step containers,
button behaviors, validation) inside ZenRows browser."""
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

            # structural dump: what sections/steps exist and which is visible
            info = await page.evaluate("""() => {
                const out = {sections: [], buttons: [], stepNav: []};
                // candidate step containers
                for (const sel of ['section', '.step', '[data-step]', 'fieldset', '.wizard-step', '.form-step']) {
                    document.querySelectorAll(sel).forEach(e => {
                        const r = e.getBoundingClientRect();
                        if (e.querySelector('input,select,textarea') || sel === '.step' || e.hasAttribute('data-step')) {
                            out.sections.push({sel, cls: e.className, step: e.getAttribute('data-step'),
                                               id: e.id, visible: r.width > 0 || r.height > 0 || e.offsetParent !== null,
                                               h: (e.querySelector('h1,h2,h3,h4,legend')||{}).textContent || ''});
                        }
                    });
                }
                document.querySelectorAll('button, input[type=submit]').forEach(b => {
                    const r = b.getBoundingClientRect();
                    out.buttons.push({tag: b.tagName, cls: b.className, type: b.type,
                                       text: (b.textContent||'').trim().slice(0,40),
                                       visible: r.width > 0 || r.height > 0});
                });
                // step navigation markers
                document.querySelectorAll('li, .steps li, nav li').forEach(li => {
                    const t = (li.textContent||'').trim();
                    if (t && t.length < 30 && /about you|tell us|confirmation/i.test(t)) {
                        const r = li.getBoundingClientRect();
                        out.stepNav.push({text: t, cls: li.className, visible: r.width>0});
                    }
                });
                return out;
            }""")
            print(json.dumps(info, indent=1)[:4000])
        finally:
            try:
                await page.close()
            except Exception:
                pass
            await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
