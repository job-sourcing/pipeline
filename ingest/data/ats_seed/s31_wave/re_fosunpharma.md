# re_fosunpharma — Fosun Pharma (复星医药)
VERDICT: CN-ONLY
- Company identity evidence: corporate page `<title>Careers_Fosun Pharma</title>` with /en/ nav; jobs portal `https://fosunpharma.zhiye.com/` title `复星医药集团` (Fosun Pharma Group, Beisen tenant). Board copy/jobs match Fosun Pharma (Shanghai pharma conglomerate: CAR-T, 药物警戒, clinical roles). No homonym conflict.
- List URL: `https://fosunpharma.zhiye.com/` — linked from `https://fosunpharma.com/en/careers/` via the "Join Us" link (`<a href="https://fosunpharma.zhiye.com/">`). The /en/careers/ page ("Job Applicants" target) is a static marketing listing (talent.html/growth.html/life.html subpages) with NO job rows.
- List mechanics: SPA (Beisen ux-recruitment-portal) — backing JSON API directly callable from sandbox egress (no browser needed):
  ```
  POST https://fosunpharma.zhiye.com/api/Jobad/GetJobAdPageList
  Content-Type: application/json; Referer: https://fosunpharma.zhiye.com/
  {"PageIndex":0,"PageSize":100,"Category":["1"],"KeyWords":"","SpecialType":0,"PortalId":"","DisplayFields":["Category","Kind","LocId","PostDate","WorkWeChatQrCode"]}
  ```
  → `{"Code":200,"Count":78,"Data":[…]}`.
- Row shape: one element of `Data[]`. Verbatim sample row fragment:
  ```json
  {"Id":"32704cf9-c608-40dc-9811-09984c946916","HeadCount":0,"JobAdId":561312404,
   "PostDateInt":1791522673000,"PostDate":"2026-10-09T13:11:13",
   "JobAdName":"品牌传播高级总监(J13868)","OrgId":1734441,"Org":null,
   "Category":"社会招聘","CategoryId":"1","LocId":null,"LocNames":["上海市"],...}
  ```
- Fields per row: title = `JobAdName` (req code in parens, e.g. `(J13868)`); location = `LocNames[]`; date = `PostDate` ISO + `PostDateInt` epoch-ms; category = `Category` (社会招聘 social / 校园招聘 campus); JD = `Duty`/`Require` (full text in list payload); detail = SPA job page keyed by `Id` GUID.
- Pagination: API PageIndex/PageSize — verified: PageSize=100 returns all 78 social rows (Count=78); campus Category "2" Count=73.
- Detail URL pattern: SPA route on fosunpharma.zhiye.com keyed by `Id` GUID (not needed — JD in list payload).
- Detail shape: N/A — `Duty`/`Require` inline in list API.
- Counts: total rows = 151 (78 social + 73 campus). US rows = 0; non-CN rows = 0; CN rows = 151 (social census: 上海市 58, 北京市 16, 广东省·深圳市 8, 四川省·成都市 4, 香港特别行政区 1, 浙江省·杭州市 1, 广东省·广州市 1, 广西·南宁 1, 辽宁·大连 1, 浙江省·温州市 1, 天津市 1, 广东·珠海 1; campus: 上海市 24, 广西·桂林 10, 广东·佛山 4, 江苏·徐州·新沂 4, 四川·成都 4, 广东·深圳 5, …). HK SAR row counted as CN.
- Sample titles (>= 6): `品牌传播高级总监(J13868)`; `AI战略运营总监(J14920)`; `AI产品经理(J14905)`; `CEO业务助理(J15013)`; `CAR-T专员/高级专员/经理 - 杭州(J15008)`; `药物警戒医生（高级总监/执行总监）(J15009)`; `BD经理(J15001)`; `临床医学高级经理/副总监/总监-实体瘤(J15000)`.
- Sample locations (>= 6): `上海市`; `浙江省·杭州市`; `北京市`; `广东省·深圳市`; `四川省·成都市`; `香港特别行政区`; `广东省·广州市`.
- postedOn source: yes — `PostDate` ISO 8601 (e.g. 2026-10-09T13:11:13) + `PostDateInt` epoch-ms.
- Adapter kind recommendation: none now (CN-ONLY). Shared Beisen pattern: same `Jobad/GetJobAdPageList` API as porton.zhiye.com and innoventbio.zhiye.com — one `beisen_zhiye` class parameterized by host would cover all three if ever needed.
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/fosunpharma.html (corporate /en/careers/), fosunpharma_zhiye.html (SPA shell), fosunpharma_zhiye_jobad.json (full social list API response).
