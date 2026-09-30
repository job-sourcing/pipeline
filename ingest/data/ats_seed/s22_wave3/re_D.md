# S22-RE-D: Custom-job-board API reverse-engineering (Greenland USA, NIU, Jereh, Ascentage, Autel Energy, Aden, Mandarin Oriental, WuXi AppTec mirror)

Boards: 8 Chinese/HK-company boards with confirmed US jobs.
Raw samples: `/tmp/s22/re_D_<board>.json`.

---
## 1. Greenland USA — pure STATIC Drupal 7 page (no feed; ATS iframe is dead)

**(1) Job-listing page**
- `https://greenlandusa.com/careers` — single page (node/24, content type `careers`), Drupal 7 (`<meta name="Generator" content="Drupal 7">`, theme "greenland"). No `wp-json`, no JSON API, no `ld+json`, no JS data — **server-rendered static HTML only**.
- The page ALSO embeds an ATS iframe: `<iframe src="https://greenlandusa.mua.hrdepartment.com/ats">` (HRsmart/HRdepartment "mua" instance) — **that host no longer resolves (DNS NXDOMAIN; hrdepartment.com root is also unreachable)**, so the iframe is dead in production too. The live job data is exclusively the static "Current Openings" field-collection below it.

**(2) Exact data endpoint**
- NONE. No API. `/rss.xml` exists but is generic site RSS (no jobs). No sitemap.xml (404). Scrape the HTML of `/careers`.

**(3) Pagination**
- NONE. All openings on one page.

**(4) Row shape — HTML parse structure (selectors)**
- Container: `article.node-type-careers` → `div.field-name-field-current-openings` → per-location blocks `div.entity.field-collection-item-field-current-openings` (block ids: `/field-collection/field-current-openings/117` = Los Angeles, `/118` = New York).
- Per block:
  - location → `div.field-name-field-career-location div.field-items div.field-item` text ("Los Angeles" / "New York")
  - rows → each `div.field-name-field-position-title div.field-items div.field-item` text (even/odd alternation; one opening per div)
- Mapped fields per row: **title** = the field-item text; **location** = block location; **country** = "United States" (all rows US; LA=Los Angeles CA, NY=New York NY — derive state from city name); **reqId** = NONE (no ids, no links); **posted date** = NONE; **timeType** = NONE; **detail URL** = NONE (titles are plain `<div>` text — not links).
- Rows saved to `/tmp/s22/re_D_greenland.json`.

