"""ImprovMX email polling helper.

priv.email is set up with a catch-all → ansgareutychis@hotmail.com. We use
distinct aliases per service (adzuna@priv.email, findwork@priv.email, ...)
so we can poll the unified ImprovMX log API and filter by recipient.

API: GET https://api.improvmx.com/v3/domains/priv.email/logs?take=N
Auth: HTTP Basic, username "api", password = the API key.

We only get metadata (subject + sender + recipient + delivery events) — NOT
the email body. Most verification codes are in the subject; if a code is
only in the body, we surface the messageId so the operator can check
Hotmail manually (per the SKILL doc).
"""
from __future__ import annotations

import datetime as dt
import re
import time
import urllib.request
import urllib.error
import json
import base64
from typing import Optional

IMPROVMX_KEY = "sk_691ff26633c94b0d80523433afe3a369"
DOMAIN = "priv.email"
API_BASE = "https://api.improvmx.com/v3"


def _auth_header() -> str:
    token = base64.b64encode(f"api:{IMPROVMX_KEY}".encode()).decode()
    return f"Basic {token}"


def list_logs(take: int = 50) -> list[dict]:
    """Return raw log entries (newest first)."""
    url = f"{API_BASE}/domains/{DOMAIN}/logs?take={take}"
    req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data.get("logs", []) or []
    except urllib.error.HTTPError as e:
        # 429 = rate limit; surface clearly
        raise RuntimeError(f"ImprovMX HTTP {e.code}: {e.read()[:200]!r}") from None
    except Exception as e:
        raise RuntimeError(f"ImprovMX unreachable: {e}") from None


def _parse_ts(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)


def _extract_code(subject: str) -> Optional[str]:
    """Find a 4-8 digit standalone code in the subject.

    Adzuna, Findwork, USAJobs, RapidAPI, Jooble all put codes in the subject.
    Careerjet puts the API key directly in the welcome email body, so we
    surface the messageId for that one.
    """
    if not subject:
        return None
    # Strip email subjects of noise words first, then look for digit groups
    candidates = re.findall(r"\b(\d{4,8})\b", subject)
    if candidates:
        # Prefer longer (6-digit) codes over 4-digit
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    return None


def wait_for_email(
    recipient: str,
    sender_contains: str | None = None,
    subject_contains: str | None = None,
    timeout_s: int = 180,
    poll_interval_s: int = 10,
    since_ts_ms: int | None = None,
) -> dict | None:
    """Poll ImprovMX until an email matches the criteria or timeout.

    Args:
        recipient: e.g. "adzuna@priv.email"
        sender_contains: substring to filter sender (e.g. "adzuna")
        subject_contains: substring the subject must contain
        timeout_s: total seconds to wait
        poll_interval_s: seconds between polls (default 10s, per skill guidance)
        since_ts_ms: only consider emails newer than this epoch-ms

    Returns:
        The matching log entry, or None on timeout.
    """
    deadline = time.time() + timeout_s
    seen_ids: set[str] = set()
    while time.time() < deadline:
        try:
            logs = list_logs(take=50)
        except RuntimeError as e:
            # 429 backoff — sleep longer
            print(f"  [improvmx] {e}; backing off {poll_interval_s*2}s")
            time.sleep(poll_interval_s * 2)
            continue

        for log in logs:
            if log["id"] in seen_ids:
                continue
            seen_ids.add(log["id"])

            # Filter by recipient
            rcpt = log.get("recipient", {}).get("email", "").lower()
            if recipient.lower() not in rcpt and rcpt != recipient.lower():
                # Allow catch-all alias match: e.g. "adzuna@priv.email" should
                # match a recipient of exactly "adzuna@priv.email"
                continue

            # Filter by sender substring
            if sender_contains:
                sender = log.get("sender", {}).get("email", "").lower()
                if sender_contains.lower() not in sender:
                    continue

            # Filter by subject substring
            if subject_contains:
                subj = log.get("subject", "").lower()
                if subject_contains.lower() not in subj:
                    continue

            # Filter by timestamp
            if since_ts_ms and log.get("created", 0) < since_ts_ms:
                continue

            return log

        time.sleep(poll_interval_s)

    return None


def find_code_in_subject(subject: str) -> Optional[str]:
    """Public alias for the regex extraction."""
    return _extract_code(subject)


def now_ms() -> int:
    """Current epoch milliseconds — pass as since_ts_ms to avoid seeing
    old emails that match the same sender/subject."""
    return int(time.time() * 1000)


if __name__ == "__main__":
    # Quick smoke test — list the last 10 emails received at any priv.email alias
    import sys
    print(f"=== Last 10 emails at *@{DOMAIN} ===")
    for log in list_logs(take=10):
        ts = _parse_ts(log["created"]).strftime("%Y-%m-%d %H:%M:%SZ")
        print(f"[{ts}] to={log['recipient']['email']}")
        print(f"  from={log['sender']['email']}")
        print(f"  subject={log.get('subject', '')[:100]}")
        code = _extract_code(log.get("subject", ""))
        if code:
            print(f"  code={code}")
        print()
