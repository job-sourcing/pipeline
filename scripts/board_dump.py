#!/usr/bin/env python3
"""Generic exhaustive per-company board snapshot (board-dump v2.4).

Generalizes scripts/workday_dump.py (the NVIDIA pilot) into the repeatable
flow the user asked to productize (design-board-v2.md D3):

  list → details → [corroborate] → [titlesearch] → [tagfacets] →
  [questionnaires] → finish

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
- **titlesearch** (OPTIONAL, S9 — run after corroborate, repeat until
  the meta reports done): per-no_match-req EXACT-title LinkedIn guest
  search; verbatim-matching cards append to {label}.li_index.jsonl
  (source "titleSearch") — re-run corroborate afterwards to fetch the
  new cards' signals. State {label}.title_search.jsonl (terminal
  statuses hit_new|hit_indexed|no_card; blocked/error retried);
  meta {label}.title_search.meta.json gates finish: an INCOMPLETE
  titlesearch ships unprobed reqs not_checked, never no_match.
- **tagfacets** (OPTIONAL, run before finish, same --country/--time-type
  as the list): per-row workerSubType + jobFamilyGroup tagging via
  facet-partitioned lists (these classifications exist ONLY as facet
  values — S8-D gaps #2/#3) → {label}.facet_tags.jsonl. Resumable at
  (param, value) granularity; the done-markers are population-
  fingerprinted against the listing (S9-C3 P1 — see phase_facet_tags).
- **questionnaires** (OPTIONAL, run before finish; needs details): the
  application-questionnaire DEFINITIONS behind the per-posting
  `questionnaireId`. Workday serves them from a separate calypso CXS
  endpoint — `GET /wday/calypso/cxs/common/{tenant}/questionnaire/{id}`
  (no auth, no site segment; discovered S11 via the apply-flow SPA) —
  and they are SHARED across postings (NVIDIA live: 4 distinct IDs for
  1,541 reqs), so the phase dedups and fetches each id ONCE into
  {label}.questionnaires.jsonl (append-only, id-idempotent — re-runs
  skip fetched ids, failures retry). finish joins by id and emits the
  linked {label}.questionnaires.csv (one row per question).
- **finish**: assemble {out}.json + the v2.6 CSV + a validation report +
  {label}.similar_edges.jsonl (one role-similarity edge per line) + the
  linked {label}.questionnaires.csv and {label}.h1b_lca.csv when their
  phases ran.

CSV v2.6 column ORDER (the contract — CSV_COLUMNS is the single source of
truth; 48 columns: v2.5 removed reqYear and re-purposed questionnaire →
questionnaireId; v2.6 appended the H-1B wage-band columns #44-#48 — see
docs/csv-v2-spec.md for per-column semantics):

    1 reqId                 2 title                3 company
    4 hiringOrg             5 timeType             6 postedOn
    7 startDate             8 postingAgeDays
    9 primaryLocation      10 nLocations          11 locations
    12 remoteFlag           13 stateCodes          14 country
    15 questionnaireId      16 similarJobsCount    17 description
    18 descriptionLength    19 url                 20 detailError
    21 linkedinUrl          22 linkedinPostedDate  23 numApplicants
    24 applicantLabel       25 dateDeltaDays       26 corroborationStatus
    27 matchMethod (reqId|title|titleMultiset|"")
                            28 firstSeenDate (watch state when present,
                            else dump date)   29 corroboratedOn
    30 dumpDate
    31 endDate              (detail endDate — 3.1% of rows)
    32 daysOnMarket         (snapshot_date − earliest evidence date —
                            startDate, or the earlier LI card date per
                            #33; snapshot_date = today at finish time,
                            passed to every row)
    33 daysOnMarketBasis    ("startDate" | "startDate+linkedin" when the
                            LI card date moved the floor earlier | "")
    34 censored             ("true" when startDate < WATCH_SEED, is
                            missing, or the watch saw the req BEFORE its
                            current startDate (a measured repost reset —
                            S11) — age is a LOWER bound only; else
                            "false")
    35 repostCount          (slug "…_JR####-N" suffix — Workday's own
                            repost counter; 0 when absent)
    36 lastResetDate        (the watch repost detector's measured
                            new_startDate for this req; "" when no event)
    37 applicationDeadline  ("Applications … accepted … until {date}"
                            from description — 98.9% coverage; falls back
                            to structured endDate. NOTE: a FLOOR, not a
                            guarantee — postings routinely stay live past
                            it)
    38 daysLeftToApply      (deadline − snapshot_date; "" when none AND
                            "" when the floor already elapsed on a live
                            posting — the window auto-extended, days-left
                            is UNKNOWN, never negative — S11)
    39 workerSubType        (tagfacets phase; empty when not run)
    40 jobFamilyGroup       (tagfacets phase; empty when not run)
    41 earliestEvidenceDate (min(startDate, linkedinPostedDate) — the
                            honest cross-source age floor, S9)
    42 crossSourceRepostEvidence ("" | reqId | title — the LI card
                            PREDATES startDate ⇒ repost evidence)
    43 applicantCensored    ("true"|"false"|"" — #23 is a BUCKET when
                            true; recomputed from (num, label), S9-audit)
    44 h1bFilings           (DOL H-1B/LCA certified NVIDIA filings
                            backing #45-#47 — scripts/h1b_extract.py
                            + .github/workflows/h1b-extract.yml)
    45 h1bWageP25           (annualized OFFERED wage p25, USD)
    46 h1bWageP50           (…median)
    47 h1bWageP75           (…p75)
    48 h1bMatchBasis        ("title+state" | "title" | "subset+state" |
                            "subset" | "" — tier + granularity; see
                            _derive_h1b_columns for the audited rules)
    49 h1bMatchTitle        (the matched pool's most-frequent raw LCA
                            title — the audit string for which LCA
                            population produced the band)

v2.5 REMOVED `reqYear` (was #9, "first 4 digits of the JR number — req
age signal"): DISPROVEN by data (S11) — the prefix matches
startDate.year on 2/1,410 rows (0.1%) and 2026 postings carry prefixes
spread 1966..2026; the JR number is an ID-space artifact, not a
calendar signal. `questionnaire` (was "true"/"false") became
`questionnaireId` — the JOIN KEY into {label}.questionnaires.csv (the
questions themselves; fetch via --phase questionnaires).
v2.6 appended the DOL H-1B/LCA wage-band columns #44-#48 (S11, design
S8-F-3): certified NVIDIA filings from the quarterly disclosure files,
annualized offered-wage percentiles joined per exact-normalized title
(+primary state when available).

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
  ... --phase titlesearch (repeat until meta done: true — probes
      no_match reqs' exact titles; then corroborate AGAIN to fetch
      the newly discovered cards' signals)
  ... --phase tagfacets  (optional; before finish)
  ... --phase questionnaires (optional; fetch the questionnaire
      definitions behind the per-posting questionnaireIds — deduped,
      once per distinct id)
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
from jobsearch.sources import site_boards, workday  # noqa: E402 — S13 site dispatch
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
    "primaryLocation",       # 9  "US, CA, Santa Clara"
    "nLocations",            # 10 total locations
    "locations",             # 11 ALL locations "; "-joined — 0A
    "remoteFlag",            # 12 any location mentions Remote
    "stateCodes",            # 13 "CA;NC;TX" derived
    "country",               # 14 country descriptor
    "questionnaireId",       # 15 the posting's application questionnaire
                             #    id — JOIN KEY into {out}.questionnaires.csv
                             #    (v2.5: was `questionnaire` "true"/"false";
                             #    the definitions are shared across postings
                             #    and live in the linked file)
    "similarJobsCount",      # 16 related postings on the board
    "description",           # 17 clean text (html_to_text) — 0B
    "descriptionLength",     # 18 chars of clean text
    "url",                   # 19 canonical apply URL
    "detailError",           # 20 "" | detail_unreachable (detail-derived
                             #    cols above are UNKNOWN, not absent)
    "linkedinUrl",           # 21 matched LI posting — 0D
    "linkedinPostedDate",    # 22 LI cross-post date
    "numApplicants",         # 23 LI applicant count
    "applicantLabel",        # 24 raw label ("Over 200 applicants")
    "dateDeltaDays",         # 25 LI date − startDate (repost lag)
    "corroborationStatus",   # 26 matched|no_match|blocked|not_checked
    "matchMethod",           # 27 reqId|title|titleMultiset|""
    "firstSeenDate",         # 28 first sighting by us (watch
                             #    first_seen when present, else dump date)
    "corroboratedOn",        # 29 ISO timestamp of signal fetch
    "dumpDate",              # 30 dump generation date
    # ── v2.1 addenda (S8-E3: S8-D gap table #5/#6 + S8-C §7/§8-R3) ──────
    "endDate",               # 31 detail endDate (ISO; 43 rows live)
    "daysOnMarket",          # 32 snapshot_date − earliest evidence date
                             #    (startDate; earlier LI card date per #33;
                             #    int; "" when no evidence) — best-estimate
                             #    listing age
    "daysOnMarketBasis",     # 33 "startDate" | "startDate+linkedin" | ""
                             #    (an LI card date PREDATES startDate ⇒ age
                             #    measured from the earlier evidence — #32)
    "censored",              # 34 "true" | "false" — see WATCH_SEED below
    "repostCount",           # 35 slug "…_JR####-N" suffix (Workday's own
                             #    repost counter; 0 when absent)
    "lastResetDate",         # 36 the watch repost detector's measured
                             #    new_startDate for this req (last event
                             #    wins; "" when no event — S8-C R1/R2,
                             #    filled since S9-audit A6r)
    "applicationDeadline",   # 37 "accepted … until {date}" parsed from
                             #    description (98.9%); endDate fallback
    "daysLeftToApply",       # 38 deadline − snapshot_date (int | "") — ""
                             #    when none AND when the floor ELAPSED on a
                             #    live posting (auto-extended: unknown, not
                             #    negative — v2.5/S11)
    "workerSubType",         # 39 tagfacets phase (Intern/NCG/…)
    "jobFamilyGroup",        # 40 tagfacets phase (NVIDIA's taxonomy)
    # ── v2.3 addenda (S9: cross-source timing floor) ──────────────────
    "earliestEvidenceDate",  # 41 min(startDate, linkedinPostedDate) —
                             #    the honest cross-source age floor
    "crossSourceRepostEvidence",  # 42 ""|reqId|title — the LI card
                             #    PREDATES startDate ⇒ the req was on the
                             #    market before its (reset) startDate;
                             #    reqId-joins = CONFIRMED repost (the
                             #    card IS the same requisition)
    # ── v2.4 addenda (S9-audit A1/C4/D3: bucket censoring + provenance) ─
    "applicantCensored",     # 43 "true"|"false"|"" — the numApplicants in
                             #    col #23 is a BUCKET when true (floor 25
                             #    "among first", cap 200 "Over"): never
                             #    average #23 without this. RECOMPUTED
                             #    from (num, label) — not the stored flag
                             #    (752/1,502 live signals predate the key)
    # ── v2.6 addenda (S11: DOL H-1B / LCA wage bands — design S8-F-3) ───
    "h1bFilings",            # 44 NVIDIA LCA filings backing the bands
                             #    (certified-only, deduped by case number)
    "h1bWageP25",            # 45 annualized OFFERED wage (WAGE_RATE_OF-
                             #    _PAY_FROM × unit multiplier) p25, USD
    "h1bWageP50",            # 46 …median
    "h1bWageP75",            # 47 …p75
    "h1bMatchBasis",         # 48 "title+state" | "title" | "subset+state"
                             #    | "subset" | "" — tier (token-exact vs
                             #    subset) + granularity (state pool vs all)
    "h1bMatchTitle",         # 49 the matched pool's most-frequent raw LCA
                             #    title — the audit string for WHICH
                             #    population produced #44-#47
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


def _split_variants(raw: str) -> list[str]:
    """S16: --li-variants values may CONTAIN commas — legal-name card
    strings like 'GE Appliances, a Haier company'. Split on ',', then
    re-join any piece that starts with whitespace onto its predecessor
    (the flag convention has always been tight commas 'A,B'; a company
    legal name is always written with ', ' — the space disambiguates)."""
    pieces = raw.split(",")
    out: list[str] = []
    for p in pieces:
        if p[:1].isspace() and out:
            out[-1] = out[-1] + "," + p
        else:
            out.append(p)
    return [v.strip() for v in out if v.strip()]


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
    # NOTE: split("\n") not splitlines() — JSON strings may legally
    # carry U+2028/U+2029/U+0085 (CJK descriptions do; tencent live
    # 2026-09-19), which splitlines() treats as line breaks, shredding
    # otherwise-valid records into "corrupt lines" (S13).
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
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
    keep = last_nl + 1 if last_nl >= 0 else 0
    lost = len(data) - keep
    # S9-audit H2 P2: r+b + truncate — an open("wb") re-write zeroes the
    # whole checkpoint BEFORE the write-back (a crash mid-repair would
    # destroy the file); truncate only shortens
    with open(path, "r+b") as f:
        f.truncate(keep)
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

    Returns (view, attempts): view = {reqId: last GOOD record, or —
    when the req never settled — the LAST error record (S9-audit H2 P1:
    never-settled rows used to vanish from the payload view entirely
    and ship detailError="" at finish; the error record carries no
    info, so info-derived fields stay honestly empty, but the marker
    survives)}; attempts = {reqId: max attempts seen on error records}.
    A record has info ⇒ settled; consumers derive that themselves."""
    good: dict[str, dict] = {}
    last_err: dict[str, dict] = {}
    attempts: dict[str, int] = {}
    for d in _load_jsonl(det_path):
        rid = d.get("reqId")
        if not rid:
            continue
        if d.get("info"):
            good[rid] = d
        else:
            last_err[rid] = d
        try:
            attempts[rid] = max(attempts.get(rid, 0),
                                int(d.get("attempts") or 0))
        except (TypeError, ValueError):
            pass
    view = dict(good)
    for rid, rec in last_err.items():
        if rid not in view:
            view[rid] = rec        # never settled — keep the marker
    return view, attempts


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
        # S13: client_filter=False — boards without a country facet list
        # ALL rows (the token predicate undercounts city-only dialects:
        # netflix 'Los Gatos' carries no US token). Classification moved
        # to --phase countryfilter (detail-payload country, honest).
        # site_boards routes ats:{kind}:{org} specs to the custom-site
        # adapters (greenhouse/ashby); workday specs unchanged.
        rows, meta = site_boards.list_board(
            args.board, country=args.country or None,
            time_type=args.time_type or None, cfg=cfg,
            sleep_s=args.sleep, progress_every=10,
            client_filter=False)
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
    # S15: the adapter-classified boards' client-side drop counts —
    # the auditable foreign trail for greenhouse dialect boards (the
    # client-filtered rows are dropped at list time; the counts are
    # the durable record, matching the census arithmetic).
    if meta.get("client_filtered"):
        status["client_filtered"] = meta.get("client_filtered")
        status["client_filtered_country"] = meta.get(
            "client_filtered_country")
        status["client_filtered_time"] = meta.get("client_filtered_time")
    if "offices_discriminate" in meta:
        status["offices_discriminate"] = meta.get("offices_discriminate")
    if meta.get("country_client"):
        # S13: the listing is the FULL global board — rows are NOT yet
        # country-classified. --phase countryfilter (after details)
        # rewrites list.jsonl to the {country} population and replaces
        # this marker; --phase finish hard-gates on it.
        status["country_client"] = True
        status["country"] = args.country or ""
        status["country_filter_pending"] = True
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
    is_site = site_boards.is_site_spec(args.board)
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
    det_view, attempts = _load_details_state(det_path)
    settled = {rid for rid, d in det_view.items() if d.get("info")}
    # error rows retry with a 3-strike cap (audit S7-A2 H: transient 429s
    # were permanently losing descriptions). attempts accumulate per req
    # across ALL records (never reset — a success settles the row out of
    # the work set anyway).
    three_strikes = {rid for rid, a in attempts.items()
                     if a >= 3 and rid not in settled}
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
                 and "similarJobs" not in (det_view.get(r["reqId"]) or {})
                 and attempts.get(r["reqId"], 0) < 3]
        todo = todo + stale
        print(f"[details] refetch-similar: {len(stale)} settled rows "
              f"lack the similarJobs list "
              f"({sum(1 for r in rows if r['reqId'] in settled and attempts.get(r['reqId'], 0) >= 3 and 'similarJobs' not in (det_view.get(r['reqId']) or {}))} past strike cap)",
              flush=True)
    n_err_rows = sum(1 for rid, a in attempts.items()
                     if a > 0 and rid not in settled)
    # S19 title-drift re-fetch (the bytedance INV-3 lesson): a settled
    # detail whose title differs from the CURRENT list row is stale —
    # the board edited the posting after we fetched its detail; the
    # verbatim join then compares a card (matched on the fresh list
    # title) against the stale detail title and INV-3 fails at finish.
    # A re-fetch is scheduled ONLY when the list title differs from the
    # one recorded at the last drift check (listTitleChecked marker on
    # the appended record) — a board whose titles SYSTEMATICALLY differ
    # between list and detail surfaces once, records the marker, and
    # never churns.
    drift = []
    for r in rows:
        rid = r.get("reqId")
        if rid not in settled or attempts.get(rid, 0) >= 3:
            continue
        det = det_view.get(rid) or {}
        det_title = (det.get("info") or {}).get("title")
        if (det_title and r.get("title") and det_title != r["title"]
                and det.get("listTitleChecked") != r["title"]):
            drift.append(r)
    if drift:
        # dedupe against the refetch-similar tier (a settled row can be in
        # BOTH — lacking similarJobs AND title-drifted — one fetch serves
        # both) and PREPEND: the drift rows are the correctness-critical
        # set (INV-3); a full batch of new rows must not defer them
        todo_ids = {r["reqId"] for r in todo}
        drift = [r for r in drift if r["reqId"] not in todo_ids]
        if drift:
            print(f"[details] title-drift: {len(drift)} settled rows "
                  f"whose detail title != current list title "
                  f"(re-fetching first; the fresh record carries "
                  f"listTitleChecked)", flush=True)
            todo = drift + todo
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
            # S13 dispatch: ats: specs → the site adapter (served from
            # the cached single board fetch — zero extra network);
            # workday specs → the CXS detail endpoint, unchanged.
            # S15 seam threading: country/time_type reach the adapter
            # so the greenhouse ladder verdict produces the detail's
            # country descriptor (list/detail agree by construction);
            # other adapters ignore them (structured payloads).
            payload = site_boards.detail_payload(
                args.board, r["externalPath"], cfg,
                country=getattr(args, "country", None) or None,
                time_type=getattr(args, "time_type", None) or None)
            rec: dict = {"reqId": r["reqId"],
                         "fetched_at": datetime.now(timezone.utc
                                                    ).isoformat(
                                             timespec="seconds")}
            if payload:
                info = payload.get("jobPostingInfo") or {}
                # RAW preservation — every jobPostingInfo field
                rec["info"] = info
                # S19 title-drift marker: remember WHICH list title this
                # detail was checked against (a systematic list-vs-detail
                # title difference must not re-fetch forever — only a
                # FUTURE list-title change re-opens the check).
                if r.get("title") and info.get("title") != r["title"]:
                    rec["listTitleChecked"] = r["title"]
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


