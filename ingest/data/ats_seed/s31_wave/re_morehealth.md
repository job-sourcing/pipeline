# re_morehealth — More Health
VERDICT: PARK (no careers page / no own board — /careers serves the homepage)
- Company identity evidence: `<title>MORE Health</title>`; homepage copy "MORE Health, Inc." — "Silicon Valley (San Mateo, CA) – International Headquarters" with Boston/Houston/New York + Beijing/Shanghai/Chengdu/Guangzhou offices, "cross-border telemedicine", "6.2 million members across six continents", footer "© 2026 MORE Health, Inc. All rights reserved." Matches More Health (US digital health / second-opinion telemedicine). No homonym conflict.
- List URL: `https://morehealth.com/careers` — returns 200 but serves the HOMEPAGE content (Next.js site with /en|/es|/zh routes; `/en/careers` is a hard **404** "This page could not be found."). The 200 on /careers is the SPA fallback to the homepage — it is NOT a careers page.
- List mechanics: Next.js SSR site — no job content anywhere: the only "job" hit on the page is a contact-form i18n label (`"jobFunction":"Job Function"` in the embedded i18n JSON). No careers/jobs route exists (checked /en/careers 404, /careers → homepage; our-story page has no ATS links).
- Row shape: none.
- Fields per row: N/A.
- Pagination: N/A.
- Detail URL pattern: N/A.
- Detail shape: N/A.
- Counts: total rows = 0. US rows = 0; non-CN rows = 0; CN rows = 0.
- Sample titles (>= 6): none exist.
- Sample locations (>= 6): none exist (company locations on homepage: San Mateo CA HQ, Boston MA, Houston TX, New York NY, Beijing, Shanghai, Chengdu, Guangzhou — corporate offices, not jobs).
- postedOn source: N/A.
- Adapter kind recommendation: none — PARK. No ATS links (no greenhouse/lever/workable/ashby/bamboo references; web search found no More Health job board either; hiring appears to run via LinkedIn/social channels). Re-check occasionally.
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/morehealth.html (/careers → homepage content).
