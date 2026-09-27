"""Source base — the fetch contract every board client implements.

Adapted from ResumeWing's aggregator pattern (parallel fetch, failure
isolation) with the D3 quota/degradation policy: a failing source NEVER
crashes the pipeline; it reports a SourceResult with error set.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from ..config import Config
from ..models import Job

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_H1B_KEYWORDS = [
    "h1b", "h-1b", "h1-b", "visa sponsor", "will sponsor",
    "sponsorship available", "sponsorship provided", "sponsor work auth",
    "open to sponsorship", "work authorization sponsor", "work visa sponsor",
    "immigration sponsor", "h1 visa",
]

# Negation phrases that flip an H1B-positive keyword match to a negative.
# Order matters: these are checked BEFORE the positive keyword list, so a
# job description saying "no sponsorship available" returns False instead
# of the (wrong) True the bare substring scan would yield.
#
# Real-world phrasings we have observed on US job postings that explicitly
# say they don't sponsor:
#   - "We do not provide sponsorship"
#   - "No sponsorship available"
#   - "Not open to sponsorship"
#   - "Cannot sponsor visas at this time"
#   - "Will not sponsor"
#   - "Without sponsorship"
_H1B_NEGATIONS = (
    # "no" / "not" patterns
    "no sponsor", "not sponsor", "don't sponsor", "doesn't sponsor",
    "do not sponsor", "cannot sponsor", "can't sponsor",
    "no visa sponsor", "not eligible for sponsorship",
    "no h1b", "no h-1b", "not offering sponsorship",
    "will not sponsor", "without sponsorship",
    "not able to sponsor", "unable to sponsor",
    "does not offer sponsorship", "doesn't offer sponsorship",
    # Phrasings where "sponsor/sponsorship" appears but the verb is "provide"/"offer"/"transfer"
    # and a negation precedes it.
    "do not provide sponsor", "doesn't provide sponsor",
    "do not provide visa", "doesn't provide visa",
    "do not offer sponsor", "doesn't offer sponsor",
    "do not offer visa", "doesn't offer visa",
    "not open to sponsor",
    "not authorized to sponsor",
    "no work visa", "not provide work visa",
    "not sponsoring", "not sponsoring visas",
)


def detect_h1b(text: str) -> bool:
    """Return True iff the text indicates the employer sponsors H1B visas.

    Lifted from ResumeWing utils/job_helpers.py with one critical fix: a
    negation guard runs BEFORE the positive keyword scan, so descriptions
    that say "no sponsorship available" or "not open to sponsorship" no
    longer false-positive as H1B-sponsoring.
    """
    if not text:
        return False
    lowered = text.lower()
    # Negation guard: any explicit "we don't sponsor" phrasing flips the
    # verdict to False, even if positive keywords appear in the same text.
    if any(neg in lowered for neg in _H1B_NEGATIONS):
        return False
    return any(kw in lowered for kw in _H1B_KEYWORDS)


def extract_email(text: str) -> Optional[str]:
    # lifted from ResumeWing utils/job_helpers.py (exp 01 noise filter)
    matches = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    noise = ("noreply", "no-reply", "example", "test@", "donotreply", "notifications@")
    filtered = [m for m in matches if not any(n in m.lower() for n in noise)]
    return filtered[0] if filtered else None


def clean_html(value: str) -> str:
    # lifted from hendrixfreire linkedin.py. S9-audit F3 (P2, the CR-1
    # entity-decoding doctrine): strip tags FIRST, then decode HTML
    # entities — raw "&amp;"/"&#39;"/"&nbsp;" sequences were flowing into
    # Job.description (and from there into TF-IDF/LLM prompts and the
    # predicate surfaces) from the call sites that don't pre-unescape.
    # Order matters: unescaping BEFORE the strip would resurrect real
    # tags from "&lt;script&gt;".
    return unescape(re.sub(r"<[^>]+>", "", value)).strip()


# ── date / salary helpers ──────────────────────────────────────────────────
# Lifted from resume-wing utils/job_helpers.py (same provenance as
# detect_h1b/extract_email above). The free-key ports (adzuna, jsearch,
# usajobs, findwork, jooble, careerjet) all leaned on these three; hosting
# them here keeps each source a thin adapter and avoids 6 local copies.

def normalize_date(value) -> Optional[str]:
    """Coerce ISO 8601 / Unix epoch / RFC 2822 / bare YYYY-MM-DD to YYYY-MM-DD.

    Returns None if the input is empty or unparseable. Mirrors resume-wing's
    utils.job_helpers.normalize_date (which the free-key clients imported).
    """
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
        s = str(value).strip()
        # Already YYYY-MM-DD — return directly.
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s
        # ISO 8601 variants with a time component.
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            pass
        # RFC 2822: "Mon, 15 Jan 2024 12:00:00 +0000" (careerjet returns these)
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(s).strftime("%Y-%m-%d")
        except Exception:
            pass
        # Fallback: grab a leading YYYY-MM-DD if present.
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]
    except Exception:
        pass
    return None


def salary_text(min_val, max_val, currency: str = "USD") -> Optional[str]:
    """Format a numeric salary range into a human-readable string.

    Returns None if both bounds are absent/zero. Lifted from resume-wing
    utils.job_helpers.salary_text.
    """
    if not min_val and not max_val:
        return None
    symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency, f"{currency} ")
    try:
        lo = int(float(min_val)) if min_val else None
        hi = int(float(max_val)) if max_val else None
        if lo and hi:
            return f"{symbol}{lo:,} – {symbol}{hi:,}/yr"
        if lo:
            return f"{symbol}{lo:,}+/yr"
        if hi:
            return f"Up to {symbol}{hi:,}/yr"
    except (TypeError, ValueError):
        pass
    return None


def is_within_days(date_str: Optional[str], days: int) -> bool:
    """True if date_str (YYYY-MM-DD) is within the last N days.

    Unknown/empty dates return True so they're never wrongly excluded (same
    policy as resume-wing utils.job_helpers.is_within_days).
    """
    if not date_str or not days:
        return True
    try:
        from datetime import datetime, timedelta, timezone
        posted = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        return posted >= cutoff
    except (ValueError, TypeError):
        return True


def fetch_json(url: str, *, params: dict | None = None, cfg: Config,
               headers: dict | None = None, method: str = "GET",
               json: dict | None = None, auth: tuple | None = None
               ) -> dict | list:
    """HTTP call every board client goes through — mockable in tests.

    Extended beyond the original GET-only contract so the free-key ports
    (Jooble POST+JSON-body, Careerjet HTTP-Basic-auth) stay on the same
    mockable seam instead of calling requests directly. Defaults keep the
    GET behaviour the existing 6 sources rely on unchanged.
    """
    r = requests.request(method, url, params=params, json=json, auth=auth,
                         timeout=cfg.http_timeout_s,
                         headers=headers or {"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()


def fetch_text(url: str, *, params: dict | None = None, cfg: Config,
               headers: dict | None = None) -> str:
    r = requests.get(url, params=params, timeout=cfg.http_timeout_s,
                     headers=headers or {"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.text


@dataclass
class Source:
    """One board client: name + fetch function.

    `is_configured_fn` is a callable taking a Config and returning bool —
    evaluated at SEARCH time, not import time, so env mutations after
    import (e.g. monkeypatching in tests, or a runtime key reload) are
    reflected. The legacy `configured: bool` field is preserved for
    backward compatibility (deprecated; defaults to True).
    """
    name: str
    fetch: Callable[..., list[Job]]
    remote_only: bool = False          # skip for city/state searches
    configured: bool = True            # legacy — frozen-at-import snapshot; prefer is_configured_fn
    is_configured_fn: Optional[Callable[[Config], bool]] = None  # dynamic check at search time
    hint: str = ""                     # how to configure, when not configured

    def is_configured(self, cfg: Config) -> bool:
        """True iff this source is configured for `cfg` right now.

        Prefers the dynamic `is_configured_fn` (evaluated fresh per call)
        so env mutations after import propagate. Falls back to the legacy
        frozen-at-import `configured` flag for sources that don't supply
        a callable (e.g. the no-key tier where it's always True).
        """
        if self.is_configured_fn is not None:
            return self.is_configured_fn(cfg)
        return self.configured