# ── phase: countryfilter (S13 — detail-based country classification) ──

def phase_countryfilter(args, out: Path) -> int:
    """Rewrite list.jsonl to the requested country's rows, classified by
    the DETAIL payload's authoritative jobPostingInfo.country.

    Why: boards without a country facet (netflix.wd108 / tencent.wd1 /
    jd.wd103 — city-level facets only) cannot be filtered server-side,
    and the S12 list-level token predicate UNDERCOUNTS on city-only
    dialects (netflix writes locationsText='Los Gatos' with no US token:
    ~200 real US postings silently dropped). Every detail payload
    carries jobPostingInfo.country={descriptor,id} — even '2 Locations'
    rows classify exactly.

    Contract:
    - drops rows whose settled detail says non-{country} → archived to
      {out}.list.foreign.jsonl (auditable, never silent)
    - KEEPS rows whose settled detail matches {country}
    - KEEPS unresolved rows (no settled detail — 3-strike / pending
      re-fetch): unknown ≠ foreign (B1 spirit); counted loudly, they
      ship as detailError rows if they stay unresolved
    - list.status: rows becomes the {country} count; the
      country_filter_pending marker is replaced by country_filtered
      {kept, dropped, unresolved, basis:'detail'}
    - idempotent: re-runs re-derive from list.jsonl + the append-only
      details state (a row whose detail settles later reclassifies)
    - boards WITH the country facet: no-op (server-side filtered at
      list time) — exit 0 with a note
    """
    list_path = out.with_suffix(".list.jsonl")
    if not list_path.exists():
        print(f"no {list_path} — run --phase list first")
        return 2
    status: dict = {}
    try:
        status = json.loads(out.with_suffix(".list.status").read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if not status.get("country_client"):
        print("[countryfilter] board filters country server-side — "
              "nothing to do")
        return 0
    country = status.get("country") or args.country or "united states"
    rows = _load_jsonl(list_path)
    if not rows:
        print("[countryfilter] list.jsonl is empty — nothing to classify")
        return 0
    det_view, _attempts = _load_details_state(out.with_suffix(
        ".details.jsonl"))
    kept: list[dict] = []
    dropped: list[dict] = []
    unresolved = 0
    for r in rows:
        d = det_view.get(r["reqId"])
        info = (d or {}).get("info")
        if info:
            # authoritative classifier: the detail's own country field
            if workday.detail_in_country({"jobPostingInfo": info},
                                         country):
                kept.append(r)
            else:
                r2 = dict(r)
                r2["country"] = workday.detail_country(
                    {"jobPostingInfo": info})
                dropped.append(r2)
        else:
            kept.append(r)      # unknown ≠ foreign
            unresolved += 1
    _atomic_write_text(list_path, "\n".join(
        json.dumps(r, ensure_ascii=False) for r in kept) + "\n")
    foreign_path = out.with_suffix(".list.foreign.jsonl")
    if dropped:
        with open(foreign_path, "a", encoding="utf-8") as ff:
            for r in dropped:
                ff.write(json.dumps(r, ensure_ascii=False) + "\n")
    status["rows"] = len(kept)
    status["country_filter_pending"] = False
    status["country_filtered"] = {
        "kept": len(kept), "dropped": len(dropped),
        "unresolved": unresolved, "basis": "detail",
        "classified_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
    }
    _atomic_write_text(out.with_suffix(".list.status"), json.dumps(status))
    print(f"[countryfilter] {country!r}: kept {len(kept)} "
          f"(unresolved {unresolved}), dropped {len(dropped)} → "
          f"{list_path} (+ {foreign_path})", flush=True)
    if unresolved:
        print(f"[countryfilter] WARNING: {unresolved} row(s) have no "
              f"settled detail — kept as unknown (re-run --phase details "
              f"then this phase; 3-strike rows ship as detailError)",
              file=sys.stderr)
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
    # S10: explicit re-index — a same-mode refresh cycle needs to re-run
    # the slice matrix to discover cards posted since the last pass
    # (done:true otherwise pins the index to its last completion date;
    # dedup-accumulate makes the re-run safe).
    if args.corroborate_index and getattr(args, "li_reindex", False) \
            and index_meta.get("done"):
        index_meta["done"] = False
        print("[corroborate] --li-reindex: re-opening index "
              f"(was done at {index_meta.get('indexed_at', '?')})",
              flush=True)
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
    # S16: the signals file is STATE — create it (empty) even when the
    # index found 0 cards. A missing file breaks downstream preconditions
    # (titlesearch refuses; s10_invariants INV-4 fails) on boards with
    # no LI surface; empty = valid state.
    if not sig_path.exists():
        _atomic_write_text(sig_path, "")
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
        _repair_jsonl_tail(sig_path)      # B7 guard (S9-audit H2)
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
    # S16: the signals file must EXIST (corroborate ran) — but it may be
    # EMPTY (0 LI cards on the board): that is valid state, probe on.
    if not sig_path.exists():
        print(f"no {sig_path} — run --phase corroborate first")
        return 2
    signals = _load_jsonl(sig_path)
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
    # S10: cross-post-lag re-probe — LinkedIn cards often appear 1-2
    # days AFTER the Workday req, but a no_card verdict is terminal
    # forever, so refresh cycles never discover the late card (the
    # req stays no_match in every future CSV). --reprobe-no-card-days
    # re-opens no_card lines for reqs whose startDate is within the
    # window (opt-in; 0 = off preserves the terminal semantics).
    reprobe_days = int(getattr(args, "reprobe_no_card_days", 0) or 0)
    if reprobe_days > 0:
        from datetime import date as _dd, timedelta as _td
        horizon = (_dd.today() - _td(days=reprobe_days)).isoformat()
        reopened = [rid for rid, st in probed.items()
                    if st == "no_card"
                    and (req_dates.get(rid) or "") >= horizon]
        for rid in reopened:
            probed.pop(rid)
        if reopened:
            print(f"[titlesearch] re-probe: {len(reopened)} no_card "
                  f"req(s) re-opened (startDate within {reprobe_days}d "
                  "— the cross-post lag window)", flush=True)
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
                loc = corroborate.row_search_location(r)
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
    if site_boards.is_site_spec(args.board):
        # S13 custom sites: no CXS facets to sub-list — the adapter's
        # list rows carry the ATS's OWN taxonomy natively (departments
        # → jobFamilyGroup; ashby team → workerSubType when present).
        # Write the same facet_tags.jsonl row shape the finish phase
        # already joins on (zero network; idempotent rewrite).
        rows = _load_jsonl(list_path)
        tag_path = out.with_suffix(".facet_tags.jsonl")
        lines = []
        for r in rows:
            dept = (r.get("departments") or [""])[0] or ""
            if dept:
                lines.append(json.dumps(
                    {"reqId": r["reqId"], "jobFamilyGroup": dept},
                    ensure_ascii=False))
            team = r.get("team") or ""
            if team:
                lines.append(json.dumps(
                    {"reqId": r["reqId"], "workerSubType": team},
                    ensure_ascii=False))
        _atomic_write_text(tag_path, "\n".join(lines) + ("\n" if lines
                                                         else ""))
        print(f"[tagfacets] site adapter: wrote {len(lines)} native-"
              f"taxonomy tag rows (departments"
              f"{'/teams' if any(r.get('team') for r in rows) else ''}) "
              f"→ {tag_path}", flush=True)
        return 0
    cfg = Config()
    board = workday.parse_board(args.board)
    try:
        first = workday._page(board, {}, 0, cfg)
    except Exception as exc:
        print(f"[tagfacets] page-0 fetch failed: {type(exc).__name__}: "
              f"{exc}", file=sys.stderr)
        return 1
    try:
        dump_facets, country_client = workday.resolve_facets(
            board, first, args.country or None, args.time_type or None)
    except ValueError as exc:
        print(f"[tagfacets] {exc}")
        return 2
    # S12 multi-company: boards without a country facet (netflix/tencent/
    # jd) filter the country client-side — the sub-lists carry the same
    # predicate so tag counts reflect the DUMPED population, not the
    # whole global board.
    # S13: the token predicate became a MEMBERSHIP predicate — the dumped
    # population is now exactly the post-countryfilter list.jsonl (the
    # detail-classified {country} set), which is STRICTLY more honest
    # than location tokens (a 'Los Gatos' row is in the dumped
    # population even though its tokens carry no country).
    _dumped = {r["reqId"] for r in _load_jsonl(list_path)}
    country_filter = (lambda row: row.get("reqId") in _dumped) \
        if country_client else None
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
    _repair_jsonl_tail(tag_path)          # B7 guard (S9-audit H2)
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
                        sleep_s=args.sleep, row_filter=country_filter)
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


