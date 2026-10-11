# re_sfus — SF Express International (US entity, kex-sf.com emails)
VERDICT: WIRE-READY
- Company identity evidence: `og:site_name 顺丰国际官网`, `<title>Careers</title>`;
  copy: "SF Express, an established player in the Chinese express delivery market";
  apply emails at `@kex-sf.com` (SF-KEX US entity = active H1B sponsor per census).
- List URL: `https://www.sf-international.com/us/en/about/careers`
- List mechanics: SSR HTML with a `window.__NUXT__` JSON island. IMPORTANT: the
  page is ~897KB and the jobs live at offset ~810K — fetching only 300KB makes
  it LOOK like an empty SPA shell (that's what the t0 "spa-shell" verdict was:
  a truncation artifact). Full static fetch gets everything; NO browser needed.
- Row shape: JSON path `window.__NUXT__` → `data[0].result[0]` (fields
  `id:322,name:"Career",code:"career",type:9`) → `content` = one big
  JS-escaped HTML string (69,648 chars decoded). Jobs are `【Title】` blocks
  inside that content. Split: `re.split(r'(?=【[^】]+】)', content)` keeping
  blocks containing "Send Resume".
  Verbatim fragment (decoded):
  ```html
  <span style="box-sizing: border-box; font-weight: bold;"><span ...>【Customer Service Representative】</span></span>
  ...
  <p ...><span ...>Location：&nbsp;</span>Carson, CA &nbsp;</p>
  ...
  Salary:&nbsp;</span></span></span>$74,000/Annual</p>
  ...
  Send Resume to:&nbsp; hr.us@kex-sf.com; &nbsp; minny.wu@kex-sf.com
  ```
- Fields per row (regex on decoded content, per block):
  - title: `【([^】]+)】`
  - location: `Location[：:](?:&nbsp;|\s)*</span>\s*([^<\n]{2,60})`
  - salary (some rows): `Salary[：:](?:&nbsp;|\s)*(?:</span>)*\s*(\$[^<\n]{2,30})`
  - JD: block text (Responsibilities/Qualifications lists)
  - apply emails: `[\w\.-]+@kex-sf\.com` (dedupe; first is always `hr.us@kex-sf.com`)
  - detail URL: the page itself (detail = card; email-apply rows are wire-able)
- Pagination: none — single CMS page, all 6 jobs in one content blob. No page 2.
- Detail URL pattern: none per job (the block IS the detail; apply = email).
- Detail shape: same block (Responsibilities + Qualifications + Location +
  optional Salary + Send-Resume emails).
- Counts: total 6 rows; US rows 6 (5× Carson, CA; 1× Springfield Garden, NY);
  non-CN 6; CN 0. Salaries on 3 rows: $74,000 / $77,000 / $79,000 per annum.
- Sample titles (verbatim, all 6): `Customer Service Representative`,
  `Fulfillment/ Office Administrator`, `Public Relationship Specialist`,
  `Market Research Analyst`, `Accountant`, `PUD Operation Manager`.
- Sample locations (verbatim): `Carson, CA`, `Carson, CA`, `Carson, CA`,
  `Springfield Garden, NY`, `Carson, CA`, `Carson, CA`.
- postedOn source: none (no dates in the CMS content).
- Adapter kind recommendation: new class `sf_cms_nuxt` (fetch full page ≥810KB,
  scan `window.__NUXT__` for `name:"Career"` → `content:"` string, JSON-unescape,
  split on 【 blocks). Or generic "email-apply static cards" class shared with
  juicefs/greptime/timecho. US-priority: all 6 rows are US.
- Raw evidence: samples/sfus_nuxt_island.html (the extracted `window.__NUXT__`
  script, 90KB — contains `data[0].result[0].content` with all 6 jobs),
  samples/sfus_career_content.html (decoded 70KB content blob),
  samples/sfus_full.html (page head, truncated 300KB), samples/sfus.html
  (300KB truncation — shows the t0 shell artifact), samples/sfus_rendered.html
  (patchright render, confirms same 6 jobs; network: only CMS menu APIs, no
  jobs XHR).
