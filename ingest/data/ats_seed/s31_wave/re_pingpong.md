# re_pingpong — PingPong (PingPong Global / cross-border payments)
VERDICT: WIRE-READY
- Company identity evidence: `<title>Careers at PingPong | Join Our Team</title>`;
  JD text: "Founded in 2015, PingPong is an embedded financial infrastructure
  platform serving more than 200 countries... processed over $350 billion"
  (matches the Chinese-founded cross-border payments unicorn; US entity is
  LCA-active). Site: Framer-built (`framer-*` classes everywhere).
- List URL: `https://www.pingpongx.com/global/career`
- List mechanics: SSR HTML (Framer static export; jobs are plain links).
- Row shape: `<a class="framer-..." href="./career-detail/{slug}">` under the
  "Open roles" heading; verbatim fragment (PC variant; a mobile `ssr-variant`
  copy exists too — dedupe by href):
  ```html
  <a class="framer-egfwlo framer-65wwu" href="./career-detail/bd-director-eu">
    ... <p class="framer-text ...">Director of Business Development</p>
    ... <p ...>Business Development</p> ... <p ...>European Union | Remote</p>
  ```
  Regex: `<a class="framer-[^"]*" href="(\./career-detail/[^"]+)"[^>]*>(.*?)</a>`
  (DOTALL; dedupe on href; inner texts = Role/Team/Location in DOM order).
- Fields per row:
  - title: 1st text `<p>` inside the anchor (Role column)
  - team: 2nd text (Team column)
  - location: 3rd text (Location column, e.g. `European Union | Remote`, `UK | London`)
  - detail URL: `https://www.pingpongx.com/global/career-detail/{slug}`
  - date: none on list or detail (no postedOn anywhere)
- Pagination: none — all 4 rows on one page ("Open roles" table). No page-2
  exists (single static page; search input is client-side only).
- Detail URL pattern: `https://www.pingpongx.com/global/career-detail/{slug}`
  (verified 200 live for `bd-director-eu`).
- Detail shape: detail page has header block: "PingPong Career / {title} /
  {team} / {location}" then full JD prose sections (WHAT'S THE CHALLENGE, WHO
  YOU'LL BE WORKING, WHAT YOU'LL BE DOING, ...) and an "Apply for this role"
  button. `<title>` is generic "PingPong Global" (do not use for title).
- Counts: total 4 rows; US rows 0; non-CN rows 4; CN rows 0 on this global board
  (CN recruiting is separate pingpong.com.cn portal).
- Sample titles (verbatim, all 4): `Director of Business Development`,
  `Director of Business Development`, `Business Development Team Lead`,
  `Customer Success Manager`.
- Sample locations (verbatim, all 4): `European Union | Remote`, `UK | London`,
  `UK | London`, `UK | London`.
- postedOn source: none available (no dates in HTML; Framer static export).
- Adapter kind recommendation: new class `framer_static_list` (generic: page has
  `a[href*="career-detail"]` rows) or simple static-HTML adapter; geo_scope
  non_cn; honest counts (0 US today, 4 EU/UK).
- Raw evidence: samples/pingpong.html (list, live), samples/pingpong_detail.html
  (bd-director-eu detail, live).