# ── phase: questionnaires — application-questionnaire definitions (S11) ──

QUESTIONNAIRE_CSV_COLUMNS = [
    "questionnaireId",     # join key == CSV v2.5 column #15
    "instructions",        # questionnaire-level intro (clean text)
    "questionId",
    "order",               # the payload's own ordering key ("a", "b", …)
    "question",            # question body (clean text)
    "required",            # "true" | "false"
    "type",                # e.g. "Multiple Choice - Single Select"
    "answers",             # "; "-joined answer texts, payload order
]


def _questionnaire_url(board: tuple[str, str, str],
                       questionnaire_id: str) -> str:
    """The calypso CXS questionnaire-definition URL (S11, live-verified
    2026-09-17 on nvidia/wd5 from an HK egress).

    Dissected from the apply-flow SPA
    (candidate-experience-apply-flow.min.js): the flow skeleton
    ``jobpostings/{id}/applyflowpages`` lists the pages; page 4 is
    "Application Questions"; its content comes from
    ``common/questionnaire/{id}`` — a SITELESS calypso route (no site
    segment) served WITHOUT auth. The plain-CXS guesses
    (/questionnaire/{id} under /wday/cxs/{tenant}/{site}/…) all 406.
    """
    tenant, instance, _site = board
    return (f"https://{tenant}.{instance}.myworkdayjobs.com"
            f"/wday/calypso/cxs/common/{tenant}/questionnaire/"
            f"{questionnaire_id}")


