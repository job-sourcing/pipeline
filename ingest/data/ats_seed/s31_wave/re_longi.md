# re_longi — LONGi Solar (LONGi Green Energy Technology)
VERDICT: WIRE-READY
- Company identity evidence: `<title>Jobs -LONGi</title>`, `og:site_name: Longi`;
  API rows have business_group "Utility Business Group", recruiter_email
  `wuhaoxuan@longi.com`; site footer `© LONGi 2025` + `陕公网安备 61019102000339号`
  (Shaanxi ICP filing — matches Xi'an HQ, world's largest wafer/module maker).
- List URL: `https://website-console.longi.com/job-openings?_limit=50&_start=0&title_contains=&_sort=updated_at%3ADESC`
  (Strapi JSON API; discovered via patchright network capture — the page itself
  `https://www.longi.com/us/jobs/` is a Nuxt SPA with NO rows in static HTML).
- List mechanics: JSON API (Strapi `_limit`/`_start`/`_sort`).
- Row shape: top-level JSON array; verbatim sample row (trimmed):
  ```json
  {"id":2,"title":"Sales Manager","business_group":{"id":3,"name":"Utility Business Group"},
   "number_of_recruitment":1,"seniority_level":{"id":8,"name":"Manager"},
   "job_category":{"id":2,"name":"Marketing"},"city":{"id":2,"name":"Dubai","country":9},
   "country":{"id":9,"name":"UAE","region":4,"code":"AE"},
   "region":{"id":4,"name":"Middle East"},"job_id":"10057931",
   "responsibilities":"<p>1. Identifying new business opportunities...</p>",
   "recruiter_email":"wuhaoxuan@longi.com",
   "recruitment_type":{"id":1,"name":"Experienced Hires"},
   "published_at":"2024-04-01T02:53:45.000Z","updated_at":"2024-04-02T02:59:43.000Z"}
  ```
- Fields per row (JSON path):
  - title: `title`
  - location: `city.name`, `country.name`, `region.name`
  - department: `job_category.name` (+ `business_group.name`, `seniority_level.name`)
  - date: `published_at` / `updated_at` (ISO 8601, e.g. `2024-04-01T02:53:45.000Z`)
  - detail URL: NONE — the SPA renders cards with no per-job URL (`/us/jobs/10057931`
    → 404; cards are `<a class="card">` with no href, click opens inline expansion).
    Detail = the row itself; `responsibilities` + `qualifications` fields carry the JD.
    Apply-by-email: `recruiter_email`.
- Pagination: `_limit`/`_start` (offset). VERIFIED LIVE: `_limit=1&_start=1` returned
  exactly the 2nd row (Algiers). Count endpoint:
  `https://website-console.longi.com/job-openings/count?title_contains=&_sort=updated_at%3ADESC` → `2`.
- Detail URL pattern: n/a (no per-job URL exists; job_id `10057931` is a requisition
  number, not a URL segment).
- Detail shape: same JSON row (JD in `responsibilities`/`qualifications` HTML strings).
- Counts: total 2 rows; US rows 0; non-CN rows 2 (UAE, Algeria); CN rows 0 on this
  global site (CN recruiting is a separate longi.com CN portal).
- Sample titles (verbatim, all 2): `Sales Manager`, `Sales Manager`.
- Sample locations (verbatim, all): `Dubai` / `UAE` / `Middle East`,
  `Algiers` / `Algeria` / `Africa`.
- postedOn source: `published_at` (ISO) + `updated_at` (ISO) — the SPA displays
  "Updated on April 2, 2024".
- Adapter kind recommendation: new class `longi_strapi` (or generic `strapi_json`
  with host `website-console.longi.com`, path `/job-openings`). No browser needed.
- Extra: filter facets API `https://javacms-prod-cn.longi.com/recruitment/filter-conditions`
  (200 JSON; recruitmentTypes incl. "Campus Recruitment-graduate"); country filter
  `_where[0][country.code]=US` returns `[]` (verified live).
- Raw evidence: samples/longi.html (static SPA shell), samples/longi_api.json
  (full API payload, 2 rows), samples/longi_rendered.html (patchright-rendered DOM
  showing the 2 cards).
