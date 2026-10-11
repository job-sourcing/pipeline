# re_henlius — Henlius (复宏汉霖)
VERDICT: PARK (own board down for maintenance; EN careers page links LinkedIn only — incl. Henlius USA LinkedIn)
- Company identity evidence: `<title>人力资源-招聘职位</title>` on henlius.com with footer "Copyright © 2019 … 上海复宏汉霖生物技术股份有限公司" (Shanghai Henlius Biotech); EN page `<title>Henlius</title>` links `https://www.linkedin.com/company/henlius/jobs/` and `https://www.linkedin.com/company/henliususa/jobs/`. This is Henlius (2696.HK, Shanghai biopharma; US arm Henlius USA Inc.). No homonym conflict.
- List URL: `https://www.henlius.com/job-opportunities.html` (CN; the EN careers page is `https://www.henlius.com/en/careers.html` — /en/job-opportunities.html is 404).
- List mechanics: SSR HTML — but the job board section is replaced by a maintenance notice. Verbatim fragment:
  ```html
  <div class="contact bg_f4f4f4">
    <div class="title">招聘系统维护中</div>
    <div class="text text-center" style="font-size:0.5rem;">
        如有应聘需求请发送邮件至 <a href="mailto:CV@henlius.com">CV@henlius.com</a>
    </div>
    ... QR codes: 官方微信公众号 / 领英主页 / 招聘公众号
  ```
  ("Recruitment system under maintenance. To apply, email CV@henlius.com.")
- Row shape: none (board down).
- Fields per row: N/A.
- Pagination: N/A.
- Detail URL pattern: N/A.
- Detail shape: N/A.
- Counts: total rows = 0. US rows = 0; non-CN rows = 0; CN rows = 0.
- Sample titles (>= 6): none exist on the site today. (LinkedIn shows US jobs at Henlius USA Inc.: "It Project & Maintenance Manager", "Medical Science Liaison", "Director US Compliance, BioPharma", "Finance Business Partner" — per web search 2026-10-11 — but that's LinkedIn, not an own board.)
- Sample locations (>= 6): none exist.
- postedOn source: N/A.
- Adapter kind recommendation: none — PARK. Re-check the maintenance notice later; if the board returns it has historically been a zhiye/Beisen-style listing (per prior census "CN-side"). For US rows, the only channel is LinkedIn (out of scope for own-board adapters).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/henlius.html (CN job-opportunities page with maintenance notice), henlius_en_careers.html (EN careers page with LinkedIn links).
