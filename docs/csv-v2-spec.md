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
> | v2.4 | 43+ | **in flight** (S9 audit round) | see §5 — regen under the composed join + 3 new/changed columns |
>
> **Snapshot honesty note (2026-09-16):** the on-disk v2.3 (1,406 rows,
> matched 1,095 / 77.9%) predates the S9-audit join fixes (cd12bc3:
> unified composition + verbatim guard). The regeneration in flight this
> round is the first honest verbatim-only composed-join deliverable —
> the matched count WILL move (misattributed title rows drop; ~35 burned
> no_match reqs can re-match). Do not cite 77.9% as final.

## 1. The 43 columns

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
| 8 | `postingAgeDays` | F | today (at row derivation) − startDate; "" when no startDate |
| 9 | `reqYear` | F | year parsed from `JR2022…` → 2022 (req-age signal) |
| 10 | `primaryLocation` | D | "US, CA, Santa Clara" |
| 11 | `nLocations` | D | total locations; UNKNOWN ≠ 1 when `detailError` set (surfaced honestly) |
| 12 | `locations` | D | ALL locations, "; "-joined (multi-location completeness) |
| 13 | `remoteFlag` | F | True when any location mentions Remote (Python bool casing) |
| 14 | `stateCodes` | F | "CA;NC;TX" derived from locations |
| 15 | `country` | D | country descriptor |
| 16 | `questionnaire` | D | True when the posting carries an application questionnaire |
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
| 29 | `firstSeenDate` | F | first sighting BY US; = dumpDate in one-shot dumps today (v2.4: from watch `first_seen` — §5) |
| 30 | `corroboratedOn` | S | ISO timestamp of the signal fetch |
| 31 | `dumpDate` | F | dump generation date (today at finish) |
| 32 | `endDate` | D | detail endDate (ISO; 43/1,406 rows live) |
| 33 | `daysOnMarket` | F | snapshot_date − earliest evidence date; "" when no startDate and no LI date |
| 34 | `daysOnMarketBasis` | F | `startDate` \| `startDate+linkedin` \| "" — see §2 |
| 35 | `censored` | F | `true` \| `false`: startDate missing or predating the watch seed (2026-09-09) ⇒ age is a LOWER bound only |
| 36 | `repostCount` | F | the slug `…_JR####-N` trailing `-N` = Workday's own repost counter (153/1,406 rows live; 0 when absent) |
| 37 | `lastResetDate` | F | "" today — reserved for the watch's repost detector (v2.4 fills it — §5) |
| 38 | `applicationDeadline` | F | "…accepted … until {date}" parsed from the description (1,391/1,406 = 98.9%); endDate fallback |
| 39 | `daysLeftToApply` | F | deadline − snapshot_date; "" when none |
| 40 | `workerSubType` | T | tagfacets phase: Regular Employee / Management / New College Graduate / Intern (Fixed Term) … |
| 41 | `jobFamilyGroup` | T | tagfacets phase (NVIDIA's own taxonomy) |
| 42 | `earliestEvidenceDate` | F | min(startDate, linkedinPostedDate) — the honest cross-source age floor (§4) |
| 43 | `crossSourceRepostEvidence` | F | "" \| `reqId` \| `title` — the LI card PREDATES startDate ⇒ on-market-before evidence (§4) |

Snapshot note: `daysOnMarket`/`daysLeftToApply` reference ONE
`snapshot_date` (today at finish, shared by all rows); `postingAgeDays`
uses today at row-derivation — same value in practice, different
mechanism. Boolean casing is inconsistent BY HISTORY and pinned
as-shipped: `remoteFlag`/`questionnaire` serialize Python-style
`True`/`False`; `censored` is the lowercase string `true`/`false`.

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
    measured from the earlier evidence (144 rows live).
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
snapshot: 374/1,095 matched rows sit at the floor, 104 at the cap.
`fetch_signals` stamps `applicantCensored: true` on bucket-at-bounds
records in `signals.jsonl` (NOT yet a CSV column — v2.4, §5).
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
- Live: 144 evidence rows (82 `reqId:` + 62 `title:`).

## 5. v2.4 — IN FLIGHT (S9 audit round, 2026-09-16)

Do not treat these as shipped until the regen commit lands:

1. **Regeneration under the composed join** (S19/S20): reqId-tier cards
   are reserved, title-tier pairs must pass `verbatim_match` — the
   matched count moves off 1,095/77.9% (misattributed title rows drop;
   ~35 burned no_match reqs can re-match).
2. **`applicantCensored`** — recomputed from the signal record into the
   CSV (the §3 bucket flag becomes a column).
3. **`firstSeenDate`** — from the board-watch state's `first_seen`
   (2026-09-09→09-15 live) instead of dumpDate (the one-shot-dump
   behavior: all 1,406 rows currently read 2026-09-15).
4. **`lastResetDate`** — from the watch's repost detections
   (37 events live) instead of the reserved "".

## 6. Sibling artifacts (same directory, `{out}` = `nvidia_us_fulltime`)

| file | what it is |
|------|-----------|
| `{out}.list.jsonl` | one row per board posting (the population; `.list.status` = pagination meta) |
| `{out}.details.jsonl` | detail records per reqId (append-only; idempotent last-wins at finish) |
| `{out}.signals.jsonl` | one record per LI card fetch: status, counts, `applicantCensored`, `fetched_at` |
| `{out}.li_index.jsonl` | the card index — 1,465 cards = 1,009 list-partitioned + 456 titleSearch-discovered (`source` field) |
| `{out}.title_search.jsonl` | per-req probe state; terminal statuses `hit_new` \| `hit_indexed` \| `no_card`; `blocked`/`error` retry (live: 490/12/288) |
| `{out}.title_search.meta.json` | completion gate `{done, probed, terminal, population, unprobed}` (live: done=true, 790/790) — finish reads it |
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
