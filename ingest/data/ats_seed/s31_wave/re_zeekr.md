# re_zeekr — Zeekr (Geely EV brand; Zeekr Technology Group, absorbed Lynk & Co 2025)
VERDICT: WIRE-READY (board = Teamtailor `zeekreu`; the probed zeekr.com URL is a HOMONYM — see below)
- Company identity evidence (homonym guard — IMPORTANT):
  - `https://www.zeekr.com/us/careers` (the t0 URL) serves "Digital House of
    Zeekr" — an affiliate marketer's personal landing page ("I'm an affiliate
    marketer / digital marketer..."). That domain is NOT the EV company → for
    that URL alone the verdict would be REFUTED.
  - The REAL board: `https://careers.zeekr.eu/jobs` (og:site_name "Zeekr EU";
    Teamtailor-powered, reachable at `https://zeekreu.teamtailor.com/`).
    JobPosting.hiringOrganization.name = "Zeekr EU", sameAs
    `https://careers.zeekr.eu`; JD text: "We are Zeekr ... premium electric
    vehicle and technology solutions brand from Geely Holding Group" — identity
    confirmed.
  - Official global marketing site `zeekrglobal.com` → 301 → `zeekrlife.com`
    (极氪官网) which has NO careers path at all (sitemap = locale marketing
    pages only, no US locale).
- List URL (canonical): `https://zeekreu.teamtailor.com/jobs.json`
  (JSON Feed, content-type `application/feed+json`; human mirror:
  `https://careers.zeekr.eu/jobs`).
- List mechanics: JSON API (Teamtailor JSON Feed).
- Row shape: `items[]`; verbatim sample (trimmed):
  ```json
  {"id":"8f965a83-7ced-412e-8415-ab829557d15c","title":"Launch Manager",
   "url":"https://zeekreu.teamtailor.com/jobs/8502434-launch-manager",
   "date_published":"2026-10-06T08:32:34+02:00",
   "content_html":"<p><strong>The Company</strong></p>...",
   "_jobposting":{"@type":"JobPosting","title":"Launch Manager",
     "identifier":{"name":"Zeekr EU","value":8502434},
     "datePosted":"2026-10-06T08:32:34+02:00",
     "hiringOrganization":{"@type":"Organization","name":"Zeekr EU","sameAs":"https://careers.zeekr.eu"},
     "jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress",
       "streetAddress":"Zeekr EU HQ","addressLocality":"Amsterdam","postalCode":"1081 KJ",
       "addressCountry":"NL","addressRegion":"Netherlands"}}]}}
  ```
- Fields per row:
  - title: `items[].title`
  - location: `items[]._jobposting.jobLocation[].address.addressLocality` +
    `addressCountry` (NL/BE/FR/DE/DK/HU/SE)
  - date: `date_published` / `_jobposting.datePosted` (ISO 8601 with offset)
  - JD: `content_html`
  - requisition: `_jobposting.identifier.value` (e.g. 8502434)
  - employmentType: NOT served (Teamtailor feed has none)
- Pagination: HTML list paginates (`/jobs` = 20 rows; `?page=2` = 2 rows;
  AJAX fragment endpoint `/jobs/show_more?page=2` also works — all VERIFIED
  LIVE today). The JSON feed returns all 22 items in one response — preferred.
- Detail URL pattern: `https://careers.zeekr.eu/jobs/{requisition}-{slug}`
  (verified 200 live; title tag `Launch Manager - Zeekr EU`).
- Detail shape: Teamtailor job page (title in `<title>`, JD HTML body, apply
  button) — but the feed's `content_html` already carries the full JD.
- Counts: total 22 rows; US rows 0; non-CN rows 22 (EU only: Amsterdam HQ NL
  9, Frankfurt DE 5, Brussels BE 3, Paris FR 1, Copenhagen DK 1, Budapest HU 1,
  Stockholm SE 1, Rokin Amsterdam 1); CN rows 0 (CN hiring is on separate
  Moka/feishu portals under Zeekr Technology Group).
- Sample titles (verbatim, 8): `Launch Manager`, `Data Privacy Counsel`,
  `International Key Account Manager - Leasing & Mobility`,
  `B2B Key Account Manager - Belgium`, `Marketing Internship`,
  `Salesforce Technical Consultant`, `Area Sales Manager Süd`,
  `Head of Sales - Germany`.
- Sample locations (verbatim): `Amsterdam`/`NL`, `Brussels`/`BE`,
  `Frankfurt`/`DE`, `Paris`/`FR`, `Copenhagen`/`DK`, `Budapest`/`HU`,
  `Stockholm`/`SE`.
- postedOn source: `date_published` (ISO 8601 +02:00) — observed range
  2026-02-02 → 2026-10-06.
- Adapter kind recommendation: EXISTING class `teamtailor` (site_boards.py
  TeamtailorAdapter, org `zeekreu`); geo_scope non_cn, 0 US today.
- Raw evidence: samples/zeekr.html (homonym page on zeekr.com),
  samples/zeekr_eu.html (zeekr.eu home — links "Career" → teamtailor),
  samples/zeekr_teamtailor.json (jobs.json feed, 22 items),
  samples/zeekr_careers_jobs.html (careers.zeekr.eu/jobs p1),
  samples/zeekr_job_detail.html (detail page).
