"""Corroboration signals — cross-source interest/competitiveness/age signals.

Added 2026-09-09 (design-board-v2.md D2, user requirement 0D): job boards
like Workday CXS expose NO view/applicant counts, but aggregator platforms
cross-posting the same roles DO (e.g. LinkedIn guest detail pages show
"122 applicants"). This module joins postings across sources by
requisition-id (exact, when the aggregator description embeds one — NVIDIA
does) or normalized title (fallback), and extracts the signals.

Providers are registry-shaped: `PROVIDERS` maps a name → provider object;
the board-dump flow asks for "linkedin" today, more (glassdoor …) later.

Design constraints (from the peer review):
- B2: LinkedIn guest pages return ~10 cards and `start` is a TRUE offset —
  the index pager advances by cards received (NEVER a fixed 25-step).
- B5: blocked ≠ no-match — every result carries an explicit status
  (matched | no_match | blocked | not_checked) so an IP wall never reads
  as "not cross-posted".
- Circuit breaker: `fetch_signals` stops after `max_consecutive_blocked`
  consecutive blocked detail fetches and reports `blocked` for the rest —
  a LinkedIn wall must never kill the rest of the dump/watch flow.
- File-free: this module fetches/parses only; resumable JSONL state is the
  caller's job (scripts/board_dump.py) per the repo's ingest-vs-scripts
  split.

S8-E1 upgrades (audit/findings-engagement-coverage.md — the 478/1,360
= 35.1% coverage funnel):
- **Applicant-count censoring**: LinkedIn's numApplicants is an ordinal
  BUCKET, not a ratio variable — "Be among the first 25 applicants"
  floors the observable count at `APPLICANT_BUCKET_FLOOR` (25) and "Over
  200 applicants" caps it at `APPLICANT_BUCKET_CAP` (200). fetch_signals
  stamps `applicantCensored: true` on matched records whose count sits at
  either bucket boundary (or whose label names a censoring form — "among
  first N" / "Over N"), false otherwise; blocked records omit the key.
  Never report mean applicant counts without this censoring note (the
  shipped CSV's mean 54 vs median 31 is cap-skewed).
- **Partitioned index mode**: the single `keywords="<company>"` query
  hits a query-SERVING ceiling (752 NVIDIA cards, meta done=true @
  offset 830) that is not a population ceiling — keyword×location slices
  surface +78 NEW cards per 3 probe pages (research §c).
  `index_cards_partitioned()` runs a slice matrix (default:
  `default_index_slices()`, 6 keywords × 5 locations = 30 queries ×
  `PARTITIONED_PAGES_PER_SLICE` (3) pages ≈ 90 requests/run),
  union-deduped by linkedin_job_id, with the same B2 stepping, page-0
  blocked contract and inter-page politeness as single mode plus
  `_SLICE_PAUSE_S` between slices.
- **Join-key upgrades**: (a) reqId extraction gains a bare JR#######
  second pass (in linkedin_guest._parse_req_id — anchored wins);
  (b) join_by_title gains a conservative location-aware tiebreak —
  same-key candidate pairs whose card location overlaps the req's
  location string (city tokens / state codes) are preferred over date
  proximity; NO new candidate pairs, still greedy 1:1.

Live-verified facts leaned on (2026-09-09):
- search: 10 cards/page, deep unique pagination (500+ positions).
- detail: "num-applicants" caption ("122 applicants" / "Over 200 …"),
  "posted-time-ago__text" span, description embeds
  "Job Requisition ID JR2023808" for NVIDIA cross-posts.
- searching BY reqId does NOT work (keywords don't match reqIds) — hence
  the index-then-join strategy.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from html import unescape
from typing import Optional
from urllib.parse import urlencode

from .config import Config
from .sources import linkedin_guest
from .sources.base import fetch_text
from .sources.linkedin_guest import (
    _CARDS_PER_PAGE,          # guest page width — short-page exhaustion
    _parse_search_results,   # card parser (10-card pages)
    job_key,                 # normalized title+company key
    SEARCH_URL,
)


# B5: explicit corroboration status — persisted alongside every signal so
# "blocked" (retry later) never masquerades as "no_match" (permanent blank).
STATUS_MATCHED = "matched"
STATUS_NO_MATCH = "no_match"
STATUS_BLOCKED = "blocked"
STATUS_NOT_CHECKED = "not_checked"

_INDEX_PAUSE_S = 1.0        # politeness between guest search pages
_DETAIL_PAUSE_S = 1.0       # politeness between detail fetches
_MAX_CONSECUTIVE_BLOCKED = 3   # circuit breaker threshold
_SLICE_PAUSE_S = 3.0        # S8-E1: politeness BETWEEN index slices

# S8-E1: LinkedIn applicant-count censoring bounds (research §a: CSV
# n=478 — min 25, median 31, mean 54, max 200; "among first 25" floors,
# "Over 200 applicants" caps; LinkedIn also resets counts on repost).
APPLICANT_BUCKET_FLOOR = 25
APPLICANT_BUCKET_CAP = 200

# S8-E1 partitioned-index budget knobs (research §f R1): 30 queries ×
# 3 pages ≈ 90 requests/run — the deliberate request bound. The union
# cap is generous because the page budget is the real bound.
PARTITIONED_PAGES_PER_SLICE = 3
PARTITIONED_MAX_CARDS = 1000

# S9 targeted title-search knobs (design-s9-titlesearch.md C1): ONE
# relevance-sorted guest page per no_match req, verbatim-match predicate
# (GT-calibrated 2026-09-15: 318 reqId-joined pairs are verbatim —
# token-F1=1.0 for 99%, seniority-set equality 100%).
TITLE_SEARCH_PAUSE_S = 1.5
TITLE_SEARCH_BREAKER = 4        # consecutive blocked/blocked_empty stop

# S9 canonical title-matching predicate API — ONE tokenizer, no script-
# local copies (design review finding 10: the probe scripts had already
# drifted 3-4 ways). Verbatim predicate = token-set F1 >= 0.95 AND
# seniority-set equality (both GT-verified properties of true pairs).
TOKEN_STOPWORDS = frozenset({
    "and", "the", "of", "for", "a", "an", "in", "to", "at", "us",
    "usa", "united", "states", "ii", "iii", "iv", "team", "teams",
    "new",
})
SENIORITY_TOKENS = frozenset({
    "senior", "sr", "staff", "principal", "distinguished", "architect",
    "manager", "director", "vp", "lead", "junior", "jr", "intern",
    "entry",
})


def title_tokens(title: str) -> list[str]:
    """Canonical tokenization for title matching (S9): lowercase
    alphanumeric runs >= 2 chars minus TOKEN_STOPWORDS.

    S9-audit fix (B1/B3): HTML entities are unescaped FIRST — the guest
    index carries e.g. 'R&amp;D' whose phantom `amp` token broke the
    pipeline's own verbatim predicate on provably-true pairs (job_key
    already unescaped; the predicate API now agrees)."""
    return [t for t in re.findall(r"[a-z0-9]{2,}",
                                  unescape(title or "").lower())
            if t not in TOKEN_STOPWORDS]


def _seniority_set(title: str) -> frozenset:
    return frozenset(t for t in re.findall(r"[a-z0-9]{2,}",
                                           unescape(title or "").lower())
                     if t in SENIORITY_TOKENS)


def _company_token(company: str) -> str:
    """Coarse company namespace for JOIN keys (S9-audit B1/D3): the
    board param 'NVIDIA' vs card company 'NVIDIA AI' must share a
    namespace or 97 cards are structurally unjoinable. First lowercased
    token. Empty stays empty (a company-less card does not join a
    company-scoped board — strict, and the data has none)."""
    c = (company or "").strip().lower()
    return c.split()[0] if c else ""


def _join_key(title: str, company: str) -> str:
    """job_key's title normalization + coarse company namespace — the
    JOIN-side key (ingestion/dedup keeps raw job_key; the join needs
    the NVIDIA/NVIDIA-AI unification)."""
    return linkedin_guest.job_key(title, _company_token(company))


def token_multiset_key(title: str, company: str) -> tuple:
    """Order/punctuation-insensitive title key (S9 C3): sorted token
    MULTISET (stopwords KEPT — removing them conflates live title pairs
    like 'Senior Account Manager - Walmart' ↔ 'Senior Account Manager,
    Walmart' under stop-set keys; the multiset keeps 213/213 probe hit
    pairs distinct). Company appends the namespace like job_key — via
    the coarse company token (S9-audit: 'NVIDIA AI' ≡ 'NVIDIA')."""
    return (tuple(sorted(re.findall(r"[a-z0-9]{2,}",
                                    unescape(title or "").lower()))),
            _company_token(company))


def verbatim_match(req_title: str, card_title: str) -> bool:
    """GT-calibrated verbatim predicate: is this LinkedIn card title a
    copy of the Workday req title (modulo punctuation / word order /
    whitespace)? Token-set F1 >= 0.95 over canonical tokens AND exact
    seniority-set equality (a senior/non-senior pair is a DIFFERENT req
    even at F1=1.0 — verified against live near-miss pages)."""
    rt, ct = set(title_tokens(req_title)), set(title_tokens(card_title))
    if not rt or not ct:
        return False
    inter = rt & ct
    if not inter:
        return False
    p, r = len(inter) / len(ct), len(inter) / len(rt)
    f1 = 2 * p * r / (p + r)
    return f1 >= 0.95 and _seniority_set(req_title) == _seniority_set(
        card_title)


# Workday primaryLocation "US, CA, Santa Clara" -> LinkedIn guest
# location "Santa Clara, California" (S9 C1; board-agnostic shape).
_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def li_location(primary_location: str) -> str:
    """Translate a Workday primaryLocation into a LinkedIn guest search
    location. Unparsable / country-level -> "United States" (neutral,
    matches the existing single-query default).

    S9-audit B1 fix: a REMOTE city part ("US, CA, Remote") maps to
    "Remote" — NOT "Remote, California", which over-restricts the
    search to CA-tagged remote cards (31/790 live probes, 11 of them
    no_card)."""
    parts = [p.strip() for p in (primary_location or "").split(",")]
    if len(parts) >= 3 and parts[1].upper() in _US_STATES:
        if parts[2].lower() == "remote":
            return "Remote"
        return f"{parts[2]}, {_US_STATES[parts[1].upper()]}"
    return "United States"


def row_search_location(row: dict) -> str:
    """Best-effort LinkedIn guest-search location for a posting ROW in
    its LIST shape (no detail payload). Preference order:
    1. `primaryLocation` — detail-shaped rows (kept first: the dump's
       detail view and every existing fixture shape still work);
    2. `locationsText` when it is a REAL location — single-location
       CXS list rows carry e.g. 'US, CA, Santa Clara'; multi-location
       rows carry 'N Locations' (useless, skipped);
    3. the `/job/US-CA-Santa-Clara/…` slug embedded in `externalPath`
       or `url` — present on EVERY CXS list row and encoding the
       PRIMARY location (verified live: 1410/1410 rows, incl. the 531
       multi-location ones whose text says nothing).
    Falls back to the neutral 'United States'.

    S9-audit H3v P2: the watch/dump title searches read ONLY
    primaryLocation — which live list rows NEVER carry — so every probe
    ran location-dead at country level (recall gap: narrower city
    queries surface deeper per-city card sets)."""
    pl = (row.get("primaryLocation") or "").strip()
    if pl:
        return li_location(pl)
    lt = (row.get("locationsText") or "").strip()
    if lt and not re.match(r"^\d+ Locations?$", lt):
        return li_location(lt)
    for key in ("externalPath", "url"):
        m = re.search(r"/job/([A-Z]{2})-([A-Z]{2})-([A-Za-z][A-Za-z-]*?)/",
                      row.get(key) or "")
        if m:
            return li_location(f"{m.group(1)}, {m.group(2)}, "
                               f"{m.group(3).replace('-', ' ')}")
    return "United States"

# Default slice matrix (research §c/§f R1): keyword variants × the card-
# location top set from the NVIDIA li_index.
_SLICE_KEYWORD_SUFFIXES = (
    "software", "engineer", "hardware", "marketing", "sales")
_SLICE_LOCATIONS = (
    "United States",
    "Santa Clara, California, United States",
    "Austin, Texas, United States",
    "Seattle, Washington, United States",
    "Remote, United States",
)

# Location-tiebreak tokens (join_by_title): 2+ letter tokens minus
# country/generic words — keeps state codes (ca/tx) and city names,
# drops the ones that would match everything ("united", "states",
# "us") or nothing useful ("city", "area").
_LOC_STOPWORDS = frozenset((
    "us", "usa", "united", "states", "america", "city", "area", "metro"))


class CorroborationBlocked(RuntimeError):
    """Raised ONLY when the provider wall-blocks the very first index page
    (nothing gained, caller marks the phase blocked) — or when a page
    serves zero cards over an HTTP 200 ('blocked_empty' — the S9 soft-wall
    doctrine: wall-ambiguous, retryable, never terminal). Mid-flow
    failures degrade gracefully instead — see fetch_signals' no-raise
    contract."""


# Card keys _blocked_record and fetch_signals' matched-record build index
# STRICTLY — a card dict missing one cannot produce a record (S9-audit
# D3 P3: a malformed li_index line must skip with a note, never crash).
_CARD_REQUIRED_KEYS = ("id", "url", "title", "company", "location")


def _blocked_record(card: dict, error: str = "blocked") -> dict:
    return {
        "linkedin_job_id": card["id"],
        "linkedin_url": card["url"],
        "title": card["title"],
        "company": card["company"],
        "location": card["location"],
        "linkedin_posted_date": card.get("date", "")[:10],
        "num_applicants": None,
        "applicants_label": "",
        "job_req_id": "",
        "posted_time_ago": "",
        "closed": False,
        "status": STATUS_BLOCKED,
        "error": error,
    }


def _company_variants(company: str) -> list[str]:
    """Company name variants worth matching cards against (NVIDIA posts
    under 'NVIDIA' and 'NVIDIA AI' — sub-brands)."""
    base = company.strip().lower()
    return [base, f"{base} ai"]


def default_index_slices(company: str = "NVIDIA") -> list[dict]:
    """Default slice matrix for a company (S8-E1, research §f R1).

    The company name plus role-word keyword variants × the top card
    locations from the NVIDIA li_index: for NVIDIA this is 6 keywords ×
    5 locations = 30 {keywords, location} queries; at
    PARTITIONED_PAGES_PER_SLICE (3) pages each that bounds a full run at
    ~90-100 requests. Generic for other companies (role-word slices
    derived from the company name).
    """
    base = (company or "").strip() or "NVIDIA"
    keywords = [base] + [f"{base} {s}" for s in _SLICE_KEYWORD_SUFFIXES]
    return [{"keywords": kw, "location": loc}
            for kw in keywords for loc in _SLICE_LOCATIONS]


def _applicant_censored(num_applicants,
                         applicants_label: str = "") -> bool:
    """True when the applicant count is a censored bucket observation:
    at the floor ("among first 25" — true count at-or-below) or the cap
    ("Over 200" — true count above), or the label itself names a
    censoring form ("among first N" / "Over N"). None → False (no
    count — blocked records omit the flag entirely)."""
    if num_applicants is None:
        return False
    if num_applicants in (APPLICANT_BUCKET_FLOOR, APPLICANT_BUCKET_CAP):
        return True
    low = (applicants_label or "").strip().lower()
    return low.startswith("among first") or low.startswith("over ")


def _location_tokens(location: str) -> frozenset:
    """Conservative overlap tokens for the join_by_title location
    tiebreak: 2+ letter words (state codes like `ca`, city names) minus
    generic/country words. Empty location → empty set (neutral)."""
    return frozenset(
        t for t in re.findall(r"[A-Za-z]{2,}", (location or "").lower())
        if t not in _LOC_STOPWORDS)


class LinkedInSignalProvider:
    """Signals via the LinkedIn guest endpoint (no auth)."""

    name = "linkedin"

    def __init__(self, cfg: Optional[Config] = None):
        # live callers (board_dump) pass no cfg — default to the real one;
        # None would crash fetch_text (cfg.http_timeout_s) — regression
        # pinned in test_corroborate.py::TestProviderDefaults
        self.cfg = cfg or Config()

    # ── index phase ────────────────────────────────────────────────────
    def _paginate_query(self, keywords: str, location: str,
                        variants: list[str], max_pages: int, max_cards: int,
                        start_offset: int, seen: set[str]
                        ) -> tuple[list[dict], int, bool, Optional[Exception]]:
        """Paginate ONE (keywords, location) guest query, B2-safe.

        Shared worker for index_cards (single mode) and
        index_cards_partitioned (slice mode). Mutates `seen` with every
        accepted card id. Returns (cards, next_offset, exhausted, exc):
        `exc` is the error that stopped the query mid-run — a transport
        error, or CorroborationBlocked('blocked_empty') when an
        HTTP-200 page serves ZERO cards (the S9 soft-wall doctrine,
        design-s9-titlesearch.md C1 — the same rule search_title
        enforces: an empty 200 page is wall-ambiguous and RETRYABLE,
        never terminal; the caller decides whether a page-0 exc is
        fatal — nothing gained → raise — or a partial result to keep
        and resume). `exhausted` is True on GENUINE end-of-serving: a
        SHORT page (< _CARDS_PER_PAGE cards) that still has cards, or
        the chromeless beyond-end STUB (H3v P2 — see inline).
        (S9-audit D3 P2: the old code treated the empty 200 page as
        terminal exhaustion — a mid-index soft wall read as done=true
        and the dump never re-opened the index.)
        """
        cards: list[dict] = []
        offset = start_offset
        exhausted = False
        for _page in range(max_pages):
            url = f"{SEARCH_URL}?{urlencode({
                'keywords': keywords, 'location': location,
                'start': offset, 'sortBy': 'DD'})}"
            try:
                html = fetch_text(url, cfg=self.cfg)
            except Exception as exc:
                return cards, offset, exhausted, exc
            page_cards = _parse_search_results(html)
            if not page_cards:
                # Split the 0-card 200 page by its BODY SHAPE (H3v P2
                # livelock fix, live-probed 2026-09-17): the guest
                # search serves a tiny chromeless STUB past the end of
                # the serving window ('<!DOCTYPE html>\\n\\n<!---->',
                # ~26 bytes) — that is GENUINE end-of-serving; a resume
                # landing there must COMPLETE, not retry forever. The
                # WALL shapes stay retryable blocked_empty: the observed
                # wall is a 302→authwall (FULL page, KBs, no card
                # markers — D3 evidence) and a truly-empty body ("",
                # unobserved live; kept ambiguous per the S9 doctrine).
                # Signature: non-empty, tiny (<100B), no results chrome.
                body = (html or "").strip()
                if 0 < len(body) < 100 \
                        and "base-search-card" not in body:
                    exhausted = True
                    return cards, offset, exhausted, None
                # Anything else with zero cards — authwall page, empty
                # body, layout change: soft-wall ambiguity — retryable,
                # never done (S9 doctrine; see docstring).
                # With the short-page rule below, this branch is only
                # reachable after a FULL page or at a resume boundary
                # that lands exactly at the end of the serving window.
                return cards, offset, exhausted, CorroborationBlocked(
                    "blocked_empty: index page at offset "
                    f"{offset} served no cards (soft wall ambiguity — "
                    "design-s9 C1; retryable, never done)")
            for card in page_cards:
                if card["id"] in seen:
                    continue
                if card["company"].strip().lower() not in variants:
                    continue    # keyword noise; keep company cards only
                seen.add(card["id"])
                cards.append(card)
            offset += max(len(page_cards), 1)
            if len(page_cards) < _CARDS_PER_PAGE:
                # a SHORT page with cards is the serving tail — genuine
                # exhaustion (guest pages serve _CARDS_PER_PAGE cards
                # until the end; only a 0-card 200 page is ambiguous).
                exhausted = True
                break
            if len(cards) >= max_cards:
                break
            time.sleep(_INDEX_PAUSE_S)
        return cards, offset, exhausted, None

    def search_title(self, req_title: str, location: str,
                     known_ids: set, company: str = "NVIDIA",
                     mark_indexed: bool = False
                     ) -> tuple[list[dict], Optional[Exception]]:
        """S9 targeted title search (design-s9-titlesearch.md C1): ONE
        relevance-sorted guest page with keywords = the EXACT Workday req
        title, returns NVIDIA cards whose title passes `verbatim_match`
        (GT-calibrated), excluding ids in `known_ids`.

        mark_indexed=True (S9-audit B3/F1 P2 fix): known cards are
        returned WITH an `indexed: True` flag instead of being dropped —
        lets the titlesearch phase distinguish hit_indexed (a verbatim
        card EXISTS but is already indexed — a CHECKED non-match) from
        no_card (nothing verbatim exists). Default False keeps the
        watch's drop-known behavior.

        Contract: transport errors are RETURNED AS VALUES (the
        `_paginate_query` shape), never propagated. A page that returns
        ZERO parsed cards is reported via the exception value
        `CorroborationBlocked('blocked_empty', ...)` — an HTTP-200-empty
        page is indistinguishable from a soft wall and must never read
        as "no results" (design review finding 5). The caller refreshes
        `known_ids` with every accepted card WITHIN its batch (sibling
        requisitions verbatim-hit the same card)."""
        url = f"{SEARCH_URL}?{urlencode({
            'keywords': (req_title or "").strip(),
            'location': location or "United States", 'start': 0})}"
        try:
            html = fetch_text(url, cfg=self.cfg)
        except Exception as exc:
            return [], exc
        cards = _parse_search_results(html)
        if not cards:
            # 200-empty: soft-wall ambiguity — retryable, never terminal
            return [], CorroborationBlocked(
                "blocked_empty: title search returned an empty page "
                "(soft wall ambiguity — design-s9 C1)")
        variants = _company_variants(company)
        out = []
        for card in cards:
            cid = str(card.get("id") or "")
            if cid in known_ids:
                if mark_indexed:
                    indexed = dict(card)
                    indexed["indexed"] = True
                    out.append(indexed)
                continue
            if (card.get("company") or "").strip().lower() not in variants:
                continue
            if not verbatim_match(req_title, card.get("title") or ""):
                continue
            out.append(card)
        return out, None

    def index_cards(self, company: str, location: str = "United States",
                    max_pages: int = 10, max_cards: int = 100,
                    start_offset: int = 0) -> tuple[list[dict], int, bool]:
        """Paginate the guest search for `company` postings (single query).

        B2-safe: advances `start` by the number of cards each page actually
        returned. Returns (cards, next_offset, exhausted) — NEVER raises on
        page N>0 failure (partial results + resume offset instead, so a
        mid-index wall keeps prior progress); raises only if page 0 itself
        is blocked or serves zero cards (caller marks the whole phase
        blocked). `exhausted` (done) is True only on a genuine serving
        tail: a short page with cards — an empty 200 page is returned as
        a blocked_empty exc (retryable), never as done (S9-audit D3).
        """
        variants = _company_variants(company)
        seen: set[str] = set()
        cards, offset, exhausted, exc = self._paginate_query(
            company, location, variants, max_pages, max_cards,
            start_offset, seen)
        if exc is not None and not cards and offset == start_offset:
            raise CorroborationBlocked(
                f"linkedin index blocked at offset 0: "
                f"{type(exc).__name__}: {exc}") from exc
        return cards[:max_cards], offset, exhausted

    def index_cards_partitioned(
            self, company: str, slices: Optional[list[dict]] = None,
            max_pages_per_slice: int = PARTITIONED_PAGES_PER_SLICE,
            max_cards: int = PARTITIONED_MAX_CARDS
            ) -> tuple[list[dict], int, bool]:
        """Slice-matrix index mode (S8-E1, research §c/§f R1).

        Runs every {keywords, location} slice in `slices` (default:
        `default_index_slices(company)` — 6 keywords × 5 locations = 30
        queries × 3 pages ≈ 90 requests) with the SAME B2-safe per-query
        pagination, company-variant filter and inter-page politeness as
        index_cards, plus `_SLICE_PAUSE_S` between slices. The union is
        deduped by linkedin_job_id ACROSS slices (and within each).

        Returns (cards, next_offset, exhausted) with the same contract
        as index_cards, except next_offset is always 0: every slice
        restarts at offset 0 on a re-run and the id-level union (kept by
        the caller) IS the resume state — re-runs are idempotent.
        `exhausted` is False only when some slice was transport-blocked
        OR served an empty 200 page mid-run (soft-wall ambiguity —
        re-run later; S9-audit D3); a slice stopping at its PAGE CAP or
        on a short tail page counts as complete — the cap is the
        designed request budget, exactly as the single query's done=true
        is a serving ceiling (research §c).
        Raises CorroborationBlocked only when the FIRST slice's page 0
        is blocked or serves zero cards and nothing was gained
        (mirrors index_cards).
        """
        if slices is None:
            slices = default_index_slices(company)
        variants = _company_variants(company)
        seen: set[str] = set()
        union: list[dict] = []
        exhausted_all = True
        for i, sl in enumerate(slices):
            keywords = str(sl.get("keywords") or company).strip() or company
            location = str(sl.get("location") or "United States").strip() \
                or "United States"
            cards, offset, _exhausted, exc = self._paginate_query(
                keywords, location, variants, max_pages_per_slice,
                max(1, max_cards - len(union)), 0, seen)
            if exc is not None:
                if i == 0 and not cards and offset == 0:
                    raise CorroborationBlocked(
                        f"linkedin partitioned index blocked on first "
                        f"slice ({keywords!r}/{location!r}) at offset 0: "
                        f"{type(exc).__name__}: {exc}") from exc
                exhausted_all = False    # partial slice → re-run later
            union.extend(cards)
            if len(union) >= max_cards:
                break
            if i + 1 < len(slices):
                time.sleep(_SLICE_PAUSE_S)
        return union[:max_cards], 0, exhausted_all

    # ── signal extraction phase ────────────────────────────────────────
    def fetch_signals(self, cards: list[dict],
                      detail_pause_s: float = _DETAIL_PAUSE_S,
                      on_record=None) -> list[dict]:
        """Fetch detail pages for `cards`, extracting corroboration signals.

        Contract: EXACTLY one record per input card; NEVER raises
        mid-batch. On a circuit-break (N consecutive blocked fetches) every
        remaining card gets a `blocked` record (error='circuit_open') — the
        caller re-runs later; partial progress is never lost.

        Every record carries `fetched_at` (ISO) — signal freshness is part
        of the deliverable contract (design D1 col 29). `on_record(rec)`
        (optional) fires per record the moment it exists — per-card
        checkpointing for callers (the details phase's per-row doctrine).

        Malformed card dicts (missing any of id/url/title/company/
        location, or not a dict at all — a well-formed-JSON li_index
        line can still be shape-wrong) are SKIPPED with a stderr note
        instead of crashing the batch (S9-audit D3 P3); well-formed
        cards keep the one-in-one-out contract.
        """
        out: list[dict] = []
        consecutive_blocked = 0
        circuit_open = False
        for card in cards:
            if not isinstance(card, dict) \
                    or any(k not in card for k in _CARD_REQUIRED_KEYS):
                missing = ("not a dict" if not isinstance(card, dict)
                           else ", ".join(k for k in _CARD_REQUIRED_KEYS
                                          if k not in card))
                print(f"[corroborate] skipping malformed index card "
                      f"(missing {missing}): {card!r}", file=sys.stderr)
                continue
            rec = None
            if circuit_open:
                rec = _blocked_record(card, error="circuit_open")
            if rec is None:
                try:
                    detail = linkedin_guest.fetch_detail(
                        card["id"], cfg=self.cfg)
                except Exception as exc:
                    rec = _blocked_record(card, error=type(exc).__name__)
                    consecutive_blocked += 1
                    if consecutive_blocked >= _MAX_CONSECUTIVE_BLOCKED:
                        circuit_open = True
                else:
                    consecutive_blocked = 0
                    num_applicants = detail.get("num_applicants")
                    applicants_label = detail.get("applicants_label", "")
                    rec = {
                        "linkedin_job_id": card["id"],
                        "linkedin_url": card["url"],
                        "title": card["title"],
                        "company": card["company"],
                        "location": card["location"],
                        "linkedin_posted_date": card.get("date", "")[:10],
                        "num_applicants": num_applicants,
                        "applicants_label": applicants_label,
                        # S8-E1: the count is a censored bucket observation
                        # (floor 25 / cap 200) — see module docstring.
                        "applicantCensored": _applicant_censored(
                            num_applicants, applicants_label),
                        "job_req_id": detail.get("job_req_id", ""),
                        "posted_time_ago": detail.get("posted_time_ago", ""),
                        "closed": detail.get("closed", False),
                        "status": STATUS_MATCHED,
                    }
            rec["fetched_at"] = datetime.now().isoformat(
                timespec="seconds")
            out.append(rec)
            if on_record:
                try:
                    on_record(rec)
                except Exception:        # checkpoint IO must not kill fetch
                    pass
            time.sleep(detail_pause_s)
        return out


# ── the join ────────────────────────────────────────────────────────────

def _claimant_rank(rec: dict) -> tuple:
    """Rank SAME-reqId claimants for join_by_req_id (S9-audit B2r:
    first-wins shipped counts up to 113 days staler than an available
    fresher card — 19 rows, 14 with provably wrong numApplicants).
    Best = smallest tuple: open (not closed) first, then freshest
    fetch, then latest card post date, then stable insertion order."""
    from datetime import date as _d
    def _ord(d: str) -> int:
        try:
            return _d.fromisoformat(str(d or "")[:10]).toordinal()
        except ValueError:
            return 0
    return (bool(rec.get("closed")),
            -_ord(rec.get("fetched_at")),
            -_ord(rec.get("linkedin_posted_date")))


def join_by_req_id(signals: list[dict],
                   req_ids: set[str]) -> dict[str, dict]:
    """Join corroboration records to postings by exact requisition id.
    Returns {req_id: signal_record} — only exact reqId matches.

    Multiple cards may embed the SAME reqId (repost twins, re-indexed
    vintages): the best claimant wins — open > closed, freshest fetch
    first (S9-audit B2r; was first-wins file order).

    Blocked records never join (S7-F2, fixing the contract the vacuous
    test_blocked_never_joins used to paper over): a blocked fetch means
    we have NO signal for that card — even a stale job_req_id recorded
    before the wall must not surface as corroboration. Callers that want
    postings whose card fetch was walled to read `blocked` (phase_finish's
    B5 status) pass the blocked records through join_by_title instead,
    where the record's status IS the payload and num_applicants is None.
    """
    best: dict[str, tuple] = {}
    joined: dict[str, dict] = {}
    for i, rec in enumerate(signals):
        if rec.get("status") == STATUS_BLOCKED:
            continue
        rid = (rec.get("job_req_id") or "").strip()
        if not (rid and rid in req_ids):
            continue
        rank = _claimant_rank(rec) + (i,)
        if rid not in joined or rank < best[rid]:
            joined[rid] = rec
            best[rid] = rank
    return joined


def join_by_title(signals: list[dict], postings: dict[str, str],
                  company: str,
                  req_dates: Optional[dict[str, str]] = None,
                  req_locations: Optional[dict[str, str]] = None
                  ) -> dict[str, dict]:
    """Fallback join: normalized title (+company) match, GREEDY 1:1.

    postings: {req_id: title}; req_dates (optional): {req_id: ISO date}
    for proximity disambiguation; req_locations (optional, S8-E1):
    {req_id: location string} (e.g. the Workday detail locations) for
    the location-aware tiebreak. Returns {req_id: signal_record}.

    1:1 contract (audit S7-A2 finding B): one LinkedIn signal describes ONE
    LinkedIn posting — copying it onto every same-key posting stamped one
    card's applicant count onto 31 different reqs. Greedy assignment: all
    (card, req) candidate pairs sharing a job_key are ranked by date
    proximity (|card date − req date|; unknown dates sort last), then
    newer card first; each card and each req is assigned at most once.
    Unassigned reqs are simply absent (caller derives no_match).

    Location-aware tiebreak (S8-E1, research §f R3b): pairs whose card
    location text overlaps the req's location string (city tokens /
    state codes — `_location_tokens`) rank BEFORE date proximity, which
    safely disambiguates the 91 duplicated Workday titles across sites.
    CONSERVATIVE by construction: it only reorders existing same-key
    candidate pairs (never creates pairs), stays neutral when either
    side's location is missing/generic, and the greedy 1:1 assignment
    itself is unchanged (no new fanout)."""
    from datetime import date as _date

    def _proximity(card_date: str, req_date: str) -> int:
        if not card_date or not req_date:
            return 9_999                     # unknown → rank last
        try:
            return abs((_date.fromisoformat(card_date[:10])
                        - _date.fromisoformat(req_date[:10])).days)
        except ValueError:
            return 9_999

    # candidate pairs (card, req) sharing a normalized key, ranked:
    # (location overlap desc, proximity asc, card-date desc, card_id, req_id)
    # S9-audit P1 fix (B1): job_key over-normalizes (it strips the
    # dash/paren suffix, so 'Software Engineer' and 'Software Engineer
    # - CUDA' share a key) — the GT-calibrated verbatim predicate is
    # now the guard on this tier (108/115 live F1-failures were exactly
    # this class: same base title, DIFFERENT specialization = different
    # requisition). The multiset tier needs no guard (multiset equality
    # implies F1 = 1.0 — B2r verified on the live corpus).
    by_key: dict[str, list[dict]] = {}
    card_loc: dict[int, frozenset] = {}
    for rec in signals:
        key = _join_key(rec["title"], rec["company"])
        if key:
            by_key.setdefault(key, []).append(rec)
            card_loc[id(rec)] = _location_tokens(
                rec.get("location") or "")
    req_loc = {rid: _location_tokens((req_locations or {}).get(rid) or "")
               for rid in postings}
    rec_by_pair: dict[tuple, dict] = {}
    for rid, title in postings.items():
        key = _join_key(title, company)
        for rec in by_key.get(key, []):
            if not verbatim_match(title, rec["title"]):
                continue          # key-equal but not a verbatim pair
            # S9-audit INV-2 residual (2 live rows): a card whose
            # description embeds ANOTHER requisition's id is that req's
            # posting — it serves NOBODY by title, even when the named
            # req has departed the board (same policy as the watch's
            # _window_match: "foreign reqId serves nobody"). jr == rid
            # (this req's OWN blocked card) still joins.
            jr = (rec.get("job_req_id") or "").strip()
            if jr and jr != rid:
                continue
            cd = str(rec.get("linkedin_posted_date") or "")
            rd = (req_dates or {}).get(rid) or ""
            cid = str(rec.get("linkedin_job_id")
                      or rec.get("id") or id(rec))   # unique fallback
            overlap = bool(card_loc.get(id(rec), frozenset())
                           & req_loc[rid])
            rec_by_pair[(not overlap, _proximity(cd, rd), cd, cid, rid)] = rec
    def _ord(d: str) -> int:
        try:
            return _date.fromisoformat(d[:10]).toordinal()
        except (ValueError, TypeError):
            return 0

    joined: dict[str, dict] = {}
    used_cards: set[str] = set()
    for key in sorted(rec_by_pair,
                      key=lambda k: (k[0], k[1], -_ord(k[2]), k[3], k[4])):
        rid, card_id = key[4], key[3]
        if rid in joined or card_id in used_cards:
            continue
        joined[rid] = rec_by_pair[key]
        used_cards.add(card_id)

    # S9 C3 tier: order/punctuation-insensitive multiset key — the
    # join-side half of the `search_title` acceptance predicate (F1>=0.95
    # is looser than job_key equality: '…Engineer - Tegra' ↔
    # '…Engineer, Tegra' fails job_key but is a true pair). Same greedy
    # 1:1 + location/proximity ranking, seniority-set equality guard,
    # only over pairs the job_key tier did NOT consume.
    remaining_reqs = [rid for rid in postings if rid not in joined]
    if remaining_reqs:
        mkey_cards: dict[tuple, list[dict]] = {}
        for rec in signals:
            if str(rec.get("linkedin_job_id") or rec.get("id")
                   or id(rec)) in used_cards:
                continue
            mk = token_multiset_key(rec["title"], rec["company"])
            if mk[0]:
                mkey_cards.setdefault(mk, []).append(rec)
        m_pairs: dict[tuple, dict] = {}
        for rid in remaining_reqs:
            mk = token_multiset_key(postings[rid], company)
            if not mk[0]:
                continue
            for rec in mkey_cards.get(mk, []):
                if _seniority_set(postings[rid]) != _seniority_set(
                        rec["title"]):
                    continue
                jr = (rec.get("job_req_id") or "").strip()
                if jr and jr != rid:
                    continue    # foreign-reqId card serves nobody
                cd = str(rec.get("linkedin_posted_date") or "")
                rd = (req_dates or {}).get(rid) or ""
                cid = str(rec.get("linkedin_job_id")
                          or rec.get("id") or id(rec))
                overlap = bool(card_loc.get(id(rec), frozenset())
                               & req_loc[rid])
                m_pairs[(not overlap, _proximity(cd, rd), cd, cid, rid)] = rec
        for key in sorted(m_pairs,
                          key=lambda k: (k[0], k[1], -_ord(k[2]), k[3], k[4])):
            rid, card_id = key[4], key[3]
            if rid in joined or card_id in used_cards:
                continue
            joined[rid] = {**m_pairs[key], "match_tier": "multiset"}
            used_cards.add(card_id)
    return joined


# ── population helper (S9 C1a — ONE canonical join-population) ────────

def _card_id(rec: dict) -> str:
    """Stable per-card identity for cross-tier reservation (S9-audit
    P0): the same string join_by_title's used_cards tracks."""
    return str(rec.get("linkedin_job_id") or rec.get("id") or id(rec))


