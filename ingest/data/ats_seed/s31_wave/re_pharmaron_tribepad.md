# re_pharmaron_tribepad — Pharmaron (global board, Tribepad ATS)
VERDICT: WIRE-READY (UK/EU board — 0 US rows; geo_scope=non_cn candidate, NOT a US wire)
- Company identity evidence: 404 page comment `<!-- This site is powered by tribepad -->`
  (vendor = Tribepad); search-page `<h1>Jobs and careers with Pharmaron</h1>`; detail
  JSON-LD `"hiringOrganization":{"name":"Pharmaron"}`; JD text "About Pharmaron …
  global contract research and manufacturing organization (CRO/CDMO) … across 28
  locations in the UK, US, China, and Singapore". Confirmed Pharmaron global.
- List URL: `https://careerssearch.pharmaron.com/jobs/search` (= page 1);
  paginated form `https://careerssearch.pharmaron.com/jobs/search/{countryId}/{page}`
  where countryId `-1` = all countries, `234` = United States (ids come from the
  `<select name="country">` options in the search form).
- List mechanics: SSR HTML (server-rendered; no XHR). This is NOT the Radancy
  dialect the repo's RadancyAdapter handles (`/en_US/careers/SearchJobs/` with
  `jobOffset`) — Tribepad is a different ATS with a different contract → new class.
- Row shape: `<li>` blocks inside the results `<ul>`; one row per
  `<a href="https://careerssearch.pharmaron.com/jobs/job/{Title-Slug}/{id}"
  class="in-app" itemprop="url">`. Verbatim fragment:
  ```html
  <li>
    <a href="https://careerssearch.pharmaron.com/jobs/job/Principal-Scientist-PKPD-Leader/777" class="in-app" itemprop="url">
      <div class="job-list-details">
        <h3><span class="job-list-title">Principal Scientist - PK/PD Leader</span></h3>
        <p itemprop='jobLocation' itemscope itemtype='http://schema.org/Place'>
          <span itemprop='address'>Hoddesdon, United Kingdom</span></p>
        <p itemprop='employmentType'><i class='far fa-clock'></i> Permanent Contract</p>
        <p>Posted on <span itemprop='datePosted'> 9 October, 2026 </span></p>
  ```
- Fields per row: title → `<span class="job-list-title">(.*?)</span>`;
  location → `itemprop='address'>([^<]+)<`; employmentType →
  `itemprop='employmentType'>\s*<i[^>]*></i>\s*([^<]+)`; date →
  `Posted on <span itemprop='datePosted'>\s*(.*?)\s*</span>` ("9 October, 2026");
  detail URL → the anchor href (id = trailing `/{id}`).
- Pagination: VERIFIED LIVE — `/jobs/search/-1/1` → 10 rows, `/jobs/search/-1/2`
  → 10 DIFFERENT rows (Director-Senior-Director-Computational-Chemistry/765 etc.),
  page 5 → 5 rows, page 6 → 0 rows + "no jobs matching your search criteria"
  empty marker. 5 pages total, 45 unique jobs. Page size fixed at 10.
  Country filter: `/jobs/search/234/1` (US) → 0 rows, live-verified empty marker.
- Detail URL pattern: `https://careerssearch.pharmaron.com/jobs/job/{Title-Slug}/{id}`
- Detail shape: SSR HTML with `application/ld+json` JobPosting:
  `title`, `description` (plain text JD — NOTE: contains unescaped quotes, so a
  tolerant parser or manual field regex is needed), `"datePosted" : "2026-10-09"`
  (EXACT ISO), `"validThrough" : "2026-11-30"`, `hiringOrganization.name`
  "Pharmaron", `jobLocation.address` (addressLocality, postalCode,
  addressCountry "United Kingdom"). Employment type also in list-row microdata.
- Counts: total 45 rows; US rows 0 (country filter 234 → "no jobs matching");
  CN rows 1 ("Ningbo, Flexible"); non-CN 44 (Hoddesdon ×30, Liverpool ×4,
  Cramlington ×3, Rushden ×3, Cardiff ×2, "Remote, Flexible" ×1,
  "Remote, United Kingdom" ×1).
- Sample titles (>= 6): "Principal Scientist - PK/PD Leader"; "Technical
  Specialist – Bioanalysis & Biotransformation"; "Undergraduate Industrial
  Placement Student- Metabolism"; "Associate Director / Director, Medicinal
  Chemistry (New Modalities)"; "Senior Disease Expert - Oncology"; "Analytical
  Chemist"; "Director - DMPK"; "Study Manager - Accelerator Mass Spectrometry";
  "Apprentice Process Technician".
- Sample locations (>= 6): Hoddesdon, United Kingdom; Liverpool, United Kingdom;
  Cramlington, United Kingdom; Rushden, United Kingdom; Cardiff, United Kingdom;
  Remote, Flexible; Ningbo, Flexible.
- postedOn source: detail JSON-LD `datePosted` ("2026-10-09", exact ISO);
  list rows carry "9 October, 2026" (human format, day-month-year).
- Adapter kind recommendation: NEW class `tribepad` — the RadancyAdapter's
  contract (`/en_US/careers/SearchJobs/?jobRecordsPerPage=10&jobOffset=` +
  `JobDetail/{slug}/{id}` hrefs) does NOT match (different path scheme
  `/jobs/search/{country}/{page}`, different row markup, no jobOffset).
  NOTE: Pharmaron's US roles are all on the isolved board (re_pharmaron_isolved);
  this board is the UK/EU/SG wing — worth keeping under geo_scope=non_cn.
- Raw evidence: samples/pharmaron_tribepad.html (home, 45KB),
  samples/pharmaron_tribepad_search.html (page 1), samples/pharmaron_tribepad_search_p2.html
  (page 2, live pagination proof), samples/pharmaron_tribepad_detail.html (detail, 33KB).
