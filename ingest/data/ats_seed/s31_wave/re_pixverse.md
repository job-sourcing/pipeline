# re_pixverse — PixVerse (AI video, ex-ByteDance founder)
VERDICT: PARK (no job rows — careers page is marketing; all CTAs link to LinkedIn)
- Company identity evidence: og:site_name "PixVerse | Create Amazing AI Videos from
  Text & Photos..."; `<title>Join Us | PixVerse</title>`; page copy "If you are
  passionate about Computer Vision, Large Models, and Artificial Intelligence,
  come join the PixVerse team... era of AI video generation". Matches the
  AIsphere/PixVerse AI-video company (Astro + React islands site).
- List URL: `https://pixverse.ai/en/careers` (+13 locale variants).
- List mechanics: SSR Astro page with a `CareerPage` astro-island; island props
  (decoded, in `astro-island[component-export="CareerPage"]` `props=` attr) carry
  ONLY 5 department cards — NO jobs:
  ```json
  {"buttonText":"Learn More","buttonLink":"https://www.linkedin.com/company/pixverse/",
   "departments":[{"title":"Engineering & Tech"},{"title":"Product & Design"},
    {"title":"Growth & Operations"},{"title":"Business Services"},
    {"title":"Functional Departments"}],"viewOpenings":"View openings"}
  ```
  The component JS (`/_astro/CareerPage.DqssTrPY.js`) renders each department
  card as `<a href={buttonLink} target="_blank">` → i.e. every "View openings"
  CTA opens the LinkedIn company page. No jobs API exists (patchright render +
  click + network capture: only analytics/speculationrules requests).
- Row shape / fields / pagination / detail: N/A (0 rows).
- Counts: 0 rows (US 0, non-CN 0, CN 0). Application channel = LinkedIn
  company page (linkedin.com/company/pixverse).
- Sample titles/locations: none (department cards only: `Engineering & Tech`,
  `Product & Design`, `Growth & Operations`, `Business Services`,
  `Functional Departments`).
- postedOn source: none.
- Adapter kind recommendation: none — PARK. Revisit if they attach an ATS.
- Raw evidence: samples/pixverse.html (SSR page), samples/pixverse_rendered.html
  (rendered after click; network log above), /tmp CareerPage.js analysis quoted.
