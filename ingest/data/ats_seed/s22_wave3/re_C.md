# S22-RE-C: Custom-job-board API reverse-engineering (TTI, Hisense, WuXi Bio, Ant Intl)

Boards: TTI (ttigroup.com + dejobs mirror), Hisense USA, WuXi Biologics, Ant International.
All counts verified live on 2026-09-30 (sandbox date). Raw samples: `/tmp/s22/re_C_<board>.json`.

---

## 1. TTI / Techtronic Industries

### 1a. Primary surface — ttigroup.com (Drupal + Workday proxy API) — RECOMMENDED

**(1) Job list page(s)**
- `https://www.ttigroup.com/careers/career-opportunities/us` (US region; other regions = `/career-opportunities/<region>`, e.g. `hk`, `global`).
- The HTML itself contains NO job rows; jobs are injected client-side via JS (Drupal custom module). Page sets `window.careerRegion = "US"` (inline `<script>` in `section#section-jobs-wd`), and `window.data_source` is `"wd"` for the US/global region (Workday-backed) — `"tn"` (talent_network) and `"tti_career"` (JSON:API-backed, used for HK) also exist in the JS but are not the US path.
- Detail page: `https://www.ttigroup.com/careers/career-opportunities/job-details?data_source=wd&job_id=<workday_id>`.

**(2) Exact data endpoint (discovered in aggregated JS `/sites/default/files/js/js_*.js?scope=footer...`, fn `window.refreshPageByFilterValues`)**
```
GET https://www.ttigroup.com/ttigroup/api/v1/workday/jobPostings?region=US&page=1&ajax_wrapper=html
```
- Plain JSON, no auth, no cookies, no WAF. Browser UA recommended but not enforced. `_format=json` optional/no-op.
- Other endpoints of same family (all `GET`, JSON):
  - `ttigroup/api/v1/workday/job_site?region=US` → job-site taxonomy (Milwaukee / TTI Power Equipment / Team TTI Careers / TTI Floor Care Careers / TTI Group Headquarters Office) with workday_id per site.
  - `ttigroup/api/v1/workday/category?region=US` → categories (Milwaukee Tool, Power Equipment/Team TTI, Floor Care, HQ Group, ...).
  - `ttigroup/api/v1/workday/location?is_html=true&region=US[&location=<prefix>&job_site=<id>]` → location filter values (HTML fragment).
  - `ttigroup/api/v1/workday/company?region=US` → companies.
  - Single job: `ttigroup/api/v1/workday/jobPostings/<workday_id>?ajax_wrapper=html` → `{topHtml, bottomHtml, sideHtml, regionalCareerListUrlSlice}` (rendered HTML; **not needed** — see (5)).

**(3) Pagination mechanics**
- Query params: `page` (1-based; default 1), `take` (page size; default 24; **respects arbitrary values — `take=1000` returns all rows in one call**).
- Response echoes: `"page":1, "take":24, "total":645`.
- Other accepted filters (same as UI): `job_site=<workday_id>` (e.g. Milwaukee `e945e55d9e460105d7c453cd04d70000` → 318 rows), plus `location` / category filters mirroring the UI form.
- Total pages at default take=24: ceil(645/24) = 27 (matches UI "27 pages").

