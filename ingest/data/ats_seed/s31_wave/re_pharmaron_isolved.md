# re_pharmaron_isolved — Pharmaron (isolved Hire US board)
VERDICT: WIRE-READY
- Company identity evidence: the SPA shell's `courierCurrentRouteData` JSON island carries
  `"career_site_name":"Pharmaron"` and `"organization_name":"Absorption Systems LLC /
  Pharmaron/ US Clinical Services Inc"` (org 1919, domain 5164). Detail pages carry
  schema.org JobPosting JSON-LD with `"hiringOrganization":{"name":"Pharmaron"}` and
  titles matching Pharmaron's CRDMO business (Discovery ADME, Biostatistics, QC
  Analytical Chemistry). Confirmed Pharmaron, not a homonym.
- List URL: `https://pharmaron.isolvedhire.com/core/jobs/5164?getParams=<URL-encoded JSON>`
  where the JSON is the page's mounting `getParams` object (minimal working form below).
  domainId `5164` comes from the shell's `componentData` for `#job_listings`
  (`organizationId: 1919, domainId: 5164, domainName: "isolvedhire.com",
  subdomainName: "pharmaron"`).
- List mechanics: JSON API (isolved "Applicant Pro" / Courier core API). The Vue
  `JobListings` component (chunk `JobListings-ce3e66ba.js`) does
  `courier.get("https://{sub}.{domain}/core/jobs/{domainId}", {getParams})`;
  axios serializes the object value as `getParams=<json>`. Discovered by fetching
  `/js/web-components/applicant-pro-components.js` → re-export → real bundle
  `public-career-site-035329e0.js` → chunk `JobListings-ce3e66ba.js` line 32.
- Row shape: one JSON object in `data.jobs[]`. Verbatim first row (trimmed):
  ```json
  {"id":1874947,"title":" Associate Scientist, Discovery ADME","city":"Exton",
   "subdomain":"pharmaron","iso3":"USA","abbreviation":"PA","classification":"Scientist",
   "siteId":5164,"startDateRef":"Sep 29, 2026","endDateRef":"Sep 29, 2031","untilFilled":1,
   "orgTitle":"Exton - Discovery In Vitro ADME","parentTitle":null,
   "domainName":"isolvedhire.com","stateName":"Pennsylvania","workplaceType":"Onsite",
   "employmentType":"Full Time","jobCategory":"Life Science","customCategory":null,
   "payRate":null,"payType":"Salary","payTypeFrame":"per year","payDetails":"",
   "minSalary":"48,000","maxSalary":"55,000","jobLocation":"Exton, PA 19341, USA",
   "streetAddress":"Creamery Way","chatToApplyEnabled":"0",
   "jobUrl":"https://pharmaron.isolvedhire.com/jobs/1874947"}
  ```
- Fields per row: title → `title`; location → `jobLocation` (or `city` + `stateName` +
  `iso3`); country → `iso3` ("USA" = ISO3 country code — authoritative);
  department → `orgTitle` (site label) or `jobCategory`; date → `startDateRef`
  ("Sep 29, 2026" — posting start); salary → `minSalary`/`maxSalary` + `payTypeFrame`;
  employmentType → `employmentType` ("Full Time"); detail URL → `jobUrl`.
- Pagination: NONE NEEDED — `data.jobCount` = 32 and `len(data.jobs)` = 32: the single
  call returns the whole board (verified live, one fetch). No `page`/`offset` param
  exists in the JobListings chunk; filters (`keywords`, `city`, `stateId`,
  `employmentTypeId`, `sort`) are merged into `getParams` by SearchFilter
  (`updateGetJobs` emit). Minimal working `getParams`:
  `{"cityUrl":"","countryAbbreviation":"","stateAbbreviation":"","isInternal":0,
  "showLocation":1,"showPayFrequency":1,"showEmploymentType":1}` — even
  `?getParams={}` (empty JSON object) is accepted; what matters is that `getParams`
  is present and JSON (without it the PHP controller 500s:
  "Json::decodeStatic() must be of the type string, null given").
- Detail URL pattern: `https://pharmaron.isolvedhire.com/jobs/{id}` (canonical
  `.html` variant `/jobs/{id}.html` appears in the JSON-LD `url`).
- Detail shape: SSR HTML with `application/ld+json` JobPosting —
  `title`, `url`, `description` (full JD HTML), `identifier.sameAs` (=id),
  `datePosted` ("2026-09-29 00:00:00" — EXACT ISO-ish timestamp),
  `employmentType` ("FULL_TIME"), `baseSalary` (USD min/max + unitText YEAR),
  `hiringOrganization.name`, `jobLocation.address` (streetAddress, locality,
  region, postalCode, addressCountry "US") and geo coordinates. Sample fragment:
  `"datePosted":"2026-09-29 00:00:00","validThrough":"2031-09-29 00:00:00T23:59"`.
- Counts: total 32 rows; US rows 32 (every row iso3=USA); CN rows 0; non-CN 32.
- Sample titles (>= 6): " Associate Scientist, Discovery ADME"; " Clinical
  Applications Programmer II"; " Preclinical Veterinary Technician, Large Animal ";
  "Associate Director, Biostatistics"; "Director, Biostatistics"; "Laboratory
  Technician, QC"; "Principal Investigator"; "Scientist II, Analytical Chemistry";
  "Trial Master File Specialist"; "Warehouse Associate".
- Sample locations (>= 6): Exton, PA; Baltimore, MD; Carlsbad, CA; Waltham, MA;
  Franklin Township, NJ; Coventry, RI; Germantown, WI; Woburn, MA (all USA).
- postedOn source: detail JSON-LD `datePosted` gives exact "YYYY-MM-DD HH:MM:SS";
  list rows give `startDateRef` "Sep 29, 2026" (human date, no ISO).
- Adapter kind recommendation: NEW class `isolvedhire` (template param:
  `{subdomain}.isolvedhire.com` + domainId discovery via the shell's
  `#job_listings` mounting script — regex `jobListingId|domainId : (\d+)`).
  NOTE on egress: from this sandbox the host WAF-flakes (503/timeout) on HTML+API
  paths with query strings; the Tier-2 Netlify edge scraper (US egress) returns
  200 consistently. The direct sandbox call DID succeed for the bare endpoint and
  JS chunks, so the flakiness is WAF-rate/geo, not a hard block.
- Raw evidence: samples/pharmaron_isolved.html (shell, 18KB),
  samples/pharmaron_isolved_core_jobs.json (full 32-job API response, 24KB),
  samples/pharmaron_isolved_detail.html (SSR detail, 33KB).
