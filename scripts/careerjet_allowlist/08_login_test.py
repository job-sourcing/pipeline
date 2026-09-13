#!/usr/bin/env python3
"""Careerjet login test from the container (login form has no Turnstile).

Reads credentials from the signup state file. On success, dumps where we
land (should be the redir target or the account area).
"""
from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ingest" / "scripts" / "signup"))

from lib.state import load_state

SERVICE = "careerjet"
CJ = "https://www.careerjet.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"


def main() -> int:
    state = load_state(SERVICE)
    email, password = state["email"], state["password"]

    cj = http.cookiejar.MozillaCookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", UA)]

    def get(url, referer=""):
        req = urllib.request.Request(url, method="GET")
        if referer:
            req.add_header("Referer", referer)
        try:
            with opener.open(req, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.url
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), url

    def post(url, data, referer=""):
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

    # 1. GET login page for CSRF
    status, html, _ = get(f"{CJ}/login?redir=%2Fpartners%2Fregister%2Fas-publisher")
    csrf = (re.search(r'name="csrf_token"[^>]*value="([0-9a-f]+)"', html) or [None, ""])[1]

    # 2. POST login
    status, body, final = post(f"{CJ}/login", {
        "csrf_token": csrf,
        "redir": "/partners/register/as-publisher",
        "universe": "jobseeker",
        "email": email,
        "password": password,
    }, referer=f"{CJ}/login?redir=%2Fpartners%2Fregister%2Fas-publisher")
    text = " ".join(re.sub(r"<[^>]+>", " ", body).split())
    print(f"login POST -> {status}, final={final}")
    print(f"body excerpt: {text[:400]}")

    logged_in = "/login" not in final and "Sign in" not in text[:200]
    print(f"logged_in: {logged_in}")

    if logged_in:
        # 3. Where can we go? Try the publisher registration page
        status, html, final = get(f"{CJ}/partners/register/as-publisher")
        text2 = " ".join(re.sub(r"<[^>]+>", " ", html).split())
        print(f"\npublisher page: {status}, final={final}")
        print(f"excerpt: {text2[:500]}")
        # dump form fields
        for m in re.finditer(r"<form[^>]*>", html):
            print("FORM:", " ".join(m.group(0).split())[:160])
        for m in re.finditer(r"<(?:input|textarea|select)[^>]*>", html):
            t = " ".join(m.group(0).split())
            if "csrf" in t or "sitekey" in t or "turnstile" in t.lower():
                print("KEY FIELD:", t[:220])
        print("\nturnstile on publisher form:", "turnstile" in html.lower())
    return 0 if logged_in else 1


if __name__ == "__main__":
    sys.exit(main())
