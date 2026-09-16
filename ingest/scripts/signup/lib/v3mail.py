"""v3-mail.priv.email admin API client — full email body access.

The priv.email ImprovMX consumer skill only gives SUBJECT + sender; the
admin@/billing@/etc. aliases ALSO forward to admin@v3-mail.priv.email, which
runs a custom worker that stores every email (subject + body + attachments)
in Cloudflare D1.

This module logs into the admin panel and exposes the read APIs:
  GET /emails/all?limit=N&include_body=true&address=X@priv.email
  GET /emails/{id}
  GET /emails/{id}/attachments
  GET /recipients
  GET /emails/stats

The admin session cookie (pm_admin_session) lasts a week; we cache it.

Credentials (from the deploy-secrets.json passed in chat) live in the
environment — os.environ first, ingest/.env fallback (the committed secret
store; S8-A scrub — never hardcoded in source):
  v3-mail.ADMIN_PASSWORD  → V3MAIL_ADMIN_PASSWORD
  v3-mail.ADMIN_PATH      → V3MAIL_ADMIN_PATH
  v3-mail.QUERY_API_TOKEN → V3MAIL_API_TOKEN (query API; this client uses
                             the admin session cookie instead)
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path
from typing import Optional

from .state import ARTIFACTS  # repo-relative since 2026-08-27 consolidation


def _env(name: str) -> str:
    """os.environ first, then ingest/.env (parents[3] = ingest/)."""
    val = os.environ.get(name)
    if val:
        return val
    env_path = Path(__file__).resolve().parents[3] / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


API_BASE = "https://v3-mail.priv.email"
ADMIN_PASSWORD = _env("V3MAIL_ADMIN_PASSWORD")
ADMIN_PATH = _env("V3MAIL_ADMIN_PATH")
API_TOKEN = _env("V3MAIL_API_TOKEN")  # query API token (unused by this client)
COOKIE_JAR = ARTIFACTS / "_v3mail_cookies.txt"
COOKIE_JAR.parent.mkdir(parents=True, exist_ok=True)

# Build a single urllib opener that handles cookies + redirects correctly
_cj = http.cookiejar.MozillaCookieJar(str(COOKIE_JAR))
if COOKIE_JAR.exists():
    try:
        _cj.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        pass
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cj),
    urllib.request.HTTPRedirectHandler(),
)
_opener.addheaders = [
    ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"),
]


def _save_cookiejar() -> None:
    _cj.save(ignore_discard=True, ignore_expires=True)


def _request(method: str, path: str, body: bytes | None = None,
             content_type: str = "application/json", timeout: int = 15) -> tuple[int, dict, bytes]:
    url = f"{API_BASE}{path}"
    headers: list[tuple[str, str]] = []
    if body is not None:
        headers.append(("Content-Type", content_type))
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with _opener.open(req, timeout=timeout) as r:
            _save_cookiejar()
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        _save_cookiejar()
        return e.code, dict(e.headers), e.read()


def login() -> bool:
    """Login to admin panel. Returns True on success."""
    # Check if existing session works
    try:
        status, _, _ = _request("GET", "/emails/stats")
        if status == 200:
            return True
    except Exception:
        pass

    body = urllib.parse.urlencode({"password": ADMIN_PASSWORD}).encode()
    status, _, _ = _request(
        "POST", f"/{ADMIN_PATH}/login", body=body,
        content_type="application/x-www-form-urlencoded",
    )
    if status in (200, 302):
        return True
    raise RuntimeError(f"v3-mail admin login failed: HTTP {status}")


def list_emails(limit: int = 50, address: Optional[str] = None,
                include_body: bool = True) -> list[dict]:
    """List emails. Optionally filter by recipient address."""
    login()
    params = [f"limit={limit}"]
    if address:
        params.append(f"address={urllib.parse.quote(address)}")
    if include_body:
        params.append("include_body=true")
    path = "/emails/all?" + "&".join(params)
    status, _, raw = _request("GET", path)
    if status != 200:
        raise RuntimeError(f"v3-mail /emails/all HTTP {status}: {raw[:200]}")
    data = json.loads(raw)
    return data.get("results", []) or []


def get_email(msg_id: int) -> dict:
    """Get a single email by ID."""
    login()
    status, _, raw = _request("GET", f"/emails/{msg_id}")
    if status != 200:
        raise RuntimeError(f"v3-mail /emails/{msg_id} HTTP {status}")
    return json.loads(raw)


def wait_for_email(
    recipient: str,
    sender_contains: Optional[str] = None,
    subject_contains: Optional[str] = None,
    timeout_s: int = 180,
    poll_interval_s: int = 10,
    since_ts_ms: Optional[int] = None,
) -> dict | None:
    """Poll v3-mail admin API for an email matching the criteria."""
    deadline = time.time() + timeout_s
    seen: set[int] = set()
    while time.time() < deadline:
        try:
            emails = list_emails(limit=50, address=recipient, include_body=True)
        except RuntimeError as e:
            print(f"  [v3-mail] {e}; backing off")
            time.sleep(poll_interval_s)
            continue

        for e in emails:
            if e["id"] in seen:
                continue
            seen.add(e["id"])

            if sender_contains:
                from_addr = (
                    str(e.get("from_address", "") or "") + " "
                    + str(e.get("from_header", "") or "")
                ).lower()
                if sender_contains.lower() not in from_addr:
                    continue

            if subject_contains:
                if subject_contains.lower() not in e.get("subject", "").lower():
                    continue

            if since_ts_ms and e.get("received_at"):
                try:
                    # received_at is ISO 8601 like "2026-08-25T11:20:11.844Z"
                    received_dt = time.strptime(e["received_at"][:19], "%Y-%m-%dT%H:%M:%S")
                    received_ms = int(time.mktime(received_dt) * 1000)
                    if received_ms < since_ts_ms:
                        continue
                except Exception:
                    pass

            return e
        time.sleep(poll_interval_s)
    return None


def extract_links_from_html(html: str, host_contains: str = "") -> list[str]:
    import re
    if not html:
        return []
    links = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>', html, flags=re.IGNORECASE)
    if not host_contains:
        return links
    return [l for l in links if host_contains.lower() in l.lower()]


def extract_code_from_text(text: str, length_range: tuple[int, int] = (4, 8)) -> Optional[str]:
    if not text:
        return None
    import re
    min_len, max_len = length_range
    matches = re.findall(rf"\b(\d{{{min_len},{max_len}}})\b", text)
    if matches:
        matches.sort(key=len, reverse=True)
        return matches[0]
    return None


if __name__ == "__main__":
    print("=== v3-mail smoke test ===")
    login()
    emails = list_emails(limit=5, include_body=False)
    print(f"Got {len(emails)} emails")
    for e in emails[:5]:
        print(f"  id={e['id']} to={e['to_address']} subj={e.get('subject', '')[:60]}")
