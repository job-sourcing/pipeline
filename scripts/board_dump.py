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
  values — S8-D gaps #2/#3) → {label}.facet_tags.jsonl. Resumable at
  (param, value) granularity; the done-markers are population-
  fingerprinted against the listing (S9-C3 P1 — see phase_facet_tags).
- **finish**: assemble {out}.json + the v2.1 CSV + a validation report +
  {label}.similar_edges.jsonl (one role-similarity edge per line).

CSV v2.4 column ORDER (the contract — CSV_COLUMNS is the single source of
truth; 44 columns: v2.1 added #32-#41, v2.3 added #42-#43, v2.4 added
#44 — see docs/csv-v2-spec.md for per-column semantics):

    1 reqId                 2 title                3 company
    4 hiringOrg             5 timeType             6 postedOn
    7 startDate             8 postingAgeDays       9 reqYear
    10 primaryLocation      11 nLocations          12 locations
    13 remoteFlag           14 stateCodes          15 country
    16 questionnaire        17 similarJobsCount    18 description
    19 descriptionLength    20 url                 21 detailError
    22 linkedinUrl          23 linkedinPostedDate  24 numApplicants
    25 applicantLabel       26 dateDeltaDays       27 corroborationStatus
    28 matchMethod (reqId|title|titleMultiset|"")
                            29 firstSeenDate (watch state when present,
                            else dump date)   30 corroboratedOn
    31 dumpDate
    32 endDate              (detail endDate — 2.9% of rows)
    33 daysOnMarket         (snapshot_date − startDate; snapshot_date =
                            today at finish time, passed to every row)
    34 daysOnMarketBasis    ("startDate" | "startDate+linkedin" when the
                            LI card date moved the floor earlier | "")
    35 censored             ("true" when startDate < WATCH_SEED or is
                            missing — age is a LOWER bound only; else
                            "false")
    36 repostCount          (slug "…_JR####-N" suffix — Workday's own
                            repost counter; 0 when absent)
    37 lastResetDate        (the watch repost detector's measured
                            new_startDate for this req; "" when no event)
    38 applicationDeadline  ("Applications … accepted … until {date}"
                            from description — 98.3% coverage; falls back
                            to structured endDate. NOTE: a FLOOR, not a
                            guarantee — postings routinely stay live past
                            it; negative #39 on a live posting is normal)
    39 daysLeftToApply      (deadline − snapshot_date; empty when none)
    40 workerSubType        (tagfacets phase; empty when not run)
    41 jobFamilyGroup       (tagfacets phase; empty when not run)
    42 earliestEvidenceDate (min(startDate, linkedinPostedDate) — the
                            honest cross-source age floor, S9)
    43 crossSourceRepostEvidence ("" | reqId | title — the LI card
                            PREDATES startDate ⇒ repost evidence)
    44 applicantCensored    ("true"|"false"|"" — #24 is a BUCKET when
                            true; recomputed from (num, label), S9-audit)

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
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timezone
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
    # ── v2.3 addenda (S9: cross-source timing floor) ──────────────────
    "earliestEvidenceDate",  # 42 min(startDate, linkedinPostedDate) —
                             #    the honest cross-source age floor
    "crossSourceRepostEvidence",  # 43 ""|reqId|title — the LI card
                             #    PREDATES startDate ⇒ the req was on the
                             #    market before its (reset) startDate;
                             #    reqId-joins = CONFIRMED repost (the
                             #    card IS the same requisition)
    # ── v2.4 addenda (S9-audit A1/C4/D3: bucket censoring + provenance) ─
    "applicantCensored",     # 44 "true"|"false"|"" — the numApplicants in
                             #    col #24 is a BUCKET when true (floor 25
                             #    "among first", cap 200 "Over"): never
                             #    average #24 without this. RECOMPUTED
                             #    from (num, label) — not the stored flag
                             #    (752/1,465 live signals predate the key)
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


def _repair_jsonl_tail(path: Path) -> None:
    """B7 torn-write guard (S9-audit C1): a crash mid-append leaves a
    partial last line; the NEXT append welds itself onto it and both
    records vanish into one corrupt line that _load_jsonl silently
    skips. Truncate the un-terminated tail BEFORE appending. Call at
    the top of every phase that appends to the file."""
    if not path.exists() or path.stat().st_size == 0:
        return
    data = path.read_bytes()
    if data.endswith(b"\n"):
        return                      # clean tail — nothing to do
    last_nl = data.rfind(b"\n")
    good = data[:last_nl + 1] if last_nl >= 0 else b""
    lost = len(data) - len(good)
    with open(path, "wb") as f:
        f.write(good)
    print(f"[warn] repaired torn tail in {path.name} "
          f"({lost} partial bytes dropped — the crashed record will be "
          f"re-fetched)", file=sys.stderr)


def _load_details_state(det_path: Path) -> tuple[dict, dict]:
    """S9-audit C1/A5 P1 fix: the payload view prefers the LAST GOOD
    record. A failed re-fetch appends {reqId, error, attempts} which
    used to last-wins-REPLACE the good record at every consumer —
    finish shipped detailError + blank columns + similarJobsCount 0
    until a retry succeeded, and the 3-strike valve then FROZE the
    downgrade. Strikes (attempts) are tracked across ALL records; a
    settled row keeps its good payload while its refetch strikes
    accumulate independently.

    Returns (good, attempts): good = {reqId: last record WITH info};
    attempts = {reqId: max attempts seen on error records}."""
    good: dict[str, dict] = {}
    attempts: dict[str, int] = {}
    for d in _load_jsonl(det_path):
        rid = d.get("reqId")
        if not rid:
            continue
        if d.get("info"):
            good[rid] = d
        try:
            attempts[rid] = max(attempts.get(rid, 0),
                                int(d.get("attempts") or 0))
        except (TypeError, ValueError):
            pass
    return good, attempts


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    _repair_jsonl_tail(path)          # B7 guard (S9-audit C1)
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
    _repair_jsonl_tail(det_path)      # B7 guard (S9-audit C1)
    # S9-audit C1/A5 P1: LAST-GOOD-WINS payload view + separate strike
    # counter. A failed re-fetch used to last-wins-REPLACE the good
    # record at every consumer (finish shipped detailError + blank
    # columns until a retry succeeded; 3-strike then froze it). Now the
    # good record survives and refetch strikes accumulate independently
    # (a settled row whose refetch struck out keeps its old payload).
    good, attempts = _load_details_state(det_path)
    settled = set(good)
    # error rows retry with a 3-strike cap (audit S7-A2 H: transient 429s
    # were permanently losing descriptions). attempts accumulate per req
    # across ALL records (never reset — a success settles the row out of
    # the work set anyway).
    three_strikes = {rid for rid, a in attempts.items()
                     if a >= 3 and rid not in good}
    todo = [r for r in rows
            if r["reqId"] not in settled and r["reqId"] not in three_strikes]
    # S8-F backfill: rows settled BEFORE the similarJobs LIST capture
    # landed carry similarJobsCount but NOT the list (the count-only era,
    # 2026-09-13) — --refetch-similar re-fetches them (append-only; the
    # good-record view means a FAILED refetch no longer destroys the
    # settled payload). Same 3-strike/batch/checkpoint machinery as the
    # first pass.
    if args.refetch_similar:
        stale = [r for r in rows
                 if r["reqId"] in settled
                 and "similarJobs" not in (good.get(r["reqId"]) or {})
                 and attempts.get(r["reqId"], 0) < 3]
        todo = todo + stale
        print(f"[details] refetch-similar: {len(stale)} settled rows "
              f"lack the similarJobs list "
              f"({sum(1 for r in rows if r['reqId'] in settled and attempts.get(r['reqId'], 0) >= 3 and 'similarJobs' not in (good.get(r['reqId']) or {}))} past strike cap)",
              flush=True)
    n_err_rows = sum(1 for rid, a in attempts.items()
                     if a > 0 and rid not in good)
    print(f"[details] {len(rows)} rows, {len(settled)} settled, "
          f"{n_err_rows} error rows "
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
    n_errors = 0
    with open(det_path, "a", encoding="utf-8") as df:
        for i, r in enumerate(batch, 1):
            payload = workday.detail_payload(
                board, r["externalPath"], cfg)
            rec: dict = {"reqId": r["reqId"],
                         "fetched_at": datetime.now(timezone.utc
                                                    ).isoformat(
                                             timespec="seconds")}
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
                rec["error"] = "detail_unreachable"
                rec["attempts"] = attempts.get(r["reqId"], 0) + 1
                n_errors += 1
            df.write(json.dumps(rec, ensure_ascii=False) + "\n")
            df.flush()
            if i % 25 == 0:
                print(f"[details] {i}/{len(batch)} this batch "
                      f"({len(settled) + i}/{len(rows)} total)", flush=True)
    remaining = len(todo) - len(batch)
    if n_errors:
        # S9-audit C4 fail-green fix: an error-writing batch used to
        # exit 0 with "ALL N rows enriched" — errors are retryable but
        # ATTENTION-NEEDING; rc 1 until the run writes zero errors
        print(f"[details] batch done with {n_errors} ERROR record(s) "
              f"written (retryable; 3-strike capped) — rc 1",
              flush=True)
        return 1
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
    # S9-audit F1 fix: the preview goes through join_population WITH
    # req_dates/req_locations (loaded like finish/titlesearch do) — the
    # old preview composed the join without them AND without the
    # cross-tier reservation, drifting from what finish ships.
    signals = _load_jsonl(sig_path)
    det_by_req, _att = _load_details_state(
        out.with_suffix(".details.jsonl"))
    req_ids = {r["reqId"] for r in rows}
    prev_dates = {rid: (det_by_req.get(rid) or {}).get("info", {}).get(
        "startDate") or "" for rid in req_ids}
    prev_locs: dict[str, str] = {}
    for rid in req_ids:
        info = (det_by_req.get(rid) or {}).get("info") or {}
        locs = [info.get("location") or ""] + list(
            info.get("additionalLocations") or [])
        loc_str = "; ".join(l for l in locs if l)
        if loc_str:
            prev_locs[rid] = loc_str
    matched = corroborate.join_by_req_id(
        [s for s in signals if s.get("status") == "matched"], req_ids)
    matched_ids, _unmatched = corroborate.join_population(
        signals, {r["reqId"]: r["title"] for r in rows}, args.company,
        req_dates={k: v for k, v in prev_dates.items() if v},
        req_locations=prev_locs)
    n_reqid = len({rid for rid in matched if rid in matched_ids})
    print(f"[corroborate] join preview: {n_reqid} by reqId, "
          f"+{len(matched_ids) - n_reqid} by title, "
          f"{len(signals)} signals total",
          flush=True)
    return 0


# ── phase: titlesearch (OPTIONAL — S9, design-s9-titlesearch.md C2) ──────

# Terminal probe statuses (a req with one of these is CHECKED — B5);
# blocked / blocked_empty / error are RETRIED on the next invocation.
_TS_TERMINAL = frozenset({"hit_new", "hit_indexed", "no_card"})
# cross-run blocked/error strike cap for probe selection (S9-audit C2):
# a persistently walled req stops burning probes after this many
# blocked lines in the state (the per-run breaker reset used to re-burn
# the same head-of-line reqs every run)
_TS_STRIKE_CAP = 3


def phase_title_search(args, out: Path) -> int:
    """Targeted LinkedIn card DISCOVERY for no_match reqs (S9).

    The partitioned index (keyword×location slices) misses ~60% of
    unmatched reqs' cards — non-engineering and niche titles never
    surface under the 6 keyword suffixes (probe evidence: 62.5% hit
    rate on 325/770 reqs, 2026-09-15). This phase searches each no_match
    req's EXACT title (one relevance-sorted guest page) and appends
    verbatim-matching cards to the li_index (source: "titleSearch") —
    the corroborate phase's signals sub-step then fetches their detail
    pages and finish joins them (title tier / C3 multiset tier).

    State: {out}.title_search.jsonl — one line per probed reqId; ONLY
    terminal lines (hit_new|hit_indexed|no_card) count as probed;
    blocked/error lines retry. Meta {out}.title_search.meta.json gates
    finish: an INCOMPLETE titlesearch ships unprobed reqs as
    not_checked, never no_match (the S7-A2-D B5 class).

    Resumable; batch-knobbed (--title-search-batch, default 50);
    circuit breaker after TITLE_SEARCH_BREAKER consecutive blocked/
    blocked_empty. Run AFTER --phase corroborate (needs signals +
    index), repeat until "done: true", then --phase corroborate again
    (fetches the new cards' signals), then --phase finish.
    """
    list_path = out.with_suffix(".list.jsonl")
    sig_path = out.with_suffix(".signals.jsonl")
    idx_path = out.with_suffix(".li_index.jsonl")
    if not list_path.exists():
        print(f"no {list_path} — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    signals = _load_jsonl(sig_path)
    if not signals:
        print(f"no {sig_path} — run --phase corroborate first")
        return 2
    details, _att = _load_details_state(
        out.with_suffix(".details.jsonl"))
    req_ids = {r["reqId"] for r in rows}

    # current join population (SAME functions finish uses — req_dates +
    # req_locations included; design review finding 8)
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
    matched_ids, no_match_ids = corroborate.join_population(
        [s for s in signals if s.get("status") == "matched"],
        {r["reqId"]: r["title"] for r in rows}, args.company,
        req_dates={k: v for k, v in req_dates.items() if v},
        req_locations=req_locations)
    print(f"[titlesearch] join population: {len(matched_ids)} matched, "
          f"{len(no_match_ids)} no_match", flush=True)

    # resume state — terminal lines only count as probed; blocked/error
    # lines accumulate a strike (S9-audit C2 P2: the per-run breaker
    # reset re-burned the same head-of-line reqs 4 probes/run against a
    # persistent wall — a cross-run strike cap, like the details 3-strike,
    # bounds total probes per walled req)
    ts_path = out.with_suffix(".title_search.jsonl")
    ts_meta_path = out.with_suffix(".title_search.meta.json")
    _repair_jsonl_tail(ts_path)
    _repair_jsonl_tail(idx_path)
    probed: dict[str, str] = {}
    ts_strikes: dict[str, int] = {}
    for rec in _load_jsonl(ts_path):
        st = rec.get("status")
        rid = rec.get("reqId")
        if not rid:
            continue
        if st in _TS_TERMINAL:
            probed[rid] = st
        elif st:
            ts_strikes[rid] = ts_strikes.get(rid, 0) + 1
    capped = {rid for rid, n in ts_strikes.items() if n >= _TS_STRIKE_CAP}
    todo = [r for r in rows if r["reqId"] in no_match_ids
            and r["reqId"] not in probed
            and r["reqId"] not in capped]
    print(f"[titlesearch] {len(probed)} terminal-probed, "
          f"{len(todo)} to probe this round "
          f"({len(capped)} past {_TS_STRIKE_CAP}-strike cap)", flush=True)

    # known card ids: li_index + every card appended in THIS batch
    # (refreshed per accepted card — sibling reqs verbatim-hit the same
    # card; a stale set double-appends; design review finding 6)
    known_ids = {str(c.get("id")) for c in _load_jsonl(idx_path)}
    provider = corroborate.get_provider(args.provider)
    batch = todo[:args.title_search_batch]
    n_hit = n_new = n_nocard = n_retry = n_idx = 0
    consecutive_blocked = 0
    aborted = False
    if batch:
        with open(ts_path, "a", encoding="utf-8") as tf, \
                open(idx_path, "a", encoding="utf-8") as ix:
            for i, r in enumerate(batch):
                title = (r.get("title") or "").strip()
                loc = corroborate.li_location(
                    r.get("primaryLocation") or "")
                rec = {"reqId": r["reqId"], "title": title,
                       "primaryLocation": r.get("primaryLocation") or "",
                       "query_location": loc}
                hits, exc = provider.search_title(
                    title, loc, known_ids, company=args.company,
                    mark_indexed=True)
                if exc is not None:
                    rec["status"] = "blocked_empty" if "blocked_empty" \
                        in str(exc) else "blocked" if (
                            "403" in str(exc) or "blocked" in str(exc).lower()
                        ) else "error"
                    rec["error"] = str(exc)[:200]
                    n_retry += 1
                    consecutive_blocked += 1
                    if consecutive_blocked >= corroborate.TITLE_SEARCH_BREAKER:
                        rec["aborted"] = True
                        tf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        print(f"[titlesearch] CIRCUIT BREAKER after "
                              f"{consecutive_blocked} consecutive blocks "
                              f"— batch stopped at {i + 1}/{len(batch)}",
                              flush=True)
                        aborted = True
                        break
                else:
                    consecutive_blocked = 0
                    # search_title(mark_indexed=True) returns known cards
                    # flagged `indexed` instead of dropping them
                    # (S9-audit B3/F1 P2: hit_indexed was unproducible —
                    # known_ids exclusion folded "card exists but is
                    # indexed" into no_card)
                    new_hits = [h for h in hits if not h.get("indexed")]
                    rec["hits"] = [
                        {k: h.get(k) for k in
                         ("id", "title", "location", "date", "url")}
                        for h in hits]
                    for h in new_hits:
                        card = dict(h)
                        card.pop("indexed", None)
                        card["source"] = "titleSearch"
                        ix.write(json.dumps(card, ensure_ascii=False) + "\n")
                        known_ids.add(str(h.get("id")))   # in-batch refresh
                    if new_hits:
                        rec["status"] = "hit_new"
                        n_hit += 1
                        n_new += len(new_hits)
                    elif hits:
                        # verbatim card(s) EXIST but are already indexed
                        # (sibling-consumed or list-era) — the join had
                        # its chance; this is a CHECKED non-match
                        rec["status"] = "hit_indexed"
                        n_idx += 1
                    else:
                        rec["status"] = "no_card"
                        n_nocard += 1
                # flush order (S9-audit C2 P2): the INDEX append is made
                # durable BEFORE the state line — a crash between flushes
                # leaves a durable card with no hit_new line (re-probed,
                # classified hit_indexed — idempotent) instead of a
                # hit_new line whose card was lost (permanent wrong
                # no_match)
                tf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ix.flush()
                tf.flush()
                print(f"  [{i + 1}/{len(batch)}] {r['reqId']} "
                      f"{rec['status']}", flush=True)
                time.sleep(corroborate.TITLE_SEARCH_PAUSE_S)

    # completion meta — the finish gate (P0 finding 1). Recomputed from
    # the state file so it reflects THIS batch's appends.
    all_probed: dict[str, str] = {}
    for rec in _load_jsonl(ts_path):
        if rec.get("reqId") and rec.get("status") in _TS_TERMINAL:
            all_probed[rec["reqId"]] = rec["status"]
    population_now = no_match_ids  # population snapshot for THIS run
    unprobed = [rid for rid in population_now if rid not in all_probed]
    done = not unprobed and not aborted
    _atomic_write_text(ts_meta_path, json.dumps({
        "done": done, "probed": len(all_probed),
        "terminal": len(all_probed),
        "population": len(population_now),
        "unprobed": len(unprobed)}, indent=1))
    print(f"[titlesearch] batch: {n_hit} hits ({n_new} new cards), "
          f"{n_idx} hit_indexed, {n_nocard} no_card, "
          f"{n_retry} retryable; "
          f"state: {len(all_probed)} terminal / {len(population_now)} "
          f"population → done={done}", flush=True)
    if not done:
        print("[titlesearch] re-run this phase until done=true, then "
              "--phase corroborate (new cards' signals), then finish",
              flush=True)
    return 0


# ── phase: tagfacets (OPTIONAL — S8-D gaps #2/#3) ─────────────────────────

def _board_pop_fingerprint(list_rows: list) -> tuple:
    """Population fingerprint of a board listing: (sha256 over the sorted
    reqId set, row count). The facet done-markers are keyed against this
    (S9-C3 P1) — tags are only valid for the population they were
    computed over."""
    req_ids = sorted({r["reqId"] for r in list_rows if r.get("reqId")})
    digest = hashlib.sha256(
        "\n".join(req_ids).encode("utf-8")).hexdigest()
    return digest, len(req_ids)


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

    Resumable at (param, value) granularity, POPULATION-FINGERPRINTED
    (S9-C3 P1 — the Sep-15 drift: a board refresh that added rows under
    EXISTING facet values reused the stale markers and shipped +0 tags,
    rc=0). The marker file carries a population record line
    {"facetPop": {"digest", "rows"}} — sha256 over {out}.list.jsonl's
    sorted reqId set — and ONLY markers after the LAST such record are
    trusted, and only while that digest matches the current listing.
    Any population change supersedes every marker: a fresh record is
    appended (the file stays append-only — crash-safe, no rewrite) and
    all values re-tag; row lines are idempotent last-wins at finish, so
    re-emission is safe. Design trade-off, deliberate: a per-value
    count re-check (~1 page-0 per done value per resume) would re-tag
    only the values whose totals moved, but pays that check on EVERY
    resume; the digest makes an unchanged-board resume FREE (zero
    sub-list requests) and invalidates unconditionally on any change.
    Residual, accepted: a same-population reclassification (one reqId
    swapping facet values while the reqId set stays constant) keeps its
    stale tag — theoretical on Workday, and the tag reflects the value
    at tagging time. A marker is written ONLY when that value's
    sub-list paginated to completion (S9-C3 P2 — a B1 network partial
    leaves the marker unwritten so the tail is re-fetched next run).
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
    digest, board_rows = _board_pop_fingerprint(_load_jsonl(list_path))
    done_pairs: set = set()
    stored_digest = None    # digest of the last facetPop record, if any
    for rec in _load_jsonl(tag_path):
        if rec.get("facetPop"):
            # population record: everything written BEFORE it was tagged
            # against a different (or unknown) board — superseded
            stored_digest = rec["facetPop"].get("digest")
            done_pairs = set()
        elif rec.get("facetDone") and rec.get("value"):
            done_pairs.add((rec["facetDone"], rec["value"]))
    rebase = stored_digest != digest
    if rebase:
        if stored_digest is not None or done_pairs:
            print(f"[tagfacets] board population changed since the markers "
                  f"were written ({board_rows} rows now) — superseding "
                  f"{len(done_pairs)} stale marker(s), re-tagging all values",
                  flush=True)
        done_pairs = set()
    elif done_pairs:
        print(f"[tagfacets] resuming: {len(done_pairs)} (facet, value) "
              f"pairs already tagged (board unchanged, {board_rows} rows)",
              flush=True)
    tagged = 0
    incomplete = 0
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
                if rebase:
                    # the superseding population record — written lazily
                    # (just before this run's first row line) so a board
                    # with no tag facets never touches the file, and a
                    # crash before any fetch leaves the old state intact
                    tf.write(json.dumps(
                        {"facetPop": {"digest": digest,
                                      "rows": board_rows}}) + "\n")
                    tf.flush()
                    rebase = False
                for rid in sub_rows:
                    # per-row checkpoint (2-min-bash doctrine)
                    tf.write(json.dumps(
                        {"reqId": rid, param: descriptor},
                        ensure_ascii=False) + "\n")
                    tf.flush()
                if sub_meta.get("complete"):
                    # S9-C3 P2: the marker is earned ONLY by a sub-list
                    # that paginated to completion — a B1 network partial
                    # must leave the pair unmarked so the un-fetched tail
                    # is retried on the next run (rows already written
                    # are harmless: idempotent last-wins at finish)
                    tf.write(json.dumps(
                        {"facetDone": param, "value": descriptor}) + "\n")
                    tf.flush()
                else:
                    incomplete += 1
                tagged += len(sub_rows)
                note = ("" if sub_meta.get("complete") else
                        ", INCOMPLETE — marker withheld, tail retried "
                        "next run")
                print(f"[tagfacets] {param}={descriptor}: {len(sub_rows)} "
                      f"reqIds ({sub_meta.get('pages')} pages{note})",
                      flush=True)
                time.sleep(args.sleep)
    tail = (f" ({incomplete} value(s) INCOMPLETE — re-run this phase to "
            f"retry their tails)" if incomplete else "")
    print(f"[tagfacets] done: +{tagged} tag rows this run → {tag_path}{tail}",
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
                    snapshot_date: str = "",
                    last_reset: str = "") -> dict:
    """One CSV row. `snapshot_date` (ISO) is the days-on-market AND
    posting-age reference date — today at finish time (all rows share one
    snapshot); defaults to today when omitted (S9-audit B2r: postingAgeDays
    used to drift with wall-clock — now frozen to the snapshot like every
    other derived date). `facet_tags` = {reqId: {workerSubType,
    jobFamilyGroup}} from the optional tagfacets phase (empty columns when
    absent). `last_reset` = the watch's repost-detector new_startDate for
    this req ("" when no event) — fills #37."""
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
            age = (snapshot - date.fromisoformat(start)).days
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
    n_locations: object = len(locations)
    if detail_error and not info:
        locations = [r.get("locationsText") or ""]
        # S9-audit C4 trap #11: locationsText "6 Locations" with NO
        # detail → the count is UNKNOWN, not 1
        n_locations = ""
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
    # ── v2.3 cross-source timing floor (S9): a LinkedIn card date that
    #    PREDATES the Workday startDate is lower-bound evidence the req
    #    existed earlier (Workday resets startDate on repost — S8-C;
    #    reqId-joined cards are the same requisition ⇒ CONFIRMED
    #    repost; title-joined ⇒ suggestive). daysOnMarket then measures
    #    from the earliest evidence and says so in the basis column.
    earliest = start
    repost_evidence = ""
    li_date = (sig or {}).get("linkedin_posted_date") or ""
    if li_date and start and li_date < start[:10]:
        earliest = li_date[:10]
        # evidence CLASS, not match method (S9-audit A3/F1): the
        # documented enum is ""|reqId|title — multiset rows report
        # `title` (their card was found by title, tier is provenance)
        method = (sig or {}).get("match_method") or "title"
        repost_evidence = ("reqId" if method == "reqId" else "title")
    elif li_date and not start:
        earliest = li_date[:10]
    if earliest and earliest != start:
        try:
            days_on_market = (snapshot - date.fromisoformat(
                earliest)).days
            dom_basis = "startDate+linkedin"
        except ValueError:
            pass
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
        "nLocations": n_locations,
        "locations": "; ".join(locations),
        "remoteFlag": "true" if any(
            "remote" in loc.lower() for loc in locations) else "false",
        "stateCodes": _state_codes(locations),
        "country": country,
        "questionnaire": "true" if info.get("questionnaireId") else "false",
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
    # S9-audit A6r/C4: filled from the watch's repost events (the
    # new_startDate the detector measured; last event wins) — was
    # hard-coded "" while the data sat in-tree
    row["lastResetDate"] = last_reset
    row["applicationDeadline"] = deadline
    row["daysLeftToApply"] = days_left
    row["workerSubType"] = tag_row.get("workerSubType") or ""
    row["jobFamilyGroup"] = tag_row.get("jobFamilyGroup") or ""
    # ── v2.3 columns (#42-#43) ─────────────────────────────────────
    row["earliestEvidenceDate"] = earliest
    row["crossSourceRepostEvidence"] = repost_evidence
    # ── v2.4 column (#44) ──────────────────────────────────────────
    # RECOMPUTED from (num, label) — D3: projecting the stored flag
    # under-reports (752/1,465 live signals predate the
    # applicantCensored key; 356 of them bucketed)
    row["applicantCensored"] = (
        "true" if sig and corroborate._applicant_censored(
            sig.get("num_applicants"),
            sig.get("applicants_label") or "") else
        "false" if sig else "")
    return row


def _watch_first_seen(out: Path) -> dict[str, str]:
    """{reqId: first_seen} from the board-watch state for THIS board
    (S9-audit A1/C4/D2: firstSeenDate shipped dump_date on all 1,406
    rows while the watch state held real first sightings up to 6 days
    earlier — the column doc promised "(dump/watch)"). Missing state ⇒
    {} (one-shot dumps keep the dump-date fallback)."""
    state = out.parent.parent / "board_watch" / f"{out.stem}.state.jsonl"
    if not state.exists():
        return {}
    seen: dict[str, str] = {}
    for rec in _load_jsonl(state):
        rid = rec.get("reqId")
        fs = rec.get("first_seen")
        if rid and fs:
            seen[rid] = fs        # last-wins (state is rewritten whole)
    return seen


def _watch_repost_resets(out: Path) -> dict[str, str]:
    """{reqId: new_startDate} from the watch's repost events (S9-audit
    A6r/C4: lastResetDate was hard-coded "" while the events sat
    in-tree — 37 events, each with the measured new startDate). Last
    event per req wins."""
    path = out.parent.parent / "board_watch" / f"{out.stem}.reposts.jsonl"
    if not path.exists():
        return {}
    resets: dict[str, str] = {}
    for rec in _load_jsonl(path):
        rid = rec.get("reqId")
        ns = str(rec.get("new_startDate") or "")[:10]
        if rid and ns:
            resets[rid] = ns
    return resets


def phase_finish(args, out: Path) -> int:
    list_path = out.with_suffix(".list.jsonl")
    if not list_path.exists():
        print("no list file — run --phase list first")
        return 2
    rows = _load_jsonl(list_path)
    # S9-audit C1/A5 P1: last-good-record view — a failed refetch's
    # error record no longer shadows the settled payload at finish
    details, _att = _load_details_state(
        out.with_suffix(".details.jsonl"))
    signals = _load_jsonl(out.with_suffix(".signals.jsonl"))
    req_ids = {r["reqId"] for r in rows}

    # facet tags (OPTIONAL tagfacets phase — S8-D gaps #2/#3): fold
    # {reqId: {workerSubType, jobFamilyGroup}} onto the CSV; empty columns
    # when the phase never ran (file absent). Row lines are last-wins —
    # duplicates from a resumed tagging run re-affirm the same value.
    # facetDone markers and facetPop population records are skipped.
    facet_tags: dict[str, dict] = {}
    for t in _load_jsonl(out.with_suffix(".facet_tags.jsonl")):
        if (t.get("facetDone") or t.get("facetPop")
                or not t.get("reqId")):
            continue          # completion markers / corrupt lines
        tag_d = facet_tags.setdefault(t["reqId"], {})
        for k in _TAG_FACETS:
            if t.get(k):
                tag_d[k] = t[k]

    # facet tag COVERAGE (S9-C3 P2): the tag artifact is append-only and
    # nothing at finish invalidates it — make any board row it fails to
    # cover VISIBLE (report.txt + stdout + a stderr warning) instead of
    # silently shipping empty #40/#41. A row counts as covered only for
    # the params the artifact actually carries (a board without
    # jobFamilyGroup must not read as "0% covered"); rc semantics are
    # deliberately unchanged (separate fix's scope).
    untagged_ids: list = []
    if facet_tags:
        tag_params = {k for k in _TAG_FACETS
                      if any(t.get(k) for t in facet_tags.values())}
        untagged_ids = sorted(
            rid for rid in req_ids
            if not all((facet_tags.get(rid) or {}).get(k)
                       for k in tag_params))

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

    # S9 titlesearch completion gate (design review finding 1, P0):
    # no_match is only final when the targeted title-search ran to
    # completion over the no_match population. An INCOMPLETE (or
    # interrupted-by-circuit-breaker) titlesearch ships unprobed reqs
    # as not_checked — B5. Absent state = the pre-S9 semantics stand
    # (index-done ⇒ no_match): the titlesearch phase is optional and
    # its absence must not regress old dumps.
    ts_state_path = out.with_suffix(".title_search.jsonl")
    ts_meta_path = out.with_suffix(".title_search.meta.json")
    ts_started = ts_state_path.exists()
    ts_done = False
    if ts_meta_path.exists():
        try:
            ts_done = bool(json.loads(
                ts_meta_path.read_text(encoding="utf-8")).get("done"))
        except json.JSONDecodeError:
            pass
    ts_terminal: set[str] = set()
    if ts_started:
        for rec in _load_jsonl(ts_state_path):
            if rec.get("reqId") and rec.get("status") in (
                    "hit_new", "hit_indexed", "no_card"):
                ts_terminal.add(rec["reqId"])

    # join: ONE canonical composition (S9-audit P0 fix — the tiers
    # were composed independently and re-served reqId-tier cards to
    # title siblings; corroborate.compose_join now reserves consumed
    # cards and drops served reqs from the title pool). req_dates =
    # detail startDates for the proximity disambiguation; req_locations
    # (S8-E1) = detail locations for the location-aware tiebreak.
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
    sig_by_req, title_join, blocked_join = corroborate.compose_join(
        signals, {r["reqId"]: r["title"] for r in rows}, args.company,
        req_dates={k: v for k, v in req_dates.items() if v},
        req_locations=req_locations)
    matched_rows = 0
    csv_rows: list[dict] = []
    enriched_rows: list[dict] = []
    dump_date = date.today().isoformat()
    # S9-audit A1/C4: first sighting from the WATCH state when it
    # covers this board (one-shot dumps keep the dump-date fallback)
    fs_map = _watch_first_seen(out)
    rs_map = _watch_repost_resets(out)
    if fs_map:
        n_fs = sum(1 for r in rows if r["reqId"] in fs_map)
        print(f"[finish] firstSeenDate: {n_fs}/{len(rows)} rows from "
              f"the watch state (rest = dump date)", flush=True)
    if rs_map:
        n_rs = sum(1 for r in rows if r["reqId"] in rs_map)
        print(f"[finish] lastResetDate: {n_rs} rows from "
              f"{len(rs_map)} watch repost events", flush=True)
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
                sig["match_method"] = ("titleMultiset"
                                       if sig.get("match_tier") == "multiset"
                                       else "title")
            elif r["reqId"] in blocked_join:
                # posting maps to a card whose fetch was blocked — status
                # `blocked`, retryable, never "no_match" (B5)
                sig = dict(blocked_join[r["reqId"]])
            elif index_done and (not ts_started
                                 or r["reqId"] in ts_terminal):
                # index exhausted + PER-REQ terminal probe state (or
                # pre-S9 semantics when titlesearch never started) =
                # honestly not cross-posted (S9-audit P1: the old ts_done
                # shortcut shipped unprobed reqs — matched at titlesearch
                # time, later drifted out — as no_match; JR2013322 was
                # the live case; a drifted row now reads not_checked
                # until the next titlesearch run re-probes it)
                sig = {"status": "no_match"}
            elif index_done:
                # S9: index done but titlesearch incomplete and this req
                # was never terminally probed — not_checked (B5)
                sig = {"status": "not_checked"}
            else:
                sig = None
        if sig and not sig.get("fetched_at"):
            sig["fetched_at"] = sig.get("fetched_at") or ""
        row = _derive_csv_row(
            r, det, sig, r.get("company") or args.company,
            dump_date, fs_map.get(r["reqId"]) or dump_date,
            facet_tags=facet_tags, snapshot_date=dump_date,
            last_reset=rs_map.get(r["reqId"], ""))
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
        report.append(
            f"facet tag coverage: {len(req_ids) - len(untagged_ids)}"
            f"/{len(req_ids)} rows ({len(untagged_ids)} untagged)")
    report_text = "\n".join(report)
    _atomic_write_text(out.with_suffix(".report.txt"), report_text)
    print(f"[finish] wrote {len(csv_rows)} rows ({matched_rows} details):")
    print(f"  {out.with_suffix('.json')}")
    print(f"  {csv_path}")
    print(f"  {edges_path} ({len(edge_rows)} edges)")
    print(report_text)
    if untagged_ids:
        # S9-C3 P2: the coverage gap must never ship silently — loud on
        # stderr too. When the listing itself is incomplete the gap is a
        # moving target: say so instead of pointing only at tagfacets.
        listing_note = ""
        try:
            if not json.loads(out.with_suffix(".list.status").read_text(
                    encoding="utf-8")).get("complete"):
                listing_note = (" — board listing itself INCOMPLETE, "
                                "re-run --phase list first")
        except (OSError, json.JSONDecodeError):
            pass
        print(f"[finish] WARNING: {len(untagged_ids)} board row(s) have "
              f"no facet tags — workerSubType/jobFamilyGroup ship empty; "
              f"re-run --phase tagfacets{listing_note}", file=sys.stderr)
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
                             "titlesearch", "tagfacets", "finish"])
    ap.add_argument("--details-batch", type=int, default=150)
    ap.add_argument("--require-details", action="store_true")
    ap.add_argument("--refetch-similar", action="store_true",
                    help="(details phase) re-fetch settled rows whose "
                         "detail record predates the similarJobs capture "
                         "[S8-F backfill]")
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
    ap.add_argument("--title-search-batch", type=int, default=50,
                    help="(titlesearch phase) no_match reqs probed per "
                         "invocation [S9]")
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
    if args.phase == "titlesearch":
        return phase_title_search(args, out)
    if args.phase == "tagfacets":
        return phase_facet_tags(args, out)
    return phase_finish(args, out)


if __name__ == "__main__":
    sys.exit(main())
