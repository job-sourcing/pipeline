#!/usr/bin/env python3
"""Careerjet publisher account registration — plain HTTP (no WAF on careerjet.com).

Registers the partner-email alias (CAREERJET_PARTNER_EMAIL, resolved from the
environment by lib.email_unified) as a Careerjet account, then walks the
publisher registration form. Persists state via the repo's signup lib so it's
resumable.

Usage:
  python3 03_register_account.py            # register + walk publisher form
"""
from __future__ import annotations

import re
import sys
import time
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from lib import email_unified as eu
from lib.state import gen_password, load_state, save_state

SERVICE = "careerjet"
CJ = "https://www.careerjet.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

cj = http.cookiejar.MozillaCookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", UA)]


def get(url: str, referer: str = ""):
    req = urllib.request.Request(url, method="GET")
    if referer:
        req.add_header("Referer", referer)
    try:
        with opener.open(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), url


def post(url: str, data: dict, referer: str = ""):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Origin", CJ)
    if referer:
        req.add_header("Referer", referer)
    try:
        with opener.open(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), url


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([0-9a-f]+)"', html)
    return m.group(1) if m else ""


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def main() -> int:
    state = load_state(SERVICE)

    if not state.get("email"):
        state["email"] = eu.alias_for_service(SERVICE)
        state["password"] = gen_password()
        save_state(SERVICE, state)
    print(f"account: {state['email']}")

    # ── Step 1: register the account ─────────────────────────────────────
    if not state.get("register_done"):
        status, html, final = get(f"{CJ}/register?redir=%2Fpartners%2Fregister%2Fas-publisher")
        csrf = csrf_of(html)
        print(f"register page: {status}, csrf={csrf[:12]}...")
        t0 = int(time.time() * 1000)
        status, body, final = post(f"{CJ}/register", {
            "csrf_token": csrf,
            "redir": "/partners/register/as-publisher",
            "email": state["email"],
            "password": state["password"],
        }, referer=f"{CJ}/register?redir=%2Fpartners%2Fregister%2Fas-publisher")
        text = text_of(body)
        print(f"register POST -> {status}, final={final}")
        print(f"  body excerpt: {text[:500]}")
        state.update({
            # Success = the error banner is absent. (The success redirect
            # target /partners/register/as-publisher itself contains
            # "/register", so a URL check would misjudge — CodeRabbit R1.)
            "register_done": status == 200 and "An error occurred" not in body,
            "register_status": status,
            "register_final_url": final,
            "register_response_excerpt": text[:800],
            "email_window_start_ms": t0,
        })
        save_state(SERVICE, state)
        if not state["register_done"]:
            print("registration may have failed — inspect state")
            return 1
    else:
        print("register: already done")

    # ── Step 2: publisher registration form ──────────────────────────────
    if not state.get("publisher_done"):
        status, html, final = get(f"{CJ}/partners/register/as-publisher")
        text = text_of(html)
        print(f"\npublisher form: {status}, final={final}")
        print(f"  excerpt: {text[:600]}")
        # dump all form fields for inspection
        form = re.search(
            r'<form[^>]*action="([^"]*)"[^>]*>(.*?)</form>', html, re.S)
        if form:
            print(f"  form action: {form.group(1)}")
            for m in re.finditer(r"<(?:input|textarea|select)[^>]*>", form.group(2)):
                t = " ".join(m.group(0).split())
                print(f"    FIELD: {t[:200]}")
        state["publisher_form_url"] = final
        state["publisher_form_excerpt"] = text[:800]
        save_state(SERVICE, state)
    else:
        print("publisher: already done")

    print("\n[state]", {k: v for k, v in state.items() if k != "password"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
