# re_catl — CATL (Contemporary Amperex Technology Co., Limited)
VERDICT: WIRE-READY
- Company identity evidence: `<title>View all jobs - CATL</title>`; footer links to
  `https://www.linkedin.com/company/contemporary-amperex-techn...`; job text: "CATL is
  building its second European production base in Debrecen ... future of
  electromobility in Europe" (matches the world's #1 battery maker, Chinese HQ,
  plants in Arnstadt/Thuringia DE + Debrecen HU). Platform = SAP RMK / Career
  Site Builder ("j2w": CSS at `/platform/css/j2w/...`, `rmkcdn.successfactors.com`).
- List URL: `https://www.catl-career.com/search/?q=&sortColumn=referencedate&sortDirection=desc`
  (whole-site, 58 rows, 3 pages). English-portal variant: `/CATH/search/?q=` (36 rows).
  The "viewalljobs" URL only renders a search form + 16 category links (its
  "Results 1–16 of 16" label counts CATEGORIES, not jobs — do not treat as rows).
- List mechanics: SSR HTML (table `#searchresults`, 20 rows/page).
- Row shape: `table#searchresults tr.data-row`; verbatim row fragment (trimmed):
  ```html
  <td class="colTitle" headers="hdrTitle">
    <span class="jobTitle hidden-phone">
      <a href="/CATH/job/Debrecen-IT-Service-Enigneer/1338740455/" class="jobTitle-link">IT Service Enigneer</a>
    </span>
    ... <span class="jobLocation">Debrecen, Hungary</span> ...
  </td>
  <td class="colLocation hidden-phone" headers="hdrLocation"><span class="jobLocation">Debrecen, Hungary</span></td>
  <td class="colDate hidden-phone" nowrap="nowrap" headers="hdrDate"><span class="jobDate">Oct 11, 2026</span></td>
  ```
  Regex for one row: `<tr class="data-row">.*?</tr>` (DOTALL). NOTE each row's
  title/location/date appear TWICE (hidden-phone + visible-phone copies) — dedupe.
- Fields per row:
  - title: `href="(/[^"]*/job/[^"]+)" class="jobTitle-link">([^<]+)<` (group 2)
  - detail URL: group 1, prefix `https://www.catl-career.com`
  - location: `<td class="colLocation[^"]*"[^>]*>\s*<span class="jobLocation">\s*([^<]+?)\s*</span>`
  - date: `<span class="jobDate">\s*([A-Z][a-z]{2} [0-9]{1,2}, [0-9]{4})\s*</span>`
  - department: not in list rows (category pages give it implicitly)
- Pagination: `&startrow=20` / `&startrow=40` query param (20 rows/page). VERIFIED
  LIVE: EN `/CATH/search/?...&startrow=20` → label `Results 21 – 36 of 36` (16
  rows = page 2 of 2); whole-site `/search/?...&startrow=40` → label
  `Results 41 – 58 of 58` (9 rows = page 3 of 3). TRAP: `/CATH/search/20/?q=`
  (path-offset instead of `startrow`) is SILENTLY IGNORED — returns page 1
  again. Category pages DO use path-offset: `/CATH/go/Full-Time-Jobs/9282755/20/?...`
  verified live → `Results 21 – 34 of 34` (14 rows).
- Detail URL pattern: `https://www.catl-career.com{path}` e.g.
  `/CATH/job/Debrecen-IT-Service-Enigneer/1338740455/` (EN portal) or
  `/job/Arnstadt-Produktionsmitarbeiter-.../846469455/` (DE portal).
- Detail shape: schema.org microdata + RMK tokens:
  - title: `<h1><span itemprop="title" data-careersite-propertyid="title">IT Service Enigneer</span></h1>`
  - date: `data-careersite-propertyid="date">Oct 11, 2026`
  - requisition: `data-careersite-propertyid="customfield4">2768` (job code)
  - employment type: `data-careersite-propertyid="department">Permanent`
  - location: `itemprop="jobLocation"` → `<meta itemprop="streetAddress" content="Debrecen, Hungary">`
  - `<title>IT Service Enigneer Job Details | CATL</title>`
- Counts: whole-site `/search/` = 58 rows (3 pages); EN `/CATH/search/` = 36 rows;
  Full-Time category = 34; Intern-Positions = 2; DE-portal rows = 22 (58-36).
  US rows: 0. non-CN rows: 58 (all DE+HU). CN rows: 0 on this portal (CN hiring is
  on a separate Moka/internal portal, not here).
- Sample titles (verbatim, from search p1-p3):
  1. IT Service Enigneer
  2. PQE technician
  3. Accounts Payable Accountant
  4. Chinese speaking IT System Engineer
  5. Warehouse Shift Leader
  6. Senior Engineer Customer Quality Assurance (m/f/d)
  7. Specialist Energymanagement (m/f/d) Facility
  8. Manager Finance and Accounting (m/f/d)
  9. Ausbildung als Mechatroniker/in
  10. Finance Intern
- Sample locations (verbatim): `Debrecen, Hungary` (dominant), `Arnstadt, Thuringia, DE`,
  `Customer Site, Thuringia, DE`, `Erfurt, Thuringia, DE`, `Magdeburg, Sachsen-Anhalt, DE`,
  `Munich, Bayern, DE`.
- postedOn source: list rows carry `<span class="jobDate">` text like `Oct 11, 2026`
  (non-ISO "MMM DD, YYYY" — convert); detail page `data-careersite-propertyid="date"`.
- Adapter kind recommendation: new class `rmk_j2w` (SAP RMK/Career Site Builder
  table layout; reusable for other RMK boards). Key params: path
  `/search/?q=&sortColumn=referencedate&sortDirection=desc`, paginate with
  `startrow` while `startrow < total` (total from `Results <b>x</b> of <b>N</b>`),
  20/page, stop when row count < 20.
- Raw evidence: samples/catl.html (viewalljobs landing), catl_fulltime_p1.html,
  catl_fulltime_p2.html (page-2 live), catl_intern.html, catl_search.html,
  catl_search_de.html, catl_search_en_p2.html (startrow=20 live),
  catl_search_de_p3.html (startrow=40 live), catl_search_p2.html (path-offset trap),
  catl_detail.html, catl_de_production.html (German content page).
