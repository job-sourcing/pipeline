# re_timecho — Timecho (Apache IoTDB commercial, Timecho Limited)
VERDICT: WIRE-READY
- Company identity evidence: `<title>About Timecho | Creator of Enterprise-Grade
  Time Series Database</title>`; copy "Founded by the initiators of Apache
  IoTDB, an international top level open source project at the Apache Software
  Foundation"; footer "Address: Emma-Joos-Straße 6, 70439 Stuttgart, Germany"
  + `京ICP2022007419号`; "Copyright © 2026 Timecho Limited". Matches the
  Apache IoTDB commercial company (per census, email-based hr@timecho.com).
- List URL: `https://www.timecho-global.com/aboutus` (jobs live in the "Join
  Us!" section; `/careers` → 404, does not exist).
- List mechanics: SSR HTML (Halo CMS static theme `timecho-website-en`).
- Row shape: `div.about-job-container > div.about-job-box` per role; verbatim
  fragment:
  ```html
  <div class="about-job-box">
    <div class="about-job-title-box">
      <h4 class="about-job-title">Frontend Development Engineer</h4>
      <div class="about-job-requires-box">
        <ul class="about-job-requires">
          <li class="about-job-require-item">1-3 yrs</li>
          <li class="about-job-require-item">Bachelor</li>
        </ul>
        <div class="about-job-address-box">
          <p class="about-job-address">[Stuttgart/Beijing/Remote]</p>
  ```
  Row regex: `<div class="about-job-box">(.*?)</div>\s*<div class="about-job-box">` or
  iterate `h4.about-job-title` anchors.
- Fields per row:
  - title: `h4.about-job-title`
  - experience: `li.about-job-require-item` (1st, e.g. `1-3 yrs`)
  - education: 2nd require item (`Bachelor`)
  - location: `p.about-job-address` (`[Stuttgart/Beijing/Remote]` on all 6)
  - JD: `div.about-job-desc-box` (Responsibilities + Requirements lists)
  - apply email: page-level `span.about-join-email` → `hr@timecho.com`,
    subject "[Name - Position Applied]"
  - date: NOT available
- Pagination: none — 6 rows on one page (no page 2).
- Detail URL pattern: none — the card is the detail (accordion-style box
  expansion; email-apply; detail = card itself).
- Detail shape: same box (Responsibilities/Requirements lists).
- Counts: total 6 rows; US rows 0; non-CN rows 6 (locations
  `[Stuttgart/Beijing/Remote]` — Stuttgart DE is the non-CN anchor; "Remote"
  unqualified); CN rows 0 exclusively-CN (Beijing is one option per row).
- Sample titles (verbatim, all 6): `Frontend Development Engineer`,
  `Database Core Test Engineer`, `Java Development Engineer`,
  `Social Media Operator`, `Page Designer`, `Solution Architect`.
- Sample locations (verbatim, all 6): `[Stuttgart/Beijing/Remote]`.
- postedOn source: none (no dates).
- Adapter kind recommendation: new class `static_email_apply_cards` (same
  family as sphereex/juicefs/greptime/emqx) — geo_scope non_cn.
- Raw evidence: samples/timecho.html (live SSR page with all 6 job boxes).
