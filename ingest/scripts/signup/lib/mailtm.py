"""mail.tm client — disposable email with FULL body access.

Why we need this: the priv.email (ImprovMX) consumer skill only exposes the
email SUBJECT (sender + recipient + delivery events) — NOT the body. Most
verification emails put a unique CLICKABLE LINK in the body (not the subject),
so without body access we cannot complete email verification for services
that require a link click.

mail.tm offers:
  - A FREE, public REST API at api.mail.tm
  - Full body retrieval (both text and HTML)
  - No phone verification, no captcha
  - 25-day mailbox retention
  - Domains rotate (currently emalupe.com)

Caveat: mail.tm domains are on common disposable-email blocklists. Some
services (especially large ones with strict anti-abuse) reject them at
signup. For those, we fall back to priv.email (which won't be flagged as
disposable) — but then we lose body access and need user help or
v3-mail.priv.email Ed25519 credentials (out of scope per the consumer skill).
"""
from __future__ import annotations

import json
import secrets
import string
import time
import urllib.request
import urllib.error
from typing import Optional

API_BASE = "https://api.mail.tm"
DEFAULT_PASSWORD = "P@ssw0rd!1234"  # mail.tm requires complexity; static is fine


def _domains() -> list[str]:
    """List active mail.tm domains."""
    try:
        req = urllib.request.Request(f"{API_BASE}/domains")
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return [d["domain"] for d in data.get("hydra:member", []) if d.get("isActive")]
    except Exception as e:
        raise RuntimeError(f"mail.tm /domains failed: {e}") from None


def create_account(address: Optional[str] = None, password: str = DEFAULT_PASSWORD) -> dict:
    """Create a new mail.tm account. Returns {address, password, id}."""
    domain = _domains()[0] if not address else address.split("@")[-1]
    if not address:
        local = "jobsearch" + "".join(secrets.choice(string.digits) for _ in range(6))
        address = f"{local}@{domain}"
    body = json.dumps({"address": address, "password": password}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/accounts",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            data["address"] = address
            data["password"] = password
            return data
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"mail.tm create HTTP {e.code}: {body[:200]}") from None


def get_token(address: str, password: str = DEFAULT_PASSWORD) -> str:
    """Login → JWT token."""
    body = json.dumps({"address": address, "password": password}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/token",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["token"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"mail.tm /token HTTP {e.code}") from None


def list_messages(token: str, page: int = 1) -> list[dict]:
    """List messages in inbox."""
    req = urllib.request.Request(
        f"{API_BASE}/messages?page={page}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get("hydra:member", [])
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"mail.tm /messages HTTP {e.code}") from None


def get_message(token: str, msg_id: str) -> dict:
    """Get full message (including body)."""
    req = urllib.request.Request(
        f"{API_BASE}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"mail.tm /messages/{msg_id} HTTP {e.code}") from None


def wait_for_message(
    token: str,
    from_contains: Optional[str] = None,
    subject_contains: Optional[str] = None,
    timeout_s: int = 180,
    poll_interval_s: int = 10,
) -> dict | None:
    """Poll inbox until a matching message arrives. Returns full message with body."""
    deadline = time.time() + timeout_s
    seen: set[str] = set()
    while time.time() < deadline:
        try:
            msgs = list_messages(token)
        except RuntimeError as e:
            print(f"  [mail.tm] {e}; backing off")
            time.sleep(poll_interval_s)
            continue

        for m in msgs:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            if from_contains and from_contains.lower() not in m.get("from", {}).get("address", "").lower():
                continue
            if subject_contains and subject_contains.lower() not in m.get("subject", "").lower():
                continue
            # Found a match — fetch the full body
            full = get_message(token, m["id"])
            return full
        time.sleep(poll_interval_s)
    return None


if __name__ == "__main__":
    # Smoke test
    import sys
    print("=== mail.tm smoke test ===")
    print("domains:", _domains())
    acc = create_account()
    print(f"created: {acc['address']}")
    tok = get_token(acc["address"], acc["password"])
    print(f"token len: {len(tok)}")
    print(f"inbox count: {len(list_messages(tok))}")
