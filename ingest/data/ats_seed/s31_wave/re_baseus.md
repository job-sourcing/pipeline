# re_baseus — Baseus (charging accessories, Shenzhen; US store)
VERDICT: PARK (no careers page on baseus.com — sitemap 97 pages with zero career hits; /pages/careers 404; census "Shopify-style careers page" is stale/gone)
- Company identity evidence: `https://www.baseus.com/` (200, tier-0) is the
  Baseus official Shopify storefront (Baseus Blade 2 power banks, recalls
  pages, mc2-grammy-story). The real Baseus consumer site.
- List URL: none. Evidence (live today, tier-0):
  * /sitemap.xml → 7 child maps (default locale only);
    sitemap_pages_1.xml → 97 page URLs; career/job/hiring/join/recruit/talent
    grep → 0 hits (closest: pages/about-us, pages/affiliate-program,
    product/recall pages).
  * Direct path probes: /pages/careers, /pages/join-us, /pages/joinus,
    /pages/we-are-hiring, /pages/hiring, /pages/jobs,
    /pages/job-opportunities, /en/pages/careers → all 404/redirect-to-404.
    /pages/about-us → 200 (checked in sitemap; no careers link in it).
  * Homepage (544KB) footer grep for career/job/hiring/join → 0 links.
  * DNS: jobs.baseus.com / careers.baseus.com do not resolve;
    baseus.jobs.personio.com → 404 (no Personio account);
    baseus.bamboohr.com / baseus.recruitee.com → wildcard redirects (no
    accounts).
- List mechanics / row shape / fields / pagination / detail: N/A.
- Counts: 0 rows.
- Sample titles/locations: none on this domain (US hiring = LinkedIn only).
- postedOn source: none.
- Adapter kind recommendation: none — PARK.
- Raw evidence: negative probes only (documented above).