def compose_join(signals: list[dict], postings: dict[str, str],
                 company: str,
                 req_dates: Optional[dict[str, str]] = None,
                 req_locations: Optional[dict[str, str]] = None
                 ) -> tuple[dict, dict, dict]:
    """THE canonical join composition (S9-audit P0 fix — A1/A2/C2
    confirmed the old call sites composed the tiers independently,
    re-serving reqId-tier cards to title-family siblings: 50 cards on
    2 rows each, 54 provably-misattributed rows, ~35 rows burned to
    no_match).

    reqId tier FIRST; its consumed cards are RESERVED and its served
    reqs REMOVED from the title-tier pool; the title tiers then run
    over only unconsumed cards and unserved reqs. Returns
    (reqid_join, title_join, blocked_join) — finish labels match_method
    per tier; blocked cards title-join so blocked reqs read `blocked`
    (B5) with the same req_locations tiebreak (S9-audit C2: the old
    blocked_join call omitted it).
    """
    matched = [s2 for s2 in signals if s2.get("status") == "matched"]
    blocked = [s2 for s2 in signals if s2.get("status") == STATUS_BLOCKED]
    reqid_join = join_by_req_id(matched, set(postings))
    consumed = {_card_id(s2) for s2 in reqid_join.values()}
    unserved = {rid: t for rid, t in postings.items()
                if rid not in reqid_join}
    free_matched = [s2 for s2 in matched if _card_id(s2) not in consumed]
    free_blocked = [s2 for s2 in blocked if _card_id(s2) not in consumed]
    title_join = join_by_title(free_matched, unserved, company,
                               req_dates=req_dates,
                               req_locations=req_locations)
    # S9-audit H1r P2 + H6r sharpening: a BLOCKED record whose card
    # has a MATCHED twin (same card id, fetch succeeded) is a stale
    # walled re-fetch of a card we already hold the truth for — the
    # matched twin is the truth-carrier and the blocked twin must serve
    # nobody (its hardcoded job_req_id:"" defeats the foreign-jr guard,
    # and the old title_served-only filter missed the reqId-loser and
    # departed-foreign-jr variants).
    matched_card_ids = {_card_id(s2) for s2 in matched}
    free_blocked = [s2 for s2 in free_blocked
                    if _card_id(s2) not in matched_card_ids]
    blocked_join = join_by_title(free_blocked, unserved, company,
                                 req_dates=req_dates,
                                 req_locations=req_locations)
    return reqid_join, title_join, blocked_join


