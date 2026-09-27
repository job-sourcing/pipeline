#!/usr/bin/env python3
"""Careerjet registration via Firecrawl browser actions (Turnstile bypass).

The register form is Turnstile-gated; our headless Chromium can't solve it.
Firecrawl runs real browsers that pass Turnstile. The account is created
server-side, so afterwards we can log in from our own container (the login
form has no Turnstile).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from lib import email_unified as eu
from lib.state import gen_password, load_state, save_state

SERVICE = "careerjet"
# Audit F3 / S8-A scrub: env-only credentials doctrine — the key lives in
# ingest/.env (FIRECRAWL_API_KEY), same lookup pattern as 15_ip_submit.py;
# no hardcoded fallback (the historical committed value was removed).


def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = HERE.parents[1] / "ingest" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"") or ""
    except OSError:
        pass
    return ""


FIRECRAWL_KEY = (os.environ.get("FIRECRAWL_API_KEY")
                 or _env_file_value("FIRECRAWL_API_KEY"))
REGISTER_URL = "https://www.careerjet.com/register?redir=%2Fpartners%2Fregister%2Fas-publisher"


def firecrawl_scrape(url: str, actions: list[dict], formats: list[str] | None = None) -> dict:
    body = json.dumps({
        "url": url,
        "actions": actions,
        "formats": formats or ["markdown"],
    }).encode()
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v2/scrape", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {FIRECRAWL_KEY}")
    with urllib.request.urlopen(req, timeout=110) as r:
        return json.loads(r.read())


def main() -> int:
    state = load_state(SERVICE)
    if not state.get("email"):
        state["email"] = eu.alias_for_service(SERVICE)
        state["password"] = gen_password()
        save_state(SERVICE, state)
    email, password = state["email"], state["password"]
    print(f"account: {email}")

    actions = [
        {"type": "wait", "selector": "input#email"},
        # focus email field and type
        {"type": "click", "selector": "input#email"},
        {"type": "write", "text": email},
        {"type": "click", "selector": "input#password"},
        {"type": "write", "text": password},
        # give Turnstile time to auto-solve
        {"type": "wait", "milliseconds": 9000},
        # verify the turnstile token got populated
        {"type": "executeJavascript",
         "script": "(() => { const i = document.querySelector('input[name=turnstile_token]'); return i ? i.value.length : -1; })()"},
        # screenshot before submit
        {"type": "screenshot", "fullPage": True},
        # submit
        {"type": "click", "selector": ".register-form button[type=submit]"},
        {"type": "wait", "milliseconds": 6000},
        {"type": "screenshot", "fullPage": True},
    ]

    print("running Firecrawl actions...")
    t0 = int(time.time() * 1000)
    try:
        result = firecrawl_scrape(REGISTER_URL, actions)
    except Exception as e:
        print(f"firecrawl error: {e}")
        return 1

    data = result.get("data", {})
    md = data.get("markdown", "") or ""
    meta = data.get("metadata", {}) or {}
    acts_out = data.get("actions", {}) or {}
    js_returns = acts_out.get("javascriptReturns", [])
    screenshots = acts_out.get("screenshots", []) or []
    url_after = meta.get("url", "") or data.get("url", "")

    print(f"final url: {url_after}")
    print(f"turnstile token length: {js_returns}")
    print(f"screenshots: {len(screenshots)}")
    print(f"markdown excerpt: {md[:500]}")

    success = ("An error occurred" not in md) and ("/register" not in url_after or "Sign in" in md)
    state.update({
        "fc_register_final_url": url_after,
        "fc_register_excerpt": md[:800],
        "fc_turnstile_js_returns": js_returns,
        "fc_screenshots": screenshots,
        "email_window_start_ms": t0,
        "register_done": bool(success),
    })
    save_state(SERVICE, state)
    print(f"\nregister_done: {success}")
    if screenshots:
        print("screenshot URLs:")
        for s in screenshots:
            print("  ", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
