"""Repost + ghost-job detection — Sprint 3 port of T2's detect-reposts.mjs,
role-matcher.mjs and the methodology §708 ghost heuristic.

Provenance: career-ops-research (T2):
- role-matcher.mjs — the shared fuzzy title matcher (stopword/baseline-token
  vocabulary, seniority-agreement rules, subset-specialization rule, true
  set-Jaccard >= 0.6).
- detect-reposts.mjs — company-grouped, title-fuzzy-matched repost clusters
  inside a sliding 90-day window (#2383's inverted-index shape preserved:
  exact-title buckets collapse O(N); fuzzy runs only on DISTINCT buckets,
  gated by necessary conditions so no verdict changes).

Adaptations for this package:
- Rows are Job-shaped objects (title/company/link/date_posted/source) —
  the TSV columns of T2 map onto the Job model; the `status == 'added'`
  filter maps to "has link + parseable date + company + title" (every
  stored job was accepted by dedup, so all rows are 'added'-equivalent).
- companyKey reuses dedup.normalize_company_name (same suffix-stripping
  job as T2's normalizeCompanyName).
- ghost_candidates() implements job_sourcing_methodology.md §708: a
  (company, title) cluster seen in 2+ aggregator sources but never in the
  company's own ATS over the window, with the oldest posting > 30 days old
  → ghost_candidate (exclude from primary index; keep for re-validation).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Protocol

from .dedup import normalize_company_name

__all__ = [
    "role_tokens", "role_fuzzy_match", "detect_reposts",
    "ghost_candidates", "ATS_DIRECT_SOURCES",
]


# ── Title vocabulary (role-matcher.mjs port) ─────────────────────────────────

SENIORITY_TOKENS = frozenset({
    "junior", "mid", "middle", "senior", "staff", "principal", "lead", "head",
    "chief", "associate", "intern", "entry",
})

# Seniority tokens that place a requisition BELOW the bare baseline title.
# "Associate X" and a bare "X" at one company are two real openings, so a
# lone sub-baseline qualifier is a level disagreement (#2009).
SUB_BASELINE_SENIORITY = frozenset({"associate", "junior", "entry", "intern"})

ROLE_STOPWORDS = frozenset({
    # seniority / level
    "junior", "mid", "middle", "senior", "staff", "principal", "lead", "head",
    "chief", "associate", "intern", "entry", "level",
    # contract / mode
    "remote", "hybrid", "onsite", "contract", "contractor", "freelance",
    "fulltime", "parttime", "permanent", "temporary", "intern", "internship",
    # generic job words
    "role", "position", "opportunity", "team", "based",
    # reposting/tracking annotations — meta noise
    "repost", "reposted", "relisted",
    # very common locations
    "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai",
    "london", "berlin", "paris", "madrid", "barcelona", "amsterdam", "dublin",
    "york", "francisco", "seattle", "boston", "austin", "chicago", "toronto",
    "tokyo", "singapore", "sydney", "melbourne", "lisbon", "warsaw",
    # regions / countries
    "europe", "emea", "apac", "latam", "americas", "india", "spain", "germany",
    "france", "italy", "canada", "brazil", "mexico", "japan",
    # prepositions leaking through the length filter
    "with", "from", "into", "over", "this", "that",
})

# Short specialty acronyms that are discriminating despite their length.
SHORT_SPECIALTY = frozenset({
    "api", "sre", "sdk", "cli", "gpu", "cpu",
    "ios", "qa", "ux", "ui", "ar", "vr", "ocr", "crm", "erp",
})

# Generic role-level descriptors: two titles whose ONLY overlap is in this set
# are merely written at the same role altitude, not the same opening.
BASELINE_TOKENS = frozenset({
    "software", "engineer", "developer", "manager", "architect",
    "analyst", "designer", "consultant", "specialist",
    "platform", "systems", "services",
    "backend", "frontend", "full", "stack", "fullstack",
    # 'product' alone cannot identify an opening (PM - Marketplace vs PM - AI).
    "product",
})

# "Member of Technical Staff" — boilerplate level-prefix, not content signal.
_MTS_PREFIX = re.compile(r"\bmember\s+of\s+technical\s+staff\b", re.IGNORECASE)

_SLASH_ACRONYM = re.compile(r"\b([a-z0-9]{1,3})/([a-z0-9]{1,3})\b")
_NON_WORD = re.compile(r"[^\w\s]|_")


def _normalize_title(value) -> str:
    """Lowercase + fold accented Latin letters onto their ASCII base.

    Only marks sitting on an ASCII base are stripped (mirrors T2's
    `(?<=[a-z])\\p{Mn}` choice): marks that carry meaning in other scripts
    (Devanagari matras, Cyrillic breve, Japanese dakuten) survive.
    """
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = text.lower()
    nfd = unicodedata.normalize("NFD", text)
    out: list[str] = []
    prev_ascii = False
    for ch in nfd:
        if unicodedata.category(ch) == "Mn" and prev_ascii:
            continue
        out.append(ch)
        prev_ascii = "a" <= ch <= "z"
    return unicodedata.normalize("NFC", "".join(out))


def role_tokens(role) -> list[str]:
    """Content tokens for fuzzy title matching (long words + short specialty
    acronyms; stopwords dropped; baseline tokens KEPT — they feed the ratio
    but can never be the sole reason two titles match)."""
    text = _normalize_title(role)
    # MTS → generic 'engineer' padding token (a bare MTS title still matches
    # its exact repost; 'engineer' is baseline so it never decides alone).
    text = _MTS_PREFIX.sub(" engineer ", text)
    # Collapse slashed short acronyms BEFORE punctuation stripping so
    # "(CI/CD)" survives as one content token (#2165).
    text = _SLASH_ACRONYM.sub(r"\1\2", text)
    text = _NON_WORD.sub(" ", text)
    return [w for w in text.split()
            if (len(w) > 3 or w in SHORT_SPECIALTY) and w not in ROLE_STOPWORDS]


def _extract_seniorities(title) -> set[str]:
    words = _NON_WORD.sub(" ", _normalize_title(title)).split()
    return {w for w in words if w in SENIORITY_TOKENS}


def role_fuzzy_match(a, b) -> bool:
    """Whether two role titles are likely the same opening (T2 semantics).

    Requires: identical text (fast path), seniority agreement when both sides
    state one (or no lone sub-baseline qualifier), 2+ shared tokens, at least
    one NON-baseline shared token, no non-baseline specialization on a strict
    superset side, and a true set-Jaccard overlap >= 0.6.
    """
    text_a = str(a or "").strip().lower()
    text_b = str(b or "").strip().lower()
    if text_a and text_a == text_b:
        return True

    sen_a = _extract_seniorities(a)
    sen_b = _extract_seniorities(b)
    if sen_a and sen_b:
        if not (sen_a & sen_b):
            return False
    elif sen_a or sen_b:
        lone = sen_a or sen_b
        if lone & SUB_BASELINE_SENIORITY:
            return False

    words_a = list(dict.fromkeys(role_tokens(a)))
    words_b = list(dict.fromkeys(role_tokens(b)))
    if not words_a or not words_b:
        return False

    set_b = set(words_b)
    overlap = [w for w in words_a if w in set_b]
    if len(overlap) < 2:
        return False
    if not any(w not in BASELINE_TOKENS for w in overlap):
        return False

    # Strict-subset specialization: "Senior Analytics Engineer" vs
    # "...Engineer, People Analytics" — the extra non-baseline word is the
    # signal these are separately-postable openings.
    smaller, larger = ((words_a, words_b) if len(words_a) <= len(words_b)
                       else (words_b, words_a))
    if len(larger) > len(smaller) and len(overlap) == len(smaller):
        smaller_set = set(smaller)
        extras = [w for w in larger if w not in smaller_set]
        if any(w not in BASELINE_TOKENS for w in extras):
            return False

    union = set(words_a) | set(words_b)
    return len(overlap) / len(union) >= 0.6


# ── Repost clusters (detect-reposts.mjs port, Job-shaped rows) ───────────────

class _Row(Protocol):
    title: str
    company: str
    link: str
    date_posted: Optional[str]


@dataclass
class RepostCluster:
    company: str
    role: str
    repost_count: int
    first_seen: str
    last_seen: str
    days_span: int
    appearances: list[dict] = field(default_factory=list)  # {url,date,title,source}

    def to_dict(self) -> dict:
        return {
            "company": self.company, "role": self.role,
            "repost_count": self.repost_count,
            "first_seen": self.first_seen, "last_seen": self.last_seen,
            "days_span": self.days_span, "appearances": self.appearances,
        }


def _parse_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _company_key(row) -> str:
    raw = (getattr(row, "company", "") or "").strip()
    return normalize_company_name(raw) or raw.lower()


def _row_date(row) -> Optional[date]:
    return _parse_date(getattr(row, "date_posted", None))


def _valid_rows(rows: Iterable) -> list:
    valid = []
    for r in rows:
        if r is None:
            continue
        url = (getattr(r, "link", "") or "").strip()
        title = (getattr(r, "title", "") or "").strip()
        company = (getattr(r, "company", "") or "").strip()
        if url and title and company and _row_date(r) is not None:
            valid.append(r)
    return valid


def detect_reposts(rows: Iterable, window_days: int = 90) -> list[RepostCluster]:
    """Repost clusters across Job-shaped rows (default 90-day window).

    A cluster = 2+ rows, same normalized company, fuzzy-matching titles, 2+
    DISTINCT links, all first_seen dates within window_days of each other.
    Same-URL rows collapse onto their earliest sighting (a dedup hit is not
    a repost).
    """
    valid = _valid_rows(rows)
    if len(valid) < 2:
        return []

    by_company: dict[str, list] = {}
    for row in valid:
        by_company.setdefault(_company_key(row), []).append(row)

    clusters: list[RepostCluster] = []
    for group_rows in by_company.values():
        if len(group_rows) < 2:
            continue
        clusters.extend(_detect_reposts_in_group(group_rows, window_days))
    clusters.sort(key=lambda c: c.last_seen, reverse=True)
    return clusters


def _detect_reposts_in_group(rows: list, window_days: int) -> list[RepostCluster]:
    results: list[RepostCluster] = []
    for group in _group_rows_by_title(rows):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=_row_date)  # stable: ties keep input order
        cluster: list = []
        for row in ordered:
            if not cluster:
                cluster = [row]
                continue
            span = (_row_date(row) - _row_date(cluster[0])).days
            if span <= window_days:
                cluster.append(row)
            else:
                # Seal, then slide the window so overlapping repost pairs
                # (Jan1+Mar15 sealed; Mar15+Jun10 also valid) survive.
                if len(cluster) >= 2:
                    c = _build_repost_cluster(cluster, window_days)
                    if c:
                        results.append(c)
                cluster = [c for c in cluster
                           if (_row_date(row) - _row_date(c)).days <= window_days]
                cluster.append(row)
        if len(cluster) >= 2:
            c = _build_repost_cluster(cluster, window_days)
            if c:
                results.append(c)
    return results


def _group_rows_by_title(rows: list) -> list[list]:
    """Bucket rows by exact lowercased title, then fuzzy-merge DISTINCT
    buckets (seed in first-appearance order). Preserves T2's #2383 shape:
    exact reposts collapse with zero fuzzy calls; fuzzy pairs are gated by
    necessary conditions (2+ shared tokens, Jaccard >= 0.6) so the gate
    filters calls, never verdicts."""

    class _Bucket:
        __slots__ = ("title", "row_idx", "tokens", "token_set")
        def __init__(self, title: str):
            self.title = title
            self.row_idx: list[int] = []
            self.tokens: list[str] = []
            self.token_set: set[str] = set()

    buckets: list[_Bucket] = []
    bucket_of_key: dict[str, int] = {}
    for i, row in enumerate(rows):
        key = _normalize_title(getattr(row, "title", ""))
        idx = bucket_of_key.get(key)
        if idx is None:
            idx = len(buckets)
            bucket_of_key[key] = idx
            buckets.append(_Bucket(getattr(row, "title", "")))
        buckets[idx].row_idx.append(i)

    if len(buckets) == 1:
        return [[rows[i] for i in buckets[0].row_idx]]

    postings: dict[str, list[int]] = {}
    for b, bucket in enumerate(buckets):
        bucket.tokens = list(dict.fromkeys(role_tokens(bucket.title)))
        bucket.token_set = set(bucket.tokens)
        for token in bucket.tokens:
            if token in BASELINE_TOKENS:
                continue  # 'engineer' appears everywhere — never justifies a match
            postings.setdefault(token, []).append(b)

    used = [False] * len(buckets)
    groups: list[list] = []
    for seed in range(len(buckets)):
        if used[seed]:
            continue
        used[seed] = True
        members = [seed]
        seed_tokens = buckets[seed].tokens

        candidates: list[int] = []
        seen = set()
        for token in seed_tokens:
            for b in postings.get(token, ()):
                if b != seed and not used[b] and b not in seen:
                    seen.add(b)
                    candidates.append(b)
        for b in sorted(candidates):
            cand_set = buckets[b].token_set
            overlap = sum(1 for t in seed_tokens if t in cand_set)
            if overlap < 2:
                continue
            union = len(seed_tokens) + len(buckets[b].tokens) - overlap
            if overlap / union < 0.6:
                continue
            if role_fuzzy_match(buckets[seed].title, buckets[b].title):
                used[b] = True
                members.append(b)

        merged = sorted(i for b in members for i in buckets[b].row_idx)
        groups.append([rows[i] for i in merged])
    return groups


def _build_repost_cluster(cluster_rows: list, window_days: int
                          ) -> Optional[RepostCluster]:
    by_url: dict[str, object] = {}
    for row in cluster_rows:
        url = (getattr(row, "link", "") or "").strip()
        if url not in by_url or _row_date(row) < _row_date(by_url[url]):
            by_url[url] = row
    deduped = sorted(by_url.values(), key=_row_date)
    if len(deduped) < 2:
        return None
    first, last = deduped[0], deduped[-1]
    span = (_row_date(last) - _row_date(first)).days
    if span > window_days:
        return None
    return RepostCluster(
        company=(getattr(cluster_rows[0], "company", "") or "").strip(),
        role=(getattr(last, "title", "") or "").strip(),
        repost_count=len(deduped),
        first_seen=str(getattr(first, "date_posted", "") or ""),
        last_seen=str(getattr(last, "date_posted", "") or ""),
        days_span=span,
        appearances=[
            {"url": getattr(r, "link", ""), "date": str(getattr(r, "date_posted", "") or ""),
             "title": getattr(r, "title", ""), "source": getattr(r, "source", "")}
            for r in deduped
        ],
    )


# ── Ghost-job heuristic (methodology §708) ───────────────────────────────────

# Sources that serve a company's OWN board (Layer-1 ATS-direct). Everything
# else is an aggregator/marketplace (Layer 2/3). The ghost heuristic needs
# this distinction: "appears in 2+ aggregators but NEVER on the company's
# own ATS" is the ghost signal.
# NOTE: ATS-direct adapters tag jobs with DOTTED sources ("Greenhouse.stripe",
# "Ashby.openai", ...) — match by the head segment, not exact equality
# (audit P1-1: exact-match made has_own_ats unreachable except for Workable,
# falsely ghost-flagging exactly the jobs that are LIVE on the employer's
# own board and suppressing their alerts).
ATS_DIRECT_SOURCES = frozenset({
    "Greenhouse", "Lever", "SmartRecruiters", "Ashby", "Personio", "Workable",
})


def _is_ats_direct_source(source: str, ats_sources: frozenset) -> bool:
    """True if `source` is (or is prefixed by) an ATS-direct source name.
    Handles both plain ("Workable") and dotted ("Greenhouse.stripe") forms,
    case-insensitively; merged-source joins ("A + B") count as ATS-direct
    only if their first member is."""
    key = (source or "").split(" + ")[0].strip().lower()
    head = key.split(".")[0]
    return head in {s.lower() for s in ats_sources}


@dataclass
class GhostCandidate:
    company: str
    title: str
    sources: list[str]
    first_seen: str
    days_old: int
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "company": self.company, "title": self.title,
            "sources": self.sources, "first_seen": self.first_seen,
            "days_old": self.days_old, "links": self.links,
        }


def ghost_candidates(rows: Iterable, *, days: int = 30,
                     today: Optional[date] = None,
                     min_aggregators: int = 2,
                     ats_sources: frozenset = ATS_DIRECT_SOURCES
                     ) -> list[GhostCandidate]:
    """§708: (company, title) seen in 2+ aggregator sources but never in the
    company's own ATS over the window, AND the oldest posting is > `days`
    days old → ghost candidate. Flag, never drop: callers keep them in cold
    storage for re-validation."""
    today = today or date.today()
    valid = _valid_rows(rows)

    by_company: dict[str, list] = {}
    for row in valid:
        by_company.setdefault(_company_key(row), []).append(row)

    ghosts: list[GhostCandidate] = []
    for group_rows in by_company.values():
        for group in _group_rows_by_title(group_rows):
            sources = {(getattr(r, "source", "") or "").strip() for r in group}
            agg_sources = sorted(s for s in sources
                                 if s and not _is_ats_direct_source(s, ats_sources))
            has_own_ats = any(_is_ats_direct_source(s, ats_sources)
                              for s in sources if s)
            if len(agg_sources) < min_aggregators or has_own_ats:
                continue
            oldest = min(_row_date(r) for r in group)
            days_old = (today - oldest).days
            if days_old <= days:
                continue
            first = min(group, key=_row_date)
            ghosts.append(GhostCandidate(
                company=(getattr(first, "company", "") or "").strip(),
                title=(getattr(first, "title", "") or "").strip(),
                sources=agg_sources,
                first_seen=str(getattr(first, "date_posted", "") or ""),
                days_old=days_old,
                links=sorted({(getattr(r, "link", "") or "").strip()
                              for r in group}),
            ))
    ghosts.sort(key=lambda g: (g.company, g.title))
    return ghosts
