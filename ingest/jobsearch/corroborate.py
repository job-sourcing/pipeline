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
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from .config import Config
from .sources import linkedin_guest
from .sources.base import fetch_text
from .sources.linkedin_guest import (
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


class CorroborationBlocked(RuntimeError):
    """Raised ONLY when the provider wall-blocks the very first index page
    (nothing gained, caller marks the phase blocked). Mid-flow failures
    degrade gracefully instead — see fetch_signals' no-raise contract."""


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


class LinkedInSignalProvider:
    """Signals via the LinkedIn guest endpoint (no auth)."""

    name = "linkedin"

    def __init__(self, cfg: Optional[Config] = None):
        # live callers (board_dump) pass no cfg — default to the real one;
        # None would crash fetch_text (cfg.http_timeout_s) — regression
        # pinned in test_corroborate.py::TestProviderDefaults
        self.cfg = cfg or Config()

    # ── index phase ────────────────────────────────────────────────────
    def index_cards(self, company: str, location: str = "United States",
                    max_pages: int = 10, max_cards: int = 100,
                    start_offset: int = 0) -> tuple[list[dict], int, bool]:
        """Paginate the guest search for `company` postings.

        B2-safe: advances `start` by the number of cards each page actually
        returned. Returns (cards, next_offset, exhausted) — NEVER raises on
        page N>0 failure (partial results + resume offset instead, so a
        mid-index wall keeps prior progress); raises only if page 0 itself
        is blocked (caller marks the whole phase blocked).
        """
        variants = _company_variants(company)
        seen: set[str] = set()
        cards: list[dict] = []
        offset = start_offset
        exhausted = False
        for _page in range(max_pages):
            url = f"{SEARCH_URL}?{urlencode({
                'keywords': company, 'location': location,
                'start': offset, 'sortBy': 'DD'})}"
            try:
                html = fetch_text(url, cfg=self.cfg)
            except Exception as exc:
                if not cards and offset == start_offset and _page == 0:
                    raise CorroborationBlocked(
                        f"linkedin index blocked at offset 0: "
                        f"{type(exc).__name__}: {exc}") from exc
                # partial: return what we have + resume offset
                return cards, offset, exhausted
            page_cards = _parse_search_results(html)
            if not page_cards:
                exhausted = True
                break
            for card in page_cards:
                if card["id"] in seen:
                    continue
                if card["company"].strip().lower() not in variants:
                    continue    # keyword noise; keep company cards only
                seen.add(card["id"])
                cards.append(card)
            offset += max(len(page_cards), 1)
            if len(cards) >= max_cards:
                break
            time.sleep(_INDEX_PAUSE_S)
        return cards[:max_cards], offset, exhausted

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
        """
        out: list[dict] = []
        consecutive_blocked = 0
        circuit_open = False
        for card in cards:
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
                    rec = {
                        "linkedin_job_id": card["id"],
                        "linkedin_url": card["url"],
                        "title": card["title"],
                        "company": card["company"],
                        "location": card["location"],
                        "linkedin_posted_date": card.get("date", "")[:10],
                        "num_applicants": detail.get("num_applicants"),
                        "applicants_label": detail.get(
                            "applicants_label", ""),
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

def join_by_req_id(signals: list[dict],
                   req_ids: set[str]) -> dict[str, dict]:
    """Join corroboration records to postings by exact requisition id.
    Returns {req_id: signal_record} — only exact reqId matches.

    Blocked records never join (S7-F2, fixing the contract the vacuous
    test_blocked_never_joins used to paper over): a blocked fetch means
    we have NO signal for that card — even a stale job_req_id recorded
    before the wall must not surface as corroboration. Callers that want
    postings whose card fetch was walled to read `blocked` (phase_finish's
    B5 status) pass the blocked records through join_by_title instead,
    where the record's status IS the payload and num_applicants is None.
    """
    joined: dict[str, dict] = {}
    for rec in signals:
        if rec.get("status") == STATUS_BLOCKED:
            continue
        rid = (rec.get("job_req_id") or "").strip()
        if rid and rid in req_ids and rid not in joined:
            joined[rid] = rec
    return joined


def join_by_title(signals: list[dict], postings: dict[str, str],
                  company: str,
                  req_dates: Optional[dict[str, str]] = None
                  ) -> dict[str, dict]:
    """Fallback join: normalized title (+company) match, GREEDY 1:1.

    postings: {req_id: title}; req_dates (optional): {req_id: ISO date}
    for proximity disambiguation. Returns {req_id: signal_record}.

    1:1 contract (audit S7-A2 finding B): one LinkedIn signal describes ONE
    LinkedIn posting — copying it onto every same-key posting stamped one
    card's applicant count onto 31 different reqs. Greedy assignment: all
    (card, req) candidate pairs sharing a job_key are ranked by date
    proximity (|card date − req date|; unknown dates sort last), then
    newer card first; each card and each req is assigned at most once.
    Unassigned reqs are simply absent (caller derives no_match)."""
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
    # (proximity asc, card-date desc, card_id, req_id)
    by_key: dict[str, list[dict]] = {}
    for rec in signals:
        key = job_key(rec["title"], rec["company"])
        if key:
            by_key.setdefault(key, []).append(rec)
    rec_by_pair: dict[tuple, dict] = {}
    for rid, title in postings.items():
        key = job_key(title, company)
        for rec in by_key.get(key, []):
            cd = str(rec.get("linkedin_posted_date") or "")
            rd = (req_dates or {}).get(rid) or ""
            cid = str(rec.get("linkedin_job_id")
                      or rec.get("id") or id(rec))   # unique fallback
            rec_by_pair[(_proximity(cd, rd), cd, cid, rid)] = rec
    def _ord(d: str) -> int:
        try:
            return _date.fromisoformat(d[:10]).toordinal()
        except (ValueError, TypeError):
            return 0

    joined: dict[str, dict] = {}
    used_cards: set[str] = set()
    for key in sorted(rec_by_pair,
                      key=lambda k: (k[0], -_ord(k[1]), k[2], k[3])):
        rid, card_id = key[3], key[2]
        if rid in joined or card_id in used_cards:
            continue
        joined[rid] = rec_by_pair[key]
        used_cards.add(card_id)
    return joined


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
