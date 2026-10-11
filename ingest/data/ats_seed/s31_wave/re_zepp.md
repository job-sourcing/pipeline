# re_zepp — Zepp Health (Amazfit; ex-Huami, NYSE: ZEPP)
VERDICT: PARK (no job rows on the corporate site — careers page only links out to LinkedIn)
- Company identity evidence: `<title>Careers</title>` at zepp.com; page copy
  "Careers at Zepp Health... mission of improving health, fitness, and
  well-being through technology"; footer `© 2026 Zepp INC.` + ICP filings
  `皖ICP备14009454号-8` (Huami/Zepp Anhui) and `京ICP备15043545号-2`; nav has
  Amazfit / Zepp Clarity / Investor Relations (NYSE-listed). Matches the
  Amazfit maker (ex-Huami).
- List URL: `https://www.zepp.com/careers` (Webflow site —
  `cdn.prod.website-files.com` assets, `w-layout-*` classes).
- List mechanics: SSR HTML, but ZERO job rows. The "open positions" claim
  resolves to a single outbound link: "Interested in Being Part of the Zepp
  Health Team? Explore Opportunities Here" →
  `https://www.linkedin.com/company/zepp-health/` (LinkedIn company page, not a
  wire-able board).
- Row shape: N/A (no rows). Fields: N/A.
- Pagination: N/A (no rows; no page 2). `/careers/jobs` and `/jobs` → 404;
  sitemap.xml → 404.
- Detail URL pattern: none.
- Detail shape: N/A.
- Counts: 0 rows (US 0, non-CN 0, CN 0). Census corroborates: feishu:zepp board
  live but EMPTY; this page adds nothing.
- Sample titles/locations: none exist.
- postedOn source: none.
- Adapter kind recommendation: none — PARK. Revisit if LinkedIn company page
  jobs become a supported source or a feishu/moka board appears with rows.
- Raw evidence: samples/zepp.html (live careers page).
