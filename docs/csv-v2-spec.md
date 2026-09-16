# CSV v2 Column Spec — the board-dump deliverable

> **What this is:** the operator-facing column contract for
> `ingest/data/workday/nvidia_us_fulltime.csv` — THE user-facing
> deliverable of board-dump v2 (`scripts/board_dump.py --phase finish`).
> Source of truth: `CSV_COLUMNS` in `scripts/board_dump.py`; if this doc
> and the code diverge, the code wins (file the drift). Design + evidence
> trail: `audit/design-board-v2.md` (v2, 31 cols) →
> `audit/design-s9-titlesearch.md` (C1–C5) → `audit/s9-audit/*.md`
> (the fix wave). Strategic rationale: `DECISIONS.md` S16–S20.
>
> **Version history**
>
> | ver | cols | date | what changed |
> |---|---|---|---|
> | v2.0 | 31 | 2026-09-10 | board-dump v2 contract (design-board-v2 D1) |
> | v2.1 | 41 | 2026-09-13 | S8-E3 recency/completeness: #32–#41 |
> | v2.2 | 41 | 2026-09-15 | S9 titlesearch regen: matched 625→1,095 (44.8%→77.9%) |
> | v2.3 | 43 | 2026-09-15 | S9 timing floor: #42 `earliestEvidenceDate`, #43 `crossSourceRepostEvidence` |
> | v2.4 | 44 | 2026-09-16 | LANDED (9ed96cb + full regen 4d33981): composed-join regen — matched 1,095→1,050 (77.9%→74.7% honest); #44 `applicantCensored`; `firstSeenDate` from watch; `lastResetDate` filled; snapshot-frozen `postingAgeDays`; uniform lowercase booleans — §5 |
>
> **Snapshot note (2026-09-16, post-regen):** the on-disk deliverable is
> v2.4 (1,406 × 44, dumpDate 2026-09-16) — regenerated under the
> composed join + verbatim guard (cd12bc3, regen 4d33981): matched
> 1,050 / 74.7% HONEST (62 misattributed title rows dropped, 17 burned
> reqs re-matched), verified end-to-end by an independent full-population
> recompute (`audit/s9-audit/H4-csv-v24-verify.md` — 0 cell diffs, all
> join invariants green). Every live count below is THIS snapshot's.

## 1. The 44 columns

Phase of origin: **L** = list · **D** = details · **S** = signals
(corroborate) · **T** = tagfacets · **F** = derived at finish.