**(4) Row shape** — response: `{"total":645,"data":[...],"page":1,"take":24,"html":{"list":[...],"pager":"..."}}`
- Outer row: `title, workday_id, job_company, job_categories[], job_locations[], job_status, job_region, job_site, id, job_json_data`.
- Workday-shaped payload nested at **`row.job_json_data`**:
  - reqId → `job_json_data.id` (Workday 32-hex id, e.g. `05368db6fe2f100111079a9696af0000`). Workday requisition code (R-number) embedded in `job_json_data.url` (`..._R78170`).
  - title → `job_json_data.title`
  - location → `job_json_data.primaryLocation.descriptor` ("Lompoc, CA"); state → `primaryLocation.region.code`/`.descriptor`.
  - country → `job_json_data.primaryLocation.country.descriptor` ("United States of America") + `.alpha3Code` ("USA"). NOTE: 7/645 rows have `primaryLocation: null` — fall back to job_site/title (they are US jobs: Byhalia MS, Grenada MS, Nationwide).
  - timeType → `job_json_data.timeType.descriptor` ("Full time"/"Part time"); null on ~65 rows.
  - posted date → **`job_json_data.startDate`** (ISO date; distinct values 2022-06-17…2026-09-29, 153 distinct — the site's posting date; newest = yesterday).
  - detail URL → site detail: `https://www.ttigroup.com/careers/career-opportunities/job-details?data_source=wd&job_id=<workday_id>`; canonical Workday URL → `job_json_data.url` (e.g. `https://tti.wd1.myworkdayjobs.com/Milwaukee/job/Assembler-1st-shift-Byhalia_R69239`).
  - extras: `categories[].descriptor`, `jobSite.descriptor` (brand board), `jobType.descriptor` (Regular / Intern / Co-op), `company.descriptor`, `spotlightJob`.
- `html.list[]` = rendered card HTML (title/location in `div.description`, href to detail page with `data-job-id`) — only needed for HTML scraping; JSON rows supersede it.

**(5) Detail payload path (description)**
- Full HTML description ALREADY in listing row: **`row.job_json_data.jobDescription`** (HTML string). No per-job fetch needed.
- If desired: `GET /ttigroup/api/v1/workday/jobPostings/<workday_id>?ajax_wrapper=html` → `bottomHtml` contains `<h3>Job Details</h3>...<div class="prose">` description; `topHtml` has title card.

**(6) US count verified live (2026-09-30)**
- `region=US&take=1000` → `total:645`, 645 rows returned; 638 rows `primaryLocation.country == USA`; 7 rows null primaryLocation but US jobs (Milwaukee/TeamTTI US sites) → **US = 645 (all rows job_region "US")**. (Brief said ~662 — board fluctuates; 645 today.)
- jobSite split: Milwaukee 318, Team TTI Careers 222, TTI Power Equipment 94, TTI Floor Care 7, TTI Group HQ 4.
- timeType: Full time 577, Part time 3, null 65.

**(7) Sample rows** — `/tmp/s22/re_C_tti_all.json` (full 645-row pull, `?region=US&page=1&take=1000&ajax_wrapper=html`). Row 1:
```json
{"total":645,"page":1,"take":1000,"data":[{
 "title":"Field Sales and Marekting Representative - Lompoc, CA",
 "workday_id":"05368db6fe2f100111079a9696af0000","job_region":"US","job_status":"1",
 "job_site":"e945e55d9e460105d7c44c95bfbe0000",
 "job_json_data":{"id":"05368db6fe2f100111079a9696af0000",
   "title":"Field Sales and Marekting Representative - Lompoc, CA",
   "timeType":{"descriptor":"Full time"},
   "categories":[{"descriptor":"Power Equipment/Team TTI"}],
   "jobSite":{"descriptor":"Team TTI Careers"},
   "primaryLocation":{"descriptor":"Lompoc, CA","country":{"descriptor":"United States of America","alpha3Code":"USA"},"region":{"code":"CA","descriptor":"California"}},
   "jobType":{"descriptor":"Regular"},"startDate":"2026-09-29",
   "url":"https://tti.wd1.myworkdayjobs.com/TeamTTI-Jobs/job/Lompoc-CA/Field-Sales-and-Marekting-Representative---Lompoc--CA_R78170",
   "jobDescription":"<p><b>Job Description:</b></p>..."} }]}
```
Detail sample: `/tmp/s22/tti_detail.json`.

### 1b. Mirror board — ttigroupna.dejobs.org (DirectEmployers/jobsyn, Nuxt SPA) — SECONDARY

**(1) Pages:** `https://ttigroupna.dejobs.org/` (home) → `https://ttigroupna.dejobs.org/jobs/` (SPA listing); SEO paths: `/locations/<slug>/jobs/`, `/job-titles/<slug>/jobs/`; detail: `/<city-slug>/<title_slug>/<GUID>/job/` (e.g. `/brookfield-wi/sr-firmware-engineer/FBCDA.../job/`).
**(2) Data endpoint** (found via browser network capture; JS `_nuxt/C-wHvfJe.js`, store `jobSearch.fetchSearchResults`):
```
GET https://prod-search-api.jobsyn.org/api/v1/solr/search?page=1&location=usa
Headers: x-origin: ttigroupna.dejobs.org   (REQUIRED — board identity; also Accept: application/json)
```
- Host `prod-search-api.jobsyn.org` is NOT WAF-protected — plain curl works with only the `x-origin` header. Without it: 400 `{"errors":{"origin":"The origin is required."}}`; wrong origin: 403 `Mismatched origin.`
- The dejobs HTML itself IS behind AWS WAF (CloudFront `x-amzn-waf-action: challenge`, HTTP 202 to curl) — do NOT scrape its HTML; use the API directly.
- Board config: `https://ttigroupna.dejobs.org/public-config.json` (domain, buids [27257,37050,37282,37283,37284,59914]) — WAF-gated via curl; not needed for API use.
**(3) Pagination:** `page` (1-based). `page_size` fixed at **10** (no rows/page_size param honored). `pagination: {total:659, total_pages:66, page, has_more_pages}`. Params in JS: `q, location, title, company, sort, coords, r, page`; `sort=date` default. `location=usa` (= united-states) filters US.
**(4) Row shape (Solr doc, flat):**
- reqId → `reqid` ("R78095"); guid → `guid` (32-hex) / `id` ("seo.joblisting.<GUID>").
- title → `title_exact`; slug → `title_slug`.
- location → `city_exact`, `state_short_exact`, `location_exact`; country → `country_exact`/`country_ac`/`country_short_exact` ("USA").
- timeType → **NOT PRESENT**.
- posted date → `date_new` (ISO8601 UTC; sorted desc; today's rows carry today's date — looks like (re)index time; `date_added`/`date_updated`/`salted_date` also present).
- detail URL → `https://ttigroupna.dejobs.org/<city-slug>/<title_slug>/<GUID>/job/` (city slug derivable from `city_slab_exact` e.g. `brookfield/wisconsin/usa/jobs::Brookfield, WI`).
- company → `company_exact` ("Milwaukee Tool"); `buid`; `description` = FULL plain-text description.
**(5) Detail payload:** `row.description` already contains the full job description text. Detail page is Nuxt SSR (WAF-gated for curl) — not needed if using the API.
**(6) US count verified live:** `location=usa` → `pagination.total: 659` (sampled rows all `country_short_exact=USA`; verified pages 1,3,7,20,40,60). Unfiltered total: 778 (US 659, Mexico 61, Canada 48, Chile 5, others 1 each — from `filters.country`).
**(7) Sample row** saved: `/tmp/s22/re_C_tti_dejobs.json`.
```json
{"id":"seo.joblisting.FBCDA61C5B7C448DA8B48D9EC507F8B2","guid":"FBCDA...","reqid":"R78095",
 "title_exact":"Sr Firmware Engineer","title_slug":"sr-firmware-engineer",
 "city_exact":"Brookfield","state_short_exact":"WI","country_exact":"United States","country_short_exact":"USA",
 "date_new":"2026-09-30T03:47:58Z","company_exact":"Milwaukee Tool","buid":59914,
 "all_locations":["53008","Brookfield","Wisconsin","WI","Brookfield, Wisconsin","United States"],
 "description":"**Job Description:**...","GeoLocation":"43.062725, -88.12318"}
```

### 1c. Which surface to scrape
**Scrape ttigroup.com `workday/jobPostings` API as primary**: unauthenticated JSON, single-call full pull (`take=1000`), canonical Workday fields (timeType, country, region, startDate, full HTML description, Workday R-code + canonical wd1 URL for dedupe). dejobs mirror = good fallback: also unauthenticated via `prod-search-api.jobsyn.org` + `x-origin` header, full plain-text description in-row, but page_size locked to 10 (66 requests for US), no timeType, posted-date is index-date, and board HTML is WAF-gated (API host is not). Counts: 645 (ttigroup) vs 659 (dejobs) US rows.

---

## 2. Hisense USA — BambooHR JSON feed (NOT WordPress)

**(1) Job list page(s)**
- `https://www.hisense-usa.com/careers/job-postings` — a **Wix** site (`<meta name="generator" content="Wix.com Website Builder">`), NOT WordPress (no `/wp-json/`). The Wix page embeds the BambooHR "Job Openings" widget; the widget's data comes from an XHR to BambooHR (captured via browser network):
- No jobs exist in the Wix HTML itself (2.2 MB of Wix boilerplate) — always use the BambooHR endpoints below.

**(2) Exact data endpoint (listing)**
```
GET https://hisenseusacorporation.bamboohr.com/jobs/embed2.php?version=1.0.0&format=json
```
- Plain JSON, no auth/cookies/WAF, plain curl works (browser UA recommended). Company subdomain = `hisenseusacorporation`.
- Response: `{"version":"1.0.0","labels":{...},"departments":[...],"success":...}`.
- Detail endpoint (XHR made by BambooHR careers page; add `X-Requested-With: XMLHttpRequest` + `Accept: application/json`):
```
GET https://hisenseusacorporation.bamboohr.com/careers/<id>/detail        → JSON
GET https://hisenseusacorporation.bamboohr.com/careers/company-info       → JSON (company name/logo)
```

**(3) Pagination**
- NONE. The embed feed returns the complete list in one response (grouped by department, not pages). The Wix page renders all rows at once (no "load more"). Detail must be fetched per-job (`/careers/<id>/detail`), but description is only there.

**(4) Row shape (listing)** — iterate `departments[]` → `positions[]`:
- reqId → `position.id` (integer, e.g. 319; stable BambooHR job-opening id)
- title → `position.name`
- location → `position.location` ("Alpharetta, GA" / "Remote") — free text
- country → not in listing; derive from detail `atsLocation.country` ("United States") or infer from state suffix (all rows are US)
- timeType → not in listing; detail → `employmentStatusLabel` ("Full-Time")
- posted date → not in listing; detail → `datePosted` ("2026-07-17")
- detail URL → `position.url` = `https://hisenseusacorporation.bamboohr.com/careers/<id>`
- department → parent `department.label` (CE Sales, HA Sales, Supply Chain Management, ...)

**(5) Detail payload path**
- `GET /careers/<id>/detail` → `result.jobOpening`:
  - `jobOpeningName` (title), `jobOpeningStatus` ("Open"), `departmentLabel`, `jobCategoryId`
  - `employmentStatusLabel` ("Full-Time") → timeType; `employmentType` (null here)
  - `atsLocation` → `{country:"United States", countryId:"1", state:"Georgia", city:"Alpharetta"}`; `location` (structured, often null); `locationType` (1)
  - **`description`** → full HTML job description (e.g. 6,387 chars)
  - `datePosted` → "2026-07-17" (posted date)
  - `compensation` (null here), `minimumExperience`, `jobOpeningShareUrl` (canonical detail URL)
  - `result.formFields` → application form fields (not needed for scraping)

**(6) US count verified live (2026-09-30)**
- Listing feed: **19 positions total, 19 US** (Alpharetta GA ×11, Suwanee GA ×3, Bentonville AR ×1, Remote ×4). Matches expected ~19.

**(7) Sample rows** — listing saved `/tmp/s22/re_C_hisense.json`; detail sample `/tmp/s22/re_C_hisense_detail.json`.
```json
{"departments":[{"id":18612,"label":"CE Sales","positions":[
  {"id":304,"name":"Sr. Sales Manager - Walmart/Sam's Club",
   "url":"https://hisenseusacorporation.bamboohr.com/careers/304",
   "location":"Bentonville, AR"}]}]}
```
Detail sample (`/careers/319/detail`):
```json
{"result":{"jobOpening":{
 "jobOpeningShareUrl":"https://hisenseusacorporation.bamboohr.com/careers/319",
 "jobOpeningName":"Home Appliances PRO Channel Director","jobOpeningStatus":"Open",
 "departmentLabel":"HA Sales","employmentStatusLabel":"Full-Time",
 "atsLocation":{"country":"United States","countryId":"1","state":"Georgia","city":"Alpharetta"},
 "description":"<p><span style=\"font-weight: bold\">Overview:</span></p>...",
 "datePosted":"2026-07-17","locationType":1,"compensation":null}},
 "formFields":[...]}
```

**(8) Adapter note:** one listing call + N detail calls (N=19). If timeType/datePosted not needed, listing alone suffices. The Wix page is irrelevant for scraping. (The hisense-usa.com site also exposes Wix `_api/` endpoints but they do not carry job data — jobs come only from BambooHR.)

---

## 3. WuXi Biologics — WordPress static table + POST `/server.php` AJAX (SAP SuccessFactors mirror)

**(1) Job list page(s)**
- `https://www.wuxibiologics.com/join-us/` (WordPress, Divi-child theme). Server-renders the FIRST 20 postings as a static HTML table (of 77 total globally). Jobs are mirrored from SAP SuccessFactors (`career55.sapsf.eu/career?company=wuxibiolog`) into WP pages.
- Detail pages: `https://www.wuxibiologics.com/join-us-<title-slug>/` (one WP page per job, e.g. `/join-us-lead-technician-process-mechanic/`).
- Separate China board: `job.wuxibiologics.com.cn` (redirects to `/campus-recruitment/...` — Chinese-language, not relevant for US rows).

**(2) Exact data endpoint**
- It IS WordPress but NO job CPT in REST (`wp-json/wp/v2/types` has no jobs type; `wp/v2/jobs`, `wp/v2/pages/<id>` → 404 invalid post ID; row ids are not REST-exposed). The real feed is a custom PHP AJAX handler:
```
POST https://www.wuxibiologics.com/server.php
Content-Type: application/x-www-form-urlencoded

# filter / first batch (page 1):
catId=30&country[]=United%20States&type=join_us_filterBy_sapsf

# load more (next batches):
postId=<comma-joined ids already fetched>&type=join_us_more_sapsf&catId=30&country[]=United%20States&total=77
```
- NOTE: `country` MUST be sent as an array key (`country[]`) — jQuery serializes the JS array that way; plain `country=United States` returns `{"postCount":0,"result":[]}`. No auth/WAF; works with plain curl.
- Omit `country[]` entirely to get ALL countries.
- Response (filterBy): `{"postCount":<total matching>, "result":[{id,title,href,date,country},...]}` (first 50 rows).
- Response (more): `{"postCount":<remaining>, "total":"77", "result":[...]}` (next 20 rows).

**(3) Pagination mechanics**
- Initial server-rendered table = 20 rows; `label#total` holds global total (77); `input#postArray` holds comma-joined ids already displayed (page 1 = 45344..45363).
- `join_us_filterBy_sapsf` returns the FIRST 50 matching rows + `postCount` (total match count).
- `join_us_more_sapsf` returns the NEXT 20 rows each call; pass `postId` = all ids fetched so far (cumulative, comma-joined), `total` = global total. Repeat until `result: []` / rows collected == postCount.
- US pull = filterBy (50) + 1 more call (11) = 61 rows.

**(4) Row shape** (flat, from `result[]`):
- reqId → `id` (WP post id, e.g. 45344; stable, used for pagination too)
- title → `title` ("Lead Technician, Process Mechanic")
- location/country → `country` ("United States" | "Ireland" | "Germany" | "United Kingdom"; exact string, matches filter checkbox values). NOTE: city-level location is NOT in the row — it's inside the detail description ("Location - Cranbury NJ").
- timeType → NOT PRESENT anywhere (neither row nor detail page).
- posted date → `date` ("2026-05-08", ISO; detail page shows "Posted 2026-05-08")
- detail URL → `href` (full URL `https://www.wuxibiologics.com/join-us-<slug>/`)
- rows ordered by date DESC (2026-05-08 → 2025-06-23).

**(5) Detail payload path (description)**
- Detail page HTML (server-rendered, ~580 KB WP page): right-hand column:
  - `div.applyD_right > div.title` → job title
  - `div.applyD_right > div.location > div.applyD_right_add` → `<img .../wp-content/uploads/US.svg>United States &nbsp; Posted 2026-05-08` (country + posted date)
  - **`div.applyD_right > div.text`** → FULL job description HTML (`<p><strong>Job Title</strong> - ...`, `Location - <city>`, `Job Summary`, `Job Responsibilities` as `<ul><li>`...). No per-job JSON API; parse this div.
  - apply link on page: `https://career55.sapsf.eu/career?company=wuxibiolog&career_ns=job_listing_summary` (generic SF careers URL, not per-job).
- Static-table fallback selectors (if scraping HTML instead of the API): `div.jobOpportunity_table div.table-responsive table.table` → `thead tr` (Date / Job Description / Location); rows = `tbody#news_list > tr` with `td.td_width01` (date), `td.td_width02 > a` (title + href), `td.td_width03 > div.title` (country text) + `div.icon img` (country flag svg). Initial render = 20 rows only.

**(6) US count verified live (2026-09-30)**
- API pull (filterBy + more, `country[]=United States`): **61 US rows** (postCount=61). Global total 77 = US 61 + Germany 12 + Ireland 3 + UK 1. NOTE: the brief's "~19 US rows" matches only the initial 20-row static table (19 US + 1 Ireland shown on first render); the full US board is 61.
- Rows saved: `/tmp/s22/re_C_wuxi.json` (61 US rows).

**(7) Sample rows**
```json
{"id":45344,"title":"Lead Technician, Process Mechanic",
 "href":"https://www.wuxibiologics.com/join-us-lead-technician-process-mechanic/",
 "date":"2026-05-08","country":"United States"}
{"id":45484,"title":"Executive Director, Analytical Science and Characterization Center of Excellence",
 "href":"https://www.wuxibiologics.com/join-us-executive-director-analytical-science-and-characterization-center-of-excellence/",
 "date":"2026-02-20","country":"United States"}
```
Detail sample (`/join-us-lead-technician-process-mechanic/`): `div.applyD_right > div.title` = "Lead Technician, Process Mechanic"; `div.applyD_right_add` = "United States, Posted 2026-05-08"; `div.text` = description starting "Job Title - Lead Technician, Process Mechanic / Location - Cranbury NJ (with regional support) / Job Summary ...".

---

## 4. Ant International — Next.js SPA → `hrcareersweb.antgroup.com` JSON API (SAP-like careers backend)

**(1) Job list page(s)**
- `https://www.ant-intl.com/en/job-search/` (Next.js App Router SPA; no `__NEXT_DATA__`, HTML has no job rows). Client component `JobList` (chunk `/_next/static/chunks/9180-fcc7182004cd81e9.js`, webpack module 18113) calls the careers API on mount.
- Campus board: `https://www.ant-intl.com/en/job-search-campus/` (separate mode, same pattern).
- Detail page for humans: `https://talent.antgroup.com/off-campus-position?positionId=<id>&from=intl` (Ant Group talent portal).

**(2) Exact data endpoint (listing)** — found in JS: `let n="https://hrcareersweb.antgroup.com"` + `fetch(`${n}/api/social/position/search`, {method:"POST", body: JSON.stringify({language:"en", channel:"group_official_site", ...e, bgCode:"M7892"})})`:
```
POST https://hrcareersweb.antgroup.com/api/social/position/search
Content-Type: application/json
Origin: https://www.ant-intl.com        (CORS: Origin/Referer of ant-intl.com or talent.antgroup.com both accepted)

{"language":"en","channel":"group_official_site","categories":"","key":"",
 "regions":"SUNNYVALE,USANYNYQEE,USADCDCWAS","subCategories":"",
 "pageIndex":1,"pageSize":49,"bgCode":"M7892"}
```
- Params: `key` (keyword search), `categories` (category codes from category API), `subCategories`, `regions` (**comma-joined CITY codes** — see below), `pageIndex` (1-based), `pageSize` (max **49**; 50 → `invalid_parameter`), `bgCode` (**"M7892" = Ant International**; hardcoded in the JS — omitting it or wrong values changes the tenant), `language`/`channel` required.
- Category taxonomy: `POST https://hrcareersweb.antgroup.com/api/social/category/list` body `{"language":"en","channel":"group_official_site"}` → Technology(130)/Algorithms/Blockchain/Products/Operations/Research trees with codes.
- **US region filter**: the region→city code map is HARDCODED in the JS chunk (not served by an API). US = `USA` with cities Sunnyvale=`SUNNYVALE`, New York=`USANYNYQEE`, Washington D.C.=`USADCDCWAS`. Passing the country code `USA` alone returns 0 — you must pass the comma-joined city codes: `regions=SUNNYVALE,USANYNYQEE,USADCDCWAS`.
- Response: `{"success":true,"content":[rows],"totalCount":161,"pageSize":49,"currentPage":1,"traceId":...}` (failures: `{"success":false,"errorCode":"invalid_parameter"/"param_can_not_be_null",...}`).

**(3) Pagination mechanics**
- `pageIndex` (1-based) + `pageSize` (server-capped at 49). `totalCount` = 161 global for bgCode M7892. Full pull = 4 pages × 49. UI shows numbered pager (17 pages × 10).
- No cursor; page/offset style.

**(4) Row shape** (`content[]`, flat):
- reqId → `id` (long int, e.g. 260826011693255); requisition code → `code` ("GP260826011693255").
- title → `name` ("Ant International-SRE Engineer (US)-Americas & EMEA Tech")
- location → `workLocations` (array of city names: "Sunnyvale", "New York"); country NOT a field — derive via city (US cities list hardcoded in JS: Sunnyvale/New York/Washington D.C.). Also `regionEnNameMap` (empty), `interviewLocations`.
- timeType → NOT PRESENT (no field; job type only in title text).
- posted date → `publishTime` (ISO8601 with offset, e.g. "2026-09-10T22:56:53.000+00:00").
- detail URL → `positionUrl` is always "" (ignore); canonical human detail: `https://talent.antgroup.com/off-campus-position?positionId=<id>&from=intl`.
- extras: `categories[]` ("Technology - Security"), `department`, `departmentPath`, `tags` (["NEW"]), `experience`, `degree`, `bucket`, `positionTagList`, `featureTagList`.

**(5) Detail payload path (description)**
- The listing row ALREADY contains full text: **`row.description`** (HTML-ish plain text, ~1.3 KB) and **`row.requirement`** (qualifications + US salary range text). No per-job fetch needed.
- Optional detail API (same object shape as row): 
```
POST https://hrcareersweb.antgroup.com/api/social/position/detail
{"channel":"group_official_site","language":"en","id":260708010806748}
```
(`?ctoken=` query param used by the portal is NOT required for reads; verified via curl. Body requires channel+language+id — `{"id":...}` alone returns `content:null`.)

**(6) US count verified live (2026-09-30)**
- `regions=SUNNYVALE,USANYNYQEE,USADCDCWAS` → `totalCount: 7` (7 rows: 6 Sunnyvale-only + 1 Sunnyvale+New York).
- Cross-check: full unfiltered pull (161 rows, 4 pages × 49) → 7 rows with US workLocations (Sunnyvale ×7, New York ×1); all other locations: Shanghai, Kuala Lumpur, Singapore, Hong Kong, Hangzhou, Tokyo, etc. (22 distinct cities). Brief said ~8 — board fluctuates.
- Saved: `/tmp/s22/re_C_ant_us.json` (7 US rows), `/tmp/s22/ant_all.json` (161 global rows).

**(7) Sample row**
```json
{"bucket":"DEFAULT","positionUrl":"","id":260826011693255,
 "name":"Ant International-SRE Engineer (US)-Americas & EMEA Tech",
 "categories":["Technology - Security"],
 "publishTime":"2026-09-10T22:56:53.000+00:00",
 "workLocations":["Sunnyvale"],
 "code":"GP260826011693255",
 "description":"...full job description text...",
 "requirement":"Qualifications: ... The US base salary range for this full-time position is $100,000-150,000...",
 "department":"...","tags":["NEW"],"experience":null,"degree":null}
```

**(8) Adapter notes:** 1 call for US (regions=US city codes, pageSize 49); full board = 4 calls. bgCode M7892 is the Ant International tenant (Ant Group main site uses different bgCode). language "en" + channel "group_official_site" are required constants. No auth tokens needed.

---

# Cross-board summary

| Board | Surface | Endpoint | US rows (live 2026-09-30) | Notes |
|---|---|---|---|---|
| TTI (primary) | ttigroup.com Workday proxy | `GET https://www.ttigroup.com/ttigroup/api/v1/workday/jobPostings?region=US&page=1&take=1000&ajax_wrapper=html` | **645** | full data incl. HTML description in-row; single call |
| TTI (mirror) | dejobs/jobsyn | `GET https://prod-search-api.jobsyn.org/api/v1/solr/search?page=1&location=usa` + header `x-origin: ttigroupna.dejobs.org` | **659** | page_size locked 10; board HTML WAF-gated (API open) |
| Hisense USA | BambooHR | `GET https://hisenseusacorporation.bamboohr.com/jobs/embed2.php?version=1.0.0&format=json` (+ `/careers/<id>/detail`) | **19** (all US) | Wix page is irrelevant; listing+19 detail calls |
| WuXi Biologics | WP + custom AJAX | `POST https://www.wuxibiologics.com/server.php` (`type=join_us_filterBy_sapsf` / `join_us_more_sapsf`, `country[]=United States`) | **61** (initial page shows 19 US) | pagination via cumulative `postId`; desc in detail page `div.applyD_right > div.text` |
| Ant International | hrcareersweb API | `POST https://hrcareersweb.antgroup.com/api/social/position/search` (bgCode M7892, `regions=SUNNYVALE,USANYNYQEE,USADCDCWAS`) | **7** | desc+requirement in-row; pageSize max 49 |