def _fetch_questionnaire(board: tuple[str, str, str], qid: str,
                         cfg: Config) -> Optional[dict]:
    """GET one questionnaire definition; None on any failure shape
    (transport error, non-dict, errorCode body, questions key absent —
    the detail-fetch failure convention)."""
    try:
        payload = workday.fetch_json(
            _questionnaire_url(board, qid), cfg=cfg,
            headers={"Accept": "application/json"})
    except Exception:  # noqa: BLE001 — enrichment, never fatal
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("errorCode"):
        return None
    if not isinstance(payload.get("questions"), list):
        return None
    return payload


def _detail_questionnaire_ids(details: dict) -> list[str]:
    """Distinct questionnaireIds across the last-good detail records,
    first-seen order (stable for re-runs; the fetch set is tiny by
    design — questionnaires are shared across postings)."""
    ids: list[str] = []
    for rid in sorted(details):
        qid = (((details[rid] or {}).get("info") or {}).get(
            "questionnaireId") or "").strip()
        if qid and qid not in ids:
            ids.append(qid)
    return ids


def phase_questionnaires(args, out: Path) -> int:
    """Fetch the application-questionnaire DEFINITIONS behind the
    per-posting questionnaireIds (the CSV v2.5 join key).

    Questionnaires are shared across postings (live: 4 distinct ids for
    1,541 reqs), so this dedups and fetches each id ONCE into
    {out}.questionnaires.jsonl (append-only; {"questionnaireId",
    "fetchedAt", "payload"} per line). Re-runs skip fetched ids;
    FAILED ids leave no record and retry on the next run (rc=1 so a
    chained caller sees the gap). finish joins by id and emits the
    linked {out}.questionnaires.csv; a missing definition ships as a
    finish-time WARNING, never silent.

    S13 custom sites: greenhouse/ashby public payloads carry NO
    application questions (they live behind the apply-form) — honest
    no-op: questionnaireId ships blank, no linked CSV, said loudly.
    """
    if site_boards.is_site_spec(args.board):
        print("[questionnaires] site adapter: public payloads carry no "
              "application questions — questionnaireId ships blank "
              "(honest no-op; questions live behind the apply form)",
              flush=True)
        return 0
    det_path = out.with_suffix(".details.jsonl")
    if not det_path.exists():
        print(f"no {det_path} — run --phase details first")
        return 2
    details, _att = _load_details_state(det_path)
    wanted = _detail_questionnaire_ids(details)
    q_path = out.with_suffix(".questionnaires.jsonl")
    have = {rec.get("questionnaireId")
            for rec in _load_jsonl(q_path)
            if rec.get("questionnaireId")}
    todo = [q for q in wanted if q not in have]
    print(f"[questionnaires] {len(wanted)} distinct id(s) across "
          f"{len(details)} detail record(s); {len(wanted) - len(todo)} "
          f"already fetched, {len(todo)} to fetch", flush=True)
    if not todo:
        return 0
    cfg = Config()
    board = workday.parse_board(args.board)
    fetched = failed = 0
    with open(q_path, "a", encoding="utf-8") as f:
        for i, qid in enumerate(todo, 1):
            payload = _fetch_questionnaire(board, qid, cfg)
            if payload is None:
                failed += 1
                print(f"  [{i}/{len(todo)}] {qid}: FETCH FAILED "
                      f"(no record written — retries next run)",
                      flush=True)
                continue
            f.write(json.dumps({
                "questionnaireId": qid,
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }, ensure_ascii=False) + "\n")
            f.flush()
            fetched += 1
            print(f"  [{i}/{len(todo)}] {qid}: "
                  f"{len(payload['questions'])} question(s)", flush=True)
            if i < len(todo):
                time.sleep(args.sleep)
    print(f"[questionnaires] fetched {fetched}, failed {failed} → "
          f"{q_path}", flush=True)
    return 1 if failed else 0


