# S22-RE-A — ATS Public Job-Board API Reverse Engineering

Live-verified: 2026-09-30 (UTC). All endpoints hit with plain `curl`.
Raw samples saved: `/tmp/s22/re_A_breezy.json`, `/tmp/s22/re_A_breezy_popmart.json`, `/tmp/s22/re_A_workstream.json`, `/tmp/s22/re_A_jobvite.json`.

---

## 1. Breezy HR (Bitdeer Technologies Group)

### 1.1 List endpoint (contract)

```
GET https://bitdeer.breezy.hr/json
     (also https://bitdeer.breezy.hr/json/ — identical body)
```

- Method: GET only. **No auth, no UA, no special headers required** (verified: plain curl with zero headers → HTTP 200).
- `Content-Type: application/json; charset=utf-8` (~230 KB for 148 rows).
- Server: `envoy` / Express behind CloudFront; CORS wide open (`access-control-allow-origin: *`, allow-methods `GET, OPTIONS`).
- Query params: **none supported**. `?location=US` is ignored (still returns all 148). No server-side filter/pagination — the whole list ships in one payload.

### 1.2 Pagination

**None.** `/json` returns the complete list (all 148 positions) in a single JSON array. No offset/page/limit/cursor params exist. No dedupe needed (148 unique `id`).

### 1.3 Row shape → field map

Response: top-level JSON **array** of position objects. Trimmed example:

```json
{
  "id": "611f14be34db",
  "friendly_id": "611f14be34db-ai-cloud-infra-software-engineer-fresh-grad",
  "name": "AI & Cloud Infra Software Engineer (Fresh Grad)",
  "url": "https://bitdeer.breezy.hr/p/611f14be34db-ai-cloud-infra-software-engineer-fresh-grad",
  "published_date": "2026-08-26T...Z",
  "type": { "id": "fullTime", "name": "Full-Time" },
  "department": "AI Cloud",
  "salary": "$145,000 – $260,000",
  "company": { "name": "Bitdeer Technologies Group", "friendly_id": "bitdeer" },
  "location": {
    "country": { "name": "United States", "id": "US" },
    "city": "Austin",
    "name": "Austin, TX",
    "primary": true,
    "is_remote": false,
    "id": "<locid>",
    "streetAddress": { "location": "...", "components": [ /* google-style geocode */ ] }
  },
  "locations": [ /* same shape as `location`, ALL locations incl. primary */ ]
}
```

Adapter field map:

| target field | source path | notes |
|---|---|---|
| reqId | `id` | 12-hex, unique; `friendly_id` = `<id>-<slug>` |
| title | `name` | plain text |
| location text(s) | `location.name` + `locations[].name` | e.g. `"Austin, TX"`, `"San Jose, CA"`; `locations` repeats the primary, dupes possible; dedupe by loc `id` |
| country marker | `location.country.id` / `locations[].country.id` | ISO-2 (`US`, `SG`, ...) |
| timeType/employmentType | `type.id` / `type.name` | `fullTime`/`Full-Time`, `other`/`Other` seen |
| posted date | `published_date` | ISO-8601 UTC (`2026-07-21T03:44:11.885Z`) |
| detail URL | `url` | absolute, pattern `https://<board>.breezy.hr/p/<friendly_id>` |
| salary (bonus) | `salary` | free text, often empty (`""`) |
| department (bonus) | `department` | free text |

US-row rule: a row is US if **any** entry in `locations[]` has `country.id == "US"` (rows are frequently multi-location, e.g. "San Jose, CA" + "Austin, TX").

### 1.4 Detail endpoint

```
GET https://bitdeer.breezy.hr/p/<friendly_id>   → text/html (Angular SSR, ~23 KB)
```

- No JSON detail API (`/json/position/...` → 302 to portal home). The HTML page is the detail source.
- **Primary parse path: JSON-LD.** Page contains two `<script type="application/ld+json">` blocks; the **second** is `@type: JobPosting` (first is `@type: WebSite` — skip it). Trimmed:

