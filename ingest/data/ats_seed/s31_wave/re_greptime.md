# re_greptime — Greptime (Rust observability database, GreptimeDB)
VERDICT: WIRE-READY
- Company identity evidence: `<title>Careers at Greptime | Greptime</title>`;
  copy "Greptime builds an open-source observability database in Rust — one
  engine for metrics, logs, and traces (github.com/GreptimeTeam/greptimedb)" +
  "venture-backed early-stage team distributed across the US and China" —
  matches the brief. US entity addresses in footer: GREPTIME INC, 440 N. Wolfe
  Road, Sunnyvale CA + Spring TX.
- List URL: `https://greptime.com/careers`
- List mechanics: SSR HTML (VitePress docs site; single static page).
- Row shape: one role per `<h2 id="{slug}">` heading + following content; the
  only live row verbatim fragment:
  ```html
  <h2 id="developer-advocate">Developer Advocate <a class="header-anchor" href="#developer-advocate" ...>​</a></h2>
  <p><strong>Remote — Americas or Europe. Full-time. Part-time or contract also OK.</strong></p>
  <h3 id="the-role">The role</h3> ...
  <h3 id="compensation">Compensation</h3>
  <p>US: $100,000–$150,000 base + equity ...</p>
  <h3 id="apply">Apply</h3>
  <p>Email <a class="__cf_email__" data-cfemail="...">[email protected]</a> with your resume ...</p>
  ```
  Row regex: `<h2 id="([^"]+)">([^<]+?)\s*<a class="header-anchor"` then read
  until the next `Join our community` / footer.
- Fields per row:
  - title: h2 text (`Developer Advocate`)
  - location/work-type: first `<p><strong>...</strong></p>` under the h2
    (`Remote — Americas or Europe. Full-time. Part-time or contract also OK.`)
  - salary: `US: $100,000–$150,000 base + equity` (in Compensation section)
  - JD: h3 sections (The role / What you'll do / What we want / Compensation / Apply)
  - apply email: `jobs@greptime.com` (decoded from Cloudflare `data-cfemail`)
  - visa note: "Visa sponsorship: case by case."
  - date: NOT available
- Pagination: none — 1 role on a single static page.
- Detail URL pattern: `https://greptime.com/careers#{slug}` (e.g.
  `#developer-advocate`); card = detail (email-apply).
- Detail shape: same section (all JD subsections inline).
- Counts: total 1 row; US-eligible rows 1 (remote Americas; US comp band listed;
  US 401(k) benefit); non-CN 1; CN 0.
- Sample titles: `Developer Advocate` (only row).
- Sample locations: `Remote — Americas or Europe` (US addresses Sunnyvale CA /
  Spring TX in footer).
- postedOn source: none.
- Adapter kind recommendation: new class `static_md_careers` (VitePress h2
  sections; email-apply) — geo_scope us.
- Raw evidence: samples/greptime.html (live SSR page, truncated 300KB of 653KB;
  full role content is inside the first 300KB).
