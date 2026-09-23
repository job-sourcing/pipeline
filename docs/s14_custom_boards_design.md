# S14 Design — Own-Platform Custom Job Boards (ByteDance / Alibaba / Trip.com)

> User ask (S14): "custom job board" = the companies deferred in S12 because
> they run SELF-BUILT platforms (not Workday, not a standard ATS): Alibaba
> etc. OpenAI/Anthropic on Ashby/Greenhouse were S13's ATS adapters — NOT
> "custom job boards". This round builds the genuinely custom ones.

## 1. Target roster (live-pinned 2026-09-19)

| label | spec | platform / API | US count | transport |
|---|---|---|---|---|
| bytedance | `custom:bytedance` | Feishu atsx-throne supplier API: `POST jobs.bytedance.com/api/v1/public/supplier/search/job/posts` (+ `/config/job/filters`), header `website-path: en` selects the international portal (joinbytedance.com SPA fronts it; the zh portal serves China) | **594** (443 Regular / 147 Intern / 4 third-party) | curl_cffi impersonate (Akamai 508s plain requests) |
| alibaba | `custom:alibaba` | Alibaba Lumos (same SPA family, different API): `POST {host}/position/search?_csrf={XSRF-TOKEN cookie}`, channel `group_overseas_official_site`, language `en`. MULTI-HOST sweep: aidc-jobs.alibaba.com (153 rows, 6 US: Sunnyvale 5/Pasadena 1) + talent-holding.alibaba.com (AGH, 0 US today) + careers-tongyi.alibaba.com (Token Foundry, EMPTY) + careers-alibabacloud.com (224 rows, ~37 US — **DNS-volatile**: delegation absent from gTLD right now though live earlier today; fail-soft + retries) | ~43 (6 confirmed + ~37 when Cloud DNS resolves) | curl_cffi; GET page once to collect XSRF cookie |
| tripcom | `custom:tripcom` | Trip.com Group own board: `POST careers.trip.com/api/oversea/getOverseaJobAd`, body `condition.country:["USA"]` = SERVER-side filter; location taxonomy `/api/oversea/getLocation` (ISO-alpha-3) | **10** | curl_cffi + `Accept: application/json` (default gives XML!) |

Excluded (documented honestly, not attempted): Huawei (career.huawei.com is
China-portal shaped; US hiring ~0), Kuaishou (no public US board).
Bonus (config-only, existing greenhouse adapter, live boards found):
`ats:greenhouse:{baidu,byd,neteasegames,shein}` (28/20/28/17 jobs).

## 2. Contract mapping (rows carry full data — one-fetch economics)

All three APIs return complete postings (description + requirements + taxonomy)
in the LIST payload — same economics as greenhouse/ashby: paginate ONCE, cache
per-process, details serve from cache. Pagination shapes:

- bytedance: `limit` (200 OK), `offset`, NO total → loop until short page.
  Full en board = 1,413 rows (7 requests); **rows carry a 3-level
  `city_info` hierarchy (city→state→country, `en_name`s) = authoritative
  country per row**. ALSO a server-side US filter exists (location_code_list
  of US city codes from the filters taxonomy). CHOICE: fetch the FULL board,
  classify each row from its OWN city_info country (the site's structured
  field — the S13 "classify from the site's own field" rule; not a token
  guess), emit rows for the target country only, count foreign in meta.
  Rationale vs. server-side filter: the row-level authority catches postings
  whose city is missing from a stale taxonomy, and the full-board cache
  serves any future country param without refetch.
  **Pagination completeness (review SEV-5):** mid-loop non-200 → raise
  (the B1 fail-safe contract — never partial-complete); a 200 with an
  EMPTY page-1 list retries once, then completes empty ONLY with a loud
  zero-rows warning (the watch's len(prior)>=10 zero-row anomaly guard is
  the second line of defense against mass false-gone).
- alibaba: `pageIndex` (pageSize capped 50), `totalCount` served. Per-host
  sweeps are ≤224 rows = ≤5 requests. `publishTime` (epoch ms) = EXACT date
  (convert on a UTC basis, pinned by test).
  `workLocations` = city-level English names ONLY → client-side country
  classification via a CURATED city→country map (the observed vocabulary
  across all 4 hosts is ~45 distinct cities; the map is data, pinned by
  tests).
  **reqIds (review SEV-1): RAW site codes, NOT namespaced** — the
  corroborate join requires exact card↔row reqId equality (corroborate.py
  drops cards whose job_req_id differs on every tier), so prefixing kills
  reqId-tier AND title/multiset-tier joins for cards carrying the code.
  Host provenance lives in `externalPath` = `/{hostkey}/{code}` (the detail
  dispatch accepts path-or-bare-id). **Cross-host duplicate codes → dedup
  keep-first + loud meta count** (same requisition shipped on two hosts
  must not ship two CSV rows).
  **Host failure semantics (review SEV-2):** careers-alibabacloud.com DNS
  is volatile (gTLD delegation absent at probe time; live earlier the same
  day) → per-host fail-soft: 3 attempts + backoff, then LOUD skip; ANY
  skipped host forces `meta["complete"]=False` so the watch NEVER computes
  gone-rows from a partial sweep (the false-gone + state-purge + re-alert
  cycle). meta carries hosts_skipped + per-host row counts.
