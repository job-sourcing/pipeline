# re_hutchmed — HUTCHMED (和黄医药)
VERDICT: CN-ONLY (own MokaHR board exists with 92 jobs — ALL CN/HK; "Global Job Openings" outsourced to LinkedIn; portal 403s from sandbox egress, firecrawl renders it)
- Company identity evidence: `<title>HUTCHMED - Career Opportunities</title>`; page copy "HUTCHMED aims to become a major pharmaceutical company developing innovative drugs for both the Chinese and global markets"; MokaHR portal header "About Us — We innovate for patients. HUTCHMED aims to become a major pharmaceutical company…"; footer "© 2026 HUTCHMED © HUTCHMED (CHINA) LIMITED"; Nasdaq/HKEX/AIM tickers in header. This is HUTCHMED (Hutchison China MediTech). No homonym conflict.
- List URL: corporate page `https://www.hutch-med.com/career-opportunities/` (200 only with verify=False — TLS cert fails from this egress). It links TWO boards:
  - "China Job Openings" → `https://recruit.hutch-med.com/en` (302) → `https://recruit.hutch-med.com/social-recruitment/hutchmed/140975?locale=en-US` (MokaHR SPA, "Experienced Hires" portal)
  - "Global Job Openings" → `https://www.linkedin.com/company/hutchmed/jobs` (LinkedIn outlink only — no global board of their own)
  - resume mailto: `Recruit@hutch-med.com`
- List mechanics: SPA (MokaHR — `public-cdn.mokahr.com` assets, hash routes `#/jobs`, `#/job/{uuid}`). Direct access from the sandbox egress → **403 Forbidden** (curl_cffi AND patchright stealth browser both 403; tier-2 netlify status 0/empty). **Tier-3 firecrawl (US egress) renders it fine** — use firecrawl with `waitFor: 12000` for the SPA.
- Row shape: (rendered DOM/markdown) each job is a link block:
  ```markdown
  [急QA Director, Supplier Management
  Posted on 2026-09-23
  Full-time
  |
  Quality
  |
  Shanghai
  Main Responsibilities1.API CMO Management...
  Apply Now](https://recruit.hutch-med.com/social-recruitment/hutchmed/140975?locale=en-US#/job/21afcaf8-bed8-4649-a11f-cdf0749f3857)
  ```
  DOM structure per job card: title heading, `Posted on {date}`, meta triple (Employment type | Function | Location), JD text, "Apply Now" link to `#/job/{uuid}`.
- Fields per row: title = card heading (many prefixed `急` = urgent); date = `Posted on YYYY-MM-DD`; location = 3rd meta token (`Shanghai`, `Hangzhou, Zhejiang`, `Wuhan, Hubei`, …); function = 2nd meta token (`Quality`, `Sales & Marketing`, …); employment type = 1st (`Full-time`, `Internship`); detail URL = `#/job/{uuid}` hash route.
- Pagination: SPA virtual scroll — filter banner "Selected 0 condition(s) | 92 result(s)"; 30 job cards rendered in the markdown capture; category tiles sum to 92 (Drug Discovery 1, Pharmaceutical Sciences 3, Clinical Development 4, Manufacturing 3, Quality 7, Sales & Marketing 66, Enabling 8). No `page=` URL param (hash routing).
- Detail URL pattern: `https://recruit.hutch-med.com/social-recruitment/hutchmed/140975?locale=en-US#/job/{uuid}`.
- Detail shape: same SPA — job card includes full JD inline in the list render (no separate fetch needed for the 30 rendered rows).
- Counts: total rows = 92. US rows = 0; non-CN rows = 0 (all locations CN cities; `Hongkong` appears in the work-location facet — treat HK as CN per project convention); CN rows = 92. Global/US jobs are LinkedIn-only.
- Sample titles (>= 6): `急合规经理` (Compliance Manager); `急QA Director, Supplier Management`; `急Senior Scientist, Analystical Physiochem`; `急小分子实验室运营支持专员`; `急AI实习生（应用与落地）`; `急（高级）市场经理`; `急区域产品经理（上海）`; `急血液线-地区经理（杭州）`.
- Sample locations (>= 6): `Shanghai`; `Hangzhou, Zhejiang`; `Wuhan, Hubei`; `Beijing`; `Guangzhou, Guangdong`; `Nanjing, Jiangsu`; `Chengdu, Sichuan`; `Tianjin`.
- postedOn source: yes — `Posted on YYYY-MM-DD` on each card (ISO-ish date, e.g. 2026-10-09).
- Adapter kind recommendation: none now (CN-ONLY). If global roles appear later: MokaHR SPA class via firecrawl render (or find MokaHR JSON API from a US egress browser session — not reachable from this sandbox).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/hutchmed.html (corporate page), hutchmed_recruit.html (firecrawl SPA HTML), hutchmed_recruit.md (portal home markdown), hutchmed_recruit_jobs.md (#/jobs list markdown, 92 results).
