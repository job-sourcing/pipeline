"""Unified email backend — uses v3-mail.priv.email admin API for full body
access (subject + text_body + html_body + attachments).

Why v3-mail: the priv.email ImprovMX consumer skill only exposes SUBJECT +
sender + recipient — NOT the email body. Most verification emails put a
unique CLICKABLE LINK in the body (not the subject), so without body access
we cannot complete email verification for services that require a link
click. The priv.email aliases (admin, billing, noreply, security, support,
shop) ALSO forward to admin@v3-mail.priv.email, which runs a custom
worker that stores every email (subject + body + attachments) in
Cloudflare D1 — full read access via the admin cookie auth.

Per-service alias assignment (uses existing aliases — no ImprovMX changes).
The admin/shop alias addresses are credential values shared with
USAJOBS_USER_AGENT / CAREERJET_PARTNER_EMAIL and are read from the
environment (os.environ first, ingest/.env fallback — S8-A scrub):
  admin alias (USAJOBS_USER_AGENT)    → adzuna, workable
  redacted@priv.email                  → findwork, personio
  redacted@priv.email                  → usajobs, wellfound
  redacted@priv.email                 → jsearch, otta
  redacted@priv.email                  → jooble
  shop alias (CAREERJET_PARTNER_EMAIL) → careerjet
"""
from __future__ import annotations

import re
import time
from typing import Optional

from . import v3mail
from .v3mail import _env

# Service → priv.email alias mapping (uses existing aliases that forward to
# admin@v3-mail.priv.email; this preserves per-service distinction AND gives
# full body access via the v3-mail admin API).
_ADMIN_ALIAS = _env("USAJOBS_USER_AGENT")
_SHOP_ALIAS = _env("CAREERJET_PARTNER_EMAIL")
_ALIAS_MAP = {
    "adzuna": _ADMIN_ALIAS,
    "findwork": "redacted@priv.email",
    "usajobs": "redacted@priv.email",
    "jsearch": "redacted@priv.email",
    "jooble": "redacted@priv.email",
    "careerjet": _SHOP_ALIAS,
    "workable": _ADMIN_ALIAS,  # not a signup target; placeholder
    "personio": "redacted@priv.email",  # same
    "wellfound": "redacted@priv.email",
    "otta": "redacted@priv.email",
}


def alias_for_service(service: str) -> str:
    """Return the priv.email alias used for this service's signup."""
    return _ALIAS_MAP.get(service, _ADMIN_ALIAS)


def wait_for_email(
    service: str,
    sender_contains: Optional[str] = None,
    subject_contains: Optional[str] = None,
    need_body: bool = True,
    timeout_s: int = 180,
    poll_interval_s: int = 10,
    since_ts_ms: Optional[int] = None,
) -> dict | None:
    """Wait for an email via v3-mail admin API (full body access).

    Args:
        service: "adzuna", "findwork", etc. Maps to a priv.email alias.
        sender_contains: substring of sender email/name to filter on
        subject_contains: substring of subject to filter on
        need_body: kept for API compat; v3-mail always returns body
        timeout_s, poll_interval_s: poll cadence
        since_ts_ms: only consider emails newer than this epoch-ms

    Returns:
        dict with keys: subject, sender, recipient, body_html, body_text,
                        message_id, id (v3-mail row id), received_at, raw
    """
    recipient = alias_for_service(service)
    print(f"  [email] polling v3-mail for {recipient}...")
    e = v3mail.wait_for_email(
        recipient=recipient,
        sender_contains=sender_contains,
        subject_contains=subject_contains,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        since_ts_ms=since_ts_ms,
    )
    if not e:
        return None
    return {
        "id": e.get("id"),
        "message_id": e.get("message_id", ""),
        "subject": e.get("subject", ""),
        "sender": e.get("from_address", "") + " " + e.get("from_header", ""),
        "recipient": e.get("to_address", ""),
        "body_html": e.get("html_body", "") or "",
        "body_text": e.get("text_body", "") or "",
        "received_at": e.get("received_at", ""),
        "raw": e,
    }


def extract_links_from_html(html: str, host_contains: str = "") -> list[str]:
    """Pull all <a href="..."> links from HTML."""
    if not html:
        return []
    links = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>', html, flags=re.IGNORECASE)
    if not host_contains:
        return links
    return [l for l in links if host_contains.lower() in l.lower()]


def extract_code_from_text(text: str, length_range: tuple[int, int] = (4, 8)) -> Optional[str]:
    """Find a digit code of length in [min, max] in the text."""
    if not text:
        return None
    min_len, max_len = length_range
    matches = re.findall(rf"\b(\d{{{min_len},{max_len}}})\b", text)
    if matches:
        matches.sort(key=len, reverse=True)
        return matches[0]
    return None


def extract_verify_link(html: str, host_contains: str = "") -> Optional[str]:
    """Extract a verification link from email HTML.

    Looks for links whose URL or anchor text contains common verification
    keywords (confirm, verify, activate, click, signup, register).
    """
    import html as html_mod
    if not html:
        return None
    links = extract_links_from_html(html, host_contains=host_contains)
    # Prioritize links with verify/confirm/activate keywords
    keywords = ("confirm", "verify", "activate", "click", "signup", "register", "enable")
    candidates = []
    for l in links:
        l_dec = html_mod.unescape(l)
        if any(kw in l_dec.lower() for kw in keywords):
            candidates.append(l_dec)
    if candidates:
        return candidates[0]
    # Fallback: any link to the same host
    if links:
        return html_mod.unescape(links[0])
    return None
