# re_sirnaomics — Sirnaomics
VERDICT: CN-ONLY
- Company identity evidence: `<title>Careers | Sirnaomics</title>` / `<title>Curent Openings | Sirnaomics</title>`; footer addresses "Sirnaomics, Inc. 20511 Seneca Meadows Parkway, Suite 200 Germantown, MD 20876 USA" + "Sirnaomics (Hong Kong) Limited … Hong Kong Science Park"; JDs are RNAi/siRNA roles — matches Sirnaomics (Germantown MD / Suzhou, RNA therapeutics). No homonym conflict.
- List URL: `https://www.sirnaomics.com/en/curent-openings/` — NOTE the typo "curent" in the real URL (the /en/careers/ page links here via `<a href="/en/curent-openings/" class="join-us-title">Current Opening</a>`).
- List mechanics: SSR HTML — full JDs inline in hidden Magnific-Popup blocks.
- Row shape: `h3.curent-title` + `.curent-meta` + `.curent-date`, each row's popup `div.curent-openings-popup` (`id="curent-opening-popup-{id}"`, e.g. 6320). Verbatim sample row fragment:
  ```html
  <span>Position</span>
  </div>
  <h3 class="curent-title">
      siRNA Design &amp; Bioinformatics Scientist
  </h3>
  <div class="curent-meta">
      <span>Experienced Hire</span>
      <span class="location">
          <svg .../>
          Location: China
      </span>
  </div>
  </div>
  <div class="curent-date">
      2026.04.28
  </div>
  </a>
  <div id="curent-opening-popup-6320" class="curent-openings-popup white-popup mfp-with-anim mfp-hide">
  ```
  (Row is wrapped in `<a href="#curent-opening-popup-{id}">`; JD popup contains "Job Description / Qualifications / Core Skills" blocks.)
- Fields per row: title = `h3.curent-title` text; employment type = first `span` in `.curent-meta` ("Experienced Hire"); location = `span.location` text after `Location: ` prefix; date = `div.curent-date` text (format `2026.04.28` = `%Y.%m.%d`); detail URL = none (popup anchor `#curent-opening-popup-{id}` only); JD = the matching popup div's `<p><strong>Job Description</strong>...` content.
- Pagination: YES — `/en/curent-openings/?page=2`, nav `nav.curent-openings__pagination > ul.curent-openings__pagination-list` with `a.pagination__item` (active class on current). **Verified LIVE**: page 1 → 8 rows; `?page=2` → 3 additional rows (Marketing Director, Investor Relations Director, Investment Director); `?page=3` → returns the same 3 rows as page 2 (beyond-last silently clamps, no error). Total = 11 rows.
- Detail URL pattern: N/A — hash-anchored popups only.
- Detail shape: N/A — JDs inline in `div.curent-openings-popup` blocks in the list HTML.
- Counts: total rows = 11 (8 + 3). US rows = 0; non-CN rows = 0; CN rows = 11 — every row's location is literally `Location: China` (Germantown MD HQ exists but no US postings).
- Sample titles (>= 6): `siRNA Design & Bioinformatics Scientist`; `Nucleic Acid Medicinal Chemist`; `Cell Biology Research Scientist (RNA Therapeutics)`; `Preclinical Pharmacology Scientist`; `CMC Process Development Scientist`; `Analytical Development Specialist`; `Regulatory Affairs Specialist`; `Clinical Research Associate (CRA)`; `Marketing Director`; `Investor Relations Director`; `Investment Director`.
- Sample locations (>= 6): `Location: China` ×11 (only value).
- postedOn source: yes — `div.curent-date` = `2026.04.28` (dot-separated, needs `%Y.%m.%d` parsing; identical on all 11 rows).
- Adapter kind recommendation: none now (CN-ONLY). If ever needed: new class `sirnaomics` (2-page SSR list; per-row popup JD).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/sirnaomics.html (/en/careers/), sirnaomics_openings.html (page 1), sirnaomics_openings_p2.html, sirnaomics_openings_p3.html.
