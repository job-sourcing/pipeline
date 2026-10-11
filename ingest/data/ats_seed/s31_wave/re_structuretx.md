# re_structuretx — Structure Therapeutics (Nasdaq: GPCR)
VERDICT: WIRE-READY (22 US rows on the findly board; the underlying BambooHR feed
carries all 32 incl. 10 Shanghai)
- Company identity evidence: `_XC_CONFIG` island in the WP shell:
  `"org":"structuretherapeutics","org_name":"Structure Therapeutics"`; the jobs
  API returns `"company_name":"Structure Therapeutics"`, `"ats_portalid":"BambooHR"`,
  address "601 Gateway Blvd, South San Francisco"; titles match the business
  (CADD, Structural Biology, GCP QA, Medical Writing — structure-based drug
  discovery). Yoast JSON-LD cross-references structuretherapeutics.site.findly.com.
  Confirmed Structure Therapeutics (NASDAQ: GPCR, WIRE candidate).
- List URL (the REAL jobs API behind the WP page — discovered by browser network
  observation of `/job-search-results/`): JSONP GET, no auth:
  ```
  https://jobsapi-google.m-cloud.io/api/job/search?callback=CWS.jobs.jobCallback
  &pageSize=10&offset=0
  &companyName=companies%2F7c57ce39-e2c3-4a0f-a3a1-748b255620d7
  &customAttributeFilter=(primary_country%3D%22US%22%20OR%20primary_country%3D%22United%20States%22)
  &orderBy=posting_publish_time%20desc
  ```
  (Symphony Talent Findly "m-cloud" jobs API; the WP site's
  `/wp-content/plugins/cws/js/cws.jobs.js?ver=4.5.4-310` builds this URL.)
  The listing PAGE is `https://careers.structuretx.com/job-search-results/`
  (path leaked by the `_XC_CONFIG` loginless_already_applied_message HTML).
  `careers.structuretx.com` = WP (Avada) skin over structuretherapeutics.site.findly.com.
- List mechanics: JSON API (JSONP wrapper `CWS.jobs.jobCallback(...)` — strip the
  wrapper prefix/suffix and parse). Page 2 verified (see Pagination).
- Row shape: one object per `searchResults[]` (each is `{summary, job}`); the job
  object fields (verbatim trimmed first row):
  ```json
  {"name":"projects/talent-cloud-jobs-prod/tenants/.../jobs/137388319620113094",
   "company_name":"Structure Therapeutics","id":23989994,"ats_portalid":"BambooHR",
   "ref":"533","language":"en-US","title":"Sr. Clinical Research Associate, Biospecimen Management",
   "primary_category":"Clinical Development Operations","addtnl_categories":[],
   "primary_city":"South San Francisco","primary_state":"CA","primary_zip":"94080",
   "primary_country":"US","primary_address":"601 Gateway Blvd, Suite 900",
   "google_locations":[{"city":"South San Francisco","state":"CA","zip":"94080",
     "country":"US","address":"601 Gateway Blvd Ste 900, South San Francisco, CA 94080, USA"}],
   "employment_type":"FTE","level":"Experienced","salary":"$135,000 - $145,000",
   "open_date":"2026-10-09T01:07:11","close_date":"2036-10-09T01:07:11",
   "timestamp":1791715338,
   "url":"https://careers.structuretx.com/job/23989994/sr-clinical-research-associate-biospecimen-management-south-san-francisco-ca/",
   "seo_url":"https://structuretx.bamboohr.com/careers/533",
   "description":"<p>...full HTML JD...</p>"}
  ```
- Fields per row: title → `title`; location → `primary_city` + `primary_state` +
  `primary_country` (STRUCTURED "US"); department → `primary_category`;
  date → `open_date` (EXACT ISO "2026-10-09T01:07:11"; also epoch `timestamp`);
  salary → `salary`; type → `employment_type` ("FTE"); reqId → `id`
  (findly id) with BambooHR requisition in `ref`; detail URL → `url`.
  The list payload already includes the FULL description HTML — no per-row
  detail fetch needed.
- Pagination: `offset` + `pageSize` (10). VERIFIED LIVE: offset=0 → 10 rows
  (totalHits 22), offset=10 → 10 rows (different: Senior Manager, Clinical
  Business Systems / AI Security Engineer / …), offset=20 → 2 rows
  (Sr. Associate, Clinical Supplies…; Sr. Clinical Research Associate) — 22 total.
- Detail URL pattern: `https://careers.structuretx.com/job/{id}/{slug}/` —
  e.g. `/job/23989994/sr-clinical-research-associate-biospecimen-management-south-san-francisco-ca/`
  (census pattern /job/{id}/{slug}/ CONFIRMED).
- Detail shape: SSR HTML — `<h1>` = job title ("Apply for … | Structure
  Therapeutics Careers" in title), Yoast JSON-LD WebPage; the apply form is
  Angular (schema-form). Detail JSON also available:
  `GET https://jobsapi-google.m-cloud.io/api/job/get?companyName=companies%2F7c57ce39-e2c3-4a0f-a3a1-748b255620d7&id={id}&callback=angular.callbacks._0`
  (observed in browser; same JSONP strip).
- BONUS (the underlying ATS): `https://structuretx.bamboohr.com/jobs/embed2.php?version=1.0.0&format=json`
  (LIVE, no auth) → the repo's EXISTING BambooHRAdapter contract
  (`departments[].positions[]` with id/name/url/location) — carries ALL 32
  positions: 20 South San Francisco + 2 Remote + 10 Shanghai, Pudong District
  (the findly index only mirrors the 22 US ones). BambooHR detail:
  https://structuretx.bamboohr.com/careers/{id}.
- Counts: findly board total 22 rows; US rows 22 (20 South San Francisco, CA +
  2 Remote — "Director, Global GCP QA", "Associate Director, Global GCP QA");
  CN rows 0 on findly, 10 Shanghai on the BambooHR feed (non-CN scope via
  BambooHR = 22).
- Sample titles (>= 6): "Sr. Clinical Research Associate, Biospecimen
  Management"; "Executive Director, Clinical Compliance"; "Director, AI
  Engineering"; "Sr. Clinical Trial Manager"; "Director, Global GCP QA";
  "Associate Director, Medical Writing"; "Data Scientist"; "Sr. Director,
  Commercial Supply Chain"; "AI Security Engineer".
- Sample locations (>= 6): South San Francisco, CA, US ×20; Remote ×2
  (slugs say "us-nationwide"); (BambooHR feed adds Shanghai, Pudong District ×10).
- postedOn source: `open_date` — EXACT ISO timestamps ("2026-10-09T01:07:11").
- Adapter kind recommendation: EXISTING class `bamboohr` with org
  `structuretx` (one GET, full board incl. CN rows, feeds country classification
  to the detail phase per the adapter's design). If the findly/m-cloud contract
  is preferred (ISO dates + structured country + full JD at list time), it would
  be a NEW class `findly`/`mcloud` (JSONP, companyName param
  `companies/7c57ce39-e2c3-4a0f-a3a1-748b255620d7`, offset paging) — same API
  family will serve other Symphony Talent-hosted boards.
- Raw evidence: samples/structuretx_home.html (WP shell, 1.26MB),
  samples/structuretx_search.html (job-search-results shell),
  samples/structuretx_jobs_p1.json, samples/structuretx_jobs_p2.json,
  samples/structuretx_jobs_p3.json (m-cloud JSONP pages 1/2/3, live),
  samples/structuretx_bamboohr_embed2.json (32-position BambooHR feed),
  samples/structuretx_detail.html (detail page).
