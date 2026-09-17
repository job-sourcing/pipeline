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
> | v2.5 | 43 | 2026-09-17 | LANDED (S11 quality round, user review): `reqYear` REMOVED (JR-number prefix ≠ a calendar signal — 0.1% year match); `questionnaire` → `questionnaireId` (join key into the NEW linked `questionnaires.csv`); `daysLeftToApply` never negative (elapsed "at least until" floors ship "" = extended/unknown); `censored=true` on measured repost resets (firstSeenDate < startDate) — §6 |
> | v2.6 | 49 | 2026-09-17 | LANDED (S11, design S8-F-3): #44-#49 DOL H-1B/LCA wage bands — `h1bFilings` / `h1bWageP25` / `h1bWageP50` / `h1bWageP75` / `h1bMatchBasis` / `h1bMatchTitle` (certified NVIDIA filings, annualized offered wage, exact-normalized title join) + linked `h1b_lca.csv` — §7 |
>
> **Snapshot note (2026-09-17, post-regen):** the on-disk deliverable is
> v2.6 (1,410 × 49, dumpDate 2026-09-17) — the v2.5 quality-round
> semantics plus the H-1B wage-band columns (bands populate once the
> GHA extract lands); same join as v2.4/v2.5 (matched 1,050 / 74.5%);
> all TEN invariants green (`scripts/s10_invariants.py` — INV-7..INV-10
> are the S11 additions). Every live count below is THIS snapshot's.
>
> **Snapshot note (2026-09-16, post-regen):** the on-disk deliverable is
> v2.4 (1,406 × 44, dumpDate 2026-09-16) — regenerated under the
> composed join + verbatim guard (cd12bc3, regen 4d33981): matched
> 1,050 / 74.7% HONEST (62 misattributed title rows dropped, 17 burned
> reqs re-matched), verified end-to-end by an independent full-population
> recompute (`audit/s9-audit/H4-csv-v24-verify.md` — 0 cell diffs, all
> join invariants green). Every live count below is THIS snapshot's.

## 1. The 49 columns (v2.6 numbering)

Phase of origin: **L** = list · **D** = details · **S** = signals
(corroborate) · **T** = tagfacets · **F** = derived at finish.