- tripcom: `pager index/size` (strings), `total` served. `publishDate`
  (ISO date), `fromId` = reqId ("MJ003945"), HTML descriptions
  (requirements + duty).

Detail payloads synthesized from the cached rows (jobPostingInfo contract:
title, location, additionalLocations, jobDescription = description +
requirement, timeType, startDate=blank-or-exact, externalUrl, jobReqId,
postedOn label, country descriptor).

Missing-data honesty (per-field): bytedance serves NO dates (postedOn blank —
verified tolerated end-to-end by the review: blank startDate →
censored=true unknown class; INV-9 skips blank startDate; corroborated rows
gain daysOnMarket from LI card dates — the only date evidence they get);
alibaba/tripcom serve exact dates. bytedance recruit_type → timeType
(Regular→Full time, Intern→Internship, Third-party Associate→Contract —
the ashby dialect map value). tripcom `kind` → timeType only when non-blank.
**Unified unknown-location rule (review SEV-6):** known non-US → drop +
count; blank/UNKNOWN location → drop + LOUDLY count as unresolved_dropped
(greenhouse-consistent — never claim US without evidence; revisit if the
unresolved count ever becomes material).
questionnaires: none of the three expose apply-form questions publicly →
questionnaireId blank (same honest no-op class as greenhouse/ashby —
INV-8's existence assert passes with the header-only CSV).

## 3. Spec grammar + dispatch (REVISED after review)

**REGISTRY-DRIVEN grammar** (review SEV-8): `^custom:([a-z0-9_]+)$` matched
against `_ADAPTERS` membership — adding a company = one class + one registry
entry, NO regex edit. `is_site_spec` covers both `ats:` and `custom:` forms;
`parse_site` returns (kind, "") for custom specs. All existing call sites
(board_dump phases, watch, invariants) route through the same two functions.
Phase-change asterisk (verified by review): the site branch of
`phase_facet_tags` maps `row["departments"][0]` → jobFamilyGroup only —
custom adapters put the primary taxonomy name first (bytedance job_category
parent en_name; alibaba categories[0]; tripcom jobFamilyGroupName) and
secondary names after (currently unused, documented).

Watch/dump wiring: label `bytedance_us_fulltime` etc., board=`custom:bytedance`,
company display names ByteDance / Alibaba Group / Trip.com Group, country
"United States", time_type "Full time" (bytedance: filter Regular only at
list time — the board mixes intern postings; Intern kept OUT of the US CSV
but counted in meta; tripcom/alibaba serve whatever kind they serve — filter
only when the field exists and is non-blank).

## 4. LI corroboration registries (per-company, the S12 pattern)

- bytedance: li_variants ["ByteDance", "TikTok", "ByteDance Ltd"]; slices:
  the observed US cities (Seattle WA, San Jose CA, Los Angeles CA, New York
  NY, San Diego CA, Ashburn VA, Boston MA — from the live city census).
- alibaba: ["Alibaba Group", "Alibaba", "Alibaba Cloud", "Alibaba.com"];
  slices: Sunnyvale CA, Bellevue WA, Seattle WA, Santa Clara CA, Pasadena CA.
- tripcom: ["Trip.com Group", "Trip.com", "Trip.com Group Limited"]; slices:
  Santa Clara/San Jose CA + whatever the 10 rows show (derive from data).

h1b employers (DOL LCA filer names, exact-match OR lists — verify against
the DOL extract when it lands, adjust as needed):
bytedance ["BYTEDANCE LTD", "TIKTOK INC."], alibaba ["ALIBABA INC",
"ALIBABA GROUP HOLDING LIMITED"], tripcom ["TRIP.COM NETWORK TECHNOLOGY",
"TRIP.COM SINGAPORE PTE. LTD."].

## 5. Transport module (shared) (REVISED after review)

New small helpers in site_boards.py: `_imp_post_json(url, json, headers)` /
`_imp_get(url)` via a module-level curl_cffi chrome-impersonated session
(lazy import; **curl_cffi ADDED to ingest/pyproject.toml dependencies —
review SEV-3 verified it is NOT a package dep today**, only a lazy import
inside scripts/h1b_extract.py; plain-requests boards keep fetch_json).
Mockable seam: adapters call the module-level helpers so tests monkeypatch
them like fetch_json. Fail-soft per host for alibaba per §2
(complete=False + hosts_skipped). bytedance Akamai failures raise (B1).
Akamai manifests as 508/Access-Denied (not 4xx) — non-200 raises.

## 6. Tests (offline pins — REVISED after review, fixtures from live captures)

1. Grammar: registry-driven custom specs parse; `custom:unknown` and
   `ats:lever:x` rejected with the registered-kinds error.
2. ByteDance: row mapping (title/city country/reqId RAW code/timeType map),
   pagination termination (short page; **non-200 raises; empty-page-1
   retry-then-warn — SEV-5**), full-board country classification (US row
   kept, Singapore/HK dropped + counted), **null/blank city_info →
   dropped + unresolved_dropped counted (SEV-6)**, intern filtering when
   time_type set, multi-word country phrase-match negatives (United
   Kingdom ≠ united), detail synthesis from cache, **externalPath
   round-trip** (list row → path → detail hit; id and code forms).
3. Alibaba: multi-host sweep merge, **RAW reqId + externalPath
   host-provenance + cross-host duplicate dedup (SEV-1)**, city→country
   map classification (Sunnyvale US, Karachi PK), **unknown-city →
   dropped + counted (SEV-6)**, **publishTime epoch-ms → UTC label**,
   **DNS-fail fail-soft: meta hosts_skipped + complete=False + rows from
   surviving hosts (SEV-2)**.
4. Trip.com: country:["USA"] request shape, **Accept: application/json
   guard**, fromId reqId, HTML description passthrough, publishDate label,
   string pager fields, kind blank → timeType blank (no guessing).
5. **Corroborate join pin (SEV-7): raw-code reqId join — a card with
   job_req_id=GP-code serves the alibaba row on the reqId tier; foreign
   code still drops (guard intact).**
6. **Transport pins: alibaba XSRF cookie flow (GET collects cookie →
   POST carries _csrf); bytedance website-path header present.**
7. Watch-config integrity: 9 watches parse (6 existing + 3 custom),
   custom boards well-formed, labels unique; h1b employer lists include
   the new names.
8. Suite must stay ≥1,263 (target +~45).

**Deferred to next session (roster recount, review SEV-4):** the 4
config-only greenhouse boards (baidu/byd/neteasegames/shein — slugs
verified live: 28/20/28/17 jobs) need their own LI registries + dump
chains; shipping them watch-only without corroboration registries would
half-ship. Documented in PLAN.md as the natural S15 opener.

## 7. Deliverable chain per company (same as S13) (REVISED)

**Roster: 6 existing + 3 custom = 9 watches in ONE GHA run.** Workflow
timeout 45→75 min (review SEV-4: worst-case per-watch budgets alone can
exceed 45min; cache-served details keep the steady state fast).
Watch states SEEDED from the fresh dumps (S12/S13 --seed-from pattern —
avoids a ~3-day cold-start enrichment tail for bytedance's 443 rows).

list → details (cache-served, 0 extra requests) → tagfacets (native
taxonomy: bytedance job_category parents/children; alibaba categories[];
tripcom jobFamilyGroupName+buName) → questionnaires (honest no-op) →
corroborate (partitioned index, per-company geography) → titlesearch →
corroborate → finish → s10_invariants ALL GREEN. Then: 6→9+ watches in
ONE GHA run, h1b-extract employers += 3, mirror ship + dispatched live
validation, CSVs to ingest/data/workday/ (49-col v2.6 contract).

## 8. Risks / open questions

- R1 careers-alibabacloud.com DNS: fail-soft now; if it stays dead from
  GHA too, alibaba ships with 6 US rows honestly (host-skipped is loud).
- R2 ByteDance dates absent → postingAgeDays/censoring machinery behaves
  like the "unknown" class (already handled for blank postedOn since S10).
- R3 Trip.com board is tiny (10 US): invariants like "n≥K" must not fail
  for small boards — verify thresholds are count-relative (existing small
  boards jd=55/tencent=34 already passed, so 10 should be fine; check).
- R4 Akamai on jobs.bytedance.com could rate-limit the watch's pagination
  (7 requests/day) — acceptable; **Akamai manifests as 508/Access-Denied
  (not 4xx) — non-200 raises (SEV-5), B1 fail-safe catches it**.
- R6 (review): the len(prior)>=10 watch guard is tripcom's exact boundary
  (10 rows) — a genuine empty day fails LOUD by design; do not "fix".
- R5 en-portal semantics: TikTok/USDS US postings live on the same en
  portal (verified: Seattle/San Jose postings incl. TikTok-tagged roles);
  li_variants includes TikTok so corroborate matches them.
