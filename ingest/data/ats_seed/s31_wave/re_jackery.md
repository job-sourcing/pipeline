# re_jackery — Jackery (portable power stations/solar; US HQ San Jose/CA)
VERDICT: PARK (no careers page on jackery.com — /pages/careers real 404, sitemap has zero career pages, no careers link in site nav; hiring runs on LinkedIn/ZipRecruiter/Indeed)
- Company identity evidence: `https://www.jackery.com/` is Jackery's Shopify
  storefront (title "Jackery: ...", CES-2026 pages, american-red-cross page);
  LinkedIn result "Jackery: Jobs | Jobs at Jackery · Senior Website Sales
  Representative · United States" confirms the company. LCA-active (per task).
- List URL: none. Evidence (all fetched live, tier-0):
  * `https://www.jackery.com/pages/careers` → 404 (1.5MB Shopify 404 page —
    the earlier 503 was rate-limiting; after retry it is a real 404).
  * `https://www.jackery.com/sitemap.xml` → 200 (sitemapindex, 39 child maps).
  * `sitemap_pages_1.xml` → 219 page URLs; career/job/hiring/join grep → only
    `pages/join-jackery-stories` (customer stories) and `pages/about-us`.
    NO careers page exists.
  * The 404 page's own footer has zero career/job links.
  * DNS: jobs.jackery.com and careers.jackery.com DO NOT RESOLVE;
    jackery.bamboohr.com is a wildcard redirect to bamboohr.com (no account).
- List mechanics: none — no jobs page exists on the domain.
- Row shape / fields / pagination / detail: N/A.
- Counts: 0 rows. (Web search: "Senior Website Sales Representative · United
  States · 6 days ago" on LinkedIn; Solar Batteries Sales Rep on Indeed;
  Jackery Senior Sales Rep on ZipRecruiter — aggregator corpus only.)
- Sample titles/locations: none on this domain.
- postedOn source: none.
- Adapter kind recommendation: none — PARK. LinkedIn-only US hiring
  (linkedin.com/company/jackery jobs tab).
- Raw evidence: samples/jackery_sitemap_pages.xml (38KB, 219 URLs — proof no
  careers page exists).