| # | column | phase | semantics |
|---|--------|-------|-----------|
| 1 | `reqId` | L | Workday requisition id (`JR…`) — the join key of the whole dump |
| 2 | `title` | D | posting title (detail record wins; list title only when detail unreachable) |
| 3 | `company` | F | dump company (CLI `--company`; "NVIDIA") |
| 4 | `hiringOrg` | D | legal hiring entity ("2100 NVIDIA USA") |
| 5 | `timeType` | D | "Full time" / "Part time" … |
| 6 | `postedOn` | L | relative label from the board list ("Posted Today") — RAW LIST TRUTH; may disagree with #7 on reposts (labels reset independently — see §4) |
| 7 | `startDate` | D | ISO posting start — **Workday RESETS this on repost** (see #41/#42) |
| 8 | `postingAgeDays` | F | `snapshot_date` − startDate, FROZEN at finish (== dumpDate − startDate on all rows; v2.4/B2r); "" when no startDate |
| 9 | `primaryLocation` | D | "US, CA, Santa Clara" |
| 10 | `nLocations` | D | total locations; UNKNOWN ≠ 1 when `detailError` set (surfaced honestly) |
| 11 | `locations` | D | ALL locations, "; "-joined (multi-location completeness) |
| 12 | `remoteFlag` | F | `true` when any location mentions Remote (lowercase) |
| 13 | `stateCodes` | F | "CA;NC;TX" derived from locations |
| 14 | `country` | D | country descriptor |
| 15 | `questionnaireId` | D | the posting's application-questionnaire id — the JOIN KEY into `{out}.questionnaires.csv` (v2.5; was a bare `true`/`false` boolean through v2.4). "" = no detail or no questionnaire. Definitions are SHARED across postings (4 distinct ids / 1,410 rows live) — `--phase questionnaires` fetches each id ONCE |
| 16 | `similarJobsCount` | D | related postings on the board per Workday |
| 17 | `description` | D | clean text (`html_to_text`) |
| 18 | `descriptionLength` | F | chars of clean text |
| 19 | `url` | L/D | canonical apply URL |
| 20 | `detailError` | D | "" \| `detail_unreachable` — when set, detail-derived cols are UNKNOWN, not absent |
| 21 | `linkedinUrl` | S | matched LinkedIn guest card URL |
| 22 | `linkedinPostedDate` | S | the LI card's posted date (ISO) |
| 23 | `numApplicants` | S | LI applicant count — a CENSORED bucket, see §3 |
| 24 | `applicantLabel` | S | raw LI label ("Over 200 applicants") |
| 25 | `dateDeltaDays` | S | linkedinPostedDate − startDate (repost lag; + = LI newer) |
| 26 | `corroborationStatus` | S | `matched` \| `no_match` \| `blocked` \| `not_checked` — see §2 |
| 27 | `matchMethod` | S/F | `reqId` \| `title` \| `titleMultiset` \| "" — see §2 |
| 28 | `firstSeenDate` | F | first sighting BY US — the board-watch state's `first_seen` when present, else dumpDate (live v2.5: 1,379/1,410 from the watch) |
| 29 | `corroboratedOn` | S | ISO timestamp of the signal fetch |
| 30 | `dumpDate` | F | dump generation date (today at finish) |
| 31 | `endDate` | D | detail endDate (ISO) |
| 32 | `daysOnMarket` | F | snapshot_date − earliest evidence date; "" when no startDate and no LI date |
| 33 | `daysOnMarketBasis` | F | `startDate` \| `startDate+linkedin` \| "" — see §2 |
| 34 | `censored` | F | `true` \| `false`: startDate missing, predating the watch seed (2026-09-09), OR `firstSeenDate` < startDate (a MEASURED repost reset — v2.5) ⇒ age is a LOWER bound only. Live v2.5: 1,231 `true` / 179 `false` |
| 35 | `repostCount` | F | the slug `…_JR####-N` trailing `-N` = Workday's own repost counter; 0 when absent |
| 36 | `lastResetDate` | F | the watch's repost-detector `new_startDate` for this req (last event wins); "" when no measured event (15 rows live) |
| 37 | `applicationDeadline` | F | "…accepted … until {date}" parsed from the description (98.9%); endDate fallback. A FLOOR ("at least until"), not a guarantee — postings routinely stay live past it (1,261/1,395 live rows have an elapsed floor) |
| 38 | `daysLeftToApply` | F | deadline − snapshot_date; "" when none AND "" when the floor already elapsed on a live posting (auto-extended → UNKNOWN, never negative — v2.5; elapsed-ness = #37 < dumpDate) |
| 39 | `workerSubType` | T | tagfacets phase: Regular Employee / Management / New College Graduate / Intern (Fixed Term) … |
| 40 | `jobFamilyGroup` | T | tagfacets phase (NVIDIA's own taxonomy) |
| 41 | `earliestEvidenceDate` | F | min(startDate, linkedinPostedDate) — the honest cross-source age floor (§4) |
| 42 | `crossSourceRepostEvidence` | F | "" \| `reqId` \| `title` — the LI card PREDATES startDate ⇒ on-market-before evidence (§4) |
| 43 | `applicantCensored` | F | `true` \| `false` \| "" — #23 is a BUCKET when `true` (floor 25 / cap 200; see §3); recomputed at finish from (num, label), NOT the stored signal flag. Live v2.5: 484 `true` / 926 `false` |
| 44 | `h1bFilings` | F | DOL H-1B/LCA certified NVIDIA filings backing #45-#47 (deduped by case number; certified both dialects + full-time + plausible-wage only; pool n ≥ 3 or "") |
| 45 | `h1bWageP25` | F | annualized OFFERED wage p25, USD (WAGE_RATE_OF_PAY_FROM × unit multiplier: Year 1 / Month 12 / Bi-Weekly 26 / Week 52 / Day 260 / Hour 2080; bounded $25k–$1M — DOL unit-corruption rows dropped) |
| 46 | `h1bWageP50` | F | …median |
| 47 | `h1bWageP75` | F | …p75 |
| 48 | `h1bMatchBasis` | F | `title+state` \| `title` \| `subset+state` \| `subset` \| "" — tier (token-exact vs subset: the posting contains every pool token, possibly more specialized) + granularity (state pool n≥3 vs all-state) — see §7 for the audited rules |
| 49 | `h1bMatchTitle` | F | the matched pool's most-frequent raw LCA title (e.g. "Engineer, Senior Systems Software") — the audit string for WHICH population produced the band |

Snapshot note: `postingAgeDays`/`daysOnMarket`/`daysLeftToApply` all
reference ONE `snapshot_date` (today at finish, shared by all rows;
`postingAgeDays` was frozen to it in v2.4/B2r — it used to read the
wall clock per row). Boolean casing is UNIFORM lowercase `true`/`false`
since v2.4 across the boolean columns (`remoteFlag`, `censored`,
`applicantCensored` — #12/#34/#43; `questionnaireId` #15 is an id
string since v2.5, not a boolean).

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

## 6. v2.5 — LANDED (S11 quality round, 2026-09-17; user CSV review)

Shipped in the on-disk deliverable (1,410 × 43, dumpDate 2026-09-17);
all NINE invariants green (`scripts/s10_invariants.py`):

1. **`reqYear` REMOVED** (was #9, "req age signal"): DISPROVEN by
   data — the JR-number prefix matched `startDate`'s year on 2/1,410
   rows (0.1%); 2026 postings carried prefixes spread 1966..2026
   (stdev 7.6 "years"; the number is a sequential ID-space artifact,
   allocated in blocks, not a calendar). Anyone curious can still
   derive it from `reqId` itself.
2. **`questionnaireId`** (#15): the join key into the NEW linked
   `{out}.questionnaires.csv` — the application-questionnaire
   DEFINITIONS (question body, required, type, answers) that the
   posting's apply flow would ask. Endpoint discovered S11 from the
   apply-flow SPA
   (`/wday/calypso/cxs/common/{tenant}/questionnaire/{id}`, siteless,
   no auth); definitions are SHARED across postings — 4 distinct ids
   covering 1,410/1,410 rows (64% share one standard immigration
   screen). `--phase questionnaires` fetches each id ONCE
   (append-only `questionnaires.jsonl`); finish warns loudly when a
   detail-carried id has no fetched definition.
3. **`daysLeftToApply` never negative** (#38): 1,261/1,395 rows had
   elapsed "at least until" floors while remaining LIVE on the board
   — the floor auto-extends, so days-left on those rows is UNKNOWN
   (""), not "-247 days" (which read as closed). #37 keeps the stated
   floor date (source truth); elapsed-ness = #37 < dumpDate.
4. **`censored` on measured resets** (#34): `firstSeenDate` <
   `startDate` is impossible without a repost reset after our first
   sighting — 77 rows flipped to `censored=true` (1,154→1,231), the
   watch-state mirror of the `crossSourceRepostEvidence` class.

## 7. v2.6 — LANDED (S11, design S8-F-3: DOL H-1B / LCA wage bands)

Public government data (no auth, no ToS issue) joined per posting:

1. **The extract** (`{out}.h1b_lca.jsonl`, built by
   `scripts/h1b_extract.py`): NVIDIA rows from the DOL's quarterly LCA
   disclosure xlsx files
   (dol.gov/agencies/eta/foreign-labor/performance), append-only,
   case-number-deduped, one line per filing with 17 LCA fields +
   `sourceFile` provenance. **The download egress matrix (measured
   2026-09-17):** HK sandbox direct → 403 Akamai; Netlify US function →
   403 Akamai; Supabase Deno proxy → 200 but TRUNCATED at ~10.5MB
   (the files are larger — the extractor's size guard turns that into
   an error, never a corrupt extract). The one working transport is a
   plain US runner: **GHA** — the `h1b-extract.yml` workflow on the
   org repo IS the transport (dispatch-only, quarterly cadence;
   `--recent 6` = the 6 latest published quarters; FY2026_Q2 404s at
   the canonical path → soft-skip). The mirror sync back-syncs
   `*.h1b_lca.jsonl` org → archive (the archive→mirror rsync --delete
   would otherwise wipe it).
2. **The join** (#44-#49, AUDITED semantics — a fresh-context design
   review round caught four live misattribution classes and a data
   bug before shipping): filings filtered to certified (BOTH status
   dialects "Certified-Withdrawn"/"Certified - Withdrawn"), full-time,
   and a PLAUSIBLE annualized wage ($25k–$1M — the DOL files contain
   unit-corruption poison: "Software Engineer" 136,000/Hour → $282.9M;
   dropped, never averaged). Join key: the stop-word-free, plural-
   folded, level-code-free token SET (the LCA comma-form "Engineer,
   Senior Systems Software" and the posting form "Senior System
   Software Engineer" share a set). NO stemming, ever —
   "engineering" ≠ "engineer" is load-bearing (it keeps Engineering
   Managers out of SWE pools). Candidates = token-sets S ⊆ posting
   tokens, |S|≥2, SUPPRESSED when the posting adds a role-block word
   (QA/test/intern/college/marketing/sales/support… — an occupation
   change, not a specialization). best = max by (|S − level tokens|,
   |S|, filings): DOMAIN conditioning outranks LEVEL conditioning.
   State hop on the (best, primaryState) pool when n≥3 (state from
   primaryLocation — stateCodes order is non-canonical, live-measured);
   final gate n≥3 or honest "". Basis: "title+state"/"title" iff S == P
   (token-exact); "subset+state"/"subset" when the posting is more
   specialized (the band is the POOL's distribution, level-mixed —
   h1bMatchTitle names the population). Live: 734/1,410 rows banded
   (28 title+state / 706 subset-family).
3. **The linked view** (`{out}.h1b_lca.csv`): one row per filing
   (incl. uncertified ones) + a derived `annualizedWage` column — the
   raw evidence behind the bands, free for re-banding.

## 8. Sibling artifacts (same directory, `{out}` = `nvidia_us_fulltime`)

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
| `{out}.questionnaires.jsonl` | questionnaire definitions as fetched — `{questionnaireId, fetchedAt, payload}` per line, append-only, one line per DISTINCT id (4 live; the raw preservation behind the linked CSV) |
| `{out}.questionnaires.csv` | the LINKED questionnaire deliverable — one row per QUESTION (`questionnaireId, instructions, questionId, order, question, required, type, answers`), joined from the main CSV's #15 (8 questions live) |
| `{out}.h1b_lca.jsonl` | the NVIDIA LCA extract as fetched (one line per filing, case-number-deduped, `sourceFile` provenance) — the raw preservation behind #44-#48 |
| `{out}.h1b_lca.csv` | the LINKED LCA view — one row per filing incl. uncertified + derived `annualizedWage` |
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
