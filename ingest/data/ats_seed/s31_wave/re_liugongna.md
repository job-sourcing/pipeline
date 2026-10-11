# re_liugongna — LiuGong North America (construction machinery; US HQ)
VERDICT: PARK (jobs-and-careers page deleted — real 404; no careers link anywhere in the site nav; LinkedIn only)
- Company identity evidence: firecrawl of `https://www.liugongna.com/` (200,
  title "LiuGong North America | Heavy Equipment | Excavators, Wheel Loaders",
  HubSpot CMS with LinkedIn link linkedin.com/company/liugong-north-america) —
  the real LiuGong NA site.
- List URL: none. `https://www.liugongna.com/jobs-and-careers/` → REAL 404 via
  firecrawl ("Page Not Found" — WAF passed, page genuinely gone). Full nav
  crawl of the homepage found ZERO career/job links (nav = products, company,
  become-a-dealer, financing, sourcewell, news, contact-us, legal-notice only).
  Sitemap.xml is also CF-walled to tier-0/Netlify (403) so it can't be used as
  evidence, but the firecrawl-rendered homepage + real 404 are conclusive.
- List mechanics: none — no jobs page exists on the domain.
- Row shape / fields / pagination / detail: N/A.
- Counts: 0 rows.
- Sample titles/locations: none.
- postedOn source: none.
- Adapter kind recommendation: none — PARK. Careers moved to
  linkedin.com/company/liugong-north-america (not wire-able).
- Raw evidence: firecrawl 404 page (25KB) + homepage (29KB) inspected live;
  statuses documented above (not saved — nav noise only).
