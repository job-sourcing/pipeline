# re_xreal — XREAL (AR glasses, ex-Magic Leap/BitVision lineage)
VERDICT: PARK (no job rows exist on the board — careers page is marketing-only + talent@xreal.com email)
- Company identity evidence: `<title>XREAL Careers | Join Us</title>`, og:site_name
  "XREAL"; page copy: "XREAL is a global technology company focused on consumer AR
  and spatial computing... from the X1 spatial computing chip to the X-Prism
  optical engine"; "the #1 position in global AR glasses sales revenue for four
  consecutive years (2022–2025), per iResearch". Matches the AR-glasses maker.
- List URL: `https://www.xreal.com/career` (and 10+ locale variants
  /us/career, /cn/career, /de/career, ... — all identical marketing content).
- List mechanics: SSR HTML (Next.js app router, 13 `self.__next_f.push` chunks),
  but the next_f payload contains ONLY i18n strings — NO jobs array (searched
  for position/job/recruit/location arrays; the only "position" hits are
  face-scan UI strings like `position_error_title`).
- Row shape: N/A — there are no job rows. The page renders 8 career *category*
  cards (`R&D / Engineering`, `Product / Operations`, `Sales / Marketing /
  eCommerce`, `Video / Design`, `HR / Finance / Legal`, `Supply Chain`) as
  images (`resource.xreal.com/.../career_card01..04.png`), not job postings.
- Fields per row: N/A. Contact: `talent@xreal.com` ("Contact Our Team").
- Pagination: N/A (no rows). Patchright render (tier 6, live 2026-10-11)
  captured all XHR: only sentry, secureprivacy.ai consent, tiktok/google
  analytics, `api.xreal.com/v1/data-web-domain` — no jobs API exists.
- Detail URL pattern: none (sitemap-0.xml has only /{locale}/career pages, no
  per-job URLs; `/career/{slug}` links = 0 on every locale).
- Detail shape: N/A.
- Counts: total rows 0; US 0; non-CN 0; CN 0. (Census said CN roles existed
  previously — today's live site shows none anywhere, including /cn/career
  "XREAL 招聘｜加入我们" which is the same marketing shell.)
- Sample titles: none exist (verbatim categories: `R&D / Engineering`,
  `Product / Operations`, `Sales / Marketing / eCommerce`, `Video / Design`,
  `HR / Finance / Legal`, `Supply Chain`).
- Sample locations: none exist.
- postedOn source: none.
- Adapter kind recommendation: none — PARK. Revisit only if a jobs API appears
  (watch `api.xreal.com`); today the only application channel is
  `talent@xreal.com`.
- Raw evidence: samples/xreal.html (static SSR), samples/xreal_rendered.html
  (patchright-rendered DOM; network log in this doc above).
