"""Fuzzy dedup — Python port of job-ops job-matching.ts
(provenance: DaKheera47/job-ops shared/src/job-matching.ts).

Ported functions:
  - normalizeCompanyName (suffix stripping: ltd, inc, corp, ...)
  - normalizeJobTitle
  - calculateSimilarity (Levenshtein ratio 0-100 with containment shortcut)
  - deduplicateJobsByTitleAndEmployer (title > 90 AND employer > 85 → merge,
    first non-null wins per field)

The merge semantics matter: near-duplicates scraped from different boards are
MERGED into one enriched listing (one board's location + another's salary both
survive), not dropped.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Protocol, TypeVar

JobT = TypeVar("JobT")  # any dataclass with title/company attrs

COMPANY_SUFFIXES = [
    "limited", "ltd", "llp", "plc", "inc", "incorporated", "corporation",
    "corp", "company", "co", "llc", "uk", "international", "intl", "group",
    "holdings", "t/a", "trading as", "&", "the",
]

FUZZY_DEDUP_TITLE_THRESHOLD = 90
FUZZY_DEDUP_EMPLOYER_THRESHOLD = 85


def normalize_match_text(value: str) -> str:
    normalized = value.lower().strip()
    normalized = re.sub(r"[.,'\"()\[\]{}!?@#$%^&*+=|\\/<>:;`~_-]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_company_name(name: str) -> str:
    normalized = normalize_match_text(name)
    for suffix in COMPANY_SUFFIXES:
        normalized = re.sub(rf"\b{re.escape(suffix)}\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_job_title(title: str) -> str:
    return normalize_match_text(title)


def calculate_similarity(s1: str, s2: str) -> int:
    """Levenshtein-based similarity 0-100 (job-ops semantics)."""
    a, b = s1.lower(), s2.lower()
    if a == b:
        return 100
    if not a or not b:
        return 0
    if a in b or b in a:
        longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
        return round(len(shorter) / len(longer) * 100)

    # Classic DP matrix (two-row rolling variant — same result, O(min) memory).
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    distance = prev[-1]
    max_len = max(len(a), len(b))
    return round((max_len - distance) / max_len * 100)


class _Mergeable(Protocol):
    title: str
    company: str


def _first_non_null(base: Any, incoming: Any) -> Any:
    """None-coalescing merge: base wins when truthy, else incoming (job-ops ?? semantics)."""
    return base if base not in (None, "", [], {}) else incoming


def dedup_jobs(jobs: Iterable[JobT], title_threshold: int = FUZZY_DEDUP_TITLE_THRESHOLD,
               employer_threshold: int = FUZZY_DEDUP_EMPLOYER_THRESHOLD) -> list[JobT]:
    """Deduplicate by fuzzy title+employer match, merging fields (first non-null wins).

    Lifted from job-ops deduplicateJobsByTitleAndEmployer; operates on any
    object with title/company attributes and mergeable dataclass fields.
    """
    merged: list[JobT] = []
    keys: list[tuple[str, str]] = []  # (normalized_title, normalized_employer)

    for incoming in jobs:
        n_title = normalize_job_title(getattr(incoming, "title", ""))
        n_employer = normalize_company_name(getattr(incoming, "company", ""))

        if not n_title or not n_employer:
            merged.append(incoming)
            keys.append((n_title, n_employer))
            continue

        match_idx = None
        for idx, (et, ee) in enumerate(keys):
            if not et or not ee:
                continue
            if calculate_similarity(n_title, et) <= title_threshold:
                continue
            if calculate_similarity(n_employer, ee) > employer_threshold:
                match_idx = idx
                break

        if match_idx is not None:
            merged[match_idx] = _merge_two(merged[match_idx], incoming)
        else:
            merged.append(incoming)
            keys.append((n_title, n_employer))

    return merged


# ── Trust-scored dedup precedence (methodology §objective-I ladder) ─────────

# Global precedence ladder, highest trust → lowest (verbatim from the
# methodology; ladder values are ordinal — only the ORDER matters):
_SOURCE_LADDER: dict[str, int] = {
    # 10 — company's own ATS (source of truth) + regional ATS
    "greenhouse": 10, "lever": 10, "ashby": 10, "smartrecruiters": 10,
    "workable": 10, "personio": 10,
    # 9 — WTTJ full Algolia schema
    "wttj": 9,
    # 7 — LinkedIn Guest (aggregator, possible schema drift)
    "linkedin guest": 7,
    # 6 — Indeed (aggregator, parsed HTML)
    "indeed": 6, "jobspy": 6,
    # 5 — Adzuna + ZipRecruiter (aggregators, decent quality)
    "adzuna": 5, "ziprecruiter": 5,
    # 4 — Glassdoor (interstitial risk)
    "glassdoor": 4,
    # 3 — Careerjet (120-char excerpts) + curated remote-only boards
    "careerjet": 3, "remoteok": 3, "remotive": 3, "wwr": 3,
    "jobicy": 3, "the muse": 3, "themuse": 3,
    # 2 — Wellfound (scraped via stealth browser)
    "wellfound": 2,
    # 1 — Findwork (re-publishes the free aggregators)
    "findwork": 1,
}
_DEFAULT_LADDER = 5                     # unknown sources: neutral middle


def source_ladder_trust(source: str) -> int:
    """Ladder rank for a source name (prefix match: 'Ashby.openai' → ashby).

    Sources can be joined duplicates ('Remotive + Adzuna') — take the MAX
    (the highest-trust sighting the merged row represents).
    """
    best = 0
    for part in str(source or "").split(" + "):
        key = part.strip().lower()
        if not key:
            continue                  # empty source part → default, not max
        # exact match first, then dotted prefix ('ashby.openai' → 'ashby').
        # Bare-startswith is deliberately NOT used: 'leveraged' must not
        # rank as 'lever' (CodeRabbit round-1 finding).
        if key in _SOURCE_LADDER:
            best = max(best, _SOURCE_LADDER[key])
            continue
        for ladder_key, v in _SOURCE_LADDER.items():
            if key.startswith(ladder_key + "."):
                best = max(best, v)
            elif len(key) >= 3 and (ladder_key.startswith(key)
                                    or ladder_key.endswith(" " + key)):
                # short caller key vs longer ladder key, as a prefix
                # ('gla' → 'glassdoor') or a final token ('muse' →
                # 'the muse' — CodeRabbit round-3: bare startswith never
                # matched the suffix case, so Muse wrongly ranked 5).
                # Guard len so a 1-2 char key can't match everything.
                best = max(best, v)
    return best or _DEFAULT_LADDER


def _source_precedes(a: str, b: str) -> bool | None:
    """True if source a outranks b on the ladder; False if strictly lower;
    None when equal (caller breaks the tie with per-job trust_score)."""
    la, lb = source_ladder_trust(a), source_ladder_trust(b)
    if la > lb:
        return True
    if la < lb:
        return False
    return None


def _merge_two(base: JobT, incoming: JobT) -> JobT:
    """Merge incoming into base: required identity from base, optional fields
    first-non-null-wins (job-ops mergeJobInputs semantics) — dataclass edition.

    Trust-scored precedence (Sprint-2 / methodology §objective-I ladder):
    when the incoming job's source ranks HIGHER on the per-source trust
    ladder than the base, the incoming job becomes the identity base (its
    title/company/source/link are kept) and the old base's fields merge
    into it — so the kept row is always the most-trusted sighting.
    """
    # ── trust precedence swap ─────────────────────────────────────
    inc_rank = _source_precedes(getattr(incoming, "source", ""),
                                getattr(base, "source", ""))
    if inc_rank is True:
        # incoming outranks base on the ladder → incoming becomes identity
        base, incoming = incoming, base
    elif inc_rank is None:
        # Same ladder level: per-job trust_score breaks the tie (Step D
        # enrichment). None-safe: missing scores keep first-seen order.
        b_score = getattr(base, "trust_score", None)
        i_score = getattr(incoming, "trust_score", None)
        if (b_score is not None and i_score is not None
                and i_score > b_score):
            base, incoming = incoming, base
    # inc_rank is False → base strictly outranks incoming → keep base

    from dataclasses import fields
    for f in fields(base):
        if f.name in ("id", "title", "company", "source", "link"):
            continue  # identity: base always wins
        base_val = getattr(base, f.name)
        inc_val = getattr(incoming, f.name)
        if base_val in (None, "", [], {}) and inc_val not in (None, "", [], {}):
            setattr(base, f.name, inc_val)
    # Track that this listing was seen at multiple sources.
    # Preserve first-seen order (a plain set here made the joined string
    # depend on PYTHONHASHSEED — nondeterministic output across runs).
    names: list[str] = []
    for s in (getattr(base, "source", ""), getattr(incoming, "source", "")):
        for part in str(s).split(" + "):
            if part and part not in names:
                names.append(part)
    setattr(base, "source", " + ".join(names))
    return base
