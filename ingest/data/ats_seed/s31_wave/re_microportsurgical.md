# re_microportsurgical — MicroPort Scientific (surgical arm, MicroPort Surgical)
VERDICT: CN-ONLY
- Company identity evidence: `<title>Career Opportunities-MicroPort</title>` (page `/talent/job/`); footer "©Copyright 2026, MicroPort Surgical (Shanghai) Co., Ltd." with Shanghai address "No. 1601, Zhangdong Road, ZJ Hi-Tech Park, Shanghai"; page copy "We are an innovative medical device company dedicated to the fields of critical care and life support" — matches MicroPort's surgical/critical-care arm (MicroPort Surgical, Shanghai; ECMO/life-support devices). No homonym conflict (distinct from microportortho.com).
- List URL: `https://www.microportsurgical.com/talent/job/` (the `/talent/` page's "Job Application" CTA links here; nav anchor is `/talent/#job` → section id `job`).
- List mechanics: SSR HTML (layui-based page; jobs fully server-rendered).
- Row shape: `div.zknps > div.pta` — one per job. Verbatim sample row fragment:
  ```html
  <div class="pta">
      <div class="t clearfix">
          <div class="tl1 tl fl font18 color6">Technician</div>
          <div class="tl2 tl fl font18 color6">Quality Department</div>
          <div class="tl3 tl fl font18 color6">Shanghai</div>
          <div class="tl4 tl fl font18 color6">1</div>
          <div class="tl5 tl fl font18 color6">2026-05-29</div>
      </div>
      <div class="cont">
          <div class="tt font30 colorBlue">Technician</div>
          <div class="t1 font22 color3 marginT30">Job Responsibilities:</div>
          <div class="c font18 color6 lh18">
              <p>1.Perform physical, mechanical, and electrical safety tests on medical devices...
  ```
- Fields per row: title = `div.tl1` (and `div.cont > div.tt`); department = `div.tl2`; location = `div.tl3`; openings count = `div.tl4`; date = `div.tl5` (ISO-ish `2026-05-29` = `%Y-%m-%d`); JD = `div.cont` ("Job Responsibilities:" + "Qualifications:" blocks); apply = email in JD footer (e.g. `xdai@microport.com`); detail URL = none (accordion `.cont`, no per-job pages).
- Pagination: none — 4 rows on one page; the search form (`select#title1` Work location: Shanghai/Dongguan Guangdong; `select#title2` Department; keyword input) is layui client-side filtering (no server paging; no `page=` param).
- Detail URL pattern: N/A (inline accordion).
- Detail shape: N/A — JDs inline in `div.cont`.
- Counts: total rows = 4. US rows = 0; non-CN rows = 0; CN rows = 4 (all `Shanghai`).
- Sample titles (>= 6): `Technician`; `Senior / Advanced Medical Blood Monitoring Sensor Engineer (Algorithm Focus)`; `Equipment Engineer`; `Regulatory Affairs Specialist` (only 4 jobs exist today).
- Sample locations (>= 6): `Shanghai` ×4 (only value; the work-location filter also offers `Dongguan, Guangdong` with 0 rows).
- postedOn source: yes — `div.tl5` release date `2026-05-29` / `2026-04-06` (`%Y-%m-%d`, directly parseable).
- Adapter kind recommendation: none now (CN-ONLY). If ever needed: new class `microportsurgical` (single SSR page, 5-field rows + inline JD).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/microportsurgical.html (/talent/), microportsurgical_job.html (/talent/job/ board).
