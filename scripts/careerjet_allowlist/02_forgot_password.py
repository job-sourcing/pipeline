#!/usr/bin/env python3
"""Trigger Careerjet forgot-password for the partner email (CAREERJET_PARTNER_EMAIL,
read from the environment) and check response.

If the account exists with that email, a reset link will arrive in the
v3-mail inbox (the shop alias forwards to the v3-mail worker).
"""
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

def _env_file_value(name: str) -> str:
    """Read `name` from ingest/.env (KEY=value lines) if present."""
    env_path = Path(__file__).resolve().parents[1] / "ingest" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""

CJ = "https://www.careerjet.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
JAR_PATH = Path("/home/z/cj_allowlist_cookies.txt")

cj = http.cookiejar.MozillaCookieJar(str(JAR_PATH))
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", UA)]


def get(url):
    req = urllib.request.Request(url, method="GET")
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", url)
    req.add_header("Origin", CJ)
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers), url


if __name__ == "__main__":
    # S8-A scrub: default alias is the CAREERJET_PARTNER_EMAIL credential
    # value — env-only (os.environ first, ingest/.env fallback).
    email = sys.argv[1] if len(sys.argv) > 1 else (
        os.environ.get("CAREERJET_PARTNER_EMAIL")
        or _env_file_value("CAREERJET_PARTNER_EMAIL"))
    # 1. GET forgot-password page for CSRF
    status, html, _ = get(f"{CJ}/password-forgotten")
    m = re.search(r'name="csrf_token"[^>]*value="([0-9a-f]+)"', html)
    if not m:
        print("NO CSRF FOUND; aborting"); sys.exit(1)
    csrf = m.group(1)
    print(f"csrf={csrf[:12]}...")

    # 2. POST the reset request
    status, body, headers, final_url = post(f"{CJ}/password-forgotten", {
        "csrf_token": csrf, "redir": "", "email": email,
    })
    cj.save(ignore_discard=True, ignore_expires=True)
    print(f"POST -> {status}, final={final_url}")
    # look for confirmation text
    text = re.sub(r"<[^>]+>", " ", body)
    text = " ".join(text.split())
    for kw in ("email", "sent", "reset", "password", "exist", "error", "not found"):
        for m2 in re.finditer(kw, text, re.I):
            s = max(0, m2.start() - 80); print(f"  ctx[{kw}]: ...{text[s:m2.end()+120]}...")
            break
