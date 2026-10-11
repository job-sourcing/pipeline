# re_topalliancebio — TopAlliance BioSciences (Junshi Biosciences' US arm; LOQTORZI™/toripalimab)
VERDICT: PARK (board is live WP at /careers but EMPTY — "Current Openings: Not available"; site is JS-WAF'd from tier-0/Netlify, firecrawl passes)
- Company identity evidence: firecrawl of `https://www.topalliancebio.com/careers`
  → 200, title "Careers – Top Alliance"; nav shows "LOQTORZI™" (toripalimab,
  Junshi's US-approved drug), "TOPALLIANCE BIOSCIENCES - 2026. All rights
  reserved.", company.info@topalliancebio.com. Matches Junshi's US arm.
- List URL: `https://www.topalliancebio.com/careers/` — the path was RIGHT all
  along; the tier-0 "200 with 0 links" was actually the WAF challenge page
  ("One moment, please... Please wait while your request is being verified..."
  with an obfuscated JS challenge — same challenge class as
  careers.trinasolar.com). Netlify US fetch also fails (status 0 / fetch
  failed). Firecrawl (stealth, JS render) returns the REAL page.
- List mechanics: SSR HTML (WordPress) — but transport must be stealth
  (firecrawl-class) because of the JS WAF.
- Row shape: N/A — the real page renders:
  "Come Join Our Dynamic Team / Current Openings / Not available."
  (0 jobs today; the openings section is a hard-coded "Not available" string,
  not a broken widget).
- Fields per row: N/A (no rows).
- Pagination: N/A.
- Detail URL pattern: N/A.
- Detail shape: N/A.
- Counts: 0 rows.
- Sample titles/locations: none.
- postedOn source: none.
- Adapter kind recommendation: none — PARK today (empty board). If it fills,
  it's a plain WP page scrape behind a JS WAF → firecrawl transport; contact
  company.info@topalliancebio.com.
- Raw evidence: samples/topalliancebio_careers_firecrawl.html (35.8KB real DOM).