| # | column | phase | semantics |
|---|--------|-------|-----------|
| 1 | `reqId` | L | Workday requisition id (`JR…`) — the join key of the whole dump |
| 2 | `title` | D | posting title (detail record wins; list title only when detail unreachable) |
| 3 | `company` | F | dump company (CLI `--company`; "NVIDIA") |
| 4 | `hiringOrg` | D | legal hiring entity ("2100 NVIDIA USA") |
| 5 | `timeType` | D | "Full time" / "Part time" … |
| 6 | `postedOn` | L | relative label from the board list ("Posted Today") |
| 7 | `startDate` | D | ISO posting start — **Workday RESETS this on repost** (see #42/#43) |
| 8 | `postingAgeDays` | F | `snapshot_date` − startDate, FROZEN at finish (== dumpDate − startDate on all rows; v2.4/B2r — was wall-clock at row derivation); "" when no startDate |
| 9 | `reqYear` | F | year parsed from `JR2022…` → 2022 (req-age signal) |
| 10 | `primaryLocation` | D | "US, CA, Santa Clara" |
| 11 | `nLocations` | D | total locations; UNKNOWN ≠ 1 when `detailError` set (surfaced honestly) |
| 12 | `locations` | D | ALL locations, "; "-joined (multi-location completeness) |
| 13 | `remoteFlag` | F | `true` when any location mentions Remote (lowercase — v2.4 uniform booleans) |
| 14 | `stateCodes` | F | "CA;NC;TX" derived from locations |
| 15 | `country` | D | country descriptor |
| 16 | `questionnaire` | D | `true` when the posting carries an application questionnaire (lowercase — v2.4 uniform booleans) |
| 17 | `similarJobsCount` | D | related postings on the board per Workday |
| 18 | `description` | D | clean text (`html_to_text`) |
| 19 | `descriptionLength` | F | chars of clean text |
| 20 | `url` | L/D | canonical apply URL |
| 21 | `detailError` | D | "" \| `detail_unreachable` — when set, detail-derived cols are UNKNOWN, not absent |
| 22 | `linkedinUrl` | S | matched LinkedIn guest card URL |
| 23 | `linkedinPostedDate` | S | the LI card's posted date (ISO) |
| 24 | `numApplicants` | S | LI applicant count — a CENSORED bucket, see §3 |
| 25 | `applicantLabel` | S | raw LI label ("Over 200 applicants") |
| 26 | `dateDeltaDays` | S | linkedinPostedDate − startDate (repost lag; + = LI newer) |
| 27 | `corroborationStatus` | S | `matched` \| `no_match` \| `blocked` \| `not_checked` — see §2 |
| 28 | `matchMethod` | S/F | `reqId` \| `title` \| `titleMultiset` \| "" — see §2 |
| 29 | `firstSeenDate` | F | first sighting BY US — the board-watch state's `first_seen` when present, else dumpDate (v2.4: 1,406/1,406 rows from the watch, 2026-09-09→09-15 — §5) |
| 30 | `corroboratedOn` | S | ISO timestamp of the signal fetch |
| 31 | `dumpDate` | F | dump generation date (today at finish) |
| 32 | `endDate` | D | detail endDate (ISO; 43/1,406 rows live) |
| 33 | `daysOnMarket` | F | snapshot_date − earliest evidence date; "" when no startDate and no LI date |
| 34 | `daysOnMarketBasis` | F | `startDate` \| `startDate+linkedin` \| "" — see §2 |
| 35 | `censored` | F | `true` \| `false`: startDate missing or predating the watch seed (2026-09-09) ⇒ age is a LOWER bound only |
| 36 | `repostCount` | F | the slug `…_JR####-N` trailing `-N` = Workday's own repost counter (153/1,406 rows live; 0 when absent) |
| 37 | `lastResetDate` | F | the watch's repost-detector `new_startDate` for this req (last event wins); "" when no measured event (10/1,406 rows live — §5) |
| 38 | `applicationDeadline` | F | "…accepted … until {date}" parsed from the description (1,391/1,406 = 98.9%); endDate fallback. A FLOOR, not a guarantee — postings routinely stay live past it; negative #39 on a live posting is NORMAL (1,319/1,391 live) |
| 39 | `daysLeftToApply` | F | deadline − snapshot_date; "" when none |
| 40 | `workerSubType` | T | tagfacets phase: Regular Employee / Management / New College Graduate / Intern (Fixed Term) … |
| 41 | `jobFamilyGroup` | T | tagfacets phase (NVIDIA's own taxonomy) |
| 42 | `earliestEvidenceDate` | F | min(startDate, linkedinPostedDate) — the honest cross-source age floor (§4) |
| 43 | `crossSourceRepostEvidence` | F | "" \| `reqId` \| `title` — the LI card PREDATES startDate ⇒ on-market-before evidence (§4) |
| 44 | `applicantCensored` | F | `true` \| `false` \| "" — #24 is a BUCKET when `true` (floor 25 / cap 200; see §3); recomputed at finish from (num, label), NOT the stored signal flag. Live: 456 `true` / 950 `false` / 0 `""` — the `false` count includes the 356 no_match rows (synthetic signal, no count; empty #24 disambiguates) |

Snapshot note: `postingAgeDays`/`daysOnMarket`/`daysLeftToApply` all
reference ONE `snapshot_date` (today at finish, shared by all rows;
`postingAgeDays` was frozen to it in v2.4/B2r — it used to read the
wall clock per row). Boolean casing is UNIFORM lowercase `true`/`false`
since v2.4 across all four boolean columns (`remoteFlag`,
`questionnaire`, `censored`, `applicantCensored` — #13/#16 serialized
Python-style `True`/`False` through v2.3).

## 2. Enums

- **`corroborationStatus`** (B5: retryable ≠ terminal, everywhere):
  - `matched` — a LinkedIn card joined this req.
  - `no_match` — fully probed, no card: index exhausted AND titlesearch
    terminally probed THIS req (per-req terminal state, not a global
    shortcut — cd12bc3).
  - `blocked` — a candidate card exists but its fetch was blocked;
    RETRYABLE, never read as no_match.
  - `not_checked` — titlesearch incomplete for this req (finish gate);
    the honest "we don't know yet".
- **`matchMethod`**:
  - `reqId` — the card's `job_req_id` names this requisition (exact;
    multiple claimants rank open > freshest fetch > latest post).
  - `title` — verbatim title join (job_key tier + `verbatim_match`
    guard, S20).
  - `titleMultiset` — order/punctuation-insensitive token-multiset join
    (the C3 tier; multiset equality implies token-F1 = 1.0). 0 rows in
    the current snapshot — legitimately empty on this data.
  - `""` — no signal (unmatched/no_match rows).
- **`daysOnMarketBasis`**:
  - `startDate` — age from Workday startDate only.
  - `startDate+linkedin` — an LI card date predates startDate; age is
    measured from the earlier evidence (125 rows live).
  - `""` — no startDate and no LI date (no measurable age).
- **`crossSourceRepostEvidence`** — an evidence CLASS, not a match
  method: `reqId` = CONFIRMED repost (the card IS the same requisition),
  `title` = suggestive, `""` = none. Multiset-tier rows report `title`
  here (their card was found by title; the tier is provenance).

## 3. `numApplicants` censoring — READ BEFORE ANY AGGREGATION

LinkedIn serves BUCKET labels, not counts (corroborate.py):
"Be among the first 25 applicants" floors the observable count at
**25** (true count ≤ 25); "Over 200 applicants" caps it at **200**
(true count > 200); LinkedIn also resets counts on repost. Live
snapshot (v2.4): 357/1,050 matched rows sit at the floor, 99 at the
cap. CSV column #44 `applicantCensored` (v2.4) recomputes the flag
from (num, label) — every bucket-at-bounds row ships `true` (456
`true` / 950 `false` live); the stored per-signal flag in
`signals.jsonl` under-reports (752/1,502 records predate the key).
**Never report mean/median applicant counts without this censoring
note** — the bucket bounds bias every moment statistic.

## 4. The v2.3 cross-source timing floor (#42/#43)

Workday resets `startDate` on repost (S8-C evidence: labels + startDate
move together), so `startDate` alone overstates freshness. The floor:

- `earliestEvidenceDate` = min(startDate, linkedinPostedDate) — every
  row carries a value; `daysOnMarket` measures from it.
- When the LI card PREDATES startDate, `crossSourceRepostEvidence`
  names the evidence class: `reqId` (confirmed — same requisition) or
  `title` (suggestive). `daysOnMarketBasis` becomes
  `startDate+linkedin` so consumers can see which rows rest on the
  earlier cross-source date.
- Live (v2.4): 125 evidence rows (81 `reqId` + 44 `title`) — every one
  has liDate STRICTLY earlier than startDate; the 20 same-day
  (li==start) boundary rows correctly carry none.

## 5. v2.4 — LANDED (S9 audit round, 2026-09-16; 9ed96cb + full regen 4d33981)

Shipped in the on-disk deliverable and verified by the independent
full-population recompute in `audit/s9-audit/H4-csv-v24-verify.md`
(0 cell diffs on 44 columns × 1,406 rows):

1. **Regeneration under the composed join** (S19/S20): reqId-tier cards
   are reserved, title-tier pairs must pass `verbatim_match` — matched
   moved 1,095/77.9% → **1,050/74.7%** (62 matched→no_match, all
   title-method, 36 carrying foreign-reqId cards; 17 no_match→matched
   burned reqs re-matched). All invariants green: 0 duplicate
   linkedinUrls, 0 foreign-reqId title rows, 0 verbatim failures,
   0 unprobed no_match.
2. **`applicantCensored`** (#44) — recomputed from (num, label) into
   the CSV (the §3 bucket flag is now a column): 456 `true` / 950
   `false` live (all 357 floor-25 and 99 cap-200 rows `true`).
3. **`firstSeenDate`** — from the board-watch state's `first_seen`:
   1,406/1,406 rows (distribution 09-09×1,299 / 09-10×20 / 09-13×65 /
   09-14×8 / 09-15×14; ZERO rows == dumpDate — the one-shot-dump
   fallback no longer fires).
4. **`lastResetDate`** — from the watch's repost detections: 10 rows
   filled (37 events live, 10 with a measured `new_startDate`; last
   event wins per req) instead of the reserved "".
5. **Mechanics** — `postingAgeDays` frozen to `snapshot_date` (B2r) and
   booleans uniform lowercase on #13/#16/#35/#44 (was Python-style
   `True`/`False` on #13/#16 through v2.3).

## 6. Sibling artifacts (same directory, `{out}` = `nvidia_us_fulltime`)

| file | what it is |
|------|-----------|
| `{out}.list.jsonl` | one row per board posting (the population; `.list.status` = pagination meta) |
| `{out}.details.jsonl` | detail records per reqId (append-only; idempotent last-wins at finish) |
| `{out}.signals.jsonl` | one record per LI card fetch: status, counts, `applicantCensored`, `fetched_at` |
| `{out}.li_index.jsonl` | the card index — 1,502 cards = 1,009 list-partitioned + 493 titleSearch-discovered (`source` field marks the latter) |
| `{out}.title_search.jsonl` | per-req probe state; terminal statuses `hit_new` \| `hit_indexed` \| `no_card`; `blocked`/`error` retry (live v2.4: 516/33/303) |
| `{out}.title_search.meta.json` | completion gate `{done, probed, terminal, population, unprobed}` (live v2.4: done=true, 852/852 probed/terminal, population 356, unprobed 0) — finish reads it |
| `{out}.facet_tags.jsonl` | tagfacets output per (param, value); `facetPop` population fingerprints + `facetDone` markers are population-keyed since e5def90 |
| `{out}.similar_edges.jsonl` | role-similarity graph: `{reqId, similar_reqId, similar_title, rank}` per edge (6,644 live) |
| `{out}.facets.json` | the facet census (7 facets / 190 values) |
| `{out}.report.txt` | finish-time validation report (counts + coverage lines) |

Watch state lives in the sibling dir `ingest/data/board_watch/`:
`nvidia_us_fulltime.state.jsonl` (per-req watch state incl. `first_seen`
— 1,406 rows), `…reposts.jsonl` — **as-shipped schema**
`{reqId, title, detected_at, prev_postedOn, new_postedOn, prev_implied,
new_implied, prev_startDate, confidence, new_startDate}` (37 events;
supersedes the draft schema in findings-recency.md §250-254, which was
never implemented), `…newposts.jsonl` (daily diff, with
`titleSearchFallback` provenance since 852bb5f).
