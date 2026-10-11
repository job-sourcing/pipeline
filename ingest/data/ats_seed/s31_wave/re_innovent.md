# re_innovent — Innovent Biologics (信达生物)
VERDICT: CN-ONLY
- Company identity evidence: EN site `en.innoventbio.com` footer "Copyright © 2011-2026 Innovent", page copy "Innovent is not just another workplace… To empower patients worldwide with affordable, high-quality biopharmaceuticals"; CN site renders "信达生物制药（苏州）有限公司" with product list (信迪利单抗/达伯舒® etc.) and the careers menu links to `http://innoventbio.zhiye.com/home` (Beisen, tenant 109097) whose footer reads "©2026 信达生物制药（苏州）有限公司 … Powered by Beisen". HOMONYM GUARD: this is Innovent Biologics (Suzhou, 1801.HK) — **NOT** innovent.io (unrelated US startup; no confusion observed — board is on innoventbio.com/zhiye.com domains only).
- List URL: EN `https://en.innoventbio.com/Careers` (Nuxt SPA); CN `https://www.innoventbio.com/#/` → careers menu "职位机会" → **`https://innoventbio.zhiye.com/home`** (the real board).
- List mechanics: EN /Careers page is a Nuxt SPA with NO job data (5 static department cards, no hrefs, no XHR — see below). The CN careers menu links out to the Beisen zhiye portal, whose JSON API is directly callable from the sandbox:
  ```
  POST https://innoventbio.zhiye.com/api/Jobad/GetJobAdPageList
  Content-Type: application/json; Referer: https://innoventbio.zhiye.com/social/jobs
  {"PageIndex":0,"PageSize":100,"Category":["1"],"KeyWords":"","SpecialType":0,"PortalId":"","DisplayFields":["Category","Kind","LocId","PostDate","WorkWeChatQrCode"]}
  ```
- Row shape: EN page "rows" are static cards — verbatim fragment (NO link, NO data):
  ```html
  <div class="card show_news_list_card imgshadow card_1">
    <img src="/_nuxt/img/career_smallpic_1.289c4e7.jpg" ...>
    <p class="card-text text_center align_center">Research <br> &amp; <br> Development</p>
  </div>
  ```
  Real rows = `Data[]` of the zhiye API: `{"Id":…,"JobAdName":"北区 - 地区销售SM/M-沈阳(J30528)","Category":"社会招聘","LocNames":["辽宁省·沈阳市"],"PostDate":"2026-10-10T09:30:48","Duty":…,"Require":…}`.
- Fields per row (zhiye API): title = `JobAdName` (req code in parens); location = `LocNames[]`; date = `PostDate` ISO (+`PostDateInt`); category = `Category`; JD = `Duty`/`Require`; detail = SPA route keyed by `Id` GUID.
- Pagination: API `PageIndex`/`PageSize` — **verified LIVE**: PageIndex=0 → 20 rows, first `北区 - 地区销售SM/M-沈阳(J30528)`; PageIndex=1 → 20 rows, first `大客户部 - 大客户销售AM/M/SM(J29596)` (different rows, totalCount=1140 both). Also fetched PageSize=100.
- Detail URL pattern: SPA route on innoventbio.zhiye.com keyed by `Id` GUID (JD already in list payload).
- Detail shape: N/A — `Duty`/`Require` inline in list API.
- Counts: total rows = 1140 social + 81 campus. US rows = 0; non-CN rows = 0; CN rows = 1221 — social census (first 100): 上海市 19, 北京市 8, 江苏省·苏州市 6, 辽宁省·沈阳市 6, 吉林省·长春市 5, 浙江省·杭州市 5, 广东省·广州市 5, 陕西省·西安市 4 … all CN cities (40 distinct CN locations; Innovent's US R&D center has no postings here). Campus census: 江苏省·苏州市 45, 上海市 32, 北京市 9, 浙江省·杭州市 8, 全国 5.
- Sample titles (>= 6): `北区 - 地区销售SM/M-沈阳(J30528)`; `财务副总裁(J25768)`; `总经理助理(J27896)`; `肝癌（高级）产品经理-上海(J27760)`; `Translational Medicine SD/ED(J31017)`; `TCE Head(J24824)`; `Head of DMPK(J28400)`; `乳腺癌-医药代表-大同(J29165)`.
- Sample locations (>= 6): `上海市`; `江苏省·苏州市`; `辽宁省·沈阳市`; `北京市`; `浙江省·杭州市`; `广东省·广州市`; `山西省·大同市`.
- postedOn source: yes — `PostDate` ISO 8601 (e.g. 2026-10-10T09:30:48; some rows have 7-digit fractional seconds like `2026-09-03T15:22:14.0855825` — parse defensively).
- Adapter kind recommendation: none now (CN-ONLY). Shared Beisen pattern: same `Jobad/GetJobAdPageList` contract as porton.zhiye.com and fosunpharma.zhiye.com — one `beisen_zhiye` adapter class parameterized by host covers all three if ever needed.
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/innovent_en.html (Nuxt shell), innovent_en_rendered.html (patchright render of EN /Careers), innovent_cn.html (CN SPA shell), innovent_cn_careers.html (CN rendered nav → zhiye link), innovent_zhiye_jobad.json (social list API, first 100 rows).
