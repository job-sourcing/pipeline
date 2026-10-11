# re_oncoc4 — OncoC4
VERDICT: WIRE-READY
- Company identity evidence: `<title>Job Opportunities | OncoC4</title>`; `og:site_name="OncoC4"`; detail-page JD references GCP/GLP/GMP audits for a clinical-stage biopharma (OncoC4, Rockville MD + Nanjing/Beijing/Guangzhou China — matches the real OncoC4). No homonym conflict.
- List URL: `https://www.oncoc4.com/en/careers/job-opportunities/` (default, no query params = United States section). Location sections are SEPARATE REQUESTS via `?location={nanjing|guangzhou|beijing}` — not one page with sections. The active filter carries class `job-opportunities__filter--active` (United States link has no query param).
- List mechanics: SSR HTML (full rows + JD modals in initial HTML; no XHR needed).
- Row shape: `div.job-opportunities__item` — one per job, detail URL in the `url-redirect` attribute. Verbatim sample row fragment:
  ```html
  <div class="job-opportunities__item" url-redirect="/en/careers/job-opportunities/united-states/quality-assurance-auditor/">
      <div class="job-opportunities__field">
          <span class="job-opportunities__label">Position</span>
          <span class="job-opportunities__value" data-bs-toggle="modal"
                data-bs-target="">Quality Assurance Auditor</span>
      </div>
      <div class="job-opportunities__field">
          <span class="job-opportunities__label">Department</span>
          <span class="job-opportunities__value">Quality and Regulatory</span>
      </div>
      <div class="job-opportunities__field">
          <span class="job-opportunities__label">Posting Date</span>
          <span class="job-opportunities__value">Sep 30, 2026</span>
      </div>
      <div class="modal fade" id="jobModal-4821" ...>
  ```
- Fields per row: title = 1st `span.job-opportunities__value` (label "Position"); department = 2nd (label "Department"); date = 3rd (label "Posting Date", format `Sep 30, 2026` = `%b %d, %Y`); detail URL = `url-redirect` attribute on the row div. JD is BOTH inline (Bootstrap modal `#jobModal-{id}` → `.job-opportunities__responsibilities-text` / `.job-opportunities__position`) AND on the detail page.
- Pagination: none — single page per location. Verified: US default page has 1 row; `?location=nanjing`, `?location=guangzhou`, `?location=beijing` each fetched LIVE today (200) and each returned **0 rows** (empty CN sections). No `page=` param exists (0 occurrences in HTML).
- Detail URL pattern: `https://www.oncoc4.com/en/careers/job-opportunities/{location-slug}/{job-slug}/` e.g. `.../united-states/quality-assurance-auditor/`.
- Detail shape: `h1.titleNews` (title), `.job-detail__meta-value` ×2 (Department, Posting Date), JD in `div.box.job-detail__description` (full HTML: `<p class="MsoNormal">The Quality Assurance (QA) Auditor plans, conducts, reports...`). Detail fetched LIVE: 200, 40529 bytes.
- Counts: total rows today = 1 (US). US rows = 1; non-CN rows = 1; CN rows = 0 (all 3 CN location filters empty).
- Sample titles (>= 6): only 1 row exists today — `Quality Assurance Auditor`. (Historical stage-1 sample also showed CN-section rows; today they are all empty.)
- Sample locations (>= 6): location sections available: `United States` (default), `Nanjing`, `Guangzhou`, `Beijing` — only the US section has a row today.
- postedOn source: yes — "Posting Date" field, human format `Sep 30, 2026` on both list row and detail meta; no ISO dates anywhere.
- Adapter kind recommendation: new class `oncoc4` (SSR list per location param; parse `job-opportunities__item`; iterate location values `["", "nanjing", "guangzhou", "beijing"]`).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/oncoc4.html (list US), oncoc4_nanjing.html / oncoc4_guangzhou.html / oncoc4_beijing.html (empty sections), oncoc4_detail.html (detail page).