# ── H-1B / LCA wage-band join (S11, design S8-F-3) ──────────────────

H1B_CSV_COLUMNS = [
    "caseNumber", "caseStatus", "caseSubmitted", "decisionDate",
    "employerName", "jobTitle", "socCode", "socTitle",
    "fullTimePosition", "wageFrom", "wageTo", "wageUnit",
    "prevailingWage", "pwUnit", "worksiteCity", "worksiteState",
    "worksitePostalCode", "annualizedWage", "sourceFile",
]

# Certified + Certified-Withdrawn both reflect an adjudicated wage
# offer (withdrawn-AFTER-certification keeps its certification);
# Withdrawn/Denied rows keep in the extract but never band a posting.
# Status strings arrive in BOTH dialects ("Certified-Withdrawn" and
# "Certified - Withdrawn" — live-measured in the FY2025/26 files):
# normalize by stripping spaces/dashes before the set test.
_H1B_CERTIFIED = {"CERTIFIED", "CERTIFIEDWITHDRAWN"}
_WAGE_ANNUAL_MULT = {
    "Year": 1, "Month": 12, "Bi-Weekly": 26, "Week": 52,
    "Day": 260, "Hour": 2080,
}
# Plausibility bound on the annualized offered wage: DOL rows exist
# with unit-column corruption (live: "Software Engineer" wageFrom
# 136,000 with unit Hour and pwUnit Year → $282.9M) — a filing whose
# annualized wage falls outside a sane US-annual band is DROPPED from
# the pools, never averaged into a percentile.
_H1B_WAGE_MIN, _H1B_WAGE_MAX = 25_000, 1_000_000
# Token vocabularies for the subset-tier ranking (design-audit S11):
# level words are the dialect-unstable tokens (the LCA comma-form
# reorders them) — domain conditioning must outrank level conditioning.
_H1B_LEVEL_TOKENS = {"senior", "staff", "principal", "lead", "chief",
                     "junior"}
