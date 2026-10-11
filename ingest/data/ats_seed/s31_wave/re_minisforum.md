# re_minisforum — Minisforum (mini-PCs; US store)
VERDICT: PARK (no careers page on minisforum.com — /pages/careers real 404, sitemap 41 pages with zero career hits; the census "careers page Shopify 429" was rate-limiting on a 404, not a real page)
- Company identity evidence: `https://www.minisforum.com/` (200, tier-0, 920KB)
  is Minisforum's Shopify storefront (mini-PC products, about-minisforum,
  brand-story pages). The real Minisforum consumer site.
- List URL: none. Evidence (live today, tier-0):
  * /sitemap.xml → 64 child maps; sitemap_pages_1.xml → 41 page URLs;
    career/job/hiring/join/recruit grep → 0 hits (closest: about-us,
    about-minisforum, brand-story, become-a-dealer, contact).
  * /pages/careers → 404 (real Shopify 404, 400KB — fetched twice with 4s
    spacing; the earlier 429s were rate-limit noise, the underlying state is
    404).
  * Homepage footer grep for career/job/hiring/join → 0 links.
  * DNS: jobs.minisforum.com / careers.minisforum.com do not resolve;
    minisforum.bamboohr.com → wildcard redirect to bamboohr.com.
- List mechanics / row shape / fields / pagination / detail: N/A.
- Counts: 0 rows.
- Sample titles/locations: none on this domain.
- postedOn source: none.
- Adapter kind recommendation: none — PARK.
- Raw evidence: negative probes only (documented above).
