#!/usr/bin/env python3
"""Shared Careerjet session helpers: login from container + fetch pages."""
from __future__ import annotations

import re
import sys
import urllib.error
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

_jar = http.cookiejar.MozillaCookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_opener.addheaders = [("User-Agent", UA)]


def get(url: str, referer: str = ""):
    req = urllib.request.Request(url, method="GET")
    if referer:
        req.add_header("Referer", referer)
    try:
        with _opener.open(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), url
    except urllib.error.URLError as e:
        raise RuntimeError(f"Careerjet unreachable: {e.reason}") from e


def post(url: str, data: dict, referer: str = ""):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Origin", CJ)
    if referer:
        req.add_header("Referer", referer)
    try:
        with _opener.open(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), url


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([0-9a-f]+)"', html)
    return m.group(1) if m else ""


def extract_api_key(text: str) -> tuple[str | None, bool]:
    """Find the Careerjet API key in page text (shared by 10/13/14).

    Careerjet pages carry several 32-hex tokens (the API key, site ids,
    CSRF values), so an unanchored regex can store the wrong one
    (CodeRabbit round-3). Prefer a token with an 'api' context window
    around it; fall back to the first plain 32-hex token only when no
    anchor exists — the caller reports anchored=False so the operator
    verifies before use. Returns (key, anchored).
    """
    best: str | None = None
    for m in re.finditer(r"\b[a-f0-9]{32}\b", text):
        ctx = text[max(0, m.start() - 80):m.end() + 80].lower()
        if "api" in ctx:
            return m.group(0), True
        if best is None:
            best = m.group(0)
    return best, False


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def login(email: str = None, password: str = None) -> bool:
    state = load_state(SERVICE)
    email = email or state["email"]
    password = password or state["password"]
    status, html, _ = get(f"{CJ}/login?redir=%2Fpartners")
    csrf = csrf_of(html)
    if not csrf:
        raise RuntimeError(
            f"Careerjet login page returned no CSRF token "
            f"(HTTP {status} — blocked or shape change)")
    status, body, final = post(f"{CJ}/login", {
        "csrf_token": csrf, "redir": "/partners", "universe": "jobseeker",
        "email": email, "password": password,
    }, referer=f"{CJ}/login?redir=%2Fpartners")
    ok = "/login" not in final
    return ok
