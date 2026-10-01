# S23-RE-E: Custom-job-board reverse-engineering (Black Sesame, Ecovacs US, Accutar, HitGen, Insilico Medicine)

Boards: 5 own-domain boards (Chinese-orig cos) with confirmed US jobs.
Raw samples: `/tmp/s23/re_E_<board>.json` (+ raw HTML dumps `/tmp/s23/re_E_<board>.html`).
All fetches done live in-session with plain `curl -A "Mozilla/5.0 … Chrome/126"` (no WAF trouble anywhere in this batch; Black Sesame sits behind Tencent "Lego Server" WAF but still serves plain curl).

---
## 1. Black Sesame Technologies — PbootCMS static page (no feed; SSR cards)

**(1) Job-listing page**
- `https://www.blacksesame.com/en/join-us/` — single SSR page, ~104 KB. CMS fingerprint: response header `X-Powered-By: PbootCMS` (also `Server: Lego Server`, `X-WAF-UUID` — Tencent CDN/WAF; plain curl works fine).
- The zh mirror `https://www.blacksesame.com/zh/join-us` (200) contains **NO job cards at all** (empty job area) — the EN page is the whole board.
- The brief's Workable board `black-sesame-technologies-1` is empty — ignore it.

**(2) Exact data endpoint**
- NONE. `/wp-json/` → 301 to `/zh/wp-json/` (pure language redirect, then 404 — not WordPress). `/api/` → 301 redirect (PbootCMS `/api/` is contact-form submission only, no job data). No JSON anywhere on the page (no `__NEXT_DATA__`, no ld+json JobPosting). Scrape the listing HTML.

**(3) Pagination**
- NONE. All 6 cards on one page.

**(4) Row shape — HTML parse structure (selectors)**
- Container: `div.jobs > div.jobs-container > div.job-list` → per-job `div.job-card[data-department="…"]`.
- Per card:
  - **title** → `h3.job-title` text
  - **department** → `div.job-department` text (== `data-department` attr; "Design Engineer" ×5, "Legal" ×1)
  - **salary** → `span.job-salary` (present but EMPTY on 5 of 6 cards; only row 1 has `"$200,000-$230,000 per annum"`)
  - **location** → `div.job-location` text after stripping the `<i class="fa fa-map-marker">` icon → `"San Jose, US"` on all 6
  - **description snippet** → `div.job-description` (partial text; full JD lives on detail page)
  - **date** → `div.job-type` text — **class is misnamed; it holds a date** (`2026-08-30` / `2025-03-13`), rendered on detail page next to `fa-clock-o`
  - **detail URL** → `a.apply-btn` `href="/en/join-us/<id>.html"` (PbootCMS numeric ids)
- Mapped fields: country = "United States" for all (location string ends ", US"); reqId = the numeric id from the detail URL (1005/875/874/873/872/456); timeType = NONE.
- Rows saved to `/tmp/s23/re_E_blacksesame.json`.

**(5) Detail description path**
- `GET https://www.blacksesame.com/en/join-us/<id>.html` (200, ~97 KB, verified for 1005):
  - title → `h1.job-detail-title`
  - meta → `div.job-detail-meta div.job-detail-meta-item` (order: map-marker = location, clock-o = date, `#job-salary` = salary)
  - body → `div.job-detail-content div.job-main-content div.job-section` — each has `h2.job-section-title` ("Job Qualification", "Job Description", commented-out "Required Skills"/"Benefits") + `div.job-description` (full HTML). "About the Team" section exists but is commented out.
  - JDs end with a postal apply line, e.g. `Black Sesame Technologies Inc., 2290 N 1st St #100, San Jose, CA 95131, Attn: HR` — good US evidence / address harvest.

