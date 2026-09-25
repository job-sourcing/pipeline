#!/usr/bin/env python3
"""Board-watch — daily GHA watch over Workday CXS boards (durable pattern #3).

The productized watch layer on top of scripts/board_dump.py (design-board-v2
.md D4): the dump is a one-shot exhaustive snapshot; the WATCH is the daily
diff engine that turns it into an alerting pipeline. Reference instances of
the durable pattern: scripts/gha_drain.py (drain) + gha_name_backfill.py.

Flow per watch (config: ingest/data/board_watch/config.json — one entry per
board; a new company is ONE JSON line):
  1. LIST current postings — server-facet filtered (country/timeType),
     total-driven pagination, dedup, wrap-guards (the board_dump list
     machinery, in-memory). ~72 pages ≈ 60s for NVIDIA US full-time.
  2. DIFF vs state ({label}.state.jsonl): new = current − state,
     gone = state − current. B1 GUARD: gone is computed ONLY when the list
     completed — a partial list never marks postings as removed.
  2b. REPOST DETECT (S8-E2, audit/findings-recency.md §7/§8): postedOn
      labels imply a posting date (Pacific run_date − bucket); a forward
      jump >1d on an reqId present in BOTH state and list = Workday reset
      the posting (repost — labels and startDate move together; 12.7% of
      measurable rows reset within 4 days of watch history). Bounded R2
      detail re-fetch (≤5/leg) confirms via the moved startDate; events →
      {label}.reposts.jsonl; REPOSTED section in the digest. FP guards:
      "30 Days"→"30+ Days" is normal aging; OPEN(30+)→small bucket IS a
      reset; unparseable labels never flag.
  3. ENRICH new postings only, bounded (DETAILS_MAX/run): full detail
     payload (description, startDate, ALL locations) + LinkedIn
     corroboration (bounded index over recent cards + signals for matched)
     — the same v2 field quality as the dump, appended to
     {label}.newposts.jsonl (the durable "new postings" feed).
  4. STATE update: new lines appended, compacted rewrite (first_seen kept,
     last_seen=today for all still-current postings).
  5. ALERT: digest → appended to {label}.alerts.log (committed — durable
     alert history, works with zero configured notification channels) +
     $GITHUB_STEP_SUMMARY (the Actions UI) + Telegram via
     alerts.send_telegram when TELEGRAM_BOT_TOKEN/CHAT_ID ever appear in
     ingest/.env (never raises; additive-only by design).

Budget guards (the 2026-09-09 GHA-burn lesson — SKILL §A3):
  - DETAILS_MAX per run (default 40) — the enrich backlog self-retriggers
    only while WATCH_RESULT=backlog, and the chain is capped at
    LEGS_MAX_PER_DAY legs/day (legs meta is date-keyed; excess work waits
    for tomorrow's cron — never an unbounded retrigger loop).
  - BUDGET_SECONDS wall-clock guard (default 240) — stop enriching in an
    orderly way, checkpoint state, report backlog.

Machine-readable result (for the workflow):
  - appends WATCH_RESULT=complete|backlog|failed to $GITHUB_ENV
    (egress_blocked watches do NOT fail the run — declared in config)
  - always exits 0 (the workflow branches on WATCH_RESULT, not rc)

Seeding (B3, review addendum): --seed-from <list.jsonl> [--first-seen DATE]
imports a full board_dump list into the state so the FIRST watch run does
not alert on ~1,400 pre-existing postings.

Usage:
  python3 scripts/gha_board_watch.py                      # all watches
  python3 scripts/gha_board_watch.py --label nvidia_us_fulltime
  python3 scripts/gha_board_watch.py --seed-from ingest/data/workday/\
nvidia_us_fulltime.list.jsonl --first-seen 2026-09-10
Env knobs: WATCH_BUDGET_SECONDS, WATCH_DETAILS_MAX, WATCH_LEGS_MAX_PER_DAY,
  WATCH_LI_INDEX_PAGES, WATCH_DETAIL_SLEEP, WATCH_LIST_SLEEP,
  WATCH_REPOST_REFETCH_MAX, WATCH_REPOST_REFETCH_SLEEP.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo         # stdlib tz (tzdata on Linux/GHA)
except ImportError:                       # pragma: no cover — py<3.9
    ZoneInfo = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.config import Config, load_config  # noqa: E402
from jobsearch.htmltext import html_to_text  # noqa: E402
from jobsearch.sources import site_boards, workday  # noqa: E402
from jobsearch import corroborate  # noqa: E402

WATCH_DIR = REPO / "ingest" / "data" / "board_watch"
CONFIG_PATH = WATCH_DIR / "config.json"

# ── budget guards (env-overridable) ──────────────────────────────────────
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


BUDGET_SECONDS = _int_env("WATCH_BUDGET_SECONDS", 240)
DETAILS_MAX = _int_env("WATCH_DETAILS_MAX", 40)
LEGS_MAX_PER_DAY = _int_env("WATCH_LEGS_MAX_PER_DAY", 6)
LI_INDEX_PAGES = _int_env("WATCH_LI_INDEX_PAGES", 10)

# S9 C5 (design-s9-titlesearch.md): targeted title-search fallback for
# new postings the daily-window index can't match (the window sees only
# the ~100 most recent cards; the S9 probe measured 62.5% of no_match
# reqs' cards are findable by exact-title search). Run-level cap shared
# by the MAIN corroborate call ONLY — the cross-post lag pass never
# title-searches (corroborate_new runs twice per watch run; a stateless
# fallback would double-search misses daily — design review finding 3).
TITLE_SEARCH_MAX = _int_env("WATCH_TITLE_SEARCH_MAX", 40)
RECORROBORATE_DAYS = _int_env("WATCH_RECORROBORATE_DAYS", 2)
def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


DETAIL_SLEEP = _float_env("WATCH_DETAIL_SLEEP", 0.25)
LIST_SLEEP = _float_env("WATCH_LIST_SLEEP", 0.2)

# ── S8-E2 repost-detector knobs (audit/findings-recency.md §7/§8) ───────
# R2 bounded detail re-fetch: at most REPOST_REFETCH_MAX detail calls per
# leg for flagged rows, spaced REPOST_REFETCH_SLEEP seconds (politeness —
# the audit's live verification used 2s spacing).
REPOST_REFETCH_MAX = _int_env("WATCH_REPOST_REFETCH_MAX", 5)
REPOST_REFETCH_SLEEP = _float_env("WATCH_REPOST_REFETCH_SLEEP", 1.5)

OPEN_BUCKET = -1     # "Posted 30+ Days Ago" — censored bucket (age ≥31d,
                    # exact age unknowable from the label)

_digest_char_limit = 3500   # Telegram hard cap is 4096; leave headroom


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_jsonl(path: Path, recs: list[dict]) -> None:
    if not recs:
        return
    with open(path, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    # S13: split("\n") not splitlines() — U+2028/U+2029 in CJK JSON
    # strings are legal JSON but splitlines() shreds the records.
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:                       # corrupt-tail tolerance (crash mid-append)
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── step 1: list (the SHARED CXS primitive — S7-B1 SA-1: this loop was
# copy #3, and the timeType-facet omission shipped from that drift) ──────
def current_postings(board_spec: str, country: str, time_type: str,
                     cfg: Config) -> tuple[dict[str, dict], bool, bool]:
    """One full listing pass → {reqId: row} + complete flag +
    country_client flag (B1: the diff only trusts 'gone' when the
    listing completed naturally).

    S13: for boards WITHOUT a country facet the listing returns the
    FULL GLOBAL board (client_filter=False — the token predicate
    undercounts city-only dialects); the caller classifies each row
    via the enrichment detail's jobPostingInfo.country instead."""
    try:
        rows, meta = site_boards.list_board(
            board_spec, country=country or None,
            time_type=time_type or None, cfg=cfg,
            sleep_s=LIST_SLEEP, progress_every=20,
            progress_label="watch", client_filter=False)
    except ValueError as exc:
        print(f"[watch] facet error on {board_spec}: {exc}", file=sys.stderr)
        return {}, False, False
    except Exception as exc:                    # page-0 failure
        print(f"[watch] list page-0 failed on {board_spec}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    return rows, bool(meta.get("complete")), \
        bool(meta.get("country_client"))


# ── step 3: enrich new postings (detail + LinkedIn signals, bounded) ─────
def _locations_of(info: dict, row: dict) -> list[str]:
    locs: list[str] = []
    if info:
        locs.append(info.get("location") or "")
        locs += [str(x) for x in (info.get("additionalLocations") or [])]
    locs = [x for x in locs if x]
    if not locs:
        locs = [row.get("locationsText") or ""]
    return [x for x in locs if x]


def enrich_new(new_rows: list[dict], board_spec: str, company: str,
               cfg: Config, deadline: float,
               prior_feed: Optional[dict] = None,
               country: Optional[str] = None,
               time_type: Optional[str] = None) -> list[dict]:
    """Bounded detail + corroboration pass over the new postings.

    Returns enriched records for {label}.newposts.jsonl (v2 field quality:
    full locations, startDate, clean-text description, signals). Enrichment
    is best-effort — an unreachable detail still yields a record with
    error status; the record itself is what makes the alert feed durable.
    board_spec-driven dispatch (S13): ats: specs serve from the
    adapter's cached single board fetch (zero extra network); workday
    specs hit the CXS detail endpoint per row, unchanged.
    S15 seam threading: country/time_type reach the adapter so the
    greenhouse ladder verdict produces the detail's country field
    (list/detail agree by construction); other adapters ignore them.
    """
    is_site = site_boards.is_site_spec(board_spec)
    if not is_site:
        board = workday.parse_board(board_spec)
    workday._DETAIL_SLEEP_S = DETAIL_SLEEP
    out: list[dict] = []
    todo = new_rows[:DETAILS_MAX]
    for i, r in enumerate(todo, 1):
        prev = (prior_feed or {}).get(r["reqId"]) or {}
        rec: dict = {
            "reqId": r["reqId"], "title": r.get("title") or "",
            "company": company,
            # S9-audit D2 P2: a retry/recovery must not reset the feed's
            # tenure field — keep the prior record's first sighting;
            # today is only for genuinely-new reqIds (the feed is
            # append-only and consumers read the LAST line per reqId).
            "first_seen": prev.get("first_seen")
            or date.today().isoformat(),
            "locationsText": r.get("locationsText") or "",
            "postedOn": r.get("postedOn") or "",
            "url": r.get("url") or "",
        }
        if is_site:
            payload = site_boards.detail_payload(board_spec,
                                                 r.get("externalPath", ""),
                                                 cfg, country=country,
                                                 time_type=time_type)
        else:
            payload = workday.detail_payload(
                board, r.get("externalPath", ""), cfg)
        info = (payload or {}).get("jobPostingInfo") or {}
        if info:
            rec["locations"] = _locations_of(info, r)
            rec["startDate"] = info.get("startDate") or ""
            rec["description"] = html_to_text(info.get("jobDescription") or "")
            rec["timeType"] = info.get("timeType") or ""
            rec["hiringOrg"] = (payload.get("hiringOrganization") or {}).get("name")
            rec["externalUrl"] = info.get("externalUrl") or rec["url"]
            # S13: the authoritative country from the detail payload —
            # the classifier for client-country boards (the watch's US
            # membership = state ∪ detail-classified-US new rows).
            rec["country"] = workday.detail_country(payload)
        else:
            rec["error"] = "detail_unreachable"
            # 3-strike (B6, S7-V1): a transient detail outage must not
            # permanently degrade the record — attempts accumulate in the
            # feed; the recovery query retries error rows until 3.
            rec["attempts"] = int(prev.get("attempts") or 0) + 1
        out.append(rec)
        if time.monotonic() > deadline:
            print(f"[watch] budget stop at {i}/{len(todo)} enrichments",
                  flush=True)
            break
        time.sleep(DETAIL_SLEEP)
    return out


def _window_match(provider, cards: list[dict], new_rows: list[dict],
                  company: str) -> tuple[dict, set]:
    """Daily-window match (S9 C5 refactor — previously inline in
    corroborate_new): job_key match of index cards → new postings,
    detail-fetch of matched cards, GREEDY 1:1 with reqId-exact override
    and the foreign-reqId-card-serves-nobody policy. Returns
    ({reqId: signal}, served_card_ids). The S9 title-search fallback
    runs AFTER this on whatever it left unmatched."""
    # match cards → new postings on the normalized title key (the LinkedIn
    # index covers the ~100 most recent cards; new postings sort to the top
    # under sortBy=DD — the right window for a daily watch)
    from jobsearch.sources.linkedin_guest import job_key
    new_by_key: dict[str, list[str]] = {}
    for r in new_rows:
        k = job_key(r.get("title") or "", company)
        if k:
            new_by_key.setdefault(k, []).append(r["reqId"])
    matched: list[tuple[dict, list[str]]] = []
    for c in cards:
        k = job_key(c.get("title") or "", c.get("company") or "")
        reqs = new_by_key.get(k)
        if reqs:
            matched.append((c, reqs))
    if not matched:
        print(f"[watch] 0/{len(new_rows)} new postings cross-posted to "
              f"LinkedIn (of {len(cards)} recent cards)", flush=True)
        return {}, set()
    signals = provider.fetch_signals([c for c, _ in matched][:DETAILS_MAX])
    sig_by_card = {str(s.get("linkedin_job_id")): s for s in signals
                   if s.get("status") == "matched"}
    # GREEDY 1:1 (S7-V1: this could fan out one card's applicants onto a
    # whole title family — the same N:1 the dump's join had): each card
    # serves at most ONE req; reqId-exact wins when the extracted reqId
    # is actually among THIS input's postings (no foreign reqIds).
    input_rids = {r["reqId"] for r in new_rows}
    out: dict[str, dict] = {}
    served_cards: set[str] = set()
    for c, reqs in matched:
        s = sig_by_card.get(str(c["id"]))
        if not s or str(c["id"]) in served_cards:
            continue
        jr = s.get("job_req_id")
        if jr:
            if jr in input_rids and jr not in out:
                out[jr] = s                   # reqId-exact
                served_cards.add(str(c["id"]))
            # jr NOT in input rids: this LinkedIn posting IS a different
            # requisition that merely shares the title family — serving
            # our rows with it would misattribute; skip the card entirely
            continue
        for rid in reqs:                      # title fallback, 1:1
            if rid in input_rids and rid not in out:
                out[rid] = s
                served_cards.add(str(c["id"]))
                break
    print(f"[watch] corroborated {len(out)}/{len(new_rows)} new postings "
          f"({len(cards)} cards indexed)", flush=True)
    return out, served_cards


def _fallback_serve(out: dict, served_cards: set, rid: str,
                   card: dict, s: dict) -> None:
    """Assign one title-search-fallback card to a req (S9-audit D1 P3:
    stamp the provenance — newposts.jsonl must be self-describing;
    window-path matches are deliberately NOT stamped)."""
    s["match_method"] = "titleSearchFallback"
    out[rid] = s
    served_cards.add(str(card["id"]))


def corroborate_new(new_rows: list[dict], company: str, cfg: Config,
                    deadline: float,
                    title_search_budget: Optional[list] = None
                    ) -> dict[str, dict]:
    """LinkedIn signals for the new postings (bounded index + bounded
    signals; circuit-breakered inside the provider). Returns
    {reqId: signal_record} — reqId-exact when the description carries one,
    title-match otherwise. A blocked index is a no-op (B5: recorded, not
    raised — next run retries).

    S9 C5: `title_search_budget` = [remaining] (a mutable cell shared at
    RUN level by the main call; the lag pass passes nothing → no title
    search). After the daily-window match, unmatched new postings get a
    targeted exact-title guest search (`corroborate.search_title`);
    verbatim hits are detail-fetched and assigned under the same policy
    FAMILY as the window path: reqId-exact, 1:1 per card, and a card
    whose job_req_id names one of THIS batch's postings serves that req
    (window parity, S9-audit D1 P2) while reqIds outside the batch serve
    nobody. Every executed search sleeps TITLE_SEARCH_PAUSE_S (B3/D1
    P2); fallback-assigned signals carry match_method="titleSearchFallback"
    so the feed is self-describing (window matches carry no stamp)."""
    if not new_rows:
        return {}
    if time.monotonic() > deadline:
        print("[watch] budget exhausted before corroboration — skipped",
              flush=True)
        return {}
    provider = corroborate.get_provider("linkedin", cfg=cfg)
    try:
        cards, _, _ = provider.index_cards(
            company=company, location="United States",
            max_pages=LI_INDEX_PAGES, max_cards=LI_INDEX_PAGES * 10,
            start_offset=0)
    except corroborate.CorroborationBlocked as exc:
        print(f"[watch] LinkedIn index blocked: {exc}", file=sys.stderr)
        return {}
    out: dict[str, dict] = {}
    served_cards: set[str] = set()
    # S9 C5: the two early-exit paths below (no cards / no window
    # matches) must FALL THROUGH to the title-search fallback, not
    # return — the fallback is exactly for postings the window can't
    # see. Both paths leave out={} and flow to the fallback block.
    if cards:
        out, served_cards = _window_match(
            provider, cards, new_rows, company)
    else:
        print(f"[watch] LinkedIn index returned 0 cards", flush=True)
    # ── S9 C5: targeted title-search fallback ─────────────────────────
    # The daily window (~100 most recent cards, sortBy=DD) misses most
    # niche-title cross-posts. For unmatched new postings: one exact-
    # title guest search each (verbatim predicate, GT-calibrated),
    # detail-fetch the hit, assign under the same policy. Bounded by the
    # run-level budget cell + wall-clock deadline + circuit breaker.
    if title_search_budget is None:
        return out
    unmatched = [r for r in new_rows if r["reqId"] not in out]
    if not unmatched or title_search_budget[0] <= 0:
        return out
    input_rids = {r["reqId"] for r in new_rows}
    n_ts = 0
    consecutive_blocked = 0
    for r in unmatched:
        if r["reqId"] in out:
            continue     # served mid-loop by a sibling's search (window
                         # parity) — don't burn budget re-searching it
        if title_search_budget[0] <= 0:
            break
        if time.monotonic() > deadline:
            print(f"[watch] budget stop in title-search at {n_ts} "
                  f"searches", flush=True)
            break
        if consecutive_blocked >= corroborate.TITLE_SEARCH_BREAKER:
            print(f"[watch] title-search circuit breaker after "
                  f"{consecutive_blocked} consecutive blocks", flush=True)
            break
        title = (r.get("title") or "").strip()
        if not title:
            continue
        loc = corroborate.row_search_location(r)
        hits, exc = provider.search_title(
            title, loc, {str(c["id"]) for c in cards}, company=company)
        title_search_budget[0] -= 1
        n_ts += 1
        # S9-audit B3/D1 P2: EVERY executed search sleeps — the pause used
        # to sit at the bottom of the jr-less serve path only, so no-hit /
        # blocked / foreign-hit searches ran back-to-back against the
        # walled guest endpoint. The pre-loop skips above (empty title,
        # budget, deadline, breaker) consume nothing and stay pause-free.
        time.sleep(corroborate.TITLE_SEARCH_PAUSE_S)
        if exc is not None:
            consecutive_blocked += 1
            continue
        consecutive_blocked = 0
        if not hits:
            continue
        rid = r["reqId"]
        # S9-audit D1 P2: iterate the hit list — hits[0] can be a
        # sibling requisition's card, and the req's OWN jr-exact card at
        # hits[1] used to be discarded without a fetch. Serve the first
        # jr-exact hit; else hold the first unserved jr-less hit (a
        # jr-exact card may follow); the served_cards check runs BEFORE
        # the detail fetch (D1 P3: no duplicate fetch of served cards).
        # One detail fetch per TRIED hit, bounded by the page size (10).
        jrless: Optional[tuple] = None
        for card in hits:
            if str(card["id"]) in served_cards:
                continue
            try:
                sigs = provider.fetch_signals([card])
            except Exception:
                continue
            s = next((x for x in sigs if x.get("status") == "matched"),
                     None)
            if not s:
                continue
            jr = s.get("job_req_id")
            if jr == rid:              # this req's OWN card — best possible
                _fallback_serve(out, served_cards, rid, card, s)
                break
            if jr:
                # names ANOTHER requisition: window-path parity (S9-audit
                # D1 P2) — if that req is one of THIS batch's postings and
                # still unserved, the card serves IT (the window does
                # exactly this); a reqId outside the batch serves nobody
                # (misattribution guard).
                if jr in input_rids and jr not in out:
                    _fallback_serve(out, served_cards, jr, card, s)
                continue
            if jrless is None and rid not in out:
                jrless = (card, s)     # hold: keep scanning for jr-exact
        if jrless is not None and rid not in out:
            card, s = jrless           # no jr-exact hit — title serve, 1:1
            _fallback_serve(out, served_cards, rid, card, s)
    if n_ts:
        n_ts_matched = sum(1 for r in unmatched if r["reqId"] in out)
        print(f"[watch] title-search fallback: +{n_ts_matched} matches "
              f"from {n_ts} searches "
              f"(budget {title_search_budget[0]} left)", flush=True)
    return out


# ── step 2b: repost / days-on-market detector (S8-E2, ────────────────────
# audit/findings-recency.md §7/§8) ────────────────────────────────────────
# CONTEXT (verified by the S8-C research): NVIDIA's Workday board resets
# `startDate` and `postedOn` labels TOGETHER on repost — 28 label
# regressions in just 4 days of watch history (12.7% of measurable rows;
# live-confirmed 8/8 with startDate moves up to +244 days). The watch
# diffed list→state but never noticed. `postedOn` is a bucketed label
# computed against PACIFIC calendar dates.

_PT_ZONE = "America/Los_Angeles"
_POSTED_OPEN_RE = re.compile(r"30\s*\+\s*days\s+ago", re.IGNORECASE)
_POSTED_NDAYS_RE = re.compile(r"(\d+)\s+days?\s+ago", re.IGNORECASE)


def _to_pt_date(now_utc: datetime) -> date:
    """Pacific calendar date of a UTC instant (pure; testable).

    The timezone rule is THE subtle one (audit §7.4, proven empirically):
    `postedOn` labels are computed by Workday against Pacific calendar
    dates — over a 03:12Z (PT 09-09) → 10:35Z (PT 09-13) window every
    normal row advanced exactly +4 label buckets, where a UTC-date
    assumption would mispredict +3 and flag 516 normal rows. The
    production cron runs 06:45Z — ALWAYS before Pacific midnight
    rollover — so run_date(UTC) − label is off by exactly 1 day, every
    single day. NEVER derive implied dates from UTC run dates or from
    git/commit timestamps: convert the live run clock to
    America/Los_Angeles first (stdlib zoneinfo, no new dep)."""
    if ZoneInfo is not None:
        try:
            return now_utc.astimezone(ZoneInfo(_PT_ZONE)).date()
        except Exception:               # pragma: no cover — missing tzdata
            pass
    # Fallback (no zoneinfo/tzdata): US DST rules — PT = UTC−8 (PST) /
    # UTC−7 (PDT), DST from the 2nd Sunday of March to the 1st Sunday of
    # November. Exact except on the 2 transition nights per year.
    d = now_utc.date()

    def _nth_sunday(month: int, n: int) -> date:
        first = date(d.year, month, 1)
        return first.replace(day=1 + (6 - first.weekday()) % 7
                             + 7 * (n - 1))

    dst = _nth_sunday(3, 2) <= d < _nth_sunday(11, 1)
    return (now_utc - timedelta(hours=7 if dst else 8)).date()


def _pt_run_date() -> date:
    """Pacific calendar date of THIS run (the only implied-date basis)."""
    return _to_pt_date(datetime.now(timezone.utc))


def parse_posted_on(label: Optional[str]) -> Optional[int]:
    """Conservative Workday `postedOn` parser → numeric day bucket.

    "Posted Today"→0 · "Posted Yesterday"→1 · "Posted N Days Ago"→N ·
    "Posted 30+ Days Ago"→OPEN_BUCKET (censored: age ≥31d, the exact
    bucket is unknowable from the label). Anything else → None
    (unparseable — callers must skip the row, NEVER guess)."""
    s = (label or "").strip().lower()
    if not s:
        return None
    if s in ("posted today", "today"):
        return 0
    if s in ("posted yesterday", "yesterday"):
        return 1
    if _POSTED_OPEN_RE.search(s):        # checked before the N-days form
        return OPEN_BUCKET
    m = _POSTED_NDAYS_RE.search(s)
    if m:
        return int(m.group(1))
    return None


def implied_post_date(bucket: Optional[int], run_date: date
                      ) -> Optional[date]:
    """Label-implied posting date = Pacific run date − bucket days.

    OPEN (30+) rows are censored → None: their resets are only catchable
    when they EXIT the bucket downward (impossible without a reset) or via
    a startDate move."""
    if bucket is None or bucket < 0:
        return None
    return run_date - timedelta(days=bucket)


def _iso_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        return None


def _prev_implied_date(p: dict, old_bucket: int) -> Optional[date]:
    """Previous implied post date for a numeric old bucket.

    Preferred source: `implied_post_date` stored by the previous run
    (computed against THAT run's Pacific date — exact, no re-derivation).
    Legacy state rows (pre-S8-E2) lack the field → fall back to
    last_seen − bucket; last_seen is the previous run's UTC date and the
    06:45Z cron runs pre-PT-rollover, so the fallback can be up to 1 day
    NEWER than the true PT-based date — strictly conservative (needs a
    >2d forward jump to flag, never a false positive)."""
    v = _iso_date(p.get("implied_post_date"))
    if v is not None:
        return v
    ls = _iso_date(p.get("last_seen"))
    if ls is not None and old_bucket >= 0:
        return ls - timedelta(days=old_bucket)
    return None


def _repost_event(rid: str, row: dict, p: dict, run_date: date,
                  label_reset: bool, label_from: str, label_to: str
                  ) -> dict:
    """One repost event (internal shape: the canonical record fields for
    {label}.reposts.jsonl — see _repost_log_record — plus internal keys
    stripped before the log write)."""
    old_b = parse_posted_on(p.get("last_postedOn"))
    new_b = parse_posted_on(row.get("postedOn"))
    prev_impl = _prev_implied_date(p, old_b) if old_b is not None else None
    new_impl = implied_post_date(new_b, run_date)
    return {
        "reqId": rid,
        "title": (row.get("title") or p.get("title") or ""),
        "detected_at": _now_iso(),
        "prev_postedOn": p.get("last_postedOn") or "",
        "new_postedOn": row.get("postedOn") or "",
        "prev_implied": prev_impl.isoformat() if prev_impl is not None else None,
        "new_implied": new_impl.isoformat() if new_impl is not None else None,
        "prev_startDate": (p.get("last_startDate") or "").strip(),
        "confidence": "label",
        # internal (never written to the event log):
        "externalPath": row.get("externalPath") or "",
        "label_reset": label_reset,
        "label_from": label_from,
        "label_to": label_to,
        "start_delta_days": None,
    }


def _start_date_delta(prev_sd: str, new_sd: str) -> Optional[int]:
    """+N days when the startDate moved LATER (a direct reset — the
    highest-confidence signal); 0 when unchanged; None when not
    comparable. A BACKWARD move should never happen (audit §7.3 data
    bug) — logged loudly, never a repost signal."""
    a, b = _iso_date(prev_sd), _iso_date(new_sd)
    if a is None or b is None:
        return None
    if b < a:
        print(f"[watch] DATA BUG: startDate moved BACKWARD "
              f"({prev_sd} → {new_sd}) — never a repost", file=sys.stderr)
        return None
    return (b - a).days


def detect_reposts(current: dict[str, dict], prior: dict[str, dict],
                   run_date: date) -> list[dict]:
    """R1: label-implied posting-date regression detector (audit §7.3 —
    pure list post-processing, zero extra requests).

    For reqIds present in BOTH the previous state and the new list, flag
    a REPOST when the implied post date jumps FORWARD by more than 1 day
    (new_implied > old_implied + 1d; the +1d absorbs label-boundary
    rounding / a 1-day label stall — Workday resets postedOn and
    startDate together, so a label that gets YOUNGER while the calendar
    advances means the posting date moved).

    False-positive guards:
    - numeric → OPEN ("Posted 30 Days Ago" → "Posted 30+ Days Ago") is
      NORMAL AGING (closed bucket 30 → open ≥30): never a regression.
    - OPEN → OPEN: both censored, nothing comparable.
    - OPEN → small bucket IS a regression by definition — the dramatic
      repost case ("Posted 30+ Days Ago" → "Posted 2 Days Ago"; audit
      §4's 9 headline resets). prev_implied stays None (censored).
    - unparseable label on either side ⇒ row skipped, never guessed.
    - no derivable previous implied date (legacy row without last_seen)
      ⇒ skipped."""
    flags: list[dict] = []
    for rid, row in current.items():
        p = prior.get(rid)
        if p is None:
            continue                     # new posting — nothing to compare
        old_b = parse_posted_on(p.get("last_postedOn"))
        new_b = parse_posted_on(row.get("postedOn"))
        if old_b is None or new_b is None:
            continue                     # unparseable — skip, never flag
        if new_b == OPEN_BUCKET:
            continue                     # → OPEN is normal aging (30 → 30+)
        if old_b == OPEN_BUCKET:
            # OPEN → small bucket: regression by definition
            flags.append(_repost_event(rid, row, p, run_date,
                                       label_reset=True, label_from="30+d",
                                       label_to=f"{new_b}d"))
            continue
        old_impl = _prev_implied_date(p, old_b)
        new_impl = implied_post_date(new_b, run_date)
        if old_impl is None:
            continue                     # nothing to compare against
        if new_impl > old_impl + timedelta(days=1):
            flags.append(_repost_event(rid, row, p, run_date,
                                       label_reset=True,
                                       label_from=f"{old_b}d",
                                       label_to=f"{new_b}d"))
    return flags


def detect_start_date_moves(current: dict[str, dict], prior: dict[str, dict],
                            new_start_dates: dict[str, str],
                            run_date: date) -> list[dict]:
    """startDate channel (audit §7.3): a startDate that moved LATER on the
    same reqId is a DIRECT reset event — the highest-confidence signal.

    Fires whenever the state has a `last_startDate` and a new detail/list
    observation provides one for the same reqId (this run's
    backlog-recovery enrichment, or a list row that carries startDate).
    Equal dates are not events; an earlier date is a data bug (logged
    loudly, never flagged)."""
    events: list[dict] = []
    for rid, new_sd in (new_start_dates or {}).items():
        p = prior.get(rid)
        row = current.get(rid)
        if p is None or row is None:
            continue
        delta = _start_date_delta(p.get("last_startDate") or "", new_sd)
        if not delta:                    # None (bug/uncomparable) or 0
            continue
        ev = _repost_event(rid, row, p, run_date, label_reset=False,
                           label_from="", label_to="")
        ev["new_startDate"] = str(new_sd).strip()
        ev["confidence"] = "high"
        ev["start_delta_days"] = delta
        events.append(ev)
    return events


def refetch_repost_details(flags: list[dict], board_spec: str,
                           cfg: Config, deadline: float
                           ) -> tuple[list[dict], dict[str, str]]:
    """R2: bounded detail re-fetch for R1-flagged rows (audit §7.3).

    ≤ REPOST_REFETCH_MAX (5) detail calls per leg, REPOST_REFETCH_SLEEP
    (1.5s) apart — captures the NEW startDate, converting label
    suspicions into measured reset events (magnitude +Nd) and feeding the
    state's `last_startDate` / `startDate_first`. Best-effort by design:
    an unreachable detail leaves the event at 'label' confidence; a
    budget stop leaves the remainder unconfirmed. Returns (events,
    {reqId: new_startDate})."""
    if not flags:
        return [], {}
    is_site = site_boards.is_site_spec(board_spec)
    if not is_site:
        board = workday.parse_board(board_spec)
    events = [dict(f) for f in flags]
    start_updates: dict[str, str] = {}
    fetched = 0
    for ev in events:
        if fetched >= REPOST_REFETCH_MAX:
            break                        # bounded: 5 detail calls max/leg
        if time.monotonic() > deadline:
            print(f"[watch] repost re-fetch budget stop after {fetched} "
                  f"of {len(events)} flagged", flush=True)
            break
        path = ev.get("externalPath") or ""
        if not path:
            continue
        fetched += 1
        if is_site:                     # S13 site dispatch (cached)
            payload = site_boards.detail_payload(board_spec, path, cfg)
        else:
            payload = workday.detail_payload(board, path, cfg)
        info = (payload or {}).get("jobPostingInfo") or {}
        new_sd = (info.get("startDate") or "").strip()
        if not new_sd:
            continue                     # detail unreachable — 'label' stays
        ev["new_startDate"] = new_sd
        delta = _start_date_delta(ev.get("prev_startDate") or "", new_sd)
        if delta:                        # moved LATER → direct reset
            ev["confidence"] = "high"
            ev["start_delta_days"] = delta
        start_updates[ev["reqId"]] = new_sd
        time.sleep(REPOST_REFETCH_SLEEP)  # politeness between detail calls
    return events, start_updates


def _merge_repost_events(events: list[dict], extra: list[dict]) -> list[dict]:
    """Merge the two detection channels into ONE event per reqId (the
    event log gets one line per repost per run). A startDate-move event
    for an already-flagged reqId upgrades it in place (confidence high,
    measured magnitude)."""
    by_rid: dict[str, dict] = {}
    for e in events:
        by_rid[e["reqId"]] = e
    for e in extra:
        base = by_rid.get(e["reqId"])
        if base is None:
            by_rid[e["reqId"]] = e
            continue
        base["new_startDate"] = e.get("new_startDate")
        base["confidence"] = "high"
        base["start_delta_days"] = e.get("start_delta_days")
        base["prev_startDate"] = (base.get("prev_startDate")
                                  or e.get("prev_startDate") or "")
    return list(by_rid.values())


_REPOST_LOG_KEYS = ("reqId", "title", "detected_at", "prev_postedOn",
                    "new_postedOn", "prev_implied", "new_implied",
                    "prev_startDate", "confidence")


def _repost_log_record(ev: dict) -> dict:
    """Canonical {label}.reposts.jsonl record (task S8-E2 / audit §7.1):
    internal detector keys (externalPath, label buckets, magnitude) are
    stripped; `new_startDate` is included only when a detail was actually
    observed this run."""
    rec = {k: ev.get(k) for k in _REPOST_LOG_KEYS}
    if ev.get("new_startDate"):
        rec["new_startDate"] = ev["new_startDate"]
    return rec


def _repost_magnitude(ev: dict) -> str:
    """Digest magnitude, e.g. 'label reset 21d→2d, startDate +244d'."""
    parts: list[str] = []
    if ev.get("label_reset"):
        parts.append(f"label reset {ev.get('label_from')}→"
                     f"{ev.get('label_to')}")
    d = ev.get("start_delta_days")
    if d:
        parts.append(f"startDate +{d}d")
    return ", ".join(parts) if parts else "unmeasured"


# ── step 4+5: state update + alert digest ────────────────────────────────
def format_digest(label: str, company: str, new_rows: list[dict],
                  enriched: list[dict], signals: dict[str, dict],
                  gone_rows: list[dict], current_count: int,
                  repost_events: Optional[list[dict]] = None) -> str:
    """Plain-text digest → alerts.log + job summary (Telegram sends HTML
    from the same lines; chunking keeps every channel under its cap)."""
    today = date.today().isoformat()
    lines = [
        f"{company} board-watch {today}: +{len(new_rows)} new, "
        f"-{len(gone_rows)} gone ({current_count} active "
        f"[{label}])"]
    enr = {e["reqId"]: e for e in enriched}
    for r in new_rows[:25]:         # digest keeps 25 (S7-A3 J: an unseeded
        e = enr.get(r["reqId"]) or {}   # first run must not send 35 chunks)
        s = signals.get(r["reqId"]) or {}
        loc = "; ".join(e.get("locations") or [r.get("locationsText") or "?"])
        extra = ""
        if s.get("num_applicants"):
            extra = f" — {s['num_applicants']} applicants"
        age = e.get("startDate") or r.get("postedOn") or ""
        url = e.get("externalUrl") or r.get("url") or ""
        lines.append(f"NEW {r.get('title')} | {loc} | {age}{extra} | {url}")
    if len(new_rows) > 25:
        lines.append(f"NEW … and {len(new_rows) - 25} more "
                     "(newposts.jsonl)")
    for r in gone_rows[:20]:        # digest keeps 20; full list in state
        lines.append(f"GONE {r.get('title')} "
                     f"(first seen {r.get('first_seen')})")
    if len(gone_rows) > 20:
        lines.append(f"GONE … and {len(gone_rows) - 20} more (state file)")
    # S8-E2: REPOSTED section (after NEW/GONE — same line format family:
    # title + magnitude, e.g. "Senior DFT Engineer (label reset 21d→2d,
    # startDate +244d)"). Digest keeps 10; full history in reposts.jsonl.
    if repost_events:
        lines.append(f"REPOSTED ({len(repost_events)})")
        for ev in repost_events[:10]:
            lines.append(f"REPOSTED {ev.get('title')} "
                         f"({_repost_magnitude(ev)})")
        if len(repost_events) > 10:
            lines.append(f"REPOSTED … and {len(repost_events) - 10} more "
                         "(reposts.jsonl)")
    return "\n".join(lines)


def _send_alerts(label: str, digest: str, cfg: Config) -> None:
    """Durable-first alerting: alerts.log always; job summary always;
    Telegram when configured (chunked, never raises)."""
    with open(WATCH_DIR / f"{label}.alerts.log", "a",
              encoding="utf-8") as lf:
        lf.write(digest + f"\n({_now_iso()})\n\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"```\n{digest}\n```\n")
    import html as _html
    from jobsearch import alerts
    for i in range(0, len(digest), _digest_char_limit):
        chunk = _html.escape(digest[i:i + _digest_char_limit])
        alerts.send_telegram(f"<pre>{chunk}</pre>", cfg)


# ── orchestration ────────────────────────────────────────────────────────
def _legs_bump(label: str) -> int:
    """Date-keyed leg counter — the retrigger cap (GHA-burn lesson).
    S18 review SEV-3 1: also tracks `egress_streak` (consecutive
    egress_blocked legs, cross-DATE persistent) for the escalation
    contract in run_watch."""
    meta = WATCH_DIR / f"{label}.legs.json"
    today = date.today().isoformat()
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        m = {}
    if m.get("date") != today:
        m = {"date": today, "legs": 0, "egress_streak": m.get("egress_streak", 0)}
    m["legs"] = int(m.get("legs", 0)) + 1
    _atomic_write(meta, json.dumps(m))
    return m["legs"]


_EGRESS_STREAK_CAP = 14      # ~2 weeks of daily blocked legs -> failed


def _egress_streak_bump(label: str) -> int:
    meta = WATCH_DIR / f"{label}.legs.json"
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        m = {}
    m["egress_streak"] = int(m.get("egress_streak", 0)) + 1
    _atomic_write(meta, json.dumps(m))
    return m["egress_streak"]


def _egress_streak_reset(label: str) -> None:
    meta = WATCH_DIR / f"{label}.legs.json"
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if m.get("egress_streak"):
        m["egress_streak"] = 0
        _atomic_write(meta, json.dumps(m))


def _validate_variant_uniqueness(watches: list[dict]) -> None:
    """S18 review SEV-3 2: a variant that names ANOTHER roster company
    (or a company that IS another watch's variant) would let that
    company's cards join this company's postings (the variant namespace
    is trust — one wrong declaration silently cross-joins). Fail LOUD
    at config-load time, before any fetch."""
    owners: dict[str, str] = {}

    def _claim(name: str, label: str) -> None:
        owner = owners.get(name)
        if owner is not None and owner != label:
            raise SystemExit(
                f"[watch] CONFIG ERROR: {name!r} is claimed by BOTH "
                f"{owner!r} and {label!r} (company/li_variants overlap) "
                f"— cross-company join risk; fix config.json")
        owners.setdefault(name, label)

    for w in watches:
        base = str(w.get("company") or "").strip().lower()
        if base:
            _claim(base, w["label"])
        for v in (w.get("li_variants") or []):
            v = str(v).strip().lower()
            if v:
                _claim(v, w["label"])


def _egress_blocked(w: dict, exc: Exception) -> bool:
    """S18 (watch runs #59/#60 + board-probe #2): job.xiaohongshu.com
    TLS-handshake-blocks the GHA US egress while serving the HK egress
    200 — a DECLARED egress restriction turns that network-class
    failure into 'egress_blocked' (loud, state untouched, auto-recovers
    if the block lifts) instead of red-failing every daily run. An
    HTTP verdict (406/404/5xx) is NEVER an egress block — verdicts
    still fail loudly; so does any undeclared board."""
    er = str(w.get("egress_restricted") or "").strip().lower()
    if not er:
        return False
    on_gha = bool(os.environ.get("GITHUB_ACTIONS"))
    if er in ("gha", "gha_us", "gha-us") and not on_gha:
        return False               # declared for the GHA egress only
    if isinstance(exc, urllib.error.HTTPError):
        return False               # server ANSWER, not an egress block
    return isinstance(exc, (urllib.error.URLError, TimeoutError,
                            ConnectionError, OSError, ssl.SSLError))


def run_watch(w: dict, cfg: Config) -> str:
    """One watch pass. Returns 'complete' | 'backlog' | 'failed' |
    'egress_blocked' (S18: a DECLARED egeo-restricted board whose
    network-class failure on this egress is loud-but-not-failed —
    state untouched, auto-recovers)."""
    label = w["label"]
    # S12 multi-company: per-company LI matching knowledge from the watch
    # config (card company variants + partitioned-index slice geography).
    # Library defaults stay NVIDIA-pilot-shaped; registered entries win.
    if w.get("li_variants"):
        corroborate.set_company_overrides(
            w.get("company", ""), variants=list(w["li_variants"]))
    if w.get("slice_locations"):
        corroborate.set_company_overrides(
            w.get("company", ""),
            slice_locations=list(w["slice_locations"]))
    state_path = WATCH_DIR / f"{label}.state.jsonl"
    leg = _legs_bump(label)
    print(f"[watch:{label}] leg {leg}/{LEGS_MAX_PER_DAY} today", flush=True)

    def _fail_summary(reason: str) -> None:
        """S7-A3 P1-1: a persistent failure must never run green — write
        the failure to the job summary AND the alerts log (durable)."""
        line = f"board-watch {label} FAILED: {reason}"
        print(f"[watch:{label}] {line}", file=sys.stderr, flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"\n**{line}**\n")
        with open(WATCH_DIR / f"{label}.alerts.log", "a",
                  encoding="utf-8") as lf:
            lf.write(f"{line}\n({_now_iso()})\n\n")

    try:
        current, list_complete, country_client = current_postings(
            w["board"], w.get("country", ""), w.get("time_type", ""), cfg)
    except Exception as exc:
        if _egress_blocked(w, exc):
            streak = _egress_streak_bump(label)
            note = (f"list fetch unreachable from THIS egress "
                    f"(declared egress_restricted="
                    f"{w.get('egress_restricted')!r}; "
                    f"{type(exc).__name__}: {exc}) — EGRESS-BLOCKED, "
                    f"state untouched; streak {streak}/"
                    f"{_EGRESS_STREAK_CAP}")
            print(f"[watch:{label}] {note}", file=sys.stderr, flush=True)
            with open(WATCH_DIR / f"{label}.alerts.log", "a",
                      encoding="utf-8") as lf:
                lf.write(f"board-watch {label} EGRESS-BLOCKED: {note}"
                         f"\n({_now_iso()})\n\n")
            # S18 review SEV-3 1: a domain-level death (NXDOMAIN /
            # connection-refused / TLS) on a declared board must
            # escalate — after the cap, egress_blocked becomes failed
            # (a permanently dead board never runs green forever)
            if streak >= _EGRESS_STREAK_CAP:
                _fail_summary(
                    f"EGRESS-BLOCKED STREAK CAP HIT ({streak} legs) — "
                    f"escalating to failed: the board is likely DEAD "
                    f"(domain/host-level), not merely egeo-filtered; "
                    f"check the board URL and re-declare or retire the "
                    f"watch")
                return "failed"
            return "egress_blocked"
        _fail_summary(f"list fetch crashed ({type(exc).__name__}: {exc})")
        return "failed"
    _egress_streak_reset(label)   # the block lifted — streak cleared
    if not current and not list_complete:
        _fail_summary("LIST FAILED — nothing to diff; state untouched "
                      "(fail-safe)")
        return "failed"

    prior = {r["reqId"]: r for r in _load_jsonl(state_path)}
    if list_complete and not current and len(prior) >= 10:
        # a real 200-with-zero on a board that had >=10 postings yesterday
        # is almost certainly an API anomaly — never mass-mark gone (B1
        # spirit: a suspicious list is not a trustworthy list)
        _fail_summary(f"EMPTY-LIST ANOMALY (had {len(prior)} postings "
                      "yesterday) — state untouched")
        return "failed"
    # S13 client-country boards: `current` is the FULL GLOBAL board (the
    # list-level token filter undercounts city-only dialects — 'Los
    # Gatos' carries no US token). Candidates are classified by their
    # enrichment detail's jobPostingInfo.country AFTER the bounded fetch
    # below; only US rows enter state/new/digest. Foreign rows cost
    # exactly ONE detail fetch each (the classifier) and live on in the
    # feed for audit. Facet boards (nvidia) are server-filtered — the
    # classification below is a no-op for them.
    #
    # S14 live-validation fix (run #38 evidence — the S13 one-fetch
    # contract was NOT actually delivered): the alert feed loads BEFORE
    # candidacy so the last feed record per reqId can (a) keep
    # foreign-classified rows OUT of the candidate queue — without this
    # they re-queued EVERY run (netflix: 5 days, 393 candidates, only
    # 214 distinct ever classified; a stable ~29-row foreign head ate
    # the DETAILS_MAX budget and starved the tail — the state sat at
    # 160 vs the true ~369 US board) — and (b) serve the classification
    # with its country verdict for rows beyond today's fetch head.
    feed_path = WATCH_DIR / f"{label}.newposts.jsonl"
    enriched_feed: dict[str, dict] = {}
    for e in _load_jsonl(feed_path):
        enriched_feed[e["reqId"]] = e            # last line wins
    today_iso = date.today().isoformat()

    cand_rows = [current[rid] for rid in current if rid not in prior]
    wcountry = w.get("country", "")
    if country_client:
        # the one-fetch contract: a candidate whose LAST feed record is
        # country-classified FOREIGN never enters state, so it would
        # re-queue forever. De-queue it — the feed keeps it as audit;
        # a genuine re-post arrives under a new reqId.
        def _feed_classified_foreign(rid: str) -> bool:
            e = enriched_feed.get(rid)
            c = (e.get("country") or "").strip() if e else ""
            return bool(c) and not workday.country_str_matches(
                c, wcountry)
        n_known_foreign = sum(1 for r in cand_rows
                              if _feed_classified_foreign(r["reqId"]))
        if n_known_foreign:
            cand_rows = [r for r in cand_rows
                         if not _feed_classified_foreign(r["reqId"])]
            print(f"[watch:{label}] {n_known_foreign} feed-classified "
                  "foreign candidate(s) de-queued (one-fetch contract)",
                  flush=True)
    new_rows = cand_rows
    gone_rows = [prior[rid] for rid in prior
                 if list_complete and rid not in current]
    print(f"[watch:{label}] {len(current)} on board "
          f"({'global' if country_client else 'country-filtered'}), "
          f"+{len(cand_rows)} candidates, -{len(gone_rows)} gone "
          f"(list_complete={list_complete})", flush=True)

    # P0-1 recovery (audit S7-A2 C / S7-A3 P0-1): postings already in
    # state but never enriched (past DETAILS_MAX / budget stop / crash)
    # are re-fed into enrichment until they land in newposts.jsonl. The
    # digest still reports only the FIRST sighting — recovery is silent.
    # S9-audit D2 P1: the rows fed to enrichment MUST be the CURRENT
    # list rows — state records carry no externalPath, so the pre-fix
    # shape fed detail_payload("") → guaranteed detail_unreachable and
    # burned the 3-strike valve on structurally doomed retries (the
    # predicate already guarantees rid ∈ current).
    def _recoverable(rid: str, p: dict) -> bool:
        feed_rec = enriched_feed.get(rid)
        if feed_rec is None:
            # never enriched: today's rows + crash survivors (flagged)
            return bool(p.get("needs_enrich")
                        or p.get("first_seen") == today_iso)
        # enriched-with-error: retry until 3 strikes (B6) — EXCEPT the
        # shape-burned class (S9-audit H3v): 25 live rows burned their
        # strikes on the pre-852bb5f bug that fed STATE-shaped rows (no
        # url) to enrich_new — guaranteed failures, not outages. Their
        # signature: an error record with no url. Those strike out on
        # garbage, so the cap is ignored for them (one successful
        # enrichment heals the row permanently).
        if feed_rec.get("error") and not (feed_rec.get("url")
                                          or "").strip():
            return True
        # enriched-with-error: retry until 3 strikes (B6)
        return bool(feed_rec.get("error")) \
            and int(feed_rec.get("attempts") or 0) < 3

    backlog_rows = [current[rid] for rid, p in prior.items()
                   if rid in current and _recoverable(rid, p)]
    if backlog_rows:
        print(f"[watch:{label}] backlog recovery: {len(backlog_rows)} "
              "state postings awaiting enrichment", flush=True)

    # S10 url-heal: bug-era feed records that enriched fine (details
    # present, no error) but carry url='' — the pre-852bb5f run fed
    # STATE-shaped rows as enrich input, so the url stamp came back
    # empty. No live consumer breaks (the digest prefers externalUrl),
    # but the durable feed should not carry rows whose url silently
    # reads "unknown". Re-stamp from the CURRENT list row — network-free
    # and idempotent: once the healed record is last-in-file, no-op.
    # (Error records with url='' are NOT touched here — that's the
    # shape-burned class, owned by the recovery query above.)
    healed = []
    for rid, fr in enriched_feed.items():
        cur_url = (current.get(rid) or {}).get("url") or ""
        if (rid in current and cur_url
                and not (fr.get("url") or "").strip()
                and not fr.get("error")):
            healed.append(dict(fr, url=cur_url))
    if healed:
        _append_jsonl(feed_path, healed)
        enriched_feed.update({h["reqId"]: h for h in healed})
        print(f"[watch:{label}] url-heal: {len(healed)} bug-era feed "
              "record(s) re-stamped with their list url", flush=True)

    # enrich the NEW postings (bounded; budget-guarded)
    deadline = time.monotonic() + BUDGET_SECONDS
    # S14 one-fetch contract: only candidates NEVER enriched (or whose
    # last feed record ERRORED — the 3-strike retry) cost a detail
    # fetch. A candidate with a non-error prior feed record feeds the
    # classification directly from that record (country verdict, or the
    # US-token fallback) — re-fetching it is the starvation bug. Live
    # evidence: 185 no-country feed rows across netflix/tencent/jd are
    # ALL 'USA - …' locations (0 foreign-token, 0 no-token) — the
    # unresolved-forever class is empty in practice and stays auditable
    # in the feed if it ever appears.
    if country_client:
        def _needs_fetch(rid: str) -> bool:
            e = enriched_feed.get(rid)
            if e is None:
                return True                    # never enriched
            if e.get("error"):
                # 3-strike valve (B6) — the same cap _recoverable
                # applies to state rows: a candidate whose detail has
                # failed 3× is terminal (token-fallback verdict);
                # re-fetching forever is the starvation bug again.
                return int(e.get("attempts") or 0) < 3
            return False                       # one-fetch contract
        fetch_rows = [r for r in cand_rows
                      if _needs_fetch(r["reqId"])]
    else:
        fetch_rows = cand_rows
    enrich_input = (fetch_rows + backlog_rows)[:DETAILS_MAX * 2] \
        if backlog_rows else fetch_rows
    # S9-audit D2 P2: retries/recoveries must not reset the feed's
    # first_seen. prior_feed seeds each reqId with its last FEED record
    # (first_seen preserved for enriched-with-error retries); state rows
    # seed crash survivors the feed has never seen — only genuinely-new
    # reqIds get today.
    prior_feed = dict(enriched_feed)
    for rid, p in prior.items():
        if rid not in prior_feed and p.get("first_seen"):
            prior_feed[rid] = {"first_seen": p["first_seen"]}
    enriched = enrich_new(enrich_input, w["board"], w["company"], cfg,
                          deadline, prior_feed=prior_feed,
                          country=w.get("country"),
                          time_type=w.get("time_type"))
    if country_client:
        # S13: classify the candidates by their detail country — the
        # authoritative signal (jobPostingInfo.country on the payload
        # enrich_new just fetched). new_rows (the alerting/US set) =
        # US-classified candidates; foreign rows stay in the feed as
        # audit; 3-strike rows fall back to the token predicate (S12
        # behavior, undercount-accepting for that tail only); rows
        # beyond DETAILS_MAX / budget stay PENDING (retried next leg).
        # S14 fix, two changes: (1) the record consulted is TODAY's
        # enrichment OR the last feed record — a row classified in a
        # prior leg (beyond today's fetch head) KEEPS its verdict
        # instead of resetting to pending; (2) an enriched record with
        # NO country field (the netflix 'USA - Remote' class — 108 rows
        # pending-forever at S14 validation) falls back to the
        # location-token predicate on its own enriched locations —
        # conservative phrase semantics: no US token = still pending.
        enr_by_rid = {e["reqId"]: e for e in enriched}
        us_rids: set[str] = set()
        n_foreign = n_pending = n_token = 0
        for r in cand_rows:
            e = enr_by_rid.get(r["reqId"]) \
                or enriched_feed.get(r["reqId"])
            if e and not e.get("error") and (e.get("country") or "").strip():
                if workday.country_str_matches(e["country"], wcountry):
                    us_rids.add(r["reqId"])
                else:
                    n_foreign += 1
            elif e and e.get("error") \
                    and int(e.get("attempts") or 0) >= 3:
                # 3-strike: token fallback (last resort, marked)
                if workday._row_in_country(r, wcountry):
                    us_rids.add(r["reqId"])
                    n_token += 1
                else:
                    n_foreign += 1
            elif e and not e.get("error"):
                # enriched detail WITHOUT a country field (S14): token
                # fallback on the record's own locations — the record
                # carries locationsText, so the row predicate works
                if workday._row_in_country(e, wcountry):
                    us_rids.add(r["reqId"])
                    n_token += 1
                else:
                    n_pending += 1
            else:
                n_pending += 1     # no detail yet — retry next leg
        new_rows = [r for r in cand_rows if r["reqId"] in us_rids]
        print(f"[watch:{label}] country-classified: {len(new_rows)} US, "
              f"{n_foreign} foreign (feed-only), {n_pending} pending "
              f"({n_token} via token fallback)", flush=True)
    # S9 C5: run-level title-search budget cell — shared by THIS main
    # corroborate call only (the lag pass below never title-searches)
    signals = corroborate_new((new_rows + backlog_rows) if country_client
                              else enrich_input, w["company"], cfg,
                              deadline, title_search_budget=[TITLE_SEARCH_MAX])
    for e in enriched:
        s = signals.get(e["reqId"])
        if s:
            e["signals"] = s
    _append_jsonl(WATCH_DIR / f"{label}.newposts.jsonl", enriched)

    # ── S8-E2 repost detector: R1 label regression + R2 bounded re-fetch ─
    # (additive phase — a failure here is logged and skipped, never
    # fatal; the diff/enrich/state phases above are untouched). run_date
    # is computed OUTSIDE the try: the state rewrite below needs it too.
    run_date = _pt_run_date()
    repost_events: list[dict] = []
    refetch_sd: dict[str, str] = {}
    try:
        flags = detect_reposts(current, prior, run_date)
        if flags:
            print(f"[watch:{label}] repost detector: {len(flags)} label "
                  "regression(s)", flush=True)
        events, refetch_sd = refetch_repost_details(
            flags, w["board"], cfg, deadline)
        # startDate-move channel from THIS run's detail observations:
        # backlog-recovery enrichment re-fetches existing reqIds — a
        # startDate that moved LATER on the same reqId is a direct reset
        # (highest-confidence signal), independent of the labels.
        enr_sd = {e["reqId"]: str(e.get("startDate") or "").strip()
                  for e in enriched
                  if e.get("reqId") in prior
                  and str(e.get("startDate") or "").strip()}
        extra = detect_start_date_moves(current, prior, enr_sd, run_date)
        repost_events = _merge_repost_events(events, extra)
        if repost_events:
            _append_jsonl(WATCH_DIR / f"{label}.reposts.jsonl",
                          [_repost_log_record(e) for e in repost_events])
            n_high = sum(1 for e in repost_events
                         if e.get("confidence") == "high")
            print(f"[watch:{label}] reposts: {len(repost_events)} "
                  f"event(s) ({n_high} startDate-confirmed) → "
                  f"{label}.reposts.jsonl", flush=True)
    except Exception as exc:    # additive detector — never fatal
        repost_events, refetch_sd = [], {}
        print(f"[watch:{label}] repost detector skipped: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)

    # cross-post lag pass: LinkedIn often cross-posts 1-2 days AFTER the
    # Workday listing — postings first-seen within RECORROBORATE_DAYS that
    # still carry no signals get another bounded corroboration attempt.
    # The feed is append-only: an updated record supersedes the older line
    # for the same reqId (consumers read the LAST line per reqId).
    if deadline - time.monotonic() > 30:      # only when budget remains
        try:
            horizon = date.today().toordinal() - RECORROBORATE_DAYS
            recent: dict[str, dict] = {}
            for r in _load_jsonl(WATCH_DIR / f"{label}.newposts.jsonl"):
                fs = r.get("first_seen") or ""
                try:
                    ok = date.fromisoformat(fs).toordinal() >= horizon
                except ValueError:
                    ok = False
                # S13: foreign feed rows (classified by detail country)
                # never enter corroboration — they are not US postings.
                if country_client and (r.get("country") or "").strip() \
                        and not workday.country_str_matches(
                            r.get("country") or "", w.get("country", "")):
                    ok = False
                if ok and not r.get("signals"):
                    recent[r["reqId"]] = r          # last line wins
            for rid in signals:                    # just-corroborated skip
                recent.pop(rid, None)
            if recent:
                sigs2 = corroborate_new(list(recent.values()),
                                        w["company"], cfg, deadline)
                updates = []
                for rid, s in sigs2.items():
                    r = recent.get(rid)
                    if r:
                        r2 = dict(r)
                        r2["signals"] = s
                        updates.append(r2)
                if updates:
                    _append_jsonl(
                        WATCH_DIR / f"{label}.newposts.jsonl", updates)
                    signals.update(sigs2)
                    print(f"[watch:{label}] cross-post-lag pass: "
                          f"+{len(updates)} newly corroborated",
                          flush=True)
        except Exception as exc:   # additive feature — never fatal
            print(f"[watch:{label}] re-corroborate pass skipped: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

    # state: compact rewrite (first_seen preserved; last_seen=today for
    # everything still current; gone rows dropped from state — their
    # history lives in newposts.jsonl + alerts.log). B1: when the listing
    # is INCOMPLETE, prior rows absent from the partial list are kept
    # verbatim (unknown fate ≠ gone — a dropped row would re-alert as
    # "new" on the next complete run).
    # S13 client-country: state = the US membership ONLY (prior ∪
    # classified-US new). Foreign rows never enter state — they cost
    # one detail fetch (the classifier) and live in the feed as audit.
    today = date.today().isoformat()
    enr_by_rid = {e["reqId"]: e for e in enriched if e.get("reqId")}
    # S14: rescued rows (US verdict / startDate from a PRIOR feed
    # record, not fetched today) — the feed record is the enrichment
    # evidence for them (review MED-LOW amendment: seed startDate +
    # first_seen from it, not from today's fetch that never happened)
    feed_by_rid = enriched_feed
    need_set = {r["reqId"] for r in new_rows}
    need_set.update(r["reqId"] for r in backlog_rows)
    us_membership = set(prior) | {r["reqId"] for r in new_rows}
    membership = us_membership if country_client else set(current)
    keep = []
    for rid, r in current.items():
        if rid not in membership:
            continue          # foreign (client-country mode)
        p = prior.get(rid) or {}
        rec = {
            "reqId": rid, "title": r.get("title") or "",
            "first_seen": p.get("first_seen")
            or (feed_by_rid.get(rid) or {}).get("first_seen")
            or today,
            "last_seen": today,
            "last_postedOn": r.get("postedOn") or "",
            # S8-E2: a re-fetched (R2) startDate is the newest observation
            # for this reqId; enrichment otherwise; prior otherwise.
            # S14: a rescued row's evidence is its prior FEED record.
            "last_startDate": refetch_sd.get(rid)
            or enr_by_rid.get(rid, {}).get("startDate")
            or (feed_by_rid.get(rid) or {}).get("startDate")
            or p.get("last_startDate") or "",
        }
        # S8-E2: implied post date, computed ONCE per run against the
        # PACIFIC run date (kills the UTC off-by-one — see _to_pt_date).
        # Written for numeric buckets only — OPEN (30+) is censored and
        # unparseable labels carry no date; legacy rows without the field
        # are compared via the conservative last_seen fallback instead.
        impl = implied_post_date(parse_posted_on(r.get("postedOn")),
                                 run_date)
        if impl is not None:
            rec["implied_post_date"] = impl.isoformat()
        # S8-E2: startDate_first = earliest startDate ever observed for
        # this reqId. Written when a detail was observed THIS run (new-
        # posting enrichment or repost re-fetch); rows with no new detail
        # evidence keep whatever they had (legacy rows: absent).
        sd_first = p.get("startDate_first") or ""
        new_obs = refetch_sd.get(rid) \
            or enr_by_rid.get(rid, {}).get("startDate") \
            or (feed_by_rid.get(rid) or {}).get("startDate") or ""
        if new_obs:
            sd_first = min(x for x in (sd_first,
                                       p.get("last_startDate") or "",
                                       new_obs) if x)
        if sd_first:
            rec["startDate_first"] = sd_first
        if rid in need_set and rid not in enr_by_rid:
            # S14: a row resolved from its prior feed record is NOT
            # awaiting enrichment — never stamp it (review LOW #2)
            fr = enriched_feed.get(rid)
            if not (fr and not fr.get("error")):
                rec["needs_enrich"] = True    # survives crash / leg cap
        keep.append(rec)
    if not list_complete:
        keep.extend(p for rid, p in prior.items() if rid not in current)
        keep.sort(key=lambda r: r["reqId"])
    _atomic_write(state_path, "\n".join(
        json.dumps(r, ensure_ascii=False) for r in keep) + "\n")

    digest = format_digest(label, w["company"], new_rows, enriched,
                           signals, gone_rows,
                           len(membership & set(current))
                           if country_client else len(current),
                           repost_events=repost_events)
    # S8-E2: reposts alone justify an alert (the whole point — resets
    # happen on days with no new/gone churn at all)
    if new_rows or gone_rows or repost_events:
        _send_alerts(label, digest, cfg)
    print(f"[watch:{label}] digest:\n{digest}", flush=True)

    # backlog = postings (new OR recovered) still awaiting enrichment
    # (DETAILS_MAX/budget bound) — drives the self-retrigger. S13
    # client-country: UNCLASSIFIED candidates count too (their detail
    # fetch IS the classifier; next leg re-candidates them).
    need = {r["reqId"] for r in new_rows}
    if country_client:
        need.update(r["reqId"] for r in cand_rows)
    need.update(r["reqId"] for r in backlog_rows)
    enriched_rids = {e["reqId"] for e in enriched if e.get("reqId")}
    if country_client:
        # S14 one-fetch contract: a candidate is RESOLVED (not
        # backlog) when its prior feed record is non-error (verdict or
        # US-token fallback) OR terminal-3-strike (token fallback
        # already decided). Error records below the cap retry on a
        # LATER leg/day via _needs_fetch — only rows beyond today's
        # fetch head drive the same-day retrigger (a row that errored
        # IN today's head counts as handled for today).
        enriched_rids.update(
            rid for rid, e in enriched_feed.items()
            if rid in current
            and (not e.get("error")
                 or int(e.get("attempts") or 0) >= 3))
    unenriched = len(need - enriched_rids)
    if unenriched > 0 and leg < LEGS_MAX_PER_DAY:
        print(f"[watch:{label}] backlog: {unenriched} new postings "
              "awaiting enrichment — will self-retrigger", flush=True)
        return "backlog"
    if unenriched > 0:
        print(f"[watch:{label}] {unenriched} unenriched but leg cap "
              f"reached — tomorrow's cron continues", flush=True)
    return "complete"


def seed_state(w: dict, list_path: Path, first_seen: str) -> None:
    """B3: import a full board_dump list into the state (the first watch
    run must not alert on ~1,400 pre-existing postings)."""
    rows = _load_jsonl(list_path)
    state_path = WATCH_DIR / f"{w['label']}.state.jsonl"
    if state_path.exists():
        print(f"[watch:{w['label']}] state exists — seed refused "
              "(delete the state file to force)", file=sys.stderr)
        return
    today = date.today().isoformat()
    keep = [{
        "reqId": r["reqId"], "title": r.get("title") or "",
        "first_seen": first_seen or today,
        "last_seen": first_seen or today,
        "last_postedOn": r.get("postedOn") or "",
        "last_startDate": "",
    } for r in rows]
    _atomic_write(state_path, "\n".join(
        json.dumps(r, ensure_ascii=False) for r in keep) + "\n")
    print(f"[watch:{w['label']}] seeded {len(keep)} postings "
          f"(first_seen={first_seen or today})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--label", help="run a single watch by label")
    ap.add_argument("--seed-from", help="list.jsonl to seed the state from")
    ap.add_argument("--first-seen", help="date for --seed-from (default today)")
    args = ap.parse_args()

    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps({"watches": []}, indent=1) + "\n",
                               encoding="utf-8")
        print(f"[watch] no config — created empty {CONFIG_PATH}")
        return 0
    try:
        watches = json.loads(
            CONFIG_PATH.read_text(encoding="utf-8")).get("watches", [])
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[watch] config unreadable: {exc}", file=sys.stderr)
        return 0                       # exit-0 contract; tomorrow retries
    _validate_variant_uniqueness(watches)
    if args.label:
        watches = [w for w in watches if w["label"] == args.label]
        if not watches:
            print(f"[watch] unknown label {args.label!r}")
            return 2
    if args.seed_from and len(watches) != 1:
        print("[watch] --seed-from requires --label (seeding EVERY watch "
              "from one list would cross-contaminate state)",
              file=sys.stderr)
        return 2

    cfg = load_config()
    results: dict[str, str] = {}
    for w in watches:
        if args.seed_from:
            seed_state(w, Path(args.seed_from), args.first_seen or "")
            continue
        try:
            results[w["label"]] = run_watch(w, cfg)
        except Exception as exc:      # fail-safe: one watch never kills all
            print(f"[watch:{w['label']}] FAILED: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as f:
                    f.write(f"\n**board-watch {w['label']} CRASHED: "
                            f"{type(exc).__name__}: {exc}**\n")
            results[w["label"]] = "failed"

    gha_env = os.environ.get("GITHUB_ENV")
    if gha_env:
        vals = set(results.values())
        # failed is LOUDER than backlog (S7-V1 P3-1): a dead watch must
        # never be masked by another watch's pending work
        overall = ("failed" if "failed" in vals
                   else "backlog" if "backlog" in vals else "complete")
        with open(gha_env, "a", encoding="utf-8") as f:
            f.write(f"WATCH_RESULT={overall}\n")
            f.write(f"WATCH_RESULTS_JSON={json.dumps(results)}\n")
    print(f"[watch] results: {results or 'seeded'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
