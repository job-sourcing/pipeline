# S15 Design — the four Greenhouse dialect boards (Baidu / BYD / NetEase Games / SHEIN) + the deep-audit round

> Status: FINAL (post-review). Fresh-context review 2026-09-24 returned
> GO-WITH-AMENDMENTS (3 SEV-1, 5 SEV-2, 5 SEV-3 — all applied below;
> review facts folded into the census + ladder text).
> Census date: 2026-09-24 (live, `?content=true` — the adapter's exact URL).

## 1. Scope

1. Onboard the 4 config-only Greenhouse boards deferred at S14 close:
   `ats:greenhouse:baidu` · `ats:greenhouse:byd` ·
   `ats:greenhouse:neteasegames` · `ats:greenhouse:shein` — each with
   its full dump chain (list → details → tagfacets → corroborate
   partitioned index → titlesearch → finish → invariants), LI
   registries, watch rows, h1b employers, and test pins. "Config-only"
   was the S14 assumption; this design documents why it is FALSE (the
   adapter's classifier needs a dialect upgrade first — §3).
2. After the four land: the user-mandated **deep audit** across all
   new companies (S13+S14+S15 roster — §8).

**Non-goals:** Huawei + Kuaishou (remain unspecced, PLAN carries them);
any aggregator-source changes; any csv-v2 column changes.

## 2. Live dialect census (evidence)

All five boards fetched live 2026-09-24 with `?content=true`
(offices/departments only appear on the content URL — the bare
`/jobs` endpoint serves a lighter payload; this cost the aborted
session a false alarm before the sandbox recycled it).

| board | jobs | offices shape | location.name dialect | requisition_id | metadata |
|---|---|---|---|---|---|
| anthropic (template) | 629 | 629/629 carry offices but only 68/629 have >1; 58 office records have NULL location (Remote-Friendly US ×46, Seoul ×12 — **31 jobs' only office has no location string at all**); 21 office ids board-wide, per-job sets vary | `SF, CA; NYC, NY` + `Remote-Friendly, United States` | 629/629 (`REQ-26-…`, numeric) | `Location Type` |
| **baidu** | 28 | 26/28 non-empty but ALL the SAME office id 1570 `"Sunnyvale, CA, United States"` (the HQ default — including on the 3 Toronto rows) | `Sunnyvale, CA` · `Mountain View, CA` · `Los Angels, CA` · `Los Angeles, California` · `Toronto, ON` ×2 · `Toronto, Ontario, Canada` | **0/28 — all None** (id fallback) | none |
| **byd** | 22 | 22/22 present but malformed: last segments `CA 93535` ×4, `CA 95337` ×3, `""` ×2, proper `United States` ×13 | `Cupertino, California, United States` · `Lancaster, CA` · `Manteca,California,United States` · `Pasadena,LA,California` · bare `Cupertino` ×2 · `3465 E East Foothills, Fl 2, Pasadena, California, United States` | 22/22 (numeric) | none |
| **neteasegames** | 31 | 31/31 present but useless: `""` or `Vancouver-Onsite` only | semicolon country tokens: `Canada-Remote; Spain-Remote; United Kingdom - Guildford Onsite; United States-Remote` · `Singapore-Guoco Midtown` · `Hong Kong` · `Guangzhou Office` | 31/31 (numeric) | legacy keys |
| **shein** | 18 | 18/18 proper: `Los Angeles, California, United States` ×13 / `San Diego, California, United States` ×5 — **3 office ids** (LA Studio / LA Office / SD; two ids share one location string), per-job sets vary | city-only: `Los Angeles` · `San Diego` | 18/18 (`GRQ20260630-0002`) | **15 keys incl. `Employment Type`** (`Full-time` ×17, `Part-time` ×1 — the part-time job is `Los Angeles Marketing Counsel (Part-Time, Contract)`), `Pay Range`, `Job Family`, `Legal Entity`… |

## 3. Problem: the classifier pinned on anthropic breaks on 3 of 4

`GreenhouseAdapter._office_countries()` takes each office's
last-comma-segment as the country. On the new dialects:

- **baidu**: every row resolves to `United States` (the default
  office) — including the 3 Toronto rows whose titles say
  `- Canada` and locations say `Toronto, ON` / `Toronto, Ontario,
  Canada`. The office record is recruiter sloppiness (one office id
  shared board-wide = the company default, not per-job truth).
  Classifying by it would import 3 false-US rows.
- **byd**: `CA 93535` / `CA 95337` / `""` last segments are not
  country strings → `country_str_matches` fails → rows would drop
  despite being unambiguously US (all 22 jobs are CA/NV cities).
- **neteasegames**: office locations are empty or `Vancouver-Onsite`
  — the real geography lives in `location.name`'s semicolon tokens
  (`United States-Remote`). Current classifier drops ALL 31 rows.
- **shein**: offices proper → current classifier works as-is; the new
  value here is `Employment Type` metadata (the first Greenhouse
  board that serves a structured employment-type field).

## 4. Design A — the unified evidence ladder `_job_in_country()`

Replace the implicit "office last-segments, any-match" classifier
with ONE row-level predicate used by BOTH call sites (§6):

```python
def _job_in_country(self, job, country, offices_discriminate) -> bool:
```

**Evidence channels** (segments = split on `;` `|` `,`):
- E1 office segments: every office's `location` string, all segments
  (not just the last — byd's `CA 95337` is a trailing segment with a
  state token inside).
- E2 location segments: `location.name` split on `;` `|` `,` (handles
  anthropic's `;`-lists and neteasegames's `Canada-Remote; …`
  multi-country strings).

**Office-channel discrimination (board-level, computed once per
listing):** collect each job's office-id SET (empty set for
office-less jobs); the channel DISCRIMINATES iff the set of
non-empty per-job signatures has >1 distinct member. baidu: every
office-bearing job is `{1570}` → one signature → NOT discriminating
(2 office-less jobs contribute nothing — their empty signatures are
ignored). anthropic (21 ids, varying per-job sets), byd (5 ids),
shein (3 ids), neteasegames (15 ids): all vary → discriminating.

**When the office channel does NOT discriminate it is DISQUALIFIED
as evidence for every rung — it contributes no phrase match (rung 2)
and no state token (rung 3).** (Review SEV-1: the draft's "demoted
below E2" wording left rung 3 scanning the default office's `CA`
token, which would have kept the 3 Toronto rows.) One narrow
exception — the null-E2 fallback: if E1 is disqualified AND E2 is
empty/whitespace (no location.name at all), fall back to E1 rather
than dropping (a board with zero free-text geography should not lose
rows to the demotion heuristic; census-replay pins guard this).

**Ladder** (first hit wins):
1. **Explicit target-country phrase in E2** —
   `workday.country_str_matches(segment, country)` applied to each
   E2 segment (phrase tokens hyphen-split INSIDE the segment, so
   `United States-Remote` matches; NO pooled whole-string
   single-token matching — the `_row_in_country` pooled reading
   strips only `,()` and would miss `…; United States` after a
   semicolon, and would add a whole-string `us` channel; the review
   verified both divergences on census rows).
2. **E1 phrase evidence — only when offices discriminate** — same
   per-segment `country_str_matches` over E1 segments (anthropic's
   `…, United States` office endings; shein's proper offices).
3. **State-token fallback (country == "United States" only):**
   any whitespace token of any E2 segment — plus E1 segments ONLY
   when offices discriminate — that is a US state abbreviation or
   state name (`CA`, `California`, `NV`, `LA` as a state abbrev;
   `CA 95337` yields `CA`; `Pasadena,LA,California` yields `LA` +
   `California`). Catches byd's malformed offices, baidu's
   `Los Angels, CA`, `Sunnyvale,CA`.
4. No hit → **not in country** (dropped, `client_filtered` count —
   the greenhouse class's shipped audit convention).

**Row fields:** `countries = [country]` when the verdict is true,
else `[]` — the verdict-stamped ashby/custom convention (repo grep:
nothing downstream consumes the greenhouse `countries` list beyond
the adapter's own filter). Multi-country rows (neteasegames
`Canada-…; United States-Remote`) are US-eligible via rung 1 — the
same ANY-location semantics anthropic ships for multi-office rows.

**Honest undercount, kept:** city-only foreign-ish rows drop without
a US token — neteasegames `Bothell - Onsite; Irvine - Onsite;
Mountain View-Onsite; Vancouver-Onsite` (a North-America mixed row
with zero explicit country tokens) drops to the filter count. No US
city gazetteer — that is the guessing line the greenhouse class
already draws for timeType ("no guessing"), and the dropped count is
loud in `list.status` + the report.

**Expected verdicts** (census arithmetic, review-re-verified):
- baidu: 25 US / 28 (3 Toronto rows drop — correct, their titles say
  Canada; E2 has no US token and the default office is disqualified)
- byd: 22 US / 22 (all CA/NV cities; rungs 2–3)
- neteasegames: 3 US / 31 (the 3 `United States-Remote` rows — 3 US
  animator roles; the board is Singapore/Canada/UK-dominant, the
  alibaba/tripcom size class)
- shein: 18 US / 18 (rung 2, discriminating offices)
- **anthropic: 469 → 500 on the 629-job census** (+31 rescues, 0
  regressions-out — review finding 2; the 31 are rows whose ONLY
  office carries a null location string — `Remote-Friendly US
  (Travel Required)` — so the office channel never saw them, and
  E2's `Remote-Friendly, United States` or a state token rescues
  them). This is a deliberate, honest improvement, not a regression:
  the shipped classifier silently dropped US remote roles. Two
  operational consequences are OWNED (§7): the anthropic dump
  refresh (list/details/tagfacets/corroborate/finish re-run in S15)
  and the anthropic watch state re-seed from the fresh dump (else
  the first post-change cron reports ~31 one-time "new" postings —
  re-seeded instead of accepted-as-drift).

## 5. Design B — shein's `Employment Type` metadata → timeType

Greenhouse's public payload serves no top-level timeType, but shein's
`metadata` list carries `{name: "Employment Type", value:
"Full-time"|"Part-time"}`. Wire a `_time_type_from_metadata()`
extractor (reusing the ashby `_TT` normalization, HOISTED to module
level so the two adapters share one table — `Full-time` → `Full time`; review verified the hyphenated keys are covered) into
`row["timeType"]` + the detail payload. **Mixed-metadata boards**
(review #11): the extractor applies per-row when present; rows
without the metadata pass the value through unmapped (the ashby
precedent at site_boards.py:366 — a future `Contract` surfaces
honestly, not blank); the honest NOTE prints only when the metadata
is absent board-wide AND a time filter was requested. Consequence:
shein's watch/config rows carry `time_type: Full time`
(the openai/ashby precedent — the field is served, so the filter is
honest); shein's US Full-time expectation = 17 rows (1 Part-time
dropped by a structured field, not by guessing).

Non-goals within B: Pay Range / Job Family / Legal Entity metadata
stays un-mapped (csv-v2 has no columns for them; noted in the report
text only if trivial).

## 6. Design C — the detail seam threading

`list_board` and `detail_payload` MUST derive their country/timeType
verdict from the SAME helpers — and review SEV-1 #3: `detail_payload`'s
signature (and the dispatch seam, and all three production call
sites — board_dump.py:577 details phase, gha_board_watch.py:249
enrich, gha_board_watch.py:762 repost-refetch) take NO country today.

**Mechanism:** thread optional `country=` / `time_type=` params
through the dispatch seam `site_boards.detail_payload(spec, path,
cfg, country=None, time_type=None)`; the dump details phase passes
`args.country` (+ `args.time_type`), the watch enrich passes
`w["country"]`, the repost refetch may pass None. **None path
(back-compat, pinned by the existing test at test_site_boards.py:140):**
country not threaded → the detail payload's country descriptor stays
the OFFICE-derived `countries[0]` (the pre-S15 helper, kept for this
path); time_type not threaded → the metadata-derived value when the
board serves it, else `""`. When threaded, both fields come from the
SAME ladder verdict as the list row — so `detail_in_country`, the
countryfilter no-op path, and the watch's enriched-record
classification agree with the list verdict by construction. The
2026-09-19 docstring field map gets a dialect addendum documenting
rungs 1–4 and the threading contract.

## 7. Wiring (the S14 pattern, ×4)

- **Watch config** rows (label · board · company · li_variants ·
  slice_locations — literal config grammar, comma-containing
  locations; slices trimmed to cities that ACTUALLY appear on the
  board per the census — dead slices are pure LI-budget burn):
  - `baidu_us_fulltime` · `ats:greenhouse:baidu` · company `Baidu`
    · variants `Baidu, Baidu USA` · slices: `United States` +
    `Sunnyvale, California, United States` · `Mountain View,
    California, United States` · `Los Angeles, California, United
    States` (census: Sunnyvale 11 / Mountain View 12 / LA 2 — no
    San Jose)
  - `byd_us_fulltime` · `ats:greenhouse:byd` · `BYD` · variants
    `BYD, BYD Motors, BYD North America` · slices: US +
    `Pasadena/Lancaster/Cupertino/Manteca, California` + `Las
    Vegas, Nevada, United States` (census: Cupertino 8 / Pasadena
    8 / Lancaster 7 / Manteca 4 / Las Vegas 1 — no Irvine)
  - `neteasegames_us_fulltime` · `ats:greenhouse:neteasegames` ·
    `NetEase Games` · variants `NetEase Games, NetEase` · slices:
    `United States` + `Remote, United States` ONLY (the 3 US rows
    are all `United States-Remote`; Bothell/Irvine/Mountain View
    appear only in the rung-4-dropped mixed row)
  - `shein_us_fulltime` · `ats:greenhouse:shein` · `SHEIN` ·
    variants `SHEIN, SHEIN Distribution` · slices: US + `Los
    Angeles, California, United States` + `San Diego, California,
    United States` (census: LA 13 / SD 5) + `time_type: Full
    time`
- **anthropic refresh + re-seed** (review #2 consequence): re-run
  the anthropic dump chain (list → details → tagfacets →
  corroborate `--li-reindex` → titlesearch → finish) so the
  artifact reflects the census + the 31 rescues, then re-seed
  `anthropic_us_fulltime.state.jsonl` from the fresh dump
  (`--seed-from`) — the first post-change cron then reports drift,
  not a 31-row "new" flood.
- **h1b-extract.yml** employers += `baidu_us_fulltime:BAIDU` ·
  `byd_us_fulltime:BYD` · `neteasegames_us_fulltime:NETEASE` ·
  `shein_us_fulltime:SHEIN,ROADGET` (Roadget Business Pte is SHEIN's
  US-employer entity of record on many LCAs; grammar verified:
  semicolon `label:EMP,EMP` pairs, substring match on lowercased
  EMPLOYER_NAME). Manual `workflow_dispatch` required — the
  workflow has no cron (review #12).
- **board-watch.yml timeout**: 9→13 watches. Greenhouse watches are
  cheap (one HTTP GET per board per run — the whole board incl.
  descriptions arrives in the list fetch; detail_payload is
  cache-served) but corroborate/titlesearch budgets scale per watch
  (per-watch budget default 240 s × 13 ≈ 52 min floor).
  75 → 95 min (conservative; revisit at live validation).
- **Integrity pin**: `TestShippedWatchConfig` roster 9 → 13 (a NEW
  `== 13` assertion — the current pin asserts only `>= 4`; plus the
  4 boards added to `test_stress_test_roster_is_configured`); spec
  §9 recount; PLAN S15 record.
- **State seeds**: after each dump chain completes, seed the watch
  state from the dump's list.jsonl (the S13 `--seed-from` pattern) —
  the first cron run then reports drift, not a 25-row "new" flood.

## 8. The deep-audit round (the user's explicit ask)

After the four boards land, audit ALL new companies (S13 openai/
anthropic + S14 bytedance/alibaba/tripcom + S15 ×4 — 9 companies)
across these dimensions, each producing a PASS/FAIL line in an audit
report committed to the repo:

1. **Invariants**: `s10_invariants.py` re-run per label — ALL TEN
   green (the non-negotiable).
2. **Ten-invariant chain spot-audit**: re-derive 2 random rows per
   label by hand from the raw payloads (reqId → list row → detail
   record → CSV row) and diff against the shipped artifacts.
3. **LI join sanity**: matched ratio per company in the
   jd-0/55-class honest band — for n<20 boards (neteasegames n=3)
   report n and hand-verify every 0-match row instead of a band
   (review #12); verify 0-match rows are genuinely un-joined (title
   overlap absent), not pin/registry failures.
4. **H-1B band coverage**: banded/total per company; for 0-band
   companies re-verify the non-overlap story (alibaba/tripcom
   precedent); byd/netease/shein get their first extract this round
   (manual dispatch).
5. **State convergence**: netflix one-fetch rescue — active count
   climbed toward ~369 and status complete (the S14 pending item);
   all 13 watches' state vs board counts within drift tolerance;
   the anthropic state re-seed reconciled (435 → ~466 census-truth,
   review #13d).
6. **Data freshness**: every label's dump age < 7 days at audit time;
   list.status complete=True everywhere; `complete=False` only where
   a documented fail-soft host exists (alibaba cloud).
7. **Roster coherence**: csv-v2-spec §9 table == the actual
   `ingest/data/workday/` artifact set == the 13 watch rows == the
   13 h1b employers — the four rosters agree.
8. **Foreign trail / dialect replay**: for each of the 4 new boards,
   the client_filtered counts match a re-derivation from the LIVE
   payload (census arithmetic §4); **plus the anthropic delta
   re-derivation (469→500, +31 remote-US rescues, 0 out)** — the
   adapter change is itself audited, not just the new boards
   (review #13a).
9. **Adapter health telemetry** (review #13c): office-id cardinality
   + modal-office coverage per greenhouse board recorded in the
   audit report — the drift detector for future default-office
   regressions (a board drifting toward single-signature offices
   flips the discrimination heuristic; the numbers make it visible).
10. **shein timeType column check** (review #13b): the CSV timeType
    column non-blank on the 17 Full-time rows; exactly 1 Part-time
    row dropped by the structured filter.

## 9. Test pin plan

- `TestGreenhouseAdapter` — new fixture jobs per dialect (baidu
  default-office + Toronto, byd malformed CA-zip offices, netease
  semicolon tokens, shein metadata timeType) pinning:
  rung-1 phrase match (incl. `United States-Remote`), the
  office-disqualification verdict (a Toronto-shaped fixture: default
  office + `Toronto, ON` → dropped), rung-3 state tokens (`CA
  95337`, `Sunnyvale,CA`, `Pasadena,LA,California`), the countries
  verdict stamp, the detail seam (list verdict == detail descriptor
  when threaded; office-derived descriptor when NOT threaded),
  shein timeType mapping + the Part-time filter drop + the NOTE
  gating (prints for time-requested boards WITHOUT metadata; silent
  for shein), baidu id-fallback reqId, the null-E2 fallback (E1
  disqualified + empty location.name → office evidence), the
  anthropic rescue shape (only office has null location +
  `Remote-Friendly, United States` E2 → kept).
- **Census-replay pins** (review #7a — promised by R1, was missing):
  load the 4 committed census payloads from
  `ingest/data/ats_seed/s15_census/` (NOT the 9.1MB anthropic file —
  a distilled fixture covers it) and pin the board verdict counts
  25/22/3/18 + client_filtered 3/0/28/0 (shein: +1 tt-drop with the
  time filter).
- **Detail-side timeType pin** (review #7b — the CSV column reads
  the DETAIL payload): `info["timeType"] == "Full time"` for a
  shein-shaped fixture.
- `TestShippedWatchConfig`: NEW `== 13` roster assertion + the 4 new
  boards in `test_stress_test_roster_is_configured`; shein
  time_type present; the other 3 greenhouse rows without it.
- Watch tests: a greenhouse-dialect watch row flows through
  current_postings with `country_client=False` and zero detail
  fetches (cache-served).

## 10. Risks

- **R1 — E2 phrase false-positives** (`"us"` as a token inside a
  foreign string, e.g. `Ratus`…). Mitigation: phrase tokens use the
  existing `_country_matchers` tokenization (word-boundary tokens,
  not substrings); census-replay test pins the 4 boards' real
  strings.
- **R2 — state-abbrev collisions** (`ON` Ontario vs Ohio `OH`;
  `LA` Los Angeles vs Louisiana; `IN` Indiana vs India's ISO suffix
  — review #9). Accepted: in job-location dialects `CA`/`LA`/`NV`
  post-city are US-conventional; Canada provinces (`ON`, `BC`, `QC`)
  are not US state abbrevs; no census row uses a `City, IN` India
  dialect. Pinned by tests + the census-replay guard.
- **R3 — neteasegames 3-row board** looks thin. Not a bug: the US
  footprint IS 3 remote roles (census §4). The roster records it.
- **R4 — watch timeout mis-estimate** (13 watches × corroborate
  budgets). Mitigation: 95-min timeout + live validation before
  close; the run log prints per-watch timings.
- **R5 — SHEIN LCA entity mismatch** (Roadget vs SHEIN naming).
  Mitigation: employer OR-list includes both; verify at extract.