**(5) Detail description path**
- NONE. Titles do not link anywhere; no per-job pages. Description text does not exist publicly (apply via the site's general "Inquire" form `form#reggreenland-form` on the same page).

**(6) US count verified live (2026-09-30)**
- **8 US rows**: Los Angeles ×4 (Escrow Closing Supervisor/Manager, HR Generalist, Marketing Director, Sr. Construction Accountant), New York ×4 (Accounting Manager, Construction Management Coordinator, Interior Design Manager, Recruiter). Matches brief exactly.

**(7) Sample rows** (from `/tmp/s22/re_D_greenland.json`)
```json
{"location": "Los Angeles", "titles": ["Escrow Closing Supervisor/Manager (Bilingual in Chinese Preferred)", "HR Generalist", "Marketing Director", "Sr. Construction Accountant – Construction/Real Estate Experience Required"]}
{"location": "New York", "titles": ["Accounting Manager – Construction/Real Estate Experience Required", "Construction Management Coordinator", "Interior Design Manager – Construction Administration/Management Required", "Recruiter"]}
```

**(8) Quirks / adapter notes**
- ATS iframe (`greenlandusa.mua.hrdepartment.com/ats`) is DEAD (NXDOMAIN) — do not attempt it; if it ever comes back it would be a HRsmart/HireRoad board.
- No per-row links/ids/dates: dedupe by (location,title) string; expect manual edits when the marketing team edits the page.
- Watch entity-encoded en-dashes (–) in titles; UTF-8 decode required.
- Plain curl works; no WAF, no cookies needed.

---
## 2. NIU Technologies — Sanity CMS (dataset is PUBLIC) + SSR Next.js page

**(1) Job-listing page**
- `https://global.niu.com/en-us/jobs` — Next.js (App Router) storefront, fully **server-side-rendered**; all job rows are in the initial HTML (no client XHR needed). Content lives in **Sanity** (projectId `cd9iwvgl`, dataset `production`) — job PDFs are `cdn.sanity.io/files/cd9iwvgl/production/<hash>.pdf`.
- Other-language mirrors of same doc: `en-ca` (`/en-ca/jobs`) — identical Jobs page doc per language.

**(2) Exact data endpoint** — the Sanity Content Lake HTTP API is open (no auth):
```
GET https://cd9iwvgl.api.sanity.io/v2024-07-15/data/query/production?query=<GROQ URL-encoded>
```
- One document holds the entire board: `*[_type=="page" && slug.current=="Jobs" && language=="en-us"][0]` (doc `_id` 52785da4-10d2-4299-9cfc-6ed15b7d2fd2, ~40 KB, fields `pageBuilder[]`, `_createdAt`, `_updatedAt`).
- Simpler GROQ for rows only (h4 block = title, first `normal` block = subtitle/region, rest = description, `ctas.ctas[0].link.url` = detail PDF):
  `*[_type=="page" && slug.current=="Jobs" && language=="en-us"][0]{pageBuilder, _updatedAt}`
- `count(*)` = 4990 docs; doc types: page ×762, product, blogPost, sanity.fileAsset ×71 … (no dedicated `job` type — jobs are `pageBuilder.textArea` sections).
- Plain curl works; no tokens/CORS issues (server-side API, not the graphql endpoint).

**(3) Pagination**
- NONE. Single page / single document. All 7 jobs in one response.

**(4) Row shape (derived from `pageBuilder[]` sections 5..11 of the Jobs page doc):**
- Job = one `textArea` section: `_type:"textArea"`, `text[]` portable-text blocks, `ctas.ctas[0]`.
  - title → first block with `style=="h4"` (`block.children[].text` joined)
  - location/region → first `style=="normal"` block ("DEALERSHIPS & FLAGSHIPS - MID-WEST USA" / "GTM STRATEGY FOR GOLF CARTS & LSV VEHICLES")
  - country → "United States" (from preceding `h2` "United States: " section — the whole page currently has ONLY the US region)
  - description → remaining `normal` blocks (full job description text, in-row)
  - detail URL → `ctas.ctas[0].link.url` = PDF on `cdn.sanity.io/files/cd9iwvgl/production/<sha1>.pdf` (verified: HTTP 200, `application/pdf`, e.g. 248 KB Marketing Director JD)
  - reqId → NONE (no requisition ids; use PDF hash or title as key)
  - posted date → not per row; page-level `_updatedAt` = **2026-05-12T12:20:27Z** (created 2025-01-16) — board is edited in place, so treat page `_updatedAt` as "last board change"
  - timeType → NOT PRESENT
- Rendered-HTML fallback selectors (SSR page): each job = `section.styles_wrapper__S0_vG` containing `h4` (title), `p` (subtitle then description), `div.ctas a.button` (PDF link); region header = `h2` ("United States:").

**(5) Detail payload path (description)**
- Full description text is already in the listing (portable-text blocks / rendered `<p>`s). The "detail" is a **PDF** (`View Job Listing` button → `cdn.sanity.io/files/.../<hash>.pdf`) — fetch + pdf-to-text if the richer formatted JD is wanted; not required for basic pipeline.

**(6) US count verified live (2026-09-30)**
- **7 US rows** (all rows on the page are US): Sales Director Dealerships, Key Account Manager ×4 (Mid-West / North East / North West / Central USA), Marketing Director, Channel Marketing Manager (Best Buy/Target). Matches brief.

**(7) Sample rows** — `/tmp/s22/re_D_niu.json` (7 rows + page timestamps)
```json
{"title": "SALES DIRECTOR DEALERSHIPS (M/F/D)", "subtitle": "GTM STRATEGY FOR GOLF CARTS & LSV VEHICLES",
 "country": "United States",
 "description": "As the Sales Director of Dealerships, you will play a critical role in driving the go-to-market (GTM) strategy ...",
 "detail_pdf": "https://cdn.sanity.io/files/cd9iwvgl/production/7bef58a1a84a728055baadf95445992daf585834.pdf",
 "page_url": "https://global.niu.com/en-us/jobs"}
{"title": "KEY ACCOUNT MANAGER (M/F/D)", "subtitle": "DEALERSHIPS & FLAGSHIPS - NORTH EAST USA", ...,
 "detail_pdf": "https://cdn.sanity.io/files/cd9iwvgl/production/e1e103f1ca03f6dc2f53e30e494505ddcdfcddd5.pdf"}
```

**(8) Quirks / adapter notes**
- Sanity dataset is **public & unauthenticated** — extremely stable feed; use GROQ directly (API version path `v2024-07-15` from site env: `NEXT_PUBLIC_SANITY_API_VERSION||"2024-07-15"`).
- GROQ gotcha: this API version rejects `array::unique(*::_type)` (parse error) — use `*[] | order(_type) | {"t":_type}` to enumerate types.
- No per-row ids/dates: dedupe by title+subtitle; track page `_updatedAt` for change detection.
- The full website HTML is 1.1 MB (whole product catalog SSR'd) — prefer the Sanity API (40 KB) over HTML parsing.
- An `en-ca` Jobs doc also exists (slug "Jobs") — filter `language=="en-us"` to avoid double rows.
- `rawHTML` section (pageBuilder[0]) contains a legacy JS fragment with `escapeHTML(dict.title)` — ignore it, not job data.

---
## 3. Jereh Group — jereh-nag.com JSP board (+ americanjereh.com static page) — NO JSON feed; JSP fragments

**(1) Job-listing page(s)**
- Main: `https://www.jereh-nag.com/careers/careers.jsp` (Jereh Energy Equipment and Technologies Corp = Jereh North American Group, Houston TX). Plain JSP page, rows fully server-rendered.
- Secondary: `https://www.americanjereh.com/en/service/Careers.htm` (American Jereh International Corporation — sibling US entity; JS language-redirect from `/` to `/en`, careers link `/en/service/Careers.htm`). NOTE: `https://americanjereh.com/careers/` does NOT exist (404); the brief's URL is wrong.

**(2) Exact data endpoints**
- Listing: NONE (no API/JSON) — rows are inline in `careers.jsp` HTML.
- Detail (per job): found in site JS `/resources/web/js/job_list.js` (`$.ajax({url:'/ext/ajax_job.jsp', data:{flag:'jobList', jobId:<data-id>}})`):
```
GET https://www.jereh-nag.com/ext/ajax_job.jsp?flag=jobList&jobId=<id>   → HTML fragment (modal body)
```
- Plain curl works (no auth; `X-Requested-With` not required). Verified for ids 75,77,78,79,81,86.
- americanjereh Careers.htm: pure static HTML, no endpoints at all.

**(3) Pagination**
- NONE. All 6 rows in one page (jereh-nag); 1 row (americanjereh).

**(4) Row shape (jereh-nag listing, from `ul.full-row > li[data-id]`):**
- reqId → `li@data-id` ("75","77","78","79","81","86") — internal CMS id used by the detail AJAX
- title → `div.title` text
- location → 2nd `<p>` in `div.info` — free text "Location: Jereh Energy Equipment and Technologies, Houston, Texas" / "..., Remote" / "..., Canada"
- country → derive from location tail: Houston→US, Remote→US (US-entity remote role), Canada→Canada
- posted date → last `<p>` "Release time: July, 2025" (month-year only)
- timeType → NOT PRESENT (full-time implied)
- detail URL → no per-job page; detail modal only (AJAX above). Human apply = "Indeed Job Page" link at page top → `https://www.indeed.com/cmp/American-Jereh-Corporation?...` (company page on Indeed, not per-job)
- `Job Number：1` (1st `<p>`) = number of openings, not a req id
- commented-out `<!--<p>Position Summary:...-->` in HTML = truncated summary teaser (ignore; full text in detail)
- Rows saved: `/tmp/s22/re_D_jereh.json`.

**(5) Detail payload path (description)**
- `GET /ext/ajax_job.jsp?flag=jobList&jobId=<id>` → HTML fragment: title, "Release time", "Job Number", "Location", then **`Job Description:` … full JD text** (Key Responsibilities etc., ~3–5 KB). Parse text nodes of the fragment (no stable classes; it's a modal markup). Files: `/tmp/s22/jereh_detail_*.html`.
- americanjereh: description is inline in the static page (single `Marketing Specialist` JD paragraph + mail-to apply, job code CYL001).

**(6) US count verified live (2026-09-30)**
- jereh-nag.com: **5 US rows** (Houston, Texas ×4 — Senior HR Recruiter, Global Supply Chain Director, Sales Support Specialist, Director of Engineering; "Remote" ×1 — Sr. Account Manager NexGen Frac & Mobile Power Solutions [US-entity remote]) + 1 Canada row (Account Director (Canada)). Brief said 4–5 — 4 strictly-Houston + 1 remote.
- americanjereh.com: +1 US row (Marketing Specialist, Houston TX, code CYL001). Total across both surfaces: 6 US.

**(7) Sample rows** (from `/tmp/s22/re_D_jereh.json`)
```json
{"reqId":"75","title":"Senior HR Recruiter, Oil and Gas/Energy Sector",
 "location":"Location: Jereh Energy Equipment and Technologies, Houston, Texas",
 "posted":"Release time: July, 2025",
 "detail_api":"https://www.jereh-nag.com/ext/ajax_job.jsp?flag=jobList&jobId=75"}
{"reqId":"86","title":"Director of Engineering",
 "location":"Location: Jereh Energy Equipment and Technologies, Houston",
 "posted":"Release time: July, 2025",
 "detail_api":"https://www.jereh-nag.com/ext/ajax_job.jsp?flag=jobList&jobId=86"}
```
Detail fragment (id 86) text starts: "Director of Engineering / CLOSE / Release time: July, 2025 / Job Number: 1 / Location: Jereh Energy Equipment and Technologies, Houston / Job Description: We are seeking an accomplished Director of Engineering to lead our North American engineering division..."

**(8) Quirks / adapter notes**
- Two distinct US surfaces (jereh-nag.com = Jereh EET/NAG; americanjereh.com = American Jereh Intl Corp) — scrape both for full coverage; they are different CMS instances (jereh-nag JSP+seajs; americanjereh static .htm).
- Site uses seajs + jQuery; the "click for detail" is a GET AJAX returning HTML — trivially scrapable per id.
- Location strings are prefixed "Location: Jereh Energy Equipment and Technologies, " — strip prefix.
- "Remote" row has no city — mark location "Remote (US)".
- Posted date granularity = month+year only. All rows currently "July, 2025".
- The `li` markup renders `data-id` gaps (76,80,82-85 missing) — ids are CMS-internal; do not assume contiguity (iterate from listing HTML, not id range).
- Charset: full-width colon in "Job Number：" (UTF-8). Careful with en-dash in titles.

---
## 4. Ascentage Pharma — WordPress + Simple Job Board plugin → public REST (`jobpost` CPT)

**(1) Job-listing page**
- `https://www.ascentage.com/careers/job-opportunities/` — WordPress 5.9.13 page embedding the **Simple Job Board** plugin (markup classes `sjb-*`). Board is mostly China; US rows are those with taxonomy location "United States" (often "Flexible, United States").
- Detail pages: `https://www.ascentage.com/job-opportunities/<slug>/`.

**(2) Exact data endpoint**
```
GET https://www.ascentage.com/wp-json/wp/v2/jobpost?per_page=100
GET https://www.ascentage.com/wp-json/wp/v2/jobpost_location?per_page=100   (taxonomy: 38=United States, 37=Flexible, 39=Beijing, ...)
GET https://www.ascentage.com/wp-json/wp/v2/jobpost_category?per_page=100   (departments)
GET https://www.ascentage.com/wp-json/wp/v2/jobpost_job_type?per_page=100   (empty — unused)
```
- Plain curl, no auth. REST namespace confirmed via `/wp-json/wp/v2/types` (`jobpost` exposed with rest_base `jobpost`).

**(3) Pagination**
- Standard WP REST: `per_page` (≤100), `page`. Response header `X-WP-Total` = 7 — single call gets everything.

**(4) Row shape**
- reqId → `id` (WP post id, e.g. 6647)
- title → `title.rendered`
- location → `jobpost_location[]` (term ids) → join taxonomy names ("Flexible, United States"); country = US iff term **38** ("United States") present
- department → `jobpost_category[]` → names ("Regulatory Affairs")
- posted date → `date` ("2021-02-05T16:51:20") — matches page's "Posted 6 years ago"
- timeType → `jobpost_job_type[]` is EMPTY for all rows (plugin field unused) — no timeType
- detail URL → `link` (`https://www.ascentage.com/job-opportunities/<slug>/`)
- HTML fallback selectors (listing page): `div.v2` per job: `span.job-title` (title+link), `div.job-category`, `div.job-location`, `div.job-date` ("Posted N years ago").

**(5) Detail payload path (description)**
- Already in listing row: `content.rendered` = full JD HTML (plain `<p>` paragraphs, ~2.8 KB). No second call needed. (Detail page renders the same + an "Apply Now" email link.)

**(6) US count verified live (2026-09-30)**
- 7 total rows; **4 rows whose location contains "United States"** (term 38): Senior Instructional Designer (United States, Investor Relation, 2023-01-19); Associate Director/Director, Quality Assurance (Flexible, United States, 2021-02-05); Regulatory CMC Lead (Flexible, United States, 2021-02-03); Project Lead, Regulatory Affairs (Flexible, United States, 2021-02-03). Other 3 rows = China (Beijing/Guangzhou/Shanghai/Jiangsu/Suzhou/Taizhou). Matches brief's 4.

**(7) Sample rows** — `/tmp/s22/re_D_ascentage.json` (4 US rows incl. description)
```json
{"reqId":6647,"title":"Senior Instructional Designer","location":"United States","country":"United States",
 "department":"Investor Relation","posted":"2023-01-19",
 "detail_url":"https://www.ascentage.com/job-opportunities/senior-instructional-designer/",
 "description_html":"<p>Lead the instructional design process to develop content ... </p>"}
{"reqId":3855,"title":"Associate Director/Director, Quality Assurance","location":"Flexible, United States",
 "department":"Regulatory Affairs","posted":"2021-02-05",
 "detail_url":"https://www.ascentage.com/job-opportunities/associate-director-director-quality-assurance/"}
```

**(8) Quirks / adapter notes**
- Jobs are OLD (2021–2023 "Posted N years ago") — stale board; low crawl frequency is fine.
- Location is a multi-value taxonomy: "Flexible" (37) + "United States" (38) join as "Flexible, United States" — US filter = term 38 membership (NOT string-contains, though string-contains "United States" also works today).
- `jobpost_job_type` taxonomy exists but is unused → no timeType data.
- No posted-date recency: `date`/`modified` are the WP post timestamps.
- Titles contain HTML entities (`&#8211;` en-dash) — unescape.

---
## 5. Autel Energy US — Shopify + PageFly accordion page — pure STATIC (no feed)

**(1) Job-listing page**
- `https://autelenergy.us/pages/careers` — Shopify storefront (theme section `shopify-section-template--15775616860320__pf-1490c196`), page built with **PageFly** page builder (`data-pf-type` / `pf-*` classes). Jobs are static accordions, fully server-rendered (no XHR for jobs, no `ld+json`, no products involved — `/products.json` is irrelevant: jobs are not products).
- No detail pages; each job expands inline.

**(2) Exact data endpoint**
- NONE. The HTML of `/pages/careers` is the only source (jobs hand-authored in PageFly). No Shopify metaobject/storefront feed carries them.

**(3) Pagination**
- NONE. All 3 jobs on one page.

**(4) Row shape — HTML parse structure (PageFly accordion)**
- Each job = one accordion item:
  - title → `button[data-pf-type="Accordion.Header"] > span` text ("IT Manager", "Production Supervisor", "Quality Technician")
  - description/location → sibling `div[data-pf-type="Accordion.Content"]` → `p.pf-text-1` (or any `p` inside): first lines are `TITLE<br>Autel Energy<br>North Carolina – United States<br><br>` then the full JD (`<b>RESPONSIBILITIES</b>`, bullets, etc.). Convert `<br>` to newlines.
  - location parse: 3rd line of the text block ("North Carolina – United States") — country = "United States"
  - reqId → NONE (no ids; key = title)
  - posted date → NONE (static content; no dates)
  - timeType → NONE (full-time implied)
  - detail URL → NONE (no per-job link, no apply link/email anywhere on the page — apply presumably via site contact page)
- Rows saved: `/tmp/s22/re_D_autel.json` (3 rows, full text).

**(5) Detail payload path (description)**
- Inline in the accordion content (same page) — no second fetch. `re_D_autel.json` holds ~2–4 KB text per job.

**(6) US count verified live (2026-09-30)**
- **3 US rows**, all "North Carolina – United States": IT Manager, Production Supervisor, Quality Technician. Matches brief.

**(7) Sample rows** (from `/tmp/s22/re_D_autel.json`)
```json
{"title": "IT Manager", "raw_text": "IT Manager\nAutel Energy\nNorth Carolina – United States\n\nEstablished in 2004, Autel quickly became ... General: Autel, a global leader in automotive equipment is seeking an IT Manager ... RESPONSIBILITIES ...", "mailto": []}
{"title": "Quality Technician", "raw_text": "Quality Technician\nAutel Energy\nNorth Carolina – United States\nEstablished in 2004 ..."}
```

**(8) Quirks / adapter notes**
- PageFly builder → markup is stable but class names are hashed (`sc-hknPuZ htdnki`); rely on `data-pf-type` attributes (`Accordion.Header` / `Accordion.Content`), NOT classes.
- Location uses en-dash separator "North Carolina – United States".
- Page includes Locksmith (Shopify lock app) JS — irrelevant noise; page is public, no auth.
- 328 KB HTML (nav/footer bloat) — fine for 1 page.
- No apply mechanism on page → for pipeline, `detail_url` = the careers page itself + anchor not possible (accordion has no ids) — use `https://autelenergy.us/pages/careers` + title.

---
## 6. Aden Group — WordPress custom theme, hand-coded accordion — pure STATIC (no feed)

**(1) Job-listing page**
- `https://adengroup.com/career/` — WordPress (custom theme `aden-group`; REST `/wp-json/wp/v2/types` has NO job CPT — jobs are hand-coded in the page template). Jobs = FAQ-style accordion blocks.
- 5 positions total: 2 US (Akila), 3 non-US (NXpark Wuxi China; Akila Hanoi Vietnam; Aden Energies Shanghai China).

**(2) Exact data endpoint**
- NONE. Static HTML only (no REST job type, no `ld+json` JobPosting — only an Organization `ld+json` in head, no XHR for jobs).

**(3) Pagination**
- NONE. All 5 positions in one page.

**(4) Row shape — HTML parse structure**
- Each job = accordion pair:
  - header → `div.question-position` (has `onclick="toggleAnswer(N)"`) → `div.box-question-2 > p.question-faq` text = **"TITLE - Company, Location, Country"** in one string (e.g. "Project Manager (PMO) - Akila, Remote, USA"; "Technical Sales Manager-Akila, North American")
  - body → `div.answer#answer-<N>` (N = 0-based index in DOM order) → `div.box-answer-top > p` = Mission; `div.box-answer > p.medium` = "Key Responsibilities" / "Key Skills" headings + `<p>` bodies
- reqId → NONE (index N is positional — can shift when jobs are added/removed; use title as key)
- title/company/location/country → parse from the question-faq string (split on " - " and ","); country: "USA" / "North American" (=US) / "China" / "Vietnam"
- posted date → NONE; timeType → NONE
- detail URL → NONE ("View more" is a JS toggle, no links; no mailto; apply presumably via contact form on site)
- Rows saved: `/tmp/s22/re_D_aden.json` (5 rows with full description text).

**(5) Detail payload path (description)**
- Inline in `div.answer#answer-N` on the same page — no per-job fetch. ~2.4–2.8 KB text per job.

**(6) US count verified live (2026-09-30)**
- **2 US rows**: "Project Manager (PMO) - Akila, Remote, USA" (answer-0) and "Technical Sales Manager-Akila, North American" (answer-4). Matches brief.

**(7) Sample rows** (from `/tmp/s22/re_D_aden.json`)
```json
{"title": "Project Manager (PMO) - Akila, Remote, USA", "idx": 0, "answer_id": "answer-0",
 "description": "## Mission \n Akila is looking for a Project Manager (PMO) ... ## Key Responsibilities ... ## Key Skills ..."}
{"title": "Technical Sales Manager-Akila, North American", "idx": 4, "answer_id": "answer-4",
 "description": "## Mission \n Akila is looking for an Technical Sales Manager to work with ambition, creativity ..."}
```

**(8) Quirks / adapter notes**
- Title string mixes title/company/location with inconsistent separators ("-" vs "-" vs ","; "Technical Sales Manager-Akila, North American" has NO space before dash) — parse defensively; treat "North American" as US.
- Answer ids are DOM-index based (`answer-0`..`answer-4`), not stable ids.
- The board is brand-scoped: US rows are for subsidiary **Akila** (akila3d.com) — company attribution from title string.
- Page HTML is 85 KB, gzip'd fine, no WAF; plain curl works.

---
## 7. Mandarin Oriental — custom Rails careers platform (Stimulus/Turbo, Greenhouse-req codes) — HTML + per-page JSON-LD; NO public JSON API

**(1) Job-listing page(s)**
- `https://careers.mandarinoriental.com/jobs/search` (custom white-label career-site SaaS: Rails engine `Sites::Engine`, Stimulus controllers `jobs--search`/`call-to-action--form`/`blocks--*`, Hotwire turbo-frames, assets on CloudFront `d8yy0r0qfxgnb.cloudfront.net`, company_id `f610e2a19dd14212e7dce05308a75038`). Not Infor/Lumapps/Teamtailor — bespoke platform with **Greenhouse integration on the apply side** (Greenhouse education/apply-form Stimulus controllers; req codes "JR-xxxxx").
- Detail pages: `https://careers.mandarinoriental.com/jobs/<title-slug>[-<uuid>]` (slug ends with `-<city>-<region>-united-states` for US jobs).

**(2) Exact data endpoints**
- Listing (GET, server-rendered HTML — the search form submits GET to itself):
```
GET https://careers.mandarinoriental.com/jobs/search?page=N
GET https://careers.mandarinoriental.com/jobs/search?cities[]=New+York      (also Boston, Miami, Beverly Hills)
```
  Other filter params (from the form selects): `search=` (keyword), `cities[]`, `dropdown_field_1_uids[]` (Employment Type: Permanent/Casual/Temporary/… md5-like uids), `dropdown_field_2_uids[]` (Hotel/Offices uids), departments.
- **Sitemap (full job list with dates):** `https://careers.mandarinoriental.com/sitemap.xml` → 798 `<loc>` job URLs + `<lastmod>`. (robots.txt: Crawl-delay 5; disallows `/me/`, `/api/`, `/pages/*/blocks/` — there is NO public JSON API: `/api/v1/jobs` = 404, `/jobs.json` = empty 202 challenge. `.json` format not supported.)
- **Detail JSON-LD (per job):** `GET /jobs/<slug>` → `<script type="application/ld+json">` = full schema.org **JobPosting** (see (5)). This is the richest machine-readable per-job feed.
- Plain curl works (no bot wall for HTML; a few endpoints return empty 202 — avoid them, they're not data endpoints).

**(3) Pagination**
- `page` (1-based) query param, **30 rows/page**, 27 pages for 798 jobs. Footer text: "Displaying 1 - 30 of 798 in total". City-filtered pages are single-page (≤30).
- Alternative: use sitemap.xml to enumerate all 798 job URLs, then fetch details (or just fetch the 4 US city filters — 4 requests total for all US rows).

**(4) Row shape (search card HTML, `article.job-search-results-card-col`)**
- reqId → `li.job-component-requisition-identifier span` text — **two formats**: `JR-07969` (Greenhouse-style) OR plain integer `546567` (second source system; 3 of 29 US rows are integers)
- title → `h3.job-search-results-card-title a` text + href (detail URL)
- location → city filter value / detail slug tail ("boston-massachusetts-united-states"); card itself shows **hotel** (`li.job-component-dropdown-field-2 span`, e.g. "Mandarin Oriental, Boston", "New York Marketing Office")
- country → from detail JSON-LD `jobLocation[].address.addressCountry` ("US")
- timeType → `li.job-component-employment-type span` ("Full time"/"Part time"); detail JSON-LD `employmentType` ("FULL_TIME")
- posted date → only on detail: JSON-LD `datePosted` (e.g. "2026-09-28T13:39:21Z"); sitemap `lastmod` is a change-date proxy
- department → `li.job-component-department span`
- detail URL → card title/read-more href
- job uid (stable platform id) → 32-hex in JSON-LD `identifier.value` (e.g. `2be827b493b1669080691fa6bd565b92`)
- US rows saved: `/tmp/s22/re_D_mo_us.json` (29 rows).

**(5) Detail payload path (description)**
- `GET /jobs/<slug>` → JSON-LD JobPosting: `title`, **`description`** (full HTML), `datePosted`, `employmentType`, `validThrough`, `identifier.value` (uid), `jobLocation[].address` (streetAddress/addressLocality/addressRegion/postalCode/addressCountry). Sample saved: `/tmp/s22/re_D_mo_us_detail.json`.
- HTML body also has `div.block-job-description` (title + job-component details + description blocks) — JSON-LD supersedes it.

**(6) US count verified live (2026-09-30)**
- **29 US rows** (Boston 6, New York 21 incl. New York Marketing Office + Mandarin Oriental Residences NY Fifth Avenue, Miami 1, Beverly Hills 1) — verified 2 ways: 4 city-filtered searches (29 cards) and sitemap 'united-states' slug count (29). Total board = 798 jobs (brief's "150 jobs, 2 US" is outdated — board grew; US now 29).

**(7) Sample rows** (from `/tmp/s22/re_D_mo_us.json`)
```json
{"req":"JR-07969","title":"Revenue and Reservations Manager","city":"Boston","hotel":"Mandarin Oriental, Boston","timeType":"Full time",
 "url":"https://careers.mandarinoriental.com/jobs/revenue-and-reservations-manager-boston-massachusetts-united-states"}
{"req":"546567","title":"Residential Concierge","city":"New York","hotel":"Mandarin Oriental Residences, New York Fifth Avenue","timeType":"Full time",
 "url":"https://careers.mandarinoriental.com/jobs/residential-concierge-new-york-united-states-e3b20f07-082f-465d-8216-d284fe108f9b"}
```
Detail JSON-LD (Revenue and Reservations Manager): `datePosted 2026-09-28T13:39:21Z`, `employmentType FULL_TIME`, `identifier.value 2be827b493b1669080691fa6bd565b92`, `jobLocation[0].address {streetAddress:"776 Boylston Street", addressLocality:"Boston", addressRegion:"Massachusetts", addressCountry:"US"}`, description full HTML.

**(8) Quirks / adapter notes**
- No JSON list API — scrape HTML cards (stable Bootstrap classes) or sitemap; per-job JSON-LD is the best detail contract.
- Two req-id formats (JR-xxxxx Greenhouse-style vs plain integers) — don't regex-validate as one format; `identifier.value` (32-hex uid) is the most stable key.
- city filter `cities[]` must be URL-encoded array (`cities%5B%5D=Boston`); the hotel filter uses md5-ish uids (from the same page's `<option value>`s).
- Multi-word city "New York" needs `+` encoding; "Beverly Hills" too.
- `robots.txt` asks Crawl-delay 5 + blocks `/pages/*/blocks/` (turbo-frame fragments) — respect; fetch search + detail pages only.
- US city list in the dropdown = exactly {Beverly Hills, Boston, Miami, New York} — the US filter is complete with these 4.
- Some slugs carry a trailing UUID (duplicate-title disambiguator) — don't strip it (URL breaks).

---
## 8. WuXi AppTec — wuxiappteccareers.com mirror: custom Django job board synced from iCIMS (HTML + per-job JSON-LD)

**(1) Job-listing page(s)**
- `https://wuxiappteccareers.com/jobs/` (redirects to `www.`) — a **Django/gunicorn custom career-site** (form id `optimator-form`, JS header "Buyer Framework JS v0.0.1", `csrfmiddlewaretoken` forms) that **mirrors the iCIMS board** `careers-wuxiapptec.icims.com` (which itself serves a JS bot-wall shell to plain curl).
- Location-filtered listing (SEO paths): `https://www.wuxiappteccareers.com/jobs/us/` (also `/jobs/de/`, `/jobs/ch/`, `/jobs/be/`, `/jobs/gb/`, `/jobs/eu/`); category filters `/jobs/<category-slug>/`; remote: `/jobs/?remote=1`.
- Detail: `https://www.wuxiappteccareers.com/job/<site_id>/<slug>/` (e.g. `/job/326/automation-lead-engineering-us-de-middletown/`).

**(2) Exact data endpoint**
- NO JSON API (Django HTML only): `/feeds/*` (robots-disallowed) → all 404; no `/api`. Data = SSR HTML:
```
GET https://www.wuxiappteccareers.com/jobs/us/            → page 1 (20 cards + "Found 36")
GET https://www.wuxiappteccareers.com/jobs/us/?page_jobs=2  → page 2 (16 cards)
GET https://www.wuxiappteccareers.com/job/<site_id>/<slug>/ → detail page with JSON-LD
```
- **Detail pages carry full schema.org JobPosting JSON-LD** (2nd `ld+json` block; 1st is Organization): `title`, `description` (full HTML), `datePosted` (ISO date), `employmentType`, `identifier.value` (= iCIMS Job ID), `jobLocation.address` (addressLocality/Region/Country), `jobLocationType` ("TELECOMMUTE") + `applicantLocationRequirements` for remote jobs.
- **iCIMS canonical mapping (verified live):** the apply flow (`POST /apply/<site_id>/<slug>/` with email) 302-redirects to `https://careers-wuxiapptec.icims.com/jobs/<job_id>/<slugified-title>/job` — i.e. mirror `job_id` (e.g. 14224) IS the iCIMS job id and the iCIMS detail URL is derivable. (Following the handoff with session cookies also passes the iCIMS bot wall, but it's an application flow — use the mirror for scraping.)

**(3) Pagination**
- `page_jobs` (1-based) query param; **20 rows/page**; 36 US rows = 2 pages. Header "Found <strong>36</strong> jobs" (global /jobs/ = 50 jobs, 3 pages).

**(4) Row shape (listing card, `ul#job-list-section > li`)**
- reqId → `dl dd` after `<dt>Job ID:</dt>` (e.g. "14224" — the iCIMS requisition/job id); site id → in the href `/job/<site_id>/...` (e.g. 328) — internal mirror id, NOT the iCIMS id
- title → `a > h3`
- location → `dd` after `Location:` — "United States" for remote/hybrid, or full site address for on-site: "Middletown, DE" / "6 Cedarbrook Dr, Cranbury, NJ" / "6114 Nancy Ridge Dr Building D, San Diego, CA" / "6122 Nancy Ridge Dr, San Diego, CA"
- country → "United States" (all rows on /jobs/us/; detail JSON-LD `jobLocation.address.addressCountry` = "US")
- timeType → NOT a field; `Job Type:` dd = "Remote" / "On-site" / "Hybrid" (workplace type, not Full/Part time; detail `employmentType` = "OTHER" — unhelpful)
- posted date → detail JSON-LD `datePosted` only (listing has no dates)
- detail URL → card href `https://www.wuxiappteccareers.com/job/<site_id>/<slug>/`
- category → `dd` after `Category:`
- summary → `p.job-description` (~truncated 200 chars)
- All 36 US rows saved: `/tmp/s22/re_D_wx.json`.

**(5) Detail payload path (description)**
- `GET /job/<site_id>/<slug>/` → JSON-LD JobPosting (`description` = full HTML). Sample saved: `/tmp/s22/re_D_wx_us_detail.json` (Automation Lead, Middletown DE, datePosted 2026-09-09).
- Page also renders the description as HTML in the `#job-details` section (h1 + Job Details dl + body) — JSON-LD is cleaner.

**(6) US count verified live (2026-09-30)**
- `/jobs/us/` → **36 US rows** = on-site/hybrid at facilities: **Middletown DE 13, San Diego CA 7, Cranbury NJ 2** (22 site rows — brief's 18–20 grew) + 14 US remote/hybrid ("United States": 13 Remote + 1 Hybrid). Global board: 50 jobs.
- NOTE: 2 rows are near-duplicates: "Facilities Technician II" — same San Diego address, different job ids (14149 site 271 / 14198 site 313).

**(7) Sample rows** (from `/tmp/s22/re_D_wx.json`)
```json
{"site_id":"328","job_id":"14224","title":"Associate Business Development Director, TIDES",
 "category":"Business Development","location":"United States","job_type":"Remote",
 "url":"https://www.wuxiappteccareers.com/job/328/associate-business-development-director-tides-business-development-us/"}
{"site_id":"326","job_id":"14216","title":"Automation Lead","category":"Engineering",
 "location":"Middletown, DE","job_type":"On-site",
 "url":"https://www.wuxiappteccareers.com/job/326/automation-lead-engineering-us-de-middletown/"}
```
Detail JSON-LD (Automation Lead): `datePosted "2026-09-09"`, `identifier.value "14216"`, `jobLocation.address {addressLocality:"Middletown", addressRegion:"DE", addressCountry:"US"}`, full HTML description.

**(8) Quirks / adapter notes**
- Mirror is the ONLY scrape-friendly surface: iCIMS search/job pages return a JS shell to plain curl (200 but 15 KB challenge page).
- TWO ids per row: `site_id` (URL path) and `job_id` (iCIMS) — use `job_id` for dedupe/canonical (`careers-wuxiapptec.icims.com/jobs/<job_id>/<slug>/job`).
- robots.txt: Crawl-delay 2, Disallow `/apply/`, `/feeds/`, `/login/`, `/accounts/` — fetch `/jobs/us/` + details only.
- `employmentType` is always "OTHER" (their taxonomy uses Job Type Remote/On-site/Hybrid instead) — map workplace type, derive Full/Part-time from title text if needed.
- Duplicate Facilities Technician II pair — keep both (distinct job ids) but flag.
- Location strings for on-site jobs include street addresses (e.g. "6114 Nancy Ridge Dr Building D, San Diego, CA") — normalize.
- No posted dates in listing — need N detail fetches for datePosted (36 for full US board; or skip dates and use first-seen).
- Sitemap (`/sitemap.xml`) only lists category/location landing pages, NOT job URLs — don't rely on it for enumeration.

---

# Cross-board summary

| # | Board | Surface / engine | Data feed | US rows (live 2026-09-30) | Notes |
|---|---|---|---|---|---|
| 1 | Greenland USA | Drupal 7 static page; ATS iframe DEAD (NXDOMAIN) | NONE — parse HTML `field-current-openings` | **8** (LA ×4, NY ×4) | no ids/dates/descriptions; dedupe by location+title |
| 2 | NIU Technologies | Next.js SSR + Sanity CMS (dataset PUBLIC) | `GET https://cd9iwvgl.api.sanity.io/v2024-07-15/data/query/production?query=<GROQ>` (Jobs page doc) | **7** | desc in-row; detail = PDF on cdn.sanity.io; page `_updatedAt` for change detection |
| 3 | Jereh Group | jereh-nag.com JSP (+ americanjereh.com static) | listing = static HTML; detail = `GET /ext/ajax_job.jsp?flag=jobList&jobId=<id>` | **5** (4 Houston + 1 Remote) +1 on americanjereh = 6 | no JSON; month-year "Release time"; ids = `li@data-id` |
| 4 | Ascentage Pharma | WordPress + Simple Job Board plugin | `GET /wp-json/wp/v2/jobpost?per_page=100` (+ taxonomies) | **4** (location term 38) | desc in `content.rendered`; stale 2021–2023 postings; no timeType |
| 5 | Autel Energy US | Shopify + PageFly accordion | NONE — parse HTML `data-pf-type="Accordion.*"` | **3** (North Carolina) | no ids/dates/apply links; rely on data-pf attrs not classes |
| 6 | Aden Group | WordPress custom theme accordion | NONE — parse HTML `p.question-faq` + `div.answer#answer-N` | **2** (Akila Remote USA + Akila North American) | title string carries company+location; index-based ids |
| 7 | Mandarin Oriental | custom Rails careers platform (Greenhouse-req codes) | `GET /jobs/search?page=N` + `cities[]` filters (HTML cards) + sitemap.xml + per-job **JSON-LD JobPosting** | **29** (Boston 6, NY 21, Miami 1, Beverly Hills 1; total board 798) | no JSON list API; 2 req-id formats; uid in JSON-LD identifier |
| 8 | WuXi AppTec | Django mirror of iCIMS (`wuxiappteccareers.com`) | `GET /jobs/us/?page_jobs=N` (HTML cards) + per-job JSON-LD; iCIMS URL derivable from job_id | **36** (Middletown DE 13, San Diego CA 7, Cranbury NJ 2, US-remote 14) | iCIMS itself bot-walled; job_id = iCIMS id; datePosted only in detail JSON-LD |