def join_population(signals: list[dict], postings: dict[str, str],
                     company: str,
                     req_dates: Optional[dict[str, str]] = None,
                     req_locations: Optional[dict[str, str]] = None
                     ) -> tuple[set, set]:
    """The current join population over `postings` ({req_id: title}):
    (matched_req_ids, unmatched_req_ids). Goes through compose_join —
    the SAME composition finish uses (design review finding 8 + S9-audit
    F1: independent composition re-served reqId-tier cards to title
    siblings; callers MUST pass req_dates and req_locations to keep
    parity)."""
    reqid_join, title_join, _blocked = compose_join(
        signals, postings, company, req_dates=req_dates,
        req_locations=req_locations)
    joined = dict(reqid_join)
    for rid, rec in title_join.items():
        joined.setdefault(rid, rec)
    return set(joined), set(postings) - set(joined)


# ── provider registry (the generalization seam) ─────────────────────────

PROVIDERS: dict[str, type] = {
    "linkedin": LinkedInSignalProvider,
}


def get_provider(name: str, cfg: Optional[Config] = None):
    cls = PROVIDERS.get(name)
    if not cls:
        raise ValueError(
            f"unknown corroboration provider {name!r} "
            f"(available: {sorted(PROVIDERS)})")
    return cls(cfg=cfg)   # cfg=None → provider defaults to Config()