```json
{
  "@type": "JobPosting",
  "title": "AI & Cloud Infra Software Engineer (Fresh Grad)",
  "datePosted": "2026-04-01",
  "employmentType": "FULL_TIME",
  "jobLocation": { "@type": "Place", "address": { "@type": "PostalAddress", "addressCountry": "US", "addressRegion": "TX", "addressLocality": "Austin" } },
  "hiringOrganization": { "@type": "Organization", "name": "Bitdeer Technologies Group" },
  "url": "https://bitdeer.breezy.hr/p/...?source=GoogleJobs",
  "description": "<p><strong>...</strong></p>..."   // full HTML, ~3.6-3.8 KB
}
```

- description path = `JobPosting.description` (raw HTML string). Fallback: `<div class="description">` in the HTML body holds the same markup.
- `datePosted` in JSON-LD differs from `published_date` in `/json` — prefer `/json.published_date` for list freshness.
- Only ONE `jobLocation` Place is emitted even for multi-location rows — do location mapping from the list JSON, not the detail page.

### 1.5 Quirks

- No auth / no UA needed; CORS `*`; `x-powered-by: Express` behind envoy+CloudFront.
- `robots.txt` disallows only `/css /fonts /stylesheets /javascripts` (and AhrefsBot site-wide) — `/json` and `/p/*` are crawlable.
- `locations[]` can contain duplicate cities (seen: `Austin, TX` twice) — dedupe by location `id`.
- Single `/json` payload includes ALL countries (10 countries on bitdeer); no server-side filtering.

### 1.6 US counts (verified live, 2026-09-30)

- **bitdeer.breezy.hr: 148 total positions, 46 with at least one US location.**
- US city refs (rows incl. multi-loc dupes): San Jose CA 50, Austin TX 31, Needham MA 6, Aurora CO 6, Rockdale TX 4, Massillon OH 2, Clarington OH 2, "United States" 3, "TN, US" 1, etc.

**Second board checked — `pop-mart-americas-inc.breezy.hr`:**
- `GET https://pop-mart-americas-inc.breezy.hr/json` → HTTP 200, body `[]` (empty array). Board homepage renders (title "POP MART Americas Inc.") but **0 open positions / 0 US rows live**. Same API contract as bitdeer; poll it, but currently nothing to ingest.

### 1.7 Sample rows (bitdeer, US)

- AI & Cloud Infra Software Engineer (Fresh Grad) — Austin, TX (+San Jose, CA; Massillon, OH; TN) — `611f14be34db`
- AI Cloud Senior DevOps Engineer — San Jose, CA (+Austin, TX) — `c61db6faf41b`
- 2026 GTT: Indication of Interest — United States — `4e1b1785fe35`

---

## 2. Workstream (Haidilao — HDL Management USA Corporation)

### 2.1 List endpoint (contract)

There is **no public JSON API** — the board is classic **server-rendered HTML** (jQuery + Bootstrap paginator, no `__NEXT_DATA__`/Nuxt state). Probed: `/api/*` shapes → `406` (json Accept) / `404`; `gateway.workstream.us` → no route; `.json` suffix → `410`. Scrape the HTML.

```
GET https://www.workstream.us/j/hdl_management_usa_corporation      → 301 → /j/fcc54c54   (org short-id redirect, query params preserved)
GET https://www.workstream.us/j/fcc54c54                            → page 1 (same as positions?page=1)
GET https://www.workstream.us/j/fcc54c54/positions?page=N           → paginated list (searchBaseUrl exposed in inline JS)
```

