# re_atlascloud — Atlas Cloud (AI infrastructure / model-API hub)
VERDICT: PARK (no careers page exists — /careers 308-redirects to the homepage; no roles in any next_f payload)
- Company identity evidence: og:site_name "Atlas Cloud"; `<title>400+ AI Models,
  One API for Image, Video, Audio and Chat | Atlas Cloud</title>` — matches the
  Chinese-founded AI-inference/model-API company (the one whose hiring posts
  appear on V2EX per census). NOTE the t0 "200" on /careers was actually the
  redirect target homepage (t0 sample is 4.3MB with the homepage title).
- List URL: `https://atlascloud.ai/careers` → 308 → `https://www.atlascloud.ai/`
  (VERIFIED LIVE today with allow_redirects=False; also `/career` → 308 → `/`).
- List mechanics: none — Next.js marketing site (homepage has 14 next_f chunks,
  ~1.7MB payload) but ZERO job/role/career content:
  - searched payload for `career`, `job`, `position`, `hiring`, `role`,
    `join us`, `open position` → all 0 job hits (only CSS `stroke-linejoin`,
    "Join the Discord community", "Join the waitlist" CTA strings).
  - `/about` (2MB, 10 next_f chunks) also has 0 career/job/hiring hits;
    `talent` appears once in marketing copy ("The bottleneck is no longer
    talent or imagination").
- Row shape: N/A (no rows). Fields: N/A. Pagination: N/A.
- Detail URL pattern: none; `/jobs`, `/join-us` → 404.
- Counts: 0 rows (US 0, non-CN 0, CN 0). Hiring channel today = V2EX posts
  (per census) — not wire-able as a board.
- Sample titles/locations: none exist.
- postedOn source: none.
- Adapter kind recommendation: none — PARK. Watch for a careers path appearing
  (sitemap check would be the revisit probe).
- Raw evidence: samples/atlascloud.html (redirect-target homepage, truncated
  300KB), samples/atlascloud_about.html (about page, truncated).