# Role words that, appearing in the posting OUTSIDE the matched pool,
# change the occupation family (QA vs SWE, intern vs full-time,
# marketing vs product) — such matches are SUPPRESSED (honest "",
# never a misattributed band).
_H1B_ROLE_BLOCK = {"qa", "quality", "test", "sdet", "verification",
                   "validation", "intern", "coop", "college", "grad",
                   "university", "mba", "marketing", "sales", "support"}
_H1B_STOPWORDS = {"of", "and", "the", "for", "to", "in"}


def _norm_case_status(s: str) -> str:
    return re.sub(r"[\s\-]+", "", (s or "").upper())


def _norm_join_title(t: str) -> str:
    """Title normalization for the LCA join: casefold, punctuation →
    spaces, collapsed. LEXICAL only — see _title_tokens for the join
    key. NO stemming, ever: "engineering" ≠ "engineer" is LOAD-BEARING
    (it is the only thing keeping "Engineering Manager" out of the
    software-engineer pools — design-audit S11)."""
    return re.sub(r"[^a-z0-9#+]+", " ", (t or "").lower()).strip()


def _title_tokens(t: str) -> frozenset:
    """The join key: stop-word-free, level-code-free, plural-folded
    token SET (order-free — the LCA comma-form "Engineer, Senior
    Systems Software" and the posting form "Senior System Software
    Engineer" share the same set; "systems"/"system" fold together)."""
    s = re.sub(r"\bl\d+\b", " ", _norm_join_title(t))   # L11-style codes
    out = []
    for tok in s.split():
        if tok in _H1B_STOPWORDS:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return frozenset(out)


def _annualize_wage(rec: dict, *, bounded: bool = True) -> Optional[float]:
    """Annualized OFFERED wage (WAGE_RATE_OF_PAY_FROM × unit multiplier)
    for a certified full-time filing; None when uncertified,
    part-time, unparseable, or (bounded=True) implausible."""
    if _norm_case_status(rec.get("caseStatus") or "") \
            not in _H1B_CERTIFIED:
        return None
    if str(rec.get("fullTimePosition") or "").strip().upper() \
            not in ("", "Y"):
        return None
    try:
        rate = float(str(rec.get("wageFrom") or "").replace(",", ""))
    except ValueError:
        return None
    mult = _WAGE_ANNUAL_MULT.get(
        (str(rec.get("wageUnit") or "").strip().title()))
    if not mult:
        return None
    wage = rate * mult
    if bounded and not (_H1B_WAGE_MIN <= wage <= _H1B_WAGE_MAX):
        return None
    return wage


# Per-company LCA house-title conventions (S13). Some employers file
# LCAs under a house leveling wrapper that the public job titles never
# use — OpenAI files EVERYTHING as "Member of X Staff (Specialization)"
# while postings say "Software Engineer, Data Infrastructure". The
# wrapper is a KNOWN translation, not a guess (same epistemic class as
# the LinkedIn company-variants registry): unwrap it at POOL-KEY time
# so the verbatim/subset tiers can do their honest work. The pool's
# audit title stays the RAW LCA string (h1bMatchTitle shows the real
# filing). Extensible per company — regex list, first match wins.
_H1B_HOUSE_TITLE_TRANSFORMS: dict[str, list[re.Pattern]] = {
    "OpenAI": [
        # "Member of Technical Staff (Software Engineer)" → SWE;
        # "Member of Go To Market Staff (Solutions Architect)" → …;
        # "Member of Business Platform Staff (…)" → …
        re.compile(r"^member of [a-z ]*staff \((.+)\)$"),
    ],
}


def _h1b_house_title(company: str, raw: str) -> str:
    """Company-adapted LCA title for POOL KEYS (raw stays the audit
    string). No entry / no match → the raw title unchanged."""
    norm = (raw or "").strip().lower()
    for pat in _H1B_HOUSE_TITLE_TRANSFORMS.get((company or "").strip(), []):
        m = pat.match(norm)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return raw or ""


def _load_h1b_bands(out: Path,
                    company: str = "") -> tuple[dict, list[dict]]:
    """(pools, records) from {out}.h1b_lca.jsonl (the GHA-produced
    quarterly extract — scripts/h1b_extract.py +
    .github/workflows/h1b-extract.yml).

    pools: {token_set: {"states": {ST: [wages]}, "all": [wages],
    "title": most-frequent raw LCA title}} over certified full-time
    filings with a plausible annualized wage (|S| >= 2 only — 1-token
    pools like a bare "Architect" are maximally generic and excluded).
    records: every extract line (the linked-CSV view)."""
    pools: dict[frozenset, dict] = {}
    recs: list[dict] = []
    path = out.with_suffix(".h1b_lca.jsonl")
    if not path.exists():
        return pools, recs
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        recs.append(rec)
        wage = _annualize_wage(rec)
        if wage is None:
            continue
        toks = _title_tokens(
            _h1b_house_title(company, str(rec.get("jobTitle") or "")))
        if len(toks) < 2:
            continue
        pool = pools.setdefault(toks, {"states": {}, "all": [],
                                       "titles": {}})
        pool["all"].append(wage)
        st = str(rec.get("worksiteState") or "").strip().upper()
        if st:
            pool["states"].setdefault(st, []).append(wage)
        raw = str(rec.get("jobTitle") or "")
        pool["titles"][raw] = pool["titles"].get(raw, 0) + 1
    for pool in pools.values():
        pool["title"] = max(sorted(pool["titles"]),
                            key=pool["titles"].get)
    return pools, recs


def _wage_pct(sorted_vals: list[float], p: float) -> int:
    """Linear-interpolation percentile over a SORTED list (numpy-free)."""
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return int(round(sorted_vals[f]
                     + (sorted_vals[c] - sorted_vals[f]) * (k - f)))


_H1B_EMPTY = {"h1bFilings": "", "h1bWageP25": "", "h1bWageP50": "",
              "h1bWageP75": "", "h1bMatchBasis": "", "h1bMatchTitle": ""}