- Plain curl works; a browser UA is polite but not required. `text/html` only — `Accept: application/json` is ignored (still HTML).
- Optional filters (server-side, verified): `?title=<kw>` (substring, e.g. `title=busser` → 5 rows / 1 page), `?geo=<city,%20ST>&radius=<miles>` (geo radius search, e.g. `geo=Bellevue, WA&radius=50` → 2 pages), `?page=N`.
- `?locationIds=…` from the old share URL is **ignored** by the SSR board (no effect on rows/pages).

### 2.2 Pagination

- 1-based `page` query param; 10 rows per page (fixed).
- Page state is emitted as inline JS in every response: `currentPage = 1; totalPages = 6;` — **parse these two regexes to drive the loop**: `currentPage\s*=\s*(\d+)`, `totalPages\s*=\s*(\d+)`.
- `page > totalPages` still returns HTTP 200 with **0 position cards** (stop signal), e.g. page 7 → empty.
- Live math: 6 pages × 10 = 60 rows total.

### 2.3 Row shape → field map (HTML scrape)

Each job = `<div class="position-card mb16px pointer" onclick="location.href='<DETAIL_URL>'">…</div>`. Trimmed markup:

```html
<div class="position-card mb16px pointer" onclick="location.href='https://www.workstream.us/j/fcc54c54/haidilao-hot-pot/bellevue-14002/busser-9c76b218?locale=en'">
  <a class="no-underline b fz16px black" href="…/busser-9c76b218?locale=en">Busser</a>
  <span class="tag tag-small ml8px bg-flat-green">Full-time</span>          <!-- OPTIONAL: absent on many rows -->
  <div class="position-address mute fz13px">188 106th Ave NE Suite #210, Bellevue, WA 98004, USA</div>
  <div class="position-short-desc fz13px">Benefits: Provides employees discounts…</div>
  <img class="image-icons" src="/j/images/icon-rate-of-pay.svg"/> <span class="mute fz13px">$17.13 - 30.00 per hour</span>
  <img class="image-icons" src="/j/images/icon-company-type.svg"/> <span class="mute fz13px">Haidilao Hot Pot</span>
</div>
```

| target field | source | notes |
|---|---|---|
| reqId | trailing 8-hex of detail URL slug (`busser-9c76b218` → `9c76b218`) | unique per posting, verified 60/60 |
| title | inner text of `a.no-underline.b.fz16px` | may repeat across locations (Server ×9) |
| location text(s) | `div.position-address` | full street address, almost always ends `, USA` |
| country marker | address suffix `, USA` (or `<city>, <ST> <zip>` regex) | all rows US here; no explicit country field |
| timeType/employmentType | `span.tag` text (`Full-time`/`Part-time`) | **often missing on card** → take from detail JSON-LD |
| posted date | **not on list cards** — fetch detail JSON-LD `datePosted` | only source |
| detail URL | `onclick` attr or `a` href | `https://www.workstream.us/j/<orgId>/<brandSlug>/<city>-<locId>/<titleSlug>-<reqId>?locale=en` |
| salary (bonus) | span after `icon-rate-of-pay.svg` | `$17.13 - 30.00 per hour` |
| brand (bonus) | span after `icon-company-type.svg` | e.g. `Haidilao Hot Pot` |

Location id is embedded in the URL path segment `<city>-<locId>` (e.g. `bellevue-14002`, `tukwila-205833`) — useful as a stable location key.

### 2.4 Detail endpoint

```
GET https://www.workstream.us/j/fcc54c54/<brand>/<city>-<locId>/<slug>-<reqId>?locale=en   → text/html (~49 KB)
```

- Single `<script type="application/ld+json">` = `@type: JobPosting`. Trimmed:

```json
{
  "@type": "JobPosting",
  "title": "Busser",
  "datePosted": "2026-08-17",
  "validThrough": "2026-10-18",
  "employmentType": ["FULL_TIME", "PART_TIME"],
  "baseSalary": { "@type": "MonetaryAmount", "currency": "USD",
                  "value": { "@type": "QuantitativeValue", "minValue": 17.13, "maxValue": 30, "unitText": "HOUR" } },
  "jobLocation": { "@type": "Place", "address": { "@type": "PostalAddress",
      "streetAddress": "188 106th Ave NE", "addressLocality": "Bellevue", "addressRegion": "WA",
      "postalCode": "98004", "addressCountry": "US" } },
  "hiringOrganization": { "@type": "Organization", "name": "Haidilao Hot Pot" },
  "description": "<p><strong>Benefits:</strong></p><ul><li>…</li></ul>…"   // full HTML, ~4 KB
}
```

- description path = `JobPosting.description` (HTML string). `directApply: true`. `employmentType` is an **array**.
- Canonical link tag = same URL with `?locale=en`.

### 2.5 Quirks

- Slug → short-id 301 redirect: always follow redirects (`-L`) or rewrite to `/j/fcc54c54` first.
- `/api/*` returns **406** (with `Accept: application/json`) — do not bother; HTML is the interface. `gateway.workstream.us` unroutable from here.
- `robots.txt` (marketing site rules) does **not** disallow `/j/` — crawlable; board sitemap `/j/<id>/sitemap.xml` → 410 Gone (don't rely on sitemaps; main sitemap.xml has no job URLs/lastmod).
- Tag/employment-type on cards is unreliable (38/60 rows lacked it on live check) — fill from detail.
- No per-row posted date on list pages → detail fetch required for posting-date enrichment.
- Rate limiting: none observed on ~15 rapid requests; be polite anyway (1 req/sec).

### 2.6 US counts (verified live, 2026-09-30)

- **60 total rows, 60 US-located** (16 distinct US addresses: Cupertino CA 10, Tukwila WA 8, Bellevue WA 6, Chicago IL 5, Los Angeles CA 4, Mesa AZ 4, Frisco TX 4, Flushing NY 3, Duluth GA 3, Arcadia CA 5, Irvine CA 2, Fremont CA 2, San Diego CA 2, Katy TX 1, Seattle WA 1). Every address matches `<city>, <ST> <5-digit zip>`. (Task brief said "52 rows" — that count is stale; live board = 60.)
- Types on cards: Full-time 11, Part-time 11, unlabeled 38.

### 2.7 Sample rows

- Busser — 188 106th Ave NE Suite #210, Bellevue, WA 98004, USA — `9c76b218`
- Dishwasher — 2800 Southcenter Mall unit 328, Tukwila, WA 98188, USA — `3fd38d50`
- Administrative Assistant (Mandarin&English) — 188 106th Ave NE #210, Bellevue, WA — `045dc3cb`

---

## 3. Jobvite (OmniVision Technologies — careersite `ovt`)

### 3.1 List endpoint (contract)

Jobvite careersites are AngularJS SPAs with a **server-rendered fallback list** — that fallback is the scrape target. There is **no JSON list API** (the only JSON is a facets endpoint, see 3.5). RSS is dead (`CompanyJobs.rss?c=<eid>` → 302 → jobvite.com "invalid" support page).

```
GET https://jobs.jobvite.com/ovt/search?p=N          ← CANONICAL list (SSR HTML table)
GET https://jobs.jobvite.com/ovt/search              ← same as p=0
GET https://jobs.jobvite.com/ovt                     ← board home: SPA shell + INCOMPLETE SSR list (77 of 108 jobs — DO NOT USE for ingestion)
```

- Plain curl, no auth, no UA requirement. `text/html;charset=UTF-8`.
- Query params on `/ovt/search` (verified live):
  - `p` — **0-based page index**, 50 rows/page (combines with all filters, e.g. `?q=engineer&p=1` → "51-92 of 92")
  - `q` — full-text keyword (`?q=engineer` → 92 hits)
  - `c` — category facet **display name** (`?c=Engineering` → 43)
  - `r` — region facet **display name** (`?r=USA` → 61 hits) — **QUIRK: bare `?r=USA` is IGNORED (returns unfiltered 108); must pair with another param, e.g. `?r=USA&p=0` or `&nl=1`**
  - `l`/`d`/`t`/`s` (locations/departments/jobTypes/subsidiaries) — accepted by the SPA's facet API but **ignored by the SSR route**

### 3.2 Pagination

- `?p=N`, 0-based, fixed 50 rows/page. `p` beyond last → HTTP 200 with empty table (stop signal).
- Totals from `<div class="jv-pagination-text"> 51-61 of 61 </div>` (regex `jv-pagination-text">\s*(\d+)-(\d+) of (\d+)`), and next-page URL from `<a href="/ovt/search/?p=N" class="jv-pagination-next">`.
- Live: 108 jobs → p=0 (1-50), p=1 (51-100), p=2 (101-108).

### 3.3 Row shape → field map (HTML scrape)

Rows live in `<table class="jv-job-list jv-search-list">` → `<tbody>` → `<tr>`:

```html
<tr>
  <td class="jv-job-list-name">
    <a href="/ovt/job/oZicrfwE">(Senior Staff / Senior) Algorithm Engineer</a>
  </td>
  <td class="jv-job-list-location"> Shin-Yokohama, Shin-Yokohama </td>
  <!-- multi-location rows instead render: <div class="jv-meta"> 2 Locations </div> -->
</tr>
```

| target field | source | notes |
|---|---|---|
| reqId | path segment of detail link (`/ovt/job/oZicrfwE` → `oZicrfwE`) | 8-char Jobvite EId, unique (108/108) |
| title | anchor text in `td.jv-job-list-name` | |
| location text(s) | `td.jv-job-list-location` | "City, Region" (e.g. "Santa Clara, California"); **multi-location rows show only "N Locations"** → must fetch detail |
| country marker | **absent on list** — from detail JSON-LD `jobLocation[].address.addressCountry` | |
| timeType/employmentType | none anywhere (JSON-LD `employmentType` null on all 108) | jobTypes facet is literally "Uncategorized" |
| posted date | **not on list** — detail JSON-LD `datePosted` only | range seen 2023-04-25 → 2026-09-18, present on 108/108 |
| detail URL | `https://jobs.jobvite.com/ovt/job/<reqId>` | |

### 3.4 Detail endpoint

```
GET https://jobs.jobvite.com/ovt/job/<reqId>          → text/html (~20 KB, SSR)
```

Single `<script type="application/ld+json">` = `@type: JobPosting`. Trimmed:

```json
{
  "@type": "JobPosting",
  "title": "(Senior Staff / Senior) Algorithm Engineer",
  "identifier": "oZicrfwE",
  "datePosted": "2024-01-26",
  "jobLocation": [
    { "@type": "Place", "address": { "@type": "PostalAddress", "addressLocality": "Shin-Yokohama", "addressRegion": "Shin-Yokohama", "addressCountry": "Japan" } },
    { "@type": "Place", "address": { "addressLocality": "Kyoto", "addressCountry": "Japan" } }
  ],
  "baseSalary": { "…": "sometimes present" },
  "hiringOrganization": { "…": "OMNIVISION / Will Semiconductor" },
  "description": "<div><b><u>…Responsibilities…</div>…"   // full HTML, 1-12 KB
}
```

- `jobLocation` is an **ARRAY** (one Place per location) — US check = any `address.addressCountry == "United States"`.
- description path = `JobPosting.description` (HTML). Same content also in body at `<div class="jv-job-detail-description">`.
- Visible body equivalents: `<h2 class="jv-header">` (title), `<p class="jv-job-detail-meta">CATEGORY<sep>LOC1<sep>LOC2</p>` where `<sep>` = `<span class='jv-inline-separator'></span>` — this line gives **all locations as text**, solving the "N Locations" gap.
- Apply URL: `/ovt/job/<reqId>/apply`.

### 3.5 Facets JSON (bonus, no job rows)

```
GET https://jobs.jobvite.com/ovt/search/facets?nl=1    → {"facets": {"regions","locations","departments","categories","jobTypes": [{"EId","name"}, …]}}
```

For ovt: regions = APAC / EMEA / **USA** (`OdvaVfwx`); locations include "Irvine, CA" (`CzZDXfwS`), "Santa Clara, CA" (`CB4LVfw5`) — note location names here carry `", CA"` state suffixes unlike the list cells. Useful for filter EIds, but the SSR route wants **names**, not EIds.

### 3.6 Quirks

- `https://jobs.jobvite.com/robots.txt` → **404** (no crawl rules; Tomcat 404 page).
- Board home `/ovt` SSR list is **incomplete** (77 of 108; 31 engineering rows missing) — always ingest via `/ovt/search?p=N`.
- Unknown paths (`/ovt/rss.xml`, `/ovt/jobs`, `/ovt/whatever`) fall back to the board HTML (200) — don't trust status codes alone; check for `jv-search-list` + `jv-pagination-text`.
- SPA config in inline JS (useful constants): `companyEId: 'qsnaVfwE'`, `baseUrl: '/ovt'`, `careersiteName: 'ovt'`, app bundle `//d3igejkwe1ucjd.cloudfront.net/__assets__/concat/careersite/public/jv.careersite.desktop.app.js`.
- No rate limiting observed over 120+ requests (0.15 s spacing); no 406/403s at all.
- `datePosted` can be years old (oldest 2023) — Jobvite boards don't prune; expect staleness.

### 3.7 US counts (verified live, 2026-09-30)

- **108 total jobs; 61 US-located** (addressCountry "United States": Santa Clara, CA ×53, Irvine, CA ×8). Cross-verified two ways: (a) region filter `?r=USA&p=0` → "1-50 of 61" + "51-61 of 61" = 61 rows; (b) full 108-detail sweep parsed from JSON-LD `jobLocation` arrays → 61 rows with a US country. Non-US: Japan 21, Singapore 10, Norway 9, Belgium 3, Germany 3, UK 1.
- (Task brief said "60 roles, 37 US" — stale; live board is 108/61.)

### 3.8 Sample rows (US)

- Analog CAD Engineer — Santa Clara, California — `owqyzfwN`
- Analog Design Engineer — Irvine, California — `om1yzfwe`
- Sr. Linux Engineer (Contract) — Santa Clara, California — `ogBLAfwW`

---

## Summary (adapter cheat-sheet)

| platform | list endpoint | pagination | US rows / total (live) | detail description path |
|---|---|---|---|---|
| Breezy HR | `GET https://bitdeer.breezy.hr/json` (array) | none (single payload) | 46 / 148 (pop-mart board: 0 / 0) | detail page `/p/<friendly_id>` → 2nd `ld+json` `JobPosting.description` |
| Workstream | `GET https://www.workstream.us/j/fcc54c54/positions?page=N` (HTML) | `page` (1-based), 10/page, `totalPages` inline JS | 60 / 60 | detail page → single `ld+json` `JobPosting.description` |
| Jobvite | `GET https://jobs.jobvite.com/ovt/search?p=N` (HTML) | `p` (0-based), 50/page, "X-Y of Z" text | 61 / 108 | detail `/ovt/job/<id>` → single `ld+json` `JobPosting.description` |

Raw artifacts in `/tmp/s22/`: `re_A_breezy.json` (full list), `re_A_breezy_popmart.json` (empty), `re_A_workstream.json` (60 parsed rows; raw HTML pages `ws_board.html`, `ws_p2..6.html`, `ws_detail.html`), `re_A_jobvite.json` (108 parsed detail rows; raw list pages `jv_search.html`, `jv_p1/p2.html`, detail sweep in `jv_details/`), `jv_facets.json` (facets API sample).
