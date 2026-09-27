"""Per-job and per-source trust scoring (ported from T2 career-ops-research
`providers/_trust-validator.mjs`, Wave 11 — Step D 2026-08-27).

Per-JOB validator: enriches each job with trust_score (0-100), trust_flags
(list[str]) and trust_level ('high' | 'medium' | 'low'). Never drops jobs —
flag only. Four rules (methodology §6.2):

    1. invalid_url            −50   (malformed or non-http(s) link)
    2. missing_apply_url      −40   (no link at all)
    3. suspicious_domain      −25   (URL shorteners / throwaway hosts)
    4. company_domain_mismatch −15  (company name doesn't match the link
                                     host; ATS-hosted links exempted)

Levels: >=90 high · >=60 medium · else low.

Per-SOURCE trust score: rolling completeness / latency / stability signals
recorded by the aggregator into `source_stats` (see storage.py v2) plus a
static ToS-clarity map — methodology §6.2's four dimensions. Computed as a
weighted 0-100 score.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional
from urllib.parse import urlparse

DEFAULT_SUSPICIOUS_DOMAINS = [
    "bit.ly", "tinyurl.com", "t.co", "forms.gle", "goo.gl",
    "shorturl.at", "rebrand.ly", "cutt.ly",
]

DEFAULT_ATS_ALLOWLIST = [
    "greenhouse.io", "ashbyhq.com", "lever.co", "workday.com",
    "smartrecruiters.com", "jobvite.com", "myworkdayjobs.com",
    "recruitee.com", "workable.com", "icims.com", "taleo.net",
    "applytojob.com", "breezy.hr", "jazz.co", "bamboohr.com",
    "teamtailor.com",
    # added during the port: boards our own adapters emit links into
    "jobs.ashbyhq.com", "jobs.workable.com", "jobs.personio.de",
    "jobviewtrack.com",            # Careerjet redirect host
    "welcometothejungle.com",      # WTTJ
    "ziprecruiter.com", "glassdoor.com",
    "usajobs.gov", "remotive.com", "arbeitnow.com", "remoteok.com",
    "jobicy.com", "themuse.com", "adzuna.com", "findwork.dev",
    "jooble.org", "careerjet.com", "linkedin.com", "news.ycombinator.com",
]

PENALTIES = {
    "invalid_url": 50,
    "missing_apply_url": 40,
    "suspicious_domain": 25,
    "company_domain_mismatch": 15,
}


def classify_trust_level(score: int) -> str:
    if score >= 90:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def validate_url(url: str) -> tuple[bool, Optional[str]]:
    """(valid, flag) — well-formed http(s) URL check."""
    if not url or not url.strip():
        return False, "invalid_url"
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False, "invalid_url"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False, "invalid_url"
    return True, None


def matches_domain_list(hostname: str, domain_list: list[str]) -> bool:
    """True if hostname equals or is a subdomain of any list entry."""
    hostname = (hostname or "").lower()
    for domain in domain_list:
        domain = domain.lower().strip()
        if not domain:
            continue
        if hostname == domain or hostname.endswith("." + domain):
            return True
    return False


# Latin letters that do NOT decompose under NFD (stroke/bar letters and the
# Turkish dotless ı) — folded by explicit map so the [^a-z0-9 ] strip below
# can't delete them (T2's CodeRabbit-reviewed NON_DECOMPOSING_LATIN table).
_NON_DECOMPOSING_LATIN = str.maketrans({
    "ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "đ": "d", "ł": "l",
    "þ": "th", "ð": "d", "ħ": "h", "ı": "i", "ŋ": "ng", "ŧ": "t",
    "ĸ": "k", "ſ": "s",
})


def ascii_fold_for_hostname(company: str) -> str:
    """Lowercase, NFD-folded, ASCII-only company name for host comparison.

    'Société Générale' → 'societe generale'. Returns '' when nothing Latin
    survives (CJK/Cyrillic/etc. — those can never appear in an ASCII
    hostname, so absence of a match proves nothing; flagging them would be
    a systematic false positive — T2 #2924).
    """
    out = unicodedata.normalize("NFD", str(company or "").lower())
    out = "".join(ch for ch in out if not unicodedata.combining(ch))
    out = out.translate(_NON_DECOMPOSING_LATIN)
    out = re.sub(r"[^a-z0-9 ]", "", out)
    return re.sub(r"\s+", " ", out).strip()


def company_matches_hostname(company: str, hostname: str) -> bool:
    """Heuristic: does the company name plausibly match the link host?

    Full slug check first, then any word >=3 chars as substring.
    True when no comparison is possible (never flag on missing data).
    """
    if not company or not hostname:
        return True
    normalized = ascii_fold_for_hostname(company)
    if not normalized:
        return True
    hostname = hostname.lower()
    slug = normalized.replace(" ", "")
    if slug and slug in hostname:
        return True
    for word in normalized.split(" "):
        if len(word) >= 3 and word in hostname:
            return True
    return False


def score_job(url: str, company: str,
              suspicious_domains: Optional[list[str]] = None,
              ats_allowlist: Optional[list[str]] = None) -> dict:
    """Score one job posting. Returns {score, flags, level}."""
    suspicious = suspicious_domains or DEFAULT_SUSPICIOUS_DOMAINS
    ats = ats_allowlist or DEFAULT_ATS_ALLOWLIST

    flags: list[str] = []
    score = 100
    url = (url or "").strip()
    company = (company or "").strip()

    # Rule 1 — missing apply URL (can't run further URL checks)
    if not url:
        flags.append("missing_apply_url")
        score -= PENALTIES["missing_apply_url"]
        score = max(0, score)
        return {"score": score, "flags": flags,
                "level": classify_trust_level(score)}

    # Rule 2 — URL structure
    valid, flag = validate_url(url)
    if not valid:
        flags.append("invalid_url")
        score -= PENALTIES["invalid_url"]
        score = max(0, score)
        return {"score": score, "flags": flags,
                "level": classify_trust_level(score)}

    hostname = (urlparse(url).hostname or "").lower()

    # Rule 3 — suspicious domain
    if matches_domain_list(hostname, suspicious):
        flags.append("suspicious_domain")
        score -= PENALTIES["suspicious_domain"]

    # Rule 4 — company ↔ domain mismatch (ATS-hosted links exempt)
    if company and not matches_domain_list(hostname, ats):
        if not company_matches_hostname(company, hostname):
            flags.append("company_domain_mismatch")
            score -= PENALTIES["company_domain_mismatch"]

    score = max(0, min(100, score))
    return {"score": score, "flags": flags,
            "level": classify_trust_level(score)}


# ── Per-SOURCE trust (methodology §6.2 four dimensions) ─────────────────────

# Static ToS-clarity verdicts per source (1.0 = explicitly scrapable public
# API; 0.5 = no explicit grant but industry-standard practice; 0.0 = ToS
# explicitly hostile). Documented in methodology §6.2/§7.2.
_TOS_CLARITY = {
    "Remotive": 1.0, "Arbeitnow": 1.0, "RemoteOK": 1.0, "Jobicy": 1.0,
    "The Muse": 1.0, "HN Who's Hiring": 1.0, "USAJobs": 1.0,
    "Adzuna": 1.0, "Findwork": 1.0, "Jooble": 1.0, "Careerjet": 1.0,
    "JSearch": 1.0, "Greenhouse": 1.0, "Lever": 1.0, "Ashby": 1.0,
    "SmartRecruiters": 1.0, "Workable": 1.0, "WTTJ": 0.5,
    "Personio": 1.0, "Wellfound": 0.5, "LinkedIn Guest": 0.5,
    "ZipRecruiter": 0.5, "Glassdoor": 0.5, "JobSpy": 0.5,
}


def source_trust_score(source: str, stats: dict) -> int:
    """Per-source trust from rolling stats.

    stats keys (all optional):
      attempts, successes       → stability  (success ratio)
      avg_latency_ms            → latency    (<=2s great, >=10s poor)
      avg_field_completeness    → completeness (fraction of core fields set)
    Plus the static ToS-clarity map. Weights per methodology §6.2:
    completeness 30% · latency 25% · stability 25% · tos 20%.
    """
    completeness_w, latency_w, stability_w, tos_w = 0.30, 0.25, 0.25, 0.20

    # Completeness: fraction of jobs with the core fields populated.
    completeness = stats.get("avg_field_completeness")
    if completeness is None:
        completeness = 0.5                       # no data → neutral
    completeness = max(0.0, min(1.0, completeness))

    # Latency: 1.0 at <=2s, linear decay to 0 at >=10s.
    latency = stats.get("avg_latency_ms")
    if latency is None:
        latency_s = 0.5
    else:
        latency_s = max(0.0, min(1.0, 1.0 - (latency / 1000.0 - 2.0) / 8.0))

    # Stability: success ratio over recent attempts.
    attempts = stats.get("attempts") or 0
    successes = stats.get("successes") or 0
    if attempts <= 0:
        stability = 0.5
    else:
        stability = max(0.0, min(1.0, successes / attempts))

    tos = _TOS_CLARITY.get(source, 0.5)

    raw = (completeness_w * completeness + latency_w * latency_s
           + stability_w * stability + tos_w * tos)
    return round(raw * 100)


def field_completeness(jobs: list) -> float:
    """Fraction of core fields populated across a batch of Jobs.

    Core fields per the methodology: title, company, link, location,
    date_posted, salary (text or min/max), description.
    """
    if not jobs:
        return 0.0
    fields = 7
    total = 0
    for j in jobs:
        filled = sum([
            1 if (j.title or "").strip() else 0,
            1 if (j.company or "").strip() else 0,
            1 if (j.link or "").strip() else 0,
            1 if (j.location or "").strip() else 0,
            1 if (j.date_posted or "").strip() else 0,
            1 if ((j.salary_text or "").strip()
                  or j.salary_min is not None or j.salary_max is not None) else 0,
            1 if (j.description or "").strip() else 0,
        ])
        total += filled / fields
    return total / len(jobs)
