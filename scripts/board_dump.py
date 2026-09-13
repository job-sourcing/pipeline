#!/usr/bin/env python3
"""Generic exhaustive per-company board snapshot (board-dump v2.1).

Generalizes scripts/workday_dump.py (the NVIDIA pilot) into the repeatable
flow the user asked to productize (design-board-v2.md D3):

  list → details → [corroborate] → [tagfacets] → finish

- **list**: exhaustively paginate one ATS board (server-side facets for
  country / timeType), streaming JSONL, wrap-guards per the CXS quirks
  (offset past total WRAPS; near-end page total=0; limit<=20; total CAPPED
  at 2,000 — the primitive partitions by facet and recovers). Also persists
  the page-0 facet census (countries/sites/Office-Remote/timeType counts)
  to {label}.facets.json (S8-D gap #4).
- **details**: per-posting detail GET, RAW-preserved (every jobPostingInfo
  field + hiringOrganization + similarJobs list — reqId/title/externalPath
  per entry, S8-D gap #1), resumable in bounded batches.
- **corroborate**: join aggregator-platform signals (LinkedIn guest:
  applicant counts, cross-post dates, req-id exact join) — resumable,
  circuit-breakered, status-enummed (matched|no_match|blocked|not_checked).
- **tagfacets** (OPTIONAL, run before finish, same --country/--time-type
  as the list): per-row workerSubType + jobFamilyGroup tagging via
  facet-partitioned lists (these classifications exist ONLY as facet
  values — S8-D gaps #2/#3) → {label}.facet_tags.jsonl.
- **finish**: assemble {out}.json + the v2.1 CSV + a validation report +
  {label}.similar_edges.jsonl (one role-similarity edge per line).

CSV v2.1 column ORDER (the contract — CSV_COLUMNS is the single source of
truth; 41 columns, v2.1 adds #32-#41 after the 31 v2 columns):

    1 reqId                 2 title                3 company
    4 hiringOrg             5 timeType             6 postedOn
    7 startDate             8 postingAgeDays       9 reqYear
    10 primaryLocation      11 nLocations          12 locations
    13 remoteFlag           14 stateCodes          15 country
    16 questionnaire        17 similarJobsCount    18 description
    19 descriptionLength    20 url                 21 detailError
    22 linkedinUrl          23 linkedinPostedDate  24 numApplicants
    25 applicantLabel       26 dateDeltaDays       27 corroborationStatus
    28 matchMethod          29 firstSeenDate       30 corroboratedOn
    31 dumpDate
    32 endDate              (detail endDate — 2.9% of rows)
    33 daysOnMarket         (snapshot_date − startDate; snapshot_date =
                            today at finish time, passed to every row)
    34 daysOnMarketBasis    ("startDate" | "" when startDate missing)
    35 censored             ("true" when startDate < WATCH_SEED or is
                            missing — age is a LOWER bound only; else
                            "false")
    36 repostCount          (slug "…_JR####-N" suffix — Workday's own
                            repost counter; 0 when absent)
    37 lastResetDate        (empty — reserved for the watch's repost
                            detector, S8-C R1/R2)
    38 applicationDeadline  ("Applications … accepted … until {date}"
                            from description — 98.3% coverage; falls back
                            to structured endDate)
    39 daysLeftToApply      (deadline − snapshot_date; empty when none)
    40 workerSubType        (tagfacets phase; empty when not run)
    41 jobFamilyGroup       (tagfacets phase; empty when not run)

Board providers: workday is the first (the CXS facts); the phase split is
board-agnostic — corroborate/finish operate purely on the JSONL files.
Every optional phase (corroborate/tagfacets) enriches when present and is
skipped cleanly when not — list→details→finish alone still works.

Sandbox doctrine: batch + checkpoint (2-min bash limit); every phase is
idempotent and resumable; files are the state.

Usage:
  python3 scripts/board_dump.py --board nvidia|wd5|nvidiaexternalcareersite \
      --company NVIDIA --label nvidia_us_fulltime --phase list
  ... --phase details   (repeat while rows remain)
  ... --phase corroborate (repeat while cards remain; then signals)
  ... --phase tagfacets  (optional; before finish)
  ... --phase finish
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "ingest"))

from jobsearch.config import Config  # noqa: E402
from jobsearch.htmltext import html_to_text  # noqa: E402
from jobsearch.sources import workday  # noqa: E402
from jobsearch import corroborate  # noqa: E402

# ── CSV v2 column contract (design-board-v2.md D1 + review addenda) ──────
CSV_COLUMNS = [
    "reqId",                 # 1  requisition id (JR…)
    "title",                 # 2
    "company",               # 3
    "hiringOrg",             # 4  legal hiring entity ("2100 NVIDIA USA")
    "timeType",              # 5  "Full time"
    "postedOn",              # 6  relative label ("Posted Today")
    "startDate",             # 7  ISO posting date
    "postingAgeDays",        # 8  today − startDate
    "reqYear",               # 9  from JR2022xxxx → 2022 (req age signal)
    "primaryLocation",       # 10 "US, CA, Santa Clara"
    "nLocations",            # 11 total locations
    "locations",             # 12 ALL locations "; "-joined — 0A
    "remoteFlag",            # 13 any location mentions Remote
    "stateCodes",            # 14 "CA;NC;TX" derived
    "country",               # 15 country descriptor
    "questionnaire",         # 16 application questionnaire present
    "similarJobsCount",      # 17 related postings on the board
    "description",           # 18 clean text (html_to_text) — 0B
    "descriptionLength",     # 19 chars of clean text
    "url",                   # 20 canonical apply URL
    "detailError",           # 21 "" | detail_unreachable (detail-derived
                             #    cols above are UNKNOWN, not absent)
    "linkedinUrl",           # 22 matched LI posting — 0D
    "linkedinPostedDate",    # 22 LI cross-post date
    "numApplicants",         # 23 LI applicant count
    "applicantLabel",        # 24 raw label ("Over 200 applicants")
    "dateDeltaDays",         # 25 LI date − startDate (repost lag)
    "corroborationStatus",   # 26 matched|no_match|blocked|not_checked
    "matchMethod",           # 27 reqId|title|""
    "firstSeenDate",         # 28 first sighting by us (dump/watch)
    "corroboratedOn",        # 29 ISO timestamp of signal fetch
    "dumpDate",              # 30 dump generation date
    # ── v2.1 addenda (S8-E3: S8-D gap table #5/#6 + S8-C §7/§8-R3) ──────
    "endDate",               # 32 detail endDate (ISO; 2.9% of rows)
    "daysOnMarket",          # 33 snapshot_date − startDate (int; "" when
                             #    no startDate) — best-estimate listing age
    "daysOnMarketBasis",     # 34 "startDate" | "" (evidence basis; grows
                             #    to startDate+history|linkedin|slugSuffix
                             #    once the watch feeds earliest-evidence)
    "censored",              # 35 "true" | "false" — see WATCH_SEED below
    "repostCount",           # 36 slug "…_JR####-N" suffix (Workday's own
                             #    repost counter; 0 when absent)
    "lastResetDate",         # 37 "" — reserved for the watch's repost
                             #    detector (S8-C R1/R2)
    "applicationDeadline",   # 38 "accepted … until {date}" parsed from
                             #    description (98.3%); endDate fallback
    "daysLeftToApply",       # 39 deadline − snapshot_date (int | "")
    "workerSubType",         # 40 tagfacets phase (Intern/NCG/…)
    "jobFamilyGroup",        # 41 tagfacets phase (NVIDIA's taxonomy)
]

# The board-watch's first observation date (2026-09-09 — the NVIDIA seed
# dump). Rows whose startDate PREDATES it were never observed from birth:
# their first_seen is only a LOWER bound (the posting may already have
# been reposted with a reset startDate before we ever looked — S8-C §7.2
# censoring rule; the slug -N suffix proves ~11% of seed rows were).
# Rows with NO startDate are censored too (no evidence at all).
# NOTE: board-specific constant, hardcoded by design per S8-E3 — a future
# generic tenant drain should parameterize it.
WATCH_SEED = date(2026, 9, 9)

# The board's standard close-date sentence (S8-C §1: 1337/1360 rows):
#   "Applications for this job will be accepted at least until April 11, 2026"
# plus the natural variant "… are accepted … until {date}". Date is always
# "{Month D, YYYY}" (168 distinct values, all full month names).
_APPLICATION_DEADLINE_RE = re.compile(
    r"applications?\b[^.\n]{0,80}?\baccepted\b[^.\n]{0,60}?\buntil\s+"
    r"([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE)

# Facet parameters tagged by the optional --phase tagfacets (S8-D #2/#3).
_TAG_FACETS = ("workerSubType", "jobFamilyGroup")


def _slug_req_id(path: str) -> str:
    """reqId from a CXS externalPath slug: '…/Title_JR2018179-3' →
    'JR2018179' (the trailing -N repost suffix is stripped — same
    requisition, Nth posting; S8-C §4b). Empty string when the slug
    carries no '_' reqId delimiter."""
    seg = (path or "").rstrip("/").rsplit("/", 1)[-1]
    if "_" not in seg:
        return ""
    return re.sub(r"-\d+$", "", seg.rsplit("_", 1)[-1])


def _slug_repost_count(path: str) -> int:
    """The trailing '-N' on a CXS slug = Workday's own repost counter
    (151/1,360 rows carry one; S8-C §4b). 0 when absent."""
    seg = (path or "").rstrip("/").rsplit("/", 1)[-1]
    if "_" not in seg:
        return 0
    m = re.search(r"-(\d+)$", seg.rsplit("_", 1)[-1])
    return int(m.group(1)) if m else 0


def _parse_application_deadline(desc_text: str) -> str:
    """'Applications … accepted … until {Month D, YYYY}' → ISO date;
    empty string when absent/unparseable. Whitespace-normalized first
    (clean text can wrap the sentence across lines)."""
    if not desc_text:
        return ""
    text = re.sub(r"\s+", " ", desc_text)
    m = _APPLICATION_DEADLINE_RE.search(text)
    if not m:
        return ""
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


_STATE_RE = re.compile(r"^[A-Z]{2},\s*([A-Z]{2}),")


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    # corrupt trailing line (crash mid-append) — tolerate
                    print(f"[warn] skipping corrupt line in {path.name}",
                          file=sys.stderr)
    return rows


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── phase: list ──────────────────────────────────────────────────────────

def phase_list(args, out: Path) -> int:
    cfg = Config()
    list_path = out.with_suffix(".list.jsonl")
    # honest status from the START (audit S7-A2 M): a crash mid-list must
    # not leave the PREVIOUS run's "complete: true" next to a truncated
    # list file. Rewritten on completion below.
    _atomic_write_text(out.with_suffix(".list.status"), json.dumps(
        {"rows": 0, "total": 0, "complete": False, "pages": 0}))
    try:
        rows, meta = workday.list_board(
            args.board, country=args.country or None,
            time_type=args.time_type or None, cfg=cfg,
            sleep_s=args.sleep, progress_every=10)
    except ValueError as exc:
        print(f"[list] {exc}")
        return 2
    except RuntimeError as exc:
        # capped-total partition failure (S8-D 1g) — enumeration is
        # impossible under the current facet set, not a transient error
        print(f"[list] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[list] page-0 fetch failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    with open(list_path, "w", encoding="utf-8") as lf:
        lf.write("\n".join(
            json.dumps(r, ensure_ascii=False)
            for r in rows.values()) + "\n")
    complete = bool(meta.get("complete"))
    print(f"[list] done: {len(rows)} unique postings "
          f"(first-page total={meta.get('total')}, "
          f"pages={meta.get('pages')}) → {list_path}", flush=True)
    # facet census (S8-D gap #4): page-0 facet counts → {label}.facets.json
    # — free board-composition KPIs (countries/sites/Office-Remote/timeType)
    facets = meta.get("facets") or {}
    if facets:
        census_doc: dict = {
            "board": args.board,
            "appliedFacets": {
                k: v for k, v in (("locationHierarchy1", args.country or None),
                                  ("timeType", args.time_type or None))
                if v},
            "capturedAt": datetime.now().isoformat(),
            "scope": "board",        # counts = whole-board (discovery page-0)
            "total": meta.get("total"),
            "facets": facets,
        }
        if meta.get("facets_filtered"):
            # counts within the applied facets (the dumped slice)
            census_doc["facets_filtered"] = meta["facets_filtered"]
        facets_path = out.with_suffix(".facets.json")
        _atomic_write_text(facets_path, json.dumps(
            census_doc, ensure_ascii=False))
        n_values = sum(len(v) for v in facets.values())
        print(f"[list] facet census: {len(facets)} facets / {n_values} "
              f"values → {facets_path}", flush=True)
    if meta.get("total_capped"):
        print(f"[list] NOTE: server total was CAPPED at 2000 — recovered "
              f"{len(rows)} unique rows via "
              f"{meta.get('partition_facet')} partitions", flush=True)
    status = {"rows": len(rows), "total": meta.get("total"),
              "complete": complete, "pages": meta.get("pages")}
    if meta.get("total_capped"):
        status["total_capped"] = True
        status["partition_facet"] = meta.get("partition_facet")
        status["partitions"] = meta.get("partitions")
    _atomic_write_text(out.with_suffix(".list.status"), json.dumps(status))
    if not complete:
        print(f"[list] WARNING: INCOMPLETE listing (network break) — "
              "re-run --phase list; watch must NOT emit gone-events")
        return 1
    return 0


# ── phase: details (v2 — RAW full payload) ───────────────────────────────

def phase_details(args, out: Path) -> int:
    board = workday.parse_board(args.board)
    list_path = out.with_suffix(".list.jsonl")
    det_path = out.with_suffix(".details.jsonl")
    if not list_path.exists():
        print(f"no {list_path} — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    done: dict[str, dict] = {}
    for d in _load_jsonl(det_path):
        done[d["reqId"]] = d
    # error rows retry with a 3-strike cap (audit S7-A2 H: transient 429s
    # were permanently losing descriptions). attempts accumulate per req.
    settled = {rid for rid, d in done.items() if d.get("info")}
    three_strikes = {rid for rid, d in done.items()
                     if (d.get("error") == "detail_unreachable"
                         and int(d.get("attempts") or 1) >= 3)}
    todo = [r for r in rows
            if r["reqId"] not in settled and r["reqId"] not in three_strikes]
    print(f"[details] {len(rows)} rows, {len(settled)} settled, "
          f"{len(done) - len(settled)} error rows "
          f"({len(three_strikes)} past 3-strike cap), "
          f"{len(todo)} to fetch (batch={args.details_batch})", flush=True)
    if not todo:
        return 0
    workday._DETAIL_SLEEP_S = args.detail_sleep
    cfg = Config()          # hoisted (was per-row: 150 mkdir+env-scans/batch)
    batch = todo[:args.details_batch]
    # per-row checkpointing (the 2-min-bash doctrine): EVERY record is
    # appended+flushed the moment it is fetched — a timeout kill loses
    # nothing (v1 behavior; a memory-accumulated batch lost 125 rows to
    # a timeout kill on 2026-09-09 — never again).
    with open(det_path, "a", encoding="utf-8") as df:
        for i, r in enumerate(batch, 1):
            payload = workday.detail_payload(
                board, r["externalPath"], cfg)
            rec: dict = {"reqId": r["reqId"]}
            if payload:
                info = payload.get("jobPostingInfo") or {}
                # RAW preservation — every jobPostingInfo field
                rec["info"] = info
                org = payload.get("hiringOrganization") or {}
                rec["hiringOrg"] = org.get("name")
                sim = payload.get("similarJobs") or []
                rec["similarJobsCount"] = len(sim)
                # full list (S8-D gap #1): ~5 entries/row × ~1,300 rows ≈
                # 6,600 free role-similarity edges per snapshot. The API
                # bounds it at 5 entries — sliced defensively anyway.
                rec["similarJobs"] = [
                    {"reqId": _slug_req_id(str(s.get("externalPath") or "")),
                     "title": s.get("title"),
                     "externalPath": s.get("externalPath")}
                    for s in sim if isinstance(s, dict)][:5]
            else:
                prev = done.get(r["reqId"]) or {}
                rec["error"] = "detail_unreachable"
                rec["attempts"] = int(prev.get("attempts") or 0) + 1
            df.write(json.dumps(rec, ensure_ascii=False) + "\n")
            df.flush()
            if i % 25 == 0:
                print(f"[details] {i}/{len(batch)} this batch "
                      f"({len(settled) + i}/{len(rows)} total)", flush=True)
    remaining = len(todo) - len(batch)
    if remaining:
        print(f"[details] batch done — {remaining} rows remaining "
              f"(re-run --phase details)", flush=True)
    else:
        print(f"[details] ALL {len(rows)} rows enriched", flush=True)
    return 0


# ── phase: corroborate ───────────────────────────────────────────────────

def phase_corroborate(args, out: Path) -> int:
    """Two sub-steps, each checkpointed to its own JSONL (review Q2):
    1. LinkedIn index → {out}.li_index.jsonl (cards; ACCUMULATES across
       invocations, resume offset in {out}.li_index.meta.json)
    2. per-card signals → {out}.signals.jsonl (resumable by card id)
    Blocked is a recorded status, never fatal (B5 no-raise contract).
    """
    list_path = out.with_suffix(".list.jsonl")
    if not list_path.exists():
        print(f"no {list_path} — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    req_ids = {r["reqId"] for r in rows}
    idx_path = out.with_suffix(".li_index.jsonl")
    sig_path = out.with_suffix(".signals.jsonl")
    provider = corroborate.get_provider(args.provider)

    # ── sub-step 1: index (accumulates; resumable via meta offset) ──
    indexed: list[dict] = _load_jsonl(idx_path)
    index_meta = {"offset": 0, "done": False}
    meta_path = out.with_suffix(".li_index.meta.json")
    if meta_path.exists():
        try:
            index_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    # S8 fix: `done` is mode-specific — a completed single-query index must
    # NOT suppress a partitioned pass (mode switch re-opens the index; cards
    # accumulate with dedup so nothing is refetched or lost).
    index_mode = getattr(args, "index_mode", "single")
    if args.corroborate_index and index_meta.get("done") \
            and index_mode != index_meta.get("mode", "single"):
        index_meta["done"] = False
        print(f"[corroborate] index mode switch "
              f"{index_meta.get('mode', 'single')!r} -> {index_mode!r}: "
              "re-opening index", flush=True)
    if args.corroborate_index and not index_meta.get("done"):
        # S8-E1 index modes: "single" (back-compat default — one
        # company query) or "partitioned" (keyword×location slice
        # matrix; research §c: the single query hits a SERVING ceiling
        # — slices surface +78 new cards / 3 probe pages).
        try:
            if index_mode == "partitioned":
                cards, next_offset, exhausted = \
                    provider.index_cards_partitioned(
                        company=args.company,
                        max_pages_per_slice=getattr(
                            args, "li_slice_pages", 3),
                        max_cards=corroborate.PARTITIONED_MAX_CARDS)
            else:
                cards, next_offset, exhausted = provider.index_cards(
                    company=args.company, location=args.location,
                    max_pages=args.li_index_pages,
                    max_cards=args.li_index_cards,
                    start_offset=int(index_meta.get("offset", 0)))
        except corroborate.CorroborationBlocked as exc:
            print(f"[corroborate] INDEX BLOCKED at page 0: {exc}",
                  file=sys.stderr)
            _atomic_write_text(meta_path, json.dumps({
                "offset": int(index_meta.get("offset", 0)), "done": False,
                "blocked": True, "at": datetime.now().isoformat()}))
            return 0      # signals sub-step pointless without an index
        # accumulate (dedup by card id) — never lose prior progress
        have = {c["id"] for c in indexed}
        for c in cards:
            if c["id"] not in have:
                indexed.append(c)
        _atomic_write_text(idx_path, "\n".join(
            json.dumps(c, ensure_ascii=False) for c in indexed) + "\n")
        index_meta = {
            "offset": next_offset, "done": exhausted,
            "mode": index_mode,
            "blocked": False, "cards": len(indexed),
            "indexed_at": datetime.now().isoformat()}
        _atomic_write_text(meta_path, json.dumps(index_meta))
        print(f"[corroborate] index: +{len(cards)} new "
              f"(total {len(indexed)}, next_offset={next_offset}, "
              f"exhausted={exhausted})", flush=True)
    elif not indexed:
        print(f"[corroborate] no index yet at {idx_path} "
              "(pass --corroborate-index)", flush=True)

    # ── sub-step 2: signals for index cards (resumable by card id) ──
    # blocked = fetch failed (429 wall / circuit-open) — NOT done; the
    # audit (S7-A2 G) found blocked cards were permanently skipped. Retry
    # them; only matched records count as settled (blocked records are
    # replaced, not accumulated, by re-fetch).
    done_ids = {s.get("linkedin_job_id") for s in _load_jsonl(sig_path)
                if s.get("status") != "blocked"}
    todo_cards = [c for c in indexed if c["id"] not in done_ids]
    print(f"[corroborate] {len(indexed)} cards indexed, "
          f"{len(done_ids)} signals done, {len(todo_cards)} to fetch",
          flush=True)
    if todo_cards:
        batch = todo_cards[:args.signals_batch]
        # per-card checkpointing (on_record fires the moment each record
        # exists — a mid-batch kill loses NOTHING; an IO-failed record
        # simply isn't in the file → retried next run. The returned recs
        # are for counting only; NO batch append (would duplicate).
        with open(sig_path, "a", encoding="utf-8") as sf:

            def _checkpoint(rec: dict) -> None:
                sf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sf.flush()

            recs = provider.fetch_signals(batch, on_record=_checkpoint)
        n_ok = sum(1 for r in recs if r["status"] == "matched")
        n_blocked = sum(1 for r in recs if r["status"] == "blocked")
        remaining = len(todo_cards) - len(recs)
        print(f"[corroborate] signals +{len(recs)} "
              f"({n_ok} matched, {n_blocked} blocked, "
              f"{remaining} cards remaining)", flush=True)

    # ── join preview ──
    signals = _load_jsonl(sig_path)
    matched = corroborate.join_by_req_id(signals, req_ids)
    title_matched = corroborate.join_by_title(
        [s for s in signals if s.get("status") == "matched"],
        {r["reqId"]: r["title"] for r in rows}, args.company)
    only_title = {k: v for k, v in title_matched.items() if k not in matched}
    print(f"[corroborate] join preview: {len(matched)} by reqId, "
          f"+{len(only_title)} by title, {len(signals)} signals total",
          flush=True)
    return 0


# ── phase: tagfacets (OPTIONAL — S8-D gaps #2/#3) ─────────────────────────

def phase_facet_tags(args, out: Path) -> int:
    """Per-row workerSubType + jobFamilyGroup tagging via facet-partitioned
    lists — these classifications exist ONLY as facet values (the detail
    payload has no equivalent field; they resolve the intern/NCG policy
    flag and give every role the board's own category taxonomy).

    For each facet value from the page-0 census, lists the reqIds under
    the dump's own facets + that value (``iter_board_postings`` —
    cap-guarded, B1-partial) and appends {"reqId", "<param>":
    "<descriptor>"} lines to {out}.facet_tags.jsonl; finish folds them
    into the CSV (empty columns when this phase never ran).

    Resumable at (param, value) granularity: a completion marker line
    {"facetDone": …, "value": …} is appended per finished value and
    skipped on re-run (row lines written before a crash are re-emitted
    harmlessly — the finish join is idempotent last-wins).
    Run with the SAME --country/--time-type as --phase list, BEFORE
    --phase finish.
    """
    list_path = out.with_suffix(".list.jsonl")
    if not list_path.exists():
        print(f"no {list_path} — run --phase list first")
        return 2
    cfg = Config()
    board = workday.parse_board(args.board)
    try:
        first = workday._page(board, {}, 0, cfg)
    except Exception as exc:
        print(f"[tagfacets] page-0 fetch failed: {type(exc).__name__}: "
              f"{exc}", file=sys.stderr)
        return 1
    try:
        dump_facets = workday.resolve_facets(
            board, first, args.country or None, args.time_type or None)
    except ValueError as exc:
        print(f"[tagfacets] {exc}")
        return 2
    tag_path = out.with_suffix(".facet_tags.jsonl")
    done_pairs: set = set()
    for rec in _load_jsonl(tag_path):
        if rec.get("facetDone") and rec.get("value"):
            done_pairs.add((rec["facetDone"], rec["value"]))
    if done_pairs:
        print(f"[tagfacets] resuming: {len(done_pairs)} (facet, value) "
              f"pairs already tagged", flush=True)
    tagged = 0
    with open(tag_path, "a", encoding="utf-8") as tf:
        for param in _TAG_FACETS:
            values = [v for v in workday.facet_values(first, param) if v[2]]
            if not values:
                print(f"[tagfacets] no {param!r} facet on this board — "
                      f"column stays empty", flush=True)
                continue
            for descriptor, vid, _count in values:
                if (param, descriptor) in done_pairs:
                    continue
                sub_facets = dict(dump_facets)
                sub_facets[param] = [vid]
                try:
                    sub_first = workday._page(board, sub_facets, 0, cfg)
                except Exception as exc:
                    print(f"[tagfacets] {param}={descriptor!r} page-0 "
                          f"failed: {exc}", file=sys.stderr)
                    return 1
                try:
                    sub_rows, sub_meta = workday.iter_board_postings(
                        board, sub_facets, sub_first, cfg=cfg,
                        sleep_s=args.sleep)
                except RuntimeError as exc:   # capped sub-list (S8-D 1g)
                    print(f"[tagfacets] {exc}", file=sys.stderr)
                    return 1
                for rid in sub_rows:
                    # per-row checkpoint (2-min-bash doctrine)
                    tf.write(json.dumps(
                        {"reqId": rid, param: descriptor},
                        ensure_ascii=False) + "\n")
                    tf.flush()
                tf.write(json.dumps(
                    {"facetDone": param, "value": descriptor}) + "\n")
                tf.flush()
                tagged += len(sub_rows)
                note = "" if sub_meta.get("complete") else ", INCOMPLETE"
                print(f"[tagfacets] {param}={descriptor}: {len(sub_rows)} "
                      f"reqIds ({sub_meta.get('pages')} pages{note})",
                      flush=True)
                time.sleep(args.sleep)
    print(f"[tagfacets] done: +{tagged} tag rows this run → {tag_path}",
          flush=True)
    return 0


# ── phase: finish — CSV v2 + JSON v2 + validation report ─────────────────

def _state_codes(locations: list[str]) -> str:
    """Extract 'US, XX,' state codes → 'CA;NC;TX' ("" when none)."""
    codes = []
    for loc in locations:
        m = _STATE_RE.match(loc.strip())
        if m and m.group(1) not in codes:
            codes.append(m.group(1))
    return ";".join(codes)


def _derive_csv_row(r: dict, det: dict, sig: Optional[dict],
                    company: str, dump_date: str,
                    first_seen: str, facet_tags: Optional[dict] = None,
                    snapshot_date: str = "") -> dict:
    """One CSV row. `snapshot_date` (ISO) is the days-on-market reference
    date — today at finish time (all rows share one snapshot); defaults to
    today when omitted. `facet_tags` = {reqId: {workerSubType,
    jobFamilyGroup}} from the optional tagfacets phase (empty columns
    when absent)."""
    info = det.get("info") or {}
    primary = info.get("location") or r.get("locationsText") or ""
    additional = info.get("additionalLocations") or []
    locations = ([primary] if primary else []) + list(additional)
    if not locations and r.get("locationsText"):
        locations = [r["locationsText"]]
    today = date.today()
    snapshot = date.fromisoformat(snapshot_date) if snapshot_date else today
    start = info.get("startDate") or ""
    age = ""
    if start:
        try:
            age = (today - date.fromisoformat(start)).days
        except ValueError:
            pass
    req_year = ""
    m = re.match(r"[A-Za-z]+(\d{4})", r["reqId"] or "")
    if m:
        req_year = m.group(1)
    desc_html = info.get("jobDescription") or ""
    desc_text = html_to_text(desc_html)
    country = (info.get("country") or {}).get("descriptor", "")
    detail_error = det.get("error") or ""
    # detail-unreachable rows: locations/nLocations are UNKNOWN — surface
    # it honestly instead of nLocations=1 reading as "single location"
    # (audit S7-A1 F1: 2 rows shipped locations="6 Locations" nLocations=1)
    if detail_error and not info:
        locations = [r.get("locationsText") or ""]
    # ── v2.1 recency/completeness derivations (S8-C §7, S8-E3) ─────────
    end_date = str(info.get("endDate") or "")[:10]
    days_on_market, dom_basis = "", ""
    if start:
        try:
            days_on_market = (snapshot - date.fromisoformat(start)).days
            dom_basis = "startDate"
        except ValueError:
            pass
    # censoring: startDate predating the watch seed (or missing) means
    # the age below is a LOWER bound only — the posting may have been
    # reposted with a reset startDate before we ever observed it
    censored = "true" if (not start or start < WATCH_SEED.isoformat()) \
        else "false"
    repost_count = _slug_repost_count(
        r.get("externalPath") or r.get("url")
        or info.get("externalUrl") or "")
    deadline = _parse_application_deadline(desc_text) or end_date
    days_left = ""
    if deadline:
        try:
            days_left = (date.fromisoformat(deadline) - snapshot).days
        except ValueError:
            pass
    tag_row = (facet_tags or {}).get(r["reqId"]) or {}
    row = {
        "reqId": r["reqId"],
        "title": info.get("title") or r.get("title") or "",
        "company": company,
        "hiringOrg": det.get("hiringOrg") or "",
        "timeType": info.get("timeType") or "",
        "postedOn": r.get("postedOn") or info.get("postedOn") or "",
        "startDate": start,
        "postingAgeDays": age,
        "reqYear": req_year,
        "primaryLocation": primary,
        "nLocations": len(locations),
        "locations": "; ".join(locations),
        "remoteFlag": any("remote" in loc.lower() for loc in locations),
        "stateCodes": _state_codes(locations),
        "country": country,
        "questionnaire": bool(info.get("questionnaireId")),
        "similarJobsCount": det.get("similarJobsCount") or 0,
        "description": desc_text,
        "descriptionLength": len(desc_text),
        "url": r.get("url") or info.get("externalUrl") or "",
        "detailError": detail_error,
    }
    if sig:
        li_date = sig.get("linkedin_posted_date") or ""
        delta = ""
        if li_date and start:
            try:
                delta = (date.fromisoformat(li_date)
                         - date.fromisoformat(start)).days
            except ValueError:
                pass
        row.update({
            "linkedinUrl": sig.get("linkedin_url") or "",
            "linkedinPostedDate": li_date,
            "numApplicants": sig.get("num_applicants"),
            "applicantLabel": sig.get("applicants_label") or "",
            "dateDeltaDays": delta,
            "corroborationStatus": sig.get("status") or "",
            "matchMethod": sig.get("match_method") or "",
            "corroboratedOn": sig.get("fetched_at") or "",
        })
    else:
        row.update({
            "linkedinUrl": "", "linkedinPostedDate": "",
            "numApplicants": "", "applicantLabel": "", "dateDeltaDays": "",
            "corroborationStatus": corroborate.STATUS_NOT_CHECKED,
            "matchMethod": "", "corroboratedOn": "",
        })
    row["firstSeenDate"] = first_seen
    row["dumpDate"] = dump_date
    # ── v2.1 columns (#32-#41 — order per the module docstring) ────────
    row["endDate"] = end_date
    row["daysOnMarket"] = days_on_market
    row["daysOnMarketBasis"] = dom_basis
    row["censored"] = censored
    row["repostCount"] = repost_count
    row["lastResetDate"] = ""     # reserved for the watch's repost detector
    row["applicationDeadline"] = deadline
    row["daysLeftToApply"] = days_left
    row["workerSubType"] = tag_row.get("workerSubType") or ""
    row["jobFamilyGroup"] = tag_row.get("jobFamilyGroup") or ""
    return row


def phase_finish(args, out: Path) -> int:
    list_path = out.with_suffix(".list.jsonl")
    if not list_path.exists():
        print("no list file — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    details: dict[str, dict] = {}
    for d in _load_jsonl(out.with_suffix(".details.jsonl")):
        details[d["reqId"]] = d
    signals = _load_jsonl(out.with_suffix(".signals.jsonl"))
    req_ids = {r["reqId"] for r in rows}

    # facet tags (OPTIONAL tagfacets phase — S8-D gaps #2/#3): fold
    # {reqId: {workerSubType, jobFamilyGroup}} onto the CSV; empty columns
    # when the phase never ran (file absent). Row lines are last-wins —
    # duplicates from a resumed tagging run re-affirm the same value.
    facet_tags: dict[str, dict] = {}
    for t in _load_jsonl(out.with_suffix(".facet_tags.jsonl")):
        if t.get("facetDone") or not t.get("reqId"):
            continue          # completion markers / corrupt lines
        tag_d = facet_tags.setdefault(t["reqId"], {})
        for k in _TAG_FACETS:
            if t.get(k):
                tag_d[k] = t[k]

    # index completeness (B5): when the index finished, an unmatched
    # posting is genuinely no_match — "not cross-posted" must never
    # masquerade as "not checked" (audit S7-A2 D)
    index_done = False
    meta_path = out.with_suffix(".li_index.meta.json")
    if meta_path.exists():
        try:
            index_done = bool(
                json.loads(meta_path.read_text(encoding="utf-8"))
                .get("done"))
        except json.JSONDecodeError:
            pass

    # join: reqId exact first, then title fallback (1:1 greedy,
    # matchMethod recorded). req_dates = detail startDates for the
    # proximity disambiguation; req_locations (S8-E1) = detail
    # locations for the location-aware tiebreak in the title join.
    matched = [s for s in signals if s.get("status") == "matched"]
    blocked = [s for s in signals if s.get("status") == "blocked"]
    req_dates = {rid: (details.get(rid) or {}).get("info", {}).get(
        "startDate") or "" for rid in req_ids}
    req_locations: dict[str, str] = {}
    for rid in req_ids:
        info = (details.get(rid) or {}).get("info") or {}
        locs = [info.get("location") or ""] + list(
            info.get("additionalLocations") or [])
        loc_str = "; ".join(l for l in locs if l)
        if loc_str:
            req_locations[rid] = loc_str
    sig_by_req = corroborate.join_by_req_id(matched, req_ids)
    title_join = corroborate.join_by_title(
        matched, {r["reqId"]: r["title"] for r in rows}, args.company,
        req_dates={k: v for k, v in req_dates.items() if v},
        req_locations=req_locations)
    # blocked cards: title-join so blocked reqs read `blocked` (B5)
    blocked_join = corroborate.join_by_title(
        blocked, {r["reqId"]: r["title"] for r in rows}, args.company,
        req_dates={k: v for k, v in req_dates.items() if v})
    matched_rows = 0
    csv_rows: list[dict] = []
    enriched_rows: list[dict] = []
    dump_date = date.today().isoformat()
    first_seen = dump_date          # one-shot dump: first sighting = today
    for r in rows:
        det = details.get(r["reqId"]) or {}
        sig = sig_by_req.get(r["reqId"])
        if sig:
            sig = dict(sig)
            sig.setdefault("match_method", "reqId")
        else:
            sig = title_join.get(r["reqId"])
            if sig:
                sig = dict(sig)
                sig["match_method"] = "title"
            elif r["reqId"] in blocked_join:
                # posting maps to a card whose fetch was blocked — status
                # `blocked`, retryable, never "no_match" (B5)
                sig = dict(blocked_join[r["reqId"]])
            elif index_done:
                # index exhausted + no card for this posting = honestly
                # not cross-posted (within the indexed window)
                sig = {"status": "no_match"}
            else:
                sig = None
        if sig and not sig.get("fetched_at"):
            sig["fetched_at"] = sig.get("fetched_at") or ""
        row = _derive_csv_row(r, det, sig, r.get("company") or args.company,
                              dump_date, first_seen,
                              facet_tags=facet_tags,
                              snapshot_date=dump_date)
        csv_rows.append(row)
        enriched = dict(r)
        enriched["detail"] = det.get("info") or None
        enriched["hiringOrg"] = det.get("hiringOrg")
        enriched["similarJobsCount"] = det.get("similarJobsCount") or 0
        enriched["similarJobs"] = det.get("similarJobs") or []
        enriched["error"] = det.get("error")
        if facet_tags.get(r["reqId"]):
            enriched.update(facet_tags[r["reqId"]])
        if sig:
            enriched["signals"] = sig
        enriched_rows.append(enriched)
        if det.get("info"):
            matched_rows += 1

    # similar-job edges (S8-D gap #1): one line per (reqId, similar) pair —
    # rank = 1-based position in the board's own similarity list. Entries
    # whose slug carries no identifiable reqId are skipped (no join key).
    edge_rows: list[dict] = []
    for rid, det in details.items():
        for rank, s in enumerate(det.get("similarJobs") or [], 1):
            srid = s.get("reqId") or _slug_req_id(
                str(s.get("externalPath") or ""))
            if not srid:
                continue
            edge_rows.append({"reqId": rid, "similar_reqId": srid,
                              "similar_title": s.get("title") or "",
                              "rank": rank})
    edges_path = out.with_suffix(".similar_edges.jsonl")
    _atomic_write_text(edges_path, "\n".join(
        json.dumps(e, ensure_ascii=False) for e in edge_rows) + "\n")

    # JSON v2 (machine consumption — full nested)
    out.with_suffix(".json").write_text(json.dumps({
        "board": args.board,
        "company": args.company,
        "country": args.country,
        "time_type": args.time_type,
        "dumpDate": dump_date,
        "count": len(rows),
        "details_merged": matched_rows,
        "signals_matched": len(sig_by_req) + len(
            [k for k in title_join if k not in sig_by_req]),
        "similar_edges": len(edge_rows),
        "facet_tagged_rows": len(facet_tags),
        "rows": enriched_rows,
    }, indent=1, ensure_ascii=False), encoding="utf-8")

    # CSV v2 (user-facing) — utf-8-sig: Excel-safe bullets/newlines
    csv_path = out.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in csv_rows:
            w.writerow(row)

    # validation report
    n_desc = sum(1 for r in csv_rows if r["description"])
    n_loc = sum(1 for r in csv_rows if r["locations"]
                and "Locations" not in str(r["locations"])[:12])
    n_date = sum(1 for r in csv_rows if r["startDate"])
    n_sig = sum(1 for r in csv_rows
                if r["corroborationStatus"] == "matched")
    n_sig_blocked = sum(1 for r in csv_rows
                        if r["corroborationStatus"] == "blocked")
    n_sig_nomatch = sum(1 for r in csv_rows
                        if r["corroborationStatus"] == "no_match")
    n_sig_notchecked = sum(1 for r in csv_rows
                           if r["corroborationStatus"] == "not_checked")
    uniq = len({r["reqId"] for r in csv_rows})
    loc_sum = sum(r["nLocations"] for r in csv_rows)
    report = [
        f"rows: {len(csv_rows)} (unique reqIds: {uniq})",
        f"details merged: {matched_rows}",
        f"descriptions present: {n_desc}",
        f"locations enumerated (not 'N Locations'): {n_loc}",
        f"startDate present: {n_date}",
        f"signals: matched={n_sig} no_match={n_sig_nomatch} "
        f"blocked={n_sig_blocked} not_checked={n_sig_notchecked}",
        f"location entries total (sum nLocations): {loc_sum}",
        f"similar-job edges: {len(edge_rows)}",
    ]
    facets_path = out.with_suffix(".facets.json")
    if facets_path.exists():
        try:
            fx = json.loads(facets_path.read_text(encoding="utf-8"))
            n_fx = len(fx.get("facets") or {})
            n_fv = sum(len(v) for v in (fx.get("facets") or {}).values())
            report.append(f"facet census: {n_fx} facets / {n_fv} values "
                          f"({facets_path.name})")
        except json.JSONDecodeError:
            pass
    if facet_tags:
        n_ws = sum(1 for t in facet_tags.values() if t.get("workerSubType"))
        n_jf = sum(1 for t in facet_tags.values() if t.get("jobFamilyGroup"))
        report.append(f"facet-tagged rows: workerSubType={n_ws} "
                      f"jobFamilyGroup={n_jf} (from facet_tags.jsonl)")
    report_text = "\n".join(report)
    _atomic_write_text(out.with_suffix(".report.txt"), report_text)
    print(f"[finish] wrote {len(csv_rows)} rows ({matched_rows} details):")
    print(f"  {out.with_suffix('.json')}")
    print(f"  {csv_path}")
    print(f"  {edges_path} ({len(edge_rows)} edges)")
    print(report_text)
    missing = len(rows) - matched_rows
    return 1 if (missing and args.require_details) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", default="nvidia|wd5|nvidiaexternalcareersite",
                    help="'tenant|instance|site' (workday seed format)")
    ap.add_argument("--company", default="NVIDIA",
                    help="company display name (corroboration target)")
    ap.add_argument("--label", default="nvidia_us_fulltime",
                    help="output filename prefix")
    ap.add_argument("--country", default="United States",
                    help="locationHierarchy1 facet value (empty = all)")
    ap.add_argument("--time-type", default="Full time",
                    help="timeType facet value (empty = all)")
    ap.add_argument("--location", default="United States",
                    help="LinkedIn corroboration search location")
    ap.add_argument("--phase", default="finish",
                    choices=["list", "details", "corroborate",
                             "tagfacets", "finish"])
    ap.add_argument("--details-batch", type=int, default=150)
    ap.add_argument("--require-details", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--detail-sleep", type=float, default=0.25)
    ap.add_argument("--corroborate-index", action="store_true",
                    help="(corroborate phase) refresh the LinkedIn index")
    ap.add_argument("--index-mode", default="single",
                    choices=["single", "partitioned"],
                    help="(corroborate phase) LinkedIn index mode: single "
                         "query (default, back-compat) or partitioned "
                         "keyword×location slice matrix [S8-E1]")
    ap.add_argument("--li-index-pages", type=int, default=10,
                    help="LinkedIn index pages per invocation (10 cards/pg)")
    ap.add_argument("--li-slice-pages", type=int, default=3,
                    help="(partitioned index mode) pages per slice query "
                         "— 30 slices × 3 ≈ 90 requests/run [S8-E1]")
    ap.add_argument("--li-index-cards", type=int, default=100)
    ap.add_argument("--signals-batch", type=int, default=40,
                    help="LinkedIn detail fetches per invocation")
    ap.add_argument("--provider", default="linkedin",
                    choices=sorted(corroborate.PROVIDERS))
    ap.add_argument("--out-dir", default=str(
        REPO / "ingest" / "data" / "workday"))
    args = ap.parse_args()

    out = Path(args.out_dir) / args.label
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.phase == "list":
        return phase_list(args, out)
    if args.phase == "details":
        return phase_details(args, out)
    if args.phase == "corroborate":
        return phase_corroborate(args, out)
    if args.phase == "tagfacets":
        return phase_facet_tags(args, out)
    return phase_finish(args, out)


if __name__ == "__main__":
    sys.exit(main())
