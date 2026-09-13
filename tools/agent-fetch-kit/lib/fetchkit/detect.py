"""Challenge / WAF detection — used by the escalation router to decide when to escalate."""
from __future__ import annotations
import re

# Body markers that indicate a WAF/bot-challenge response (lowercase substrings).
# Tightened to avoid false-positives on legitimate content (e.g. a blog post about "cloudflare" or a book review of "The Forbidden").
# Generic markers like "forbidden"/"captcha"/"cloudflare" are paired with WAF-specific substrings.
CHALLENGE_MARKERS = [
    # Cloudflare (specific)
    "just a moment", "cf-chl", "cf-challenge", "cf-ray-", "cf-mitigated",
    "checking your browser", "attention required", "performing security verification",
    "challenges.cloudflare.com",
    # DataDome (specific)
    "dd-function-name", "datadome", "dd-captcha",
    # Akamai (specific)
    "akamai-bm-telemetry", "bm-verification", "_abck=",
    # PerimeterX / HUMAN (specific)
    "px-captcha", "pxjstoken", "perimeterx", "human challenge",
    # Incapsula / Imperva (specific)
    "incap_ses", "visid_incap", "incapsula",
    # Generic challenge signals (kept specific enough to avoid false positives)
    "request unsuccessful", "incapsula incident", "bot detection",
    "please verify you are human", "verify you are human",
    "unusual traffic from your network", "automated queries",
]

# Status codes that usually indicate a block/challenge
CHALLENGE_STATUS = {403, 429, 503, 504}

_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)

def _extract_title(body: bytes) -> str | None:
    if not body: return None
    # scan first 64KB (title can be deep in <head> after large inline <style>/<script>)
    m = _TITLE_RE.search(body[:65536])
    return m.group(1).decode("utf-8", "ignore").strip()[:200] if m else None

def is_challenged(status, body: bytes | str | None) -> tuple[bool, str | None]:
    """Return (challenged, reason). Reason is a short string for logging."""
    # coerce non-int status to None (some backends may return string/0 on error)
    if not isinstance(status, int):
        status = None
    if status in CHALLENGE_STATUS:
        return True, f"status {status}"
    if body is None:
        return False, None
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8", "ignore").lower()
        except Exception:
            text = ""
    else:
        text = body.lower()
    for marker in CHALLENGE_MARKERS:
        if marker in text:
            return True, f"marker: {marker}"
    # also check <title> for specific challenge-page titles
    title = _extract_title(body if isinstance(body, bytes) else (body or "").encode("utf-8", "ignore"))
    if title:
        tl = title.lower()
        for marker in ("just a moment", "attention required", "are you a robot", "verify you are human"):
            if marker in tl:
                return True, f"title: {title[:60]}"
    return False, None