**(6) US count verified live (2026-10-01)**
- **6 rows, 6 US** — all `San Jose, US`: Sr. Engineer, Embedded Software (1005, posted 2026-08-30, $200k–$230k); Software Engineer, AI Framework (875); Software Engineer, AI Compiler (874); ML Accelerator Compiler Developer (873); AI Perception Algorithm Engineer/Scientist (872); IP Counsel (456, dept Legal). Matches brief (brief's "Design Eng" is the department tag, not a title).

**(7) Sample rows** (from `/tmp/s23/re_E_blacksesame.json`)
```json
{"title": "Sr. Engineer, Embedded Software", "department": "Design Engineer", "salary": "$200,000-$230,000 per annum", "location": "San Jose, US", "date_field": "2026-08-30", "detail": "https://www.blacksesame.com/en/join-us/1005.html"}
{"title": "AI Perception Algorithm Engineer/Scientist", "department": "Design Engineer", "salary": "", "location": "San Jose, US", "date_field": "2025-03-13", "detail": "https://www.blacksesame.com/en/join-us/872.html"}
{"title": "IP Counsel", "department": "Legal", "salary": "", "location": "San Jose, US", "date_field": "2025-03-13", "detail": "https://www.blacksesame.com/en/join-us/456.html"}
```

**(8) Quirks / adapter notes**
- PbootCMS id sequence is unordered (456 sits between 872 and 1005) — never sort/derive by id; scrape link↔card pairs in DOM order.
- `div.job-type` holds the DATE (misnamed) — do not parse it as employment type.
- `span.job-salary` renders empty (not absent) on 5/6 rows — treat empty string as null.
- The "Filter by Position" buttons + `#job-count` are JS cosmetics; `job-count` shows "0" pre-hydration — ignore.
- UTF-8: titles/descriptions contain en-dashes and `&#39;` entities; decode entities.
- Dedupe key: detail URL (stable per job edit-in-place); no posted-timestamp per row other than the date field.

---
## 2. Ecovacs Robotics US — Astro+Next.js hybrid with a clean Next data JSON endpoint

**(1) Job-listing page**
- `https://www.ecovacs.com/us/careers/job-list` — Astro v4 static shell hosting a **Next.js SSR island** (`<div id="__next">`, `__N_SSP:true`, `gssp:true`, buildId **9.9.15**, assetPrefix `/v4`). Rows are fully server-rendered (visible in HTML) AND mirrored in an embedded `<script id="__NEXT_DATA__">` JSON blob.
- 60+ locale variants of the same app exist (`/de/`, `/jp/`, …); the US board is the `/us` prefix only.

**(2) Exact data endpoints**
```
GET https://www.ecovacs.com/_next/data/9.9.15/us/careers/job-list.json          → listing JSON
GET https://www.ecovacs.com/_next/data/9.9.15/us/careers/job-detail.json?id=<id> → detail JSON
```
- Both verified live (200). Payload path: `props.pageProps.pageData`.
  - listing: `.list[]`, `.page_data`, `.ApiSuccessMsg`
  - detail: `.contact[]`, `.career{…}`, `.breadcrumb`
- **buildId drift**: `9.9.15` is a build id that changes on redeploy. Adapter should GET the HTML page first and extract `buildId` from its `__NEXT_DATA__` JSON (`…| jq -r .buildId`) before constructing `/_next/data/<buildId>/us/...` URLs. Zero-dependency fallback: the same JSON is embedded in the listing page HTML (`<script id="__NEXT_DATA__" type="application/json">` — note extra attrs; regex `<script id="__NEXT_DATA__"[^>]*>(.*?)</script>`), so parsing the HTML alone is a valid contract too.
- Plain curl works; no auth/cookies required.

**(3) Pagination**
- Server-side pagination is wired: `page_data = {current_page, total_count, total_page, list_count}` where `list_count` = page size (100). Query `?page=N` is honored by the endpoint (`?page=2` → `list:[]`, `current_page:2`, `total_page:1`). Iterate `page=1..total_page` (or until `list` empty).

**(4) Row shape (JSON)**
- Listing row: `{"id": 13, "title": " Warehouse Supervisor - 3PL Operations", "type": "Full Time", "work_place": "Los Angeles, CA"}`
  - **title** has a LEADING SPACE — strip.
  - country: derive from `work_place` ("Los Angeles, CA" → US; the /us board is all-US but keep the location parser).
  - reqId = `id` (13); timeType = `type`; posted date = NONE.
- HTML fallback selectors (SSR): `div.jobs-main > div.job-item` → `a[href*="job-detail?id="]` → `div.text-large` (title), following `div > span:nth(1)` ("Full Time"), `span.border-left` ("Los Angeles, CA").
- Listing payload saved to `/tmp/s23/re_E_ecovacs.json`; detail to `/tmp/s23/re_E_ecovacs_detail.json`.

**(5) Detail description path**
- `pageData.career` object (all HTML strings):
  - `title`, `"type;"` (**key literally contains a trailing semicolon** — backend typo, verified), `work_place`
  - `description` (intro HTML), `responsibility` (duties HTML), `mini_qualification` (quals HTML), `preferred_qualification` (often `""`)
  - apply email → `pageData.contact[]` = `["na.career@ecovacs.com"]`
- Strip tags (payload has `\r\n\t` whitespace + `&nbsp;`).

**(6) US count verified live (2026-10-01)**
- **1 row, 1 US**: id 13, "Warehouse Supervisor - 3PL Operations", Full Time, "Los Angeles, CA" (`total_count: 1`). Matches brief exactly.

**(7) Sample rows** (from `/tmp/s23/re_E_ecovacs.json`)
```json
{"rows": [{"id": 13, "title": " Warehouse Supervisor - 3PL Operations", "type": "Full Time", "work_place": "Los Angeles, CA"}],
 "page_data": {"current_page": 1, "total_count": 1, "total_page": 1, "list_count": 100},
 "endpoint": "https://www.ecovacs.com/_next/data/9.9.15/us/careers/job-list.json"}
```
Detail (abridged): `{"contact":["na.career@ecovacs.com"],"career":{"title":" Warehouse Supervisor - 3PL Operations","type;":"Full Time","work_place":"Los Angeles, CA","description":"<p>Ecovacs Robotics is looking for a talented Warehouse Supervisor …","responsibility":"<ul><li>Operational Leadership in 3PL management …","mini_qualification":"<ul><li>Deep knowledge in Logistics …","preferred_qualification":""}}`

**(8) Quirks / adapter notes**
- `"type;"` semicolon key in detail JSON (but plain `"type"` in listing rows) — don't reuse the listing field map for detail.
- Leading space in `title` on both list and detail.
- Astro islands hydrate on top (header/footer/toast) — irrelevant to rows; ignore `astro-island` noise when HTML-parsing.
- `/_next/data/` route ignores wrong buildIds (404/redirect) → always re-read buildId from HTML each run.
- Detail page HTML URL is `https://www.ecovacs.com/us/careers/job-detail?id=13` (human link; also SSR with same NEXT_DATA).
- No posted dates anywhere; dedupe by `id`.

---
## 3. Accutar Biotech — WordPress page with inline Bootstrap accordion (no REST job feed)

**(1) Job-listing page**
- `https://www.accutarbio.com/careers/` — WordPress (custom theme `accutar`), server-rendered. Jobs are **page content (WP blocks), not a CPT**: `/wp-json/wp/v2/types` returns only post/page/attachment/nav/menu-item/block/template — **no `jobpost` CPT, so the Ascentage-style WPJobBoard REST trick does NOT apply here**. `/wp-json/wp/v2/pages/1132` is the `career-detail` page template (no job data in REST).
- The page renders TWO parallel structures: a desktop accordion with full descriptions, and a mobile accordion with id+link list.

**(2) Exact data endpoint**
- NONE for the job list (HTML scrape). Per-job detail pages exist as a server-rendered WP template:
```
GET https://www.accutarbio.com/career-detail/?id=<id>     (301 from /career-detail?id=<id> — follow redirects)
```
- Job ids are visible in the mobile list: 1372, 1301, 1292, 1290, 1285 (bio) / 1419, 1364, 1336, 1334, 1325 (com).

**(3) Pagination**
- NONE. 10 rows, one page.

**(4) Row shape — HTML parse structure (selectors)**
- **Desktop (full text)**: `div.c-careers div.row.pc div.right div.accordion.positions#accordion` → per job `div.card.position.(bio|com)`:
  - title → `.card-header button.position-title` text (class reused for a `<button>`)
  - description → sibling `div.collapse[id^=collapse-] div.card-body.position-requirement` inner HTML (contains `<!-- wp:paragraph -->` block comments — strip them)
  - category → `.card.position.bio|.com` (Department of Biology / Department of Computing; desktop left nav `div.category[data-category]`)
  - collapse id pattern `#collapse-bio-<n>` / `#collapse-com-<n>` == mobile `data-index`
- **Mobile (ids + links)**: `div.row.mobile div.accordion.positions-mobile#accordion2` → `div.card-body.positions div.position-title[data-category][data-index] > a[href="https://www.accutarbio.com/career-detail?id=<id>"]`
- Mapped fields: **location is NOT a field** — derive from description text regexes:
  - `Location: Accutar Biotechnology, Cranbury, New Jersey.`
  - `Send resume to HR, Accutar Biotechnology Inc. 8 Clarke Dr. Ste. 4, Cranbury, NJ 08512`
  - `… 800 West El Camino Real, Suite 180, Mountain View, CA 94040`
  - `… 11100 NE 8th St, Suite #800, Bellevue, WA 98004`
  - apply email: `biocareer@accutarbio.com` (in description); reqId = career-detail id; posted date = NONE.
- Rows (with derived locations) saved to `/tmp/s23/re_E_accutar.json`.

**(5) Detail description path**
- `career-detail/?id=<id>` → `div.container.c-careers-detail div.row.mobile div.position-detail-wrapper div.position-detail`:
  - title → `div.detail-title`
  - description → `div.detail-content` (same WP-block HTML as the accordion body; e.g. id=1372 verified, 200 after redirect).
- Listing already carries the full text — detail fetch is optional (only needed if you trust ids more than DOM order).

**(6) US count verified live (2026-10-01)**
- **10 rows, 10 US**: Cranbury NJ ×4 (Medical Scientist (Drug Discovery Project Manager); Research Associate, Cancer Biology; Research Scientist/Group Leader, Cancer Biology; Group Leader, Immuno-Oncology), Mountain View CA ×4 (Senior AI Chemist; Senior Biomedical R&D Engineer; Biomedical Engineer; Software Engineer (Drug Discovery) (multiple openings)), Bellevue WA ×2 (Senior Software Engineer; Research Software Engineer — body states "Salary: $200,000 / year" and "Work Location: Bellevue, WA").
- NOTE: brief said "all Cranbury NJ" — live page has THREE sites (Cranbury/Mountain View/Bellevue); still 10/10 US.

**(7) Sample rows** (from `/tmp/s23/re_E_accutar.json`)
```json
{"category": "bio", "title": "Medical Scientist (Drug Discovery Project Manager)", "location": "Cranbury, NJ", "detail_id": 1372}
{"category": "bio", "title": "Senior AI Chemist", "location": "Mountain View, CA", "detail_id": 1301}
{"category": "com", "title": "Research Software Engineer", "location": "Bellevue, WA", "detail_id": 1364}
{"category": "com", "title": "Software Engineer (Drug Discovery) (multiple openings)", "location": "Mountain View, CA", "detail_id": 1325}
```

**(8) Quirks / adapter notes**
- Parse strategy: take ids/links from the mobile list, full descriptions from the desktop accordion; the two are index-aligned (`data-category` + `data-index` ↔ `#collapse-<category>-<index>`). Don't parse both lists as rows (would double-count).
- `career-detail` without trailing slash 301s — use `-L` or the slashed form directly.
- Descriptions contain `&nbsp;`, nested `<strong>`, PERM-style requirement prose — location strings vary per job; maintain a 3-address regex (Cranbury/Mountain View/Bellevue) and fail loud on unknown.
- jQuery category filter is cosmetic (`.category` click toggles `.position.bio/.com` visibility); both categories are in the DOM.
- No dates, no reqIds beyond the career-detail id; dedupe by (title, detail_id).
- Salary appears only inside description prose ("Salary: $200,000 / year") — regex if wanted.

---
## 4. HitGen — ThinkPHP SSR list + per-job popup pages (filter endpoint reveals US rows)

**(1) Job-listing page**
- `https://www.hitgen.com/en/careers-position.html` — ThinkPHP 5 (`X-Powered-By: ThinkPHP`, `PHPSESSID`, nginx/1.18.0). Fully server-rendered list. CN mirror: `/cn/careers-position.html`.
- The listing carries a GET search form (`#search-positions`, action `careers-position.html`): `key` (free text), `department`, `category`, `location` (select options: `USA`, `US`, `成都天府国际生物城`).

**(2) Exact data endpoint**
- No JSON API. Two useful HTML endpoints:
```
GET https://www.hitgen.com/en/careers-position.html                 → all rows
GET https://www.hitgen.com/en/careers-position.html?location=US     → US rows ONLY (verified)
GET https://www.hitgen.com/en/careers-position-popup-<id>.html      → per-job detail popup (from site JS: layer.open content "/en/careers-position-popup-"+id+".html")
```
- Other verified filters: `?department=BD`, `?key=Director`, `?location=成都天府国际生物城` (URL-encoded).

**(3) Pagination**
- A `div.position-page` pager exists but is empty at 3 rows (server would render page links if list grew). Assume single page until proven otherwise; re-check `.position-page ul li` count if rows > ~10.

**(4) Row shape — HTML parse structure (selectors)**
- Container: `div.position-results div.results-list ul` → per job `li[data-id="N"]`:
  - **title** → `div.results-txt` text
  - **detail** → construct `/en/careers-position-popup-<data-id>.html` (NOT in href — the li click opens a layui `layer.open` iframe; links are absent from the DOM)
  - "Submit Resume" button (`div.results-btn`) is decorative.
- Mapped fields: category NONE (department select only has BD); reqId = `data-id`; posted date NONE; US flag = row present under `?location=US` (or title lacks `（Base in China）`).

**(5) Detail description path**
- `GET /en/careers-position-popup-<id>.html` (200, ~3.5–10 KB, plain curl):
  - title → `body div.popup-cnt div.popup div.popup-title`
  - description → `div.popup-dt` (HTML: inline-styled `<p><span>` blocks; strip tags)
- Verified popups: 32 (Director of Business Development — full JD, no location line), 31, 33 (both: "location of this position is: Chengdu City, Sichuan Province, China").

**(6) US count verified live (2026-10-01)**
- **3 rows total, 1 US**: `?location=US` returns exactly `data-id=32` "Director of Business Development" (also = `?department=BD`). The other two rows are `Vice President of CMC （Base in China）` (33) and `Director of Medicinal Chemistry（Base in China）` (31) — popups confirm Chengdu, China. Matches brief.

**(7) Sample rows** (from `/tmp/s23/re_E_hitgen.json`)
```json
{"id": 33, "title": "Vice President of CMC （Base in China）", "detail_popup": "https://www.hitgen.com/en/careers-position-popup-33.html", "us": false}
{"id": 31, "title": "Director of Medicinal Chemistry（Base in China）", "detail_popup": "https://www.hitgen.com/en/careers-position-popup-31.html", "us": false}
{"id": 32, "title": "Director of Business Development", "detail_popup": "https://www.hitgen.com/en/careers-position-popup-32.html", "us": true}
```

**(8) Quirks / adapter notes**
- **DB location tagging is dirty**: option `USA` maps to id 31 (Director of Medicinal Chemistry — a China job!) while `US` maps to id 32. NEVER use `?location=USA`; use `?location=US` and cross-check titles for `（Base in China）` (full-width parens — match on `Base in China` substring, not ASCII parens).
- Popup URLs must be built from `data-id` (never present as hrefs).
- PHPSESSID is set but scraping works without cookies.
- Popup 32 (the US row) states no location in its body — the US evidence is the `?location=US` filter result (+ department BD). Store source_evidence = "hitgen location=US filter".
- Full-width punctuation in titles (`（）`) — keep UTF-8.

---
## 5. Insilico Medicine — Tilda static accordion (no API; JS-walled = NO, fully SSR)

**(1) Job-listing page**
- `https://insilico.com/careers` — **Tilda** site (`t-rec` records, `tildacdn`, `t849` accordion block, record `rec620788308`). Whole board is server-rendered static HTML (~101 KB); job rows and full JDs are in the initial HTML — no client XHR for jobs.
- JSON API check: NONE — no `__NEXT_DATA__`/Next/Nuxt/Gatsby, no `/api/` (only Google Fonts + Tilda runtime). This is a static-parse board, not JS-walled.

**(2) Exact data endpoint**
- NONE. Scrape `/careers` HTML.

**(3) Pagination**
- NONE. All 3 roles on one page.

**(4) Row shape — HTML parse structure (selectors)**
- Container: `div#rec620788308` (block t849 accordion) → per job `div.t-item`:
  - **title** → `div.t849__header button` inner text (e.g. "ML Engineer (UAE)", "ML Researcher (UAE)", "Business Development Manager(s) — Conferences & AI Platform Technologies") — strip inner `div.t849__trigger-button` icon markup
  - **description** → sibling `div.t849__content div.t849__textwrapper div.t849__text[field="li_descr__<hash>"]` inner HTML (full JD, 2.5k–6.7k chars)
  - **place of work** → regex `Place of work\s*(…)` over stripped description ("Abu Dhabi, United Arab Emirates" / "Fully remote, open globally (Preferred: Japan, South Korea, Europe)")
  - apply email in body: `career@insilico.com`; BD-role contact: `aisyah@insilicomedicine.com`
- Mapped fields: reqId NONE; posted date NONE; timeType NONE; country = UAE ×2 / remote-global ×1.
- Rows saved to `/tmp/s23/re_E_insilico.json`.

**(5) Detail description path**
- Full description is IN-ROW (accordion body). No per-job pages, no popups, no apply URLs — application is by email.

**(6) US count verified live (2026-10-01)**
- **3 rows, 0 explicitly-US**: ML Engineer (UAE) → "Place of work: Abu Dhabi, United Arab Emirates"; ML Researcher (UAE) → Abu Dhabi, UAE; Business Development Manager(s) — Conferences & AI Platform Technologies → "Fully remote, open globally (Preferred: Japan, South Korea, Europe)". Matches brief (2 Abu Dhabi + 1 remote-global). US count = 1 ONLY IF the census counts remote-global roles as US-eligible; otherwise 0. Decision belongs to the census policy, not the adapter.

**(7) Sample rows** (from `/tmp/s23/re_E_insilico.json`)
```json
{"title": "ML Engineer (UAE)", "place_of_work": "Abu Dhabi, United Arab Emirates", "desc_chars": 2564}
{"title": "ML Researcher (UAE)", "place_of_work": "Abu Dhabi, United Arab Emirates", "desc_chars": 3485}
{"title": "Business Development Manager(s) — Conferences & AI Platform Technologies", "place_of_work": "Fully remote, open globally (Preferred: Japan, South Korea, Europe)", "desc_chars": 6733}
```

**(8) Quirks / adapter notes**
- Titles carry a location suffix "(UAE)" — strip or keep as location hint; don't let it collide with the "Place of work" field (authoritative).
- Description divs are found via `field="li_descr__<hash>"` attributes; hashes are unique per row — use them as stable-ish dedupe keys (change only when Tilda block is re-edited).
- Tilda wraps everything in nested divs with `style` + inline `<br/>` — convert with a lenient text extractor; em-dashes (—) and `&nbsp;` common.
- Content edits happen in-place in the Tilda editor; no dates — detect change via description hash.
- Same page also contains office gallery images (Shanghai/HK/Abu Dhabi/Suzhou) and marketing claims — scope the parser to `#rec620788308` to avoid false rows.

---
## Adapter class summary

| # | Board | Class | Listing endpoint | US rows (live 2026-10-01) |
|---|-------|-------|------------------|---------------------------|
| 1 | Black Sesame | static-parse (PbootCMS SSR) | `/en/join-us/` HTML | 6 (all San Jose, US) |
| 2 | Ecovacs US | JSON-API (Next `_next/data`) + HTML fallback | `/_next/data/<buildId>/us/careers/job-list.json` | 1 (Los Angeles, CA) |
| 3 | Accutar | static-parse (WP page accordion; NO REST feed) | `/careers/` HTML (+ `career-detail/?id=`) | 10 (Cranbury 4 / Mtn View 4 / Bellevue 2) |
| 4 | HitGen | static-parse (ThinkPHP SSR + filter) | `/en/careers-position.html?location=US` + popup pages | 1 (Director of BD) |
| 5 | Insilico | static-parse (Tilda SSR) | `/careers` HTML | 0 explicit (1 remote-global) |
