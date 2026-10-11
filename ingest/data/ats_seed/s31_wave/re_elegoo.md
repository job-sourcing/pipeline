# re_elegoo — Elegoo (3D printers/resins; US distribution)
VERDICT: PARK (no careers page exists anywhere on elegoo.com — sitemap, direct paths, and DNS all negative; no board to wire)
- Company identity evidence: `https://www.elegoo.com/pages/about` (200, tier-0)
  = Elegoo official storefront ("About ELEGOO | 3D Printers & 3D Printing
  Accessories", Centauri Carbon 2 / Jupiter 2 / Saturn 4 Ultra products,
  elegoo.com country selectors). The real Elegoo consumer site.
- List URL: none. Evidence (live today, tier-0):
  * /sitemap.xml → 13 child maps; sitemap_pages_1.xml → 69 page URLs;
    career/job/hiring/join/recruit grep → 0 hits (closest: pages/about,
    pages/contact-us, affiliate page).
  * Direct path probes: /pages/careers, /pages/join-us, /pages/joinus,
    /pages/we-are-hiring, /pages/hiring, /pages/jobs, /pages/job-opportunities,
    /pages/our-team, /pages/about-us, /en/pages/careers → all 404/no-career.
  * Homepage (1MB) footer grep for career/job/hiring/join → 0 links.
  * DNS: jobs.elegoo.com, careers.elegoo.com do not resolve;
    elegoo.bamboohr.com + elegoo.recruitee.com are wildcard redirects
    (bamboohr.com / recruitee.com homepages — no accounts);
    jobs.elegoo.eu resolves but serves a 403 nginx stub from HK and a
    fetch-failure from Netlify US (parked/placeholder, not a board).
  * /pages/about content: no hiring section, no mailto.
- List mechanics / row shape / fields / pagination / detail: N/A.
- Counts: 0 rows.
- Sample titles/locations: none on this domain (US hiring = LinkedIn).
- postedOn source: none.
- Adapter kind recommendation: none — PARK.
- Raw evidence: statuses + sitemap greps documented above (nothing worth
  saving — all negative probes).
