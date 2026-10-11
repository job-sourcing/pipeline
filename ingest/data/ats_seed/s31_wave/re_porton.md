# re_porton — Porton Pharma (博腾制药)
VERDICT: CN-ONLY
- Company identity evidence: main site `<title>以人为本的企业文化 | 博腾制药</title>` with Porton/J-STAR branding ("美国J-STAR（South Plainfield）研发生产基地", "美国J-STAR（Cranbury）", "J-STAR Research, Inc.", "© 2026 PORTON 博腾"); jobs portal title `博腾制药` (Beisen, tenant 111456), footer "©2026 博腾制药… Powered by Beisen". This is Porton Pharma Solutions (Chongqing CDMO). No homonym conflict.
- List URL: `https://porton.zhiye.com/` — linked from `https://www.portonpharma.com/career` via `<a href="https://porton.zhiye.com/">了解更多/更多职位</a>` (mailto hrm@portonpharma.com for inquiries). The corporate /career page itself is a CN-language culture page with no jobs.
- List mechanics: SPA (Beisen ux-recruitment-portal-2022) — BUT the backing JSON API is directly callable with plain HTTP (works from sandbox egress, no browser needed):
  ```
  POST https://porton.zhiye.com/api/Jobad/GetJobAdPageList
  Content-Type: application/json; Referer: https://porton.zhiye.com/social/jobs
  {"PageIndex":0,"PageSize":100,"Category":["1"],"KeyWords":"","SpecialType":0,"PortalId":"","DisplayFields":["Category","Kind","LocId","PostDate","WorkWeChatQrCode"]}
  ```
  Response: `{"Code":200,"Count":27,"Data":[{...job ad...}],"Total":0}`. Helper APIs: `GET /api/JobAd/GetJobCount?type=workLocation&codes=5000,3100,5100,3600,4200&portalId=670f9f78-1be7-4a50-8bfb-20c5be0593a9`; `POST /api/Jobad/GetJobAdSearchConditions` (location facets); `GET /api/Jobad/SearchAreasTreeConditions?categoryId=1`.
- Row shape: one element of `Data[]` in the JSON. Verbatim sample row fragment:
  ```json
  {"Id":"0f9343e7-a093-4f3a-89db-bd3286cff8a5","HeadCount":0,"JobAdId":230940217,
   "PostDateInt":1789038770000,"PostDate":"2026-09-10T19:12:50",
   "JobAdName":"酶催化研究员（多肽合成方向）(J12042)","OrgId":2014516,"Org":null,
   "Category":"社会招聘","CategoryId":"1","LocId":null,"LocNames":["上海市"],
   "Salary":null,"Duty":"1. 负责酶促多肽液相合成项目的研发计划…","Require":"1. 硕士…",...}
  ```
- Fields per row: title = `JobAdName` (contains req code in parens, e.g. `(J12042)`); location = `LocNames[]` (array, e.g. `["重庆市·长寿区"]`); date = `PostDate` ISO `2026-09-10T19:12:50` (+ `PostDateInt` epoch-ms); category = `Category` (`社会招聘` social / `校园招聘` campus; CategoryId "1"/"2"); JD = `Duty` + `Require`; department = `OrgId`/`Org` (Org null in list; department via Classification* fields); detail URL = portal job page keyed on `Id` GUID (SPA route).
- Pagination: API pagination via `PageIndex`/`PageSize` — verified: PageSize=100 returns all 27 social rows in one call (Count=27). Category "2" (campus) Count=70, Category "3" Count=3.
- Detail URL pattern: SPA detail at `https://porton.zhiye.com/social/jobs` route keyed by `Id` GUID (rendered via browser; not needed for wire — `Duty`/`Require` already in list payload).
- Detail shape: N/A — full JD in the list API (`Duty`, `Require`, `Welfare`, `Salary`, `Degree`, `YearsOfWorking`, `WorkTime` fields exist).
- Counts: total rows = 100 (27 social + 70 campus + 3 other). US rows = 0; non-CN rows = 0; CN rows = 100 — location census (social, all 27): 上海市 10, 重庆市 4, 重庆市·长寿区 5, 江西省 2, 上海市·闵行区 3, 重庆市·北碚区 2, 上海市·奉贤区 1; campus: 江苏省·苏州市 17, 上海市 8, 重庆市 4, 四川省·成都市 1... All CN provinces; the area tree contains ONLY CN locations (全国/上海/重庆/江西 + districts). No 美国/US location despite J-STAR US sites.
- Sample titles (>= 6): `酶催化研究员（多肽合成方向）(J12042)`; `分析工程师(J11679)`; `结晶工程师(J11720)`; `合成工程师(J10694)`; `结晶粉体工程师(J12096)`; `User Leader（喷雾干燥方向）(J12079)`; `多肽液相合成研究员(J12045)`; `采购工程师(J11980)`.
- Sample locations (>= 6): `上海市`; `重庆市`; `重庆市·长寿区`; `江西省`; `重庆市·北碚区`; `上海市·闵行区`; `江苏省·苏州市` (campus).
- postedOn source: yes — `PostDate` ISO 8601 + `PostDateInt` epoch-ms.
- Adapter kind recommendation: none now (CN-ONLY). If the project ever adds Beisen/zhiye: existing shared pattern — this is the same Beisen `Jobad/GetJobAdPageList` API used by fosunpharma and innoventbio zhiye boards; a single `beisen_zhiye` adapter class parameterized by portal host would cover porton + fosunpharma + innovent.
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/porton.html (corporate /career), porton_zhiye.html (SPA shell), porton_zhiye_jobad_pagelist.json (full social list API response).