def _derive_h1b_columns(title: str, primary_location: str,
                        pools: dict) -> dict:
    """The #44-#49 cells for one row (design-audit-refined S11):

    - candidates = token-sets S (|S|>=2) with S ⊆ posting-tokens P;
      SUPPRESSED when (P − S) contains a role-block word (QA/test/
      intern/marketing/… — an occupation change, not a specialization).
    - best = max by (|S − level-tokens|, |S|, all-state filings,
      lexicographic) — DOMAIN conditioning outranks LEVEL conditioning
      (level words are the dialect-unstable tokens); state-INdependent
      so identical titles in different states share a pool family.
    - state hop: (best, primaryState) pool when n >= 3, else the
      all-state pool; primaryState parsed from primaryLocation
      ("US, TX, Austin"), NEVER stateCodes[0] (non-canonical order
      live-measured). Absent/Remote → skip the hop.
    - final gate: n >= 3 or all-"" (a 1-filing pool is one wage, not a
      band). Bands are all-or-none per row (INV-10).
    - h1bMatchBasis: "title+state"/"title" iff S == P (token-exact,
      reorder/plural-tolerant); else "subset+state"/"subset" (the
      posting is title-consistent with and more specialized than the
      pool — the band is the POOL's distribution, level-mixed).
    - h1bMatchTitle: the pool's most frequent raw LCA title — the
      audit string for which population produced the band."""
    p_toks = _title_tokens(title)
    if not p_toks or not pools:
        return dict(_H1B_EMPTY)
    cands = [s for s in pools
             if len(s) >= 2 and s <= p_toks
             and not ((p_toks - s) & _H1B_ROLE_BLOCK)]
    if not cands:
        return dict(_H1B_EMPTY)
    best = max(cands, key=lambda s: (
        len(s - _H1B_LEVEL_TOKENS), len(s),
        len(pools[s]["all"]), " ".join(sorted(s))))
    tier = "title" if best == p_toks else "subset"
    m = _STATE_RE.match((primary_location or "").strip())
    primary_state = m.group(1) if m else ""
    wages: Optional[list[float]] = None
    basis = ""
    if primary_state:
        sp = pools[best]["states"].get(primary_state)
        if sp and len(sp) >= 3:
            wages, basis = sp, tier + "+state"
    if wages is None:
        ap = pools[best]["all"]
        if len(ap) >= 3:
            wages, basis = ap, tier
    if not wages:
        return dict(_H1B_EMPTY)
    vals = sorted(wages)
    return {
        "h1bFilings": len(vals),
        "h1bWageP25": _wage_pct(vals, 0.25),
        "h1bWageP50": _wage_pct(vals, 0.50),
        "h1bWageP75": _wage_pct(vals, 0.75),
        "h1bMatchBasis": basis,
        "h1bMatchTitle": pools[best]["title"],
    }


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
                    last_reset: str = "",
                    h1b_bands: Optional[tuple] = None) -> dict:
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
    # reposted with a reset startDate before we ever observed it.
    # S11: first_seen BEFORE the current startDate is the MEASURED form
    # of the same evidence — the watch saw this req alive before the
    # startDate it now carries, so the startDate was reset by a repost
    # after our first sighting (77/1,410 rows live; the mirror image of
    # #42's card-predates-startDate class, witnessed on our own state
    # instead of LinkedIn's).
    censored = "true" if (not start
                          or start < WATCH_SEED.isoformat()
                          or (first_seen and start
                              and first_seen[:10] < start)) \
        else "false"
    repost_count = _slug_repost_count(
        r.get("externalPath") or r.get("url")
        or info.get("externalUrl") or "")
    deadline = _parse_application_deadline(desc_text) or end_date
    days_left = ""
    if deadline:
        try:
            left = (date.fromisoformat(deadline) - snapshot).days
            # S11/v2.5: the parsed sentence is "accepted AT LEAST until
            # {date}" — a floor that auto-extends. A floor that already
            # elapsed while the posting is still ON the board means the
            # window extended: days-left is UNKNOWN, not negative (a
            # negative value read as "closed N days ago" on an open
            # posting — the #1 consumer confusion of the v2.4 review).
            # Elapsed-ness stays derivable: deadline < dumpDate.
            days_left = left if left >= 0 else ""
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
        # S19 finish-side drift fallback: a detail whose listTitleChecked
        # EQUALS the current list title is a VERIFIED-PERSISTENT list-vs-
        # detail divergence (the drift re-fetch already ran against this
        # exact list title). The verbatim join matched the card on the
        # LIST title, so the list title is the join-consistent spelling —
        # ship it (INV-3 green by construction) instead of failing forever
        # on a detail endpoint that never converges.
        "title": (
            r.get("title")
            if (det.get("listTitleChecked")
                and det.get("listTitleChecked") == r.get("title")
                and info.get("title")
                and info.get("title") != r.get("title"))
            else info.get("title") or r.get("title") or ""
        ),
        "company": company,
        "hiringOrg": det.get("hiringOrg") or "",
        "timeType": info.get("timeType") or "",
        "postedOn": r.get("postedOn") or info.get("postedOn") or "",
        "startDate": start,
        "postingAgeDays": age,
        "primaryLocation": primary,
        "nLocations": n_locations,
        "locations": "; ".join(locations),
        "remoteFlag": "true" if any(
            "remote" in loc.lower() for loc in locations) else "false",
        "stateCodes": _state_codes(locations),
        "country": country,
        "questionnaireId": info.get("questionnaireId") or "",
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
    # ── v2.6 columns (#44-#49) — DOL H-1B/LCA wage bands ────────────
    row.update(_derive_h1b_columns(
        info.get("title") or r.get("title") or "", primary,
        h1b_bands or {}))
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
    # S13 hard gate: a client-country listing that never ran
    # --phase countryfilter would ship the FULL GLOBAL board as the CSV
    # (the list phase intentionally skips the token filter now). Never
    # silent: refuse until the detail-based classification has run.
    try:
        _lst_status = json.loads(out.with_suffix(".list.status").read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _lst_status = {}
    if _lst_status.get("country_filter_pending"):
        print("[finish] REFUSED: list.status carries "
              "country_filter_pending — the listing is the full global "
              "board. Run --phase details (until ALL) then --phase "
              "countryfilter first (detail-based country classification)",
              file=sys.stderr)
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

    # questionnaire DEFINITIONS (OPTIONAL questionnaires phase — S11):
    # {id: payload} from {out}.questionnaires.jsonl (last-wins). finish
    # joins by id (CSV #15) and emits the linked questionnaires.csv;
    # a detail-carried id with NO definition is a WARNING, never silent.
    q_map: dict[str, dict] = {}
    for rec in _load_jsonl(out.with_suffix(".questionnaires.jsonl")):
        if rec.get("questionnaireId") and isinstance(
                rec.get("payload"), dict):
            q_map[rec["questionnaireId"]] = rec["payload"]
    detail_qids = set(_detail_questionnaire_ids(details))
    missing_q = sorted(detail_qids - set(q_map))
    # H-1B/LCA wage bands (OPTIONAL extract — S11): the GHA-produced
    # quarterly extract joined per token-set into #44-#49 + the linked
    # h1b_lca.csv.
    h1b_pools, h1b_recs = _load_h1b_bands(out, args.company or "")
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
            last_reset=rs_map.get(r["reqId"], ""),
            h1b_bands=h1b_pools)
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

    # linked questionnaire CSV (S11): one row per QUESTION — the
    # definitions behind CSV #15. Same utf-8-sig convention; clean text
    # for HTML bodies; answers "; "-joined in payload order; questions
    # sorted by the payload's own `order` key (then id for stability).
    qcsv_path = out.with_suffix(".questionnaires.csv")
    q_rows = 0
    with open(qcsv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(QUESTIONNAIRE_CSV_COLUMNS)
        for qid in sorted(q_map):
            p = q_map[qid]
            inst = html_to_text(p.get("instructions") or "").strip()
            for q in sorted(p.get("questions") or [],
                            key=lambda q: (q.get("order") or "",
                                           q.get("id") or "")):
                w.writerow([
                    qid,
                    inst,
                    q.get("id") or "",
                    q.get("order") or "",
                    html_to_text(q.get("body") or "").strip(),
                    "true" if q.get("required") else "false",
                    (q.get("type") or {}).get("descriptor") or "",
                    "; ".join(a.get("answerText") or "" for a in
                               q.get("possibleAnswers") or []),
                ])
                q_rows += 1

    # linked H-1B/LCA extract CSV (S11): one row per NVIDIA filing —
    # the raw evidence behind #44-#49 (analysts can reband freely).
    # annualizedWage uses the BOUNDED annualization: DOL unit-corruption
    # rows (e.g. wageFrom 136,000 Hour → $282.9M) ship "" rather than a
    # poison value; wageFrom/wageUnit preserve the raw source truth.
    h1b_csv_path = out.with_suffix(".h1b_lca.csv")
    if h1b_recs:
        with open(h1b_csv_path, "w", newline="",
                  encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(H1B_CSV_COLUMNS)
            for rec in sorted(h1b_recs,
                              key=lambda x: (x.get("sourceFile") or "",
                                              x.get("caseNumber") or "")):
                w.writerow([
                    rec.get("caseNumber") or "",
                    rec.get("caseStatus") or "",
                    rec.get("caseSubmitted") or "",
                    rec.get("decisionDate") or "",
                    rec.get("employerName") or "",
                    rec.get("jobTitle") or "",
                    rec.get("socCode") or "",
                    rec.get("socTitle") or "",
                    rec.get("fullTimePosition") or "",
                    rec.get("wageFrom") or "",
                    rec.get("wageTo") or "",
                    rec.get("wageUnit") or "",
                    rec.get("prevailingWage") or "",
                    rec.get("pwUnit") or "",
                    rec.get("worksiteCity") or "",
                    rec.get("worksiteState") or "",
                    rec.get("worksitePostalCode") or "",
                    int(round(_annualize_wage(rec)))
                    if _annualize_wage(rec) is not None else "",
                    rec.get("sourceFile") or "",
                ])

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
    # S9-audit H6r P1: nLocations is "" (honest unknown) on
    # detail-error rows — sum() over mixed int/str raises TypeError and
    # kills finish AFTER the CSV/JSON are written (stale report.txt)
    loc_sum = sum(r["nLocations"] for r in csv_rows
                  if isinstance(r["nLocations"], int))
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
    if detail_qids:
        n_qreqs = sum(1 for r in rows
                      if ((details.get(r["reqId"]) or {}).get("info")
                          or {}).get("questionnaireId"))
        report.append(
            f"questionnaire definitions: {len(detail_qids)} id(s) covering "
            f"{n_qreqs}/{len(rows)} rows; {len(q_map)} fetched "
            f"({q_rows} questions → {qcsv_path.name})"
            + (f"; {len(missing_q)} MISSING — run --phase questionnaires"
               if missing_q else ""))
    if h1b_recs:
        n_banded = sum(1 for r in csv_rows if r["h1bMatchBasis"])
        n_ts = sum(1 for r in csv_rows
                   if r["h1bMatchBasis"] == "title+state")
        report.append(
            f"h1b wage bands: {n_banded}/{len(csv_rows)} rows banded "
            f"({n_ts} title+state, {n_banded - n_ts} title-only) from "
            f"{len(h1b_recs)} LCA filings → {h1b_csv_path.name}")
    elif (out.with_suffix(".h1b_lca.jsonl")).exists():
        report.append("h1b wage bands: 0 usable filings in the extract "
                      "(all uncertified/unparseable?)")
    report_text = "\n".join(report)
    _atomic_write_text(out.with_suffix(".report.txt"), report_text)
    print(f"[finish] wrote {len(csv_rows)} rows ({matched_rows} details):")
    print(f"  {out.with_suffix('.json')}")
    print(f"  {csv_path}")
    print(f"  {edges_path} ({len(edge_rows)} edges)")
    if q_map:
        print(f"  {qcsv_path} ({q_rows} questions across "
              f"{len(q_map)} questionnaire(s))")
    if h1b_recs:
        print(f"  {h1b_csv_path} ({len(h1b_recs)} LCA filings)")
    print(report_text)
    if missing_q:
        print(f"[finish] WARNING: {len(missing_q)} questionnaire id(s) "
              f"have no definition — questionnaireId ships as a join key "
              f"with no linked row; re-run --phase questionnaires",
              file=sys.stderr)
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
    ap.add_argument("--li-variants", default="",
                    help="comma list of LinkedIn card company strings "
                         "beyond --company that belong to the board "
                         "(default: '<company>, <company> ai' — the "
                         "NVIDIA/NVIDIA-AI pilot behavior) [S12]. S16: "
                         "variants may CONTAIN commas (legal-name card "
                         "strings like 'GE Appliances, a Haier company') "
                         "— a piece starting with whitespace re-joins "
                         "the previous variant (separators have always "
                         "been tight commas 'A,B')")
    ap.add_argument("--slice-locations", default="",
                    help="semicolon list of LinkedIn search locations "
                         "for the partitioned index (default: the NVIDIA "
                         "SV/Austin/Seattle set; locations contain "
                         "commas so the separator is ';') [S12]")
    ap.add_argument("--phase", default="finish",
                    choices=["list", "details", "countryfilter",
                             "corroborate",
                             "titlesearch", "tagfacets", "questionnaires",
                             "finish"])
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
    ap.add_argument("--li-reindex", action="store_true",
                    help="(corroborate phase) re-run the index refresh even "
                         "when the mode meta says done — discovers cards "
                         "posted since the last pass (dedup-accumulates; "
                         "safe) [S10]")
    ap.add_argument("--reprobe-no-card-days", type=int, default=0,
                    metavar="N",
                    help="(titlesearch phase) re-open terminal no_card "
                         "lines for reqs whose startDate is within N days "
                         "— catches LinkedIn cross-post lag (cards appear "
                         "1-2 days after the Workday req; 0 = off) [S10]")
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

    # S12 multi-company: per-company LI matching knowledge (card company
    # variants + index slice geography). Library defaults stay NVIDIA-
    # pilot-shaped; explicit flags/register entries override.
    # S16: variants may CONTAIN commas (legal-name card strings — 'GE
    # Appliances, a Haier company'); the split heuristic re-joins any
    # piece that starts with whitespace onto its predecessor (the flag
    # convention has always been tight commas 'A,B', never 'A, B').
    if args.li_variants:
        corroborate.set_company_overrides(
            args.company,
            variants=_split_variants(args.li_variants))
    if args.slice_locations:
        corroborate.set_company_overrides(
            args.company,
            slice_locations=args.slice_locations.split(";"))

    out = Path(args.out_dir) / args.label
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.phase == "list":
        return phase_list(args, out)
    if args.phase == "details":
        return phase_details(args, out)
    if args.phase == "countryfilter":
        return phase_countryfilter(args, out)
    if args.phase == "corroborate":
        return phase_corroborate(args, out)
    if args.phase == "titlesearch":
        return phase_title_search(args, out)
    if args.phase == "tagfacets":
        return phase_facet_tags(args, out)
    if args.phase == "questionnaires":
        return phase_questionnaires(args, out)
    return phase_finish(args, out)


if __name__ == "__main__":
    sys.exit(main())
