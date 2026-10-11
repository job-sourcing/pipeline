# re_asymchem — Asymchem (凯莱英)
VERDICT: PARK (board EXISTS as Angular SPA at /careers but its data API is broken server-side — 0 jobs from both sandbox and US egress; re-check later)
- Company identity evidence: `<title>Asymchem: Top CDMO for Global Pharma Companies</title>`; footer "Copyright © 2024 Asymchem Inc."; SPA nav includes "US- Boston" external link to `https://asymchemdirect.com/` and LinkedIn `https://www.linkedin.com/company/asymchem/`; CN-site title `凯莱英医药集团官网-首页` (Asymchem's Chinese name). This is Asymchem Laboratories (Tianjin CDMO with US/Boston ops — LCA evidence: Asymchem BOSTON CORPORATION). No homonym conflict.
- List URL: `https://www.asymchem.com/careers` (Angular SPA route; server returns the app shell 200 — the earlier "404" report was the un-JS'd shell; route also reachable via /about/careers nav link). Detail route family: `/getjob` (app hides topbar/footer when `router.url.includes("/getjob")` — job detail pages).
- List mechanics: SPA (Angular 17+; main bundle `main.a03d14b6be3dc622.js`; routes `/careers`, `/about/careers`, `/getjob`). Jobs are fetched from `GET https://asymchem-admin.asymchem.com.cn/get_all_jobs` (XHR, Accept: application/json).
- Row shape: none today — the API responds `{"error_code": 1, "msg": "Something went Wrong"}` (HTTP 200) to every variation tried (GET/POST, ?lang=en, ?page=1&size=50) — reproduced from BOTH the sandbox egress and firecrawl/US egress, and in a real stealth browser the page renders:
  ```text
  Career Opportunities
  Find Jobs
  Clear filter
  Showing 0 - 0 of 0 jobs
  ```
  with placeholder filter options "Developer / Designer / Manager" (template dummy data). So the board is live but EMPTY due to a broken backend.
- Fields per row: unknown (no rows render). Page chrome suggests: filter select(s) + paging-size selector (Show 5/10/…/100/All) + result-count banner `Showing X - Y of Z jobs`.
- Pagination: UI has a page-size control ("Show 5 … Show All") but 0 jobs — nothing to verify.
- Detail URL pattern: `/getjob` route (from main.js: `this.router.url.includes("/getjob")`).
- Detail shape: unknown (no data).
- Counts: total rows = 0. US rows = 0; non-CN rows = 0; CN rows = 0.
- Sample titles (>= 6): none exist.
- Sample locations (>= 6): none exist.
- postedOn source: unknown (no data).
- Adapter kind recommendation: PARK — re-probe `https://asymchem-admin.asymchem.com.cn/get_all_jobs` periodically; if it ever returns jobs, the contract is a plain JSON GET + Angular rendering (likely fields for title/location/JD per row). CN site asymchem.com.cn has no /careers path (404).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/asymchem_root.html (SPA shell), asymchem_careers_rendered.html (patchright render: "Showing 0 - 0 of 0 jobs"), asymchem_jobs.json (broken API response).
