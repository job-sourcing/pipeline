# re_burningrock — Burning Rock Biotech
VERDICT: WIRE-READY
- Company identity evidence: `<title>Burning Rock Dx | Your Reliable Diagnostic Partner</title>`; JDs describe "IVD products" marketing/distribution in Europe/Brazil and "Biopharma Solutions Services" — matches Burning Rock Biotech (NYSE: BNR, Guangzhou NGS oncology diagnostics). No homonym conflict.
- List URL: `https://www.brbiotech.com/careers.php` (no query params).
- List mechanics: SSR HTML — full JDs inline.
- Row shape: `div.jobs.careers-wrap` > `div.item` (one per job). Verbatim sample row fragment:
  ```html
  <div class="item  wow fadeInUp" data-wow-duration="0.5s" data-wow-delay="0s">
      <div class="title flex flex-center flex-between">
          <div class="left">
              <div class="jobs-name">Head of Commercial- Europe</div>
              <div class="location">Location: Europe</div>
          </div>
          <div class="right">
              <div class="hamburger"><span class="line"></span>...
  ```
  JD (inline, collapsed by default): `div.hideBox > div.jobs-info` with `div.response` (`div.til` "Responsibilities" + `div.content`) and `div.skill` ("Qualifications/Skills"), apply link `a.deliver` → `mailto:ying.tang@brbiotech.com` ("I'm interested").
- Fields per row: title = `div.jobs-name` text; location = `div.location` text with `Location: ` prefix (strip it); department = none; date = none; detail URL = none (no per-job pages — all 4 items have zero non-mailto hrefs); JD = `div.response .content` + `div.skill .content` inner HTML; apply = `a.deliver[href^="mailto:"]`.
- Pagination: none — 4 `.item` blocks on one page; `swiper-pagination` occurrences are carousel CSS/JS (values/culture slides), NOT job pagination. No `page=` param.
- Detail URL pattern: N/A (accordion-style inline JDs; only mailto apply links).
- Detail shape: N/A — JD content is in the row's `.hideBox` (Responsibilities/Qualifications sections, `<p><span style="line-height:2;">...` markup).
- Counts: total rows = 4. US rows = 2 (`Director of Business Development- Biopharma Solutions Services`, `Lab Associate Scientist`); non-CN rows = 4 (Europe, Brazil, US×2 — all non-CN); CN rows = 0.
- Sample titles (>= 6): `Head of Commercial- Europe`; `Lead of Commercial- Brazil`; `Director of Business Development- Biopharma Solutions Services`; `Lab Associate Scientist` (only 4 jobs exist today).
- Sample locations (>= 6): `Location: Europe`; `Location: Brazil`; `Location: United States`; `Location: United States` (only 4 values exist).
- postedOn source: none — no dates on the page at all.
- Adapter kind recommendation: new class `burningrock` (single SSR page; title/location/inline-JD extraction).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/burningrock.html.
