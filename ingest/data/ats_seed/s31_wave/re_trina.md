# re_trina — Trina Solar US (Trinasolar; solar modules + TrinaStorage BESS)
VERDICT: WIRE-READY (board = Personio public XML feed — tier-0 crawlable, no stealth needed; 25 rows, 0 US, 25 non-CN → geo_scope=non_cn)
- Company identity evidence: careers WP page title "Trinasolar Careers"; Personio
  board title "Jobs at Trina Solar" (trina-solar-1.jobs.personio.com); XML
  `<subcompany>` values like "TrinaStorage GmbH - Germany". This is the real
  Trina Solar global board. (US hiring surfaces on LinkedIn only — see counts.)
- List URL (wire-able, tier-0): `https://trina-solar-1.jobs.personio.com/xml`
  (public Personio XML feed; 200 from HK egress, no auth, no CF).
  Discovery chain: `https://www.trinasolar.com/` → `/us/` (US region site, WP) →
  its "Join Us >" footer link points to LinkedIn, BUT
  `https://careers.trinasolar.com/` (CF-JS-walled) embeds the Personio job list
  — confirmed by firecrawl scrape of careers.trinasolar.com.
  NOTE: `https://www.trinasolar.com/us-en/careers` (the task's 404 URL) is
  simply a dead path: the US site lives at `/us/`, and unknown paths under
  `/us/` 301 back to `/us/` home (verified: /us/careers → 200 = homepage copy).
  careers.trinasolar.com is behind a "One moment, please..." JS challenge from
  tier-0 AND Netlify US egress — firecrawl passes it.
- List mechanics: XML API (Personio workzag-jobs feed) — the gold transport.
  curl_cffi GET /xml → 200, 190KB, `text/xml`.
- Row shape: `<workzag-jobs><position>...</position></workzag-jobs>`; one
  `<position>` element per job. Verbatim sample row fragment:
  ```xml
  <position>
      <id>2435428</id>
      <subcompany>TrinaStorage GmbH - Germany</subcompany>
      <office>Germany - Home Based</office>
      <department>Engineer &amp; Tech Service</department>
      <recruitingCategory>Standard Process</recruitingCategory>
      <name>Field Service Engineer (Utility BESS)</name>
      <jobDescriptions>
          <jobDescription><name>Responsibilities</name><value>...HTML...</value></jobDescription>
      </jobDescriptions>
      <employmentType>permanent</employmentType>
      <seniority>...</seniority>
      <schedule>full-time</schedule>
      <yearsOfExperience>...</yearsOfExperience>
      <occupation>...</occupation>
      <occupationCategory>...</occupationCategory>
      <createdAt>2025-11-21T09:30:07+00:00</createdAt>
  </position>
  ```
- Fields per row: title = `<name>`; location = `<office>` (plus `<subcompany>`
  for legal entity); department = `<department>`; date = `<createdAt>` (ISO
  8601 with timezone); detail URL = `https://trina-solar-1.jobs.personio.com/job/{id}`;
  employmentType/schedule = `<employmentType>`/`<schedule>`.
- Pagination: N/A — the XML feed returns ALL positions in one document (25
  `<position>` elements counted via ElementTree; verified full parse, no paging
  params exist for /xml).
- Detail URL pattern: `https://trina-solar-1.jobs.personio.com/job/{id}`
  (verified live tier-0: /job/2435428 → 200, title "Field Service Engineer
  (Utility BESS) | Jobs at Trina Solar").
- Detail shape: SSR Personio page (title tag = job name; JD sections rendered
  server-side). The XML already contains the full JD HTML in
  `jobDescriptions/jobDescription/value` — no need to fetch details.
- Counts: 25 rows; US 0 (no office/subcompany matches US|USA|United States);
  non-CN 25 (Germany, Italy, Poland, Portugal, Spain, Australia, Brazil, Chile,
  Mexico, UK, South Africa, France); CN 0. US hiring (per task LCA note) runs
  through the LinkedIn company page
  (linkedin.com/jobs/search/?f_C=696485) linked as "Join Us" from
  www.trinasolar.com/us/ — not wire-able, LinkedIn-only.
- Sample titles (6 of 25, verbatim): "Field Service Engineer (Utility BESS)",
  "Grid Connection Manager", "Senior MA&Commercial Manager",
  "LAC Sales Management Analyst", "Quality & Process Specialist LAC",
  "Head of EHS – UK & Europe".
- Sample locations (verbatim): "Germany - Home Based", "Australia - Melbourne",
  "Brazil - São Paulo", "Chile", "Mexico - Home Based",
  "UK (England & Wales) - Home Based", "South Africa - Johannesburg".
- postedOn source: YES — `<createdAt>` exact ISO 8601 (e.g.
  2026-05-05T14:38:40+00:00).
- Adapter kind recommendation: existing class `personio_xml` (generic Personio
  public-XML adapter, hostname trina-solar-1; if no personio class exists yet,
  new class `personio_xml` — this feed is the cheapest possible contract).
- Raw evidence: samples/trina_personio.xml (191KB full feed),
  samples/trina_careers_firecrawl.html (269KB rendered careers WP with the
  embedded Personio list).
