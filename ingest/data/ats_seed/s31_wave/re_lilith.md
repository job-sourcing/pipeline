# re_lilith — Lilith Games (Feishu Hire board)
VERDICT: WIRE-READY (CN-only board today: 0 US rows — the "prove/disprove" answer is
DISPROVED for US; keep only under geo_scope=non_cn if at all)
- Company identity evidence: jobs.lilith.com SPA title "莉莉丝游戏招聘官网" with
  lilith-logo.png and links to afk.lilith.com / callofdragons.lilith.com (Lilith's own
  game sites); the Feishu board's embedded `#js-websiteInfo` JSON island has
  `"tenant_info":{"tenant_name":"莉莉丝"}` ("Lilith"), website
  `"Experienced Hire Official Website"` / browser label "莉莉丝社会招聘". Confirmed
  Lilith Games (AFK Arena / AFK Journey / Call of Dragons house).
- List URL: `https://lilithgames.jobs.feishu.cn/api/v1/search/job/posts`
  (POST; the public "saas-career" Feishu-Hire/ATSX portal API). jobs.lilith.com
  itself is a marketing Vue landing page whose city buttons deep-link into this
  board (e.g. San Francisco → `?location=CT_75`).
- List mechanics: JSON API. Two-step recipe (LIVE-verified with curl_cffi
  chrome131 from this sandbox):
  1. CSRF bootstrap (same session/cookie jar):
     `POST https://lilithgames.jobs.feishu.cn/api/v1/csrf/token`
     body `{"portal_entrance":1}`, headers below → sets cookie
     `atsx-csrf-token=…` (Max-Age 604800). WITHOUT this cookie the search POST
     returns **405 with empty body** (the WAF gate — the whole reason naive
     replays fail).
  2. Search:
     `POST https://lilithgames.jobs.feishu.cn/api/v1/search/job/posts`
     body (offset 0-based, 10/page):
     ```json
     {"keyword":"","limit":10,"offset":0,"job_category_id_list":[],"tag_id_list":[],
      "location_code_list":[],"subject_id_list":[],"recruitment_id_list":[],
      "portal_type":6,"job_function_id_list":[],"storefront_id_list":[],
      "portal_entrance":1}
     ```
  Required headers (portal-type constants for a saas-career PC portal, from
  bundle 3548.83031691.js): `content-type: application/json`,
  `website-path: career`, `portal-channel: saas-career`,
  `portal-platform: pc`, `accept-language: zh-CN`, `origin`/`referer` on the
  host. The browser also appends a `_signature` query param — NOT required
  (verified: works without it once the csrf cookie is present).
- Row shape: one object in `data.job_post_list[]`. Verbatim (trimmed) row:
  ```json
  {"id":"7690515099362248996","title":"高级文案策划（都市生活模拟）",
   "description":"1. 负责撰写美术需求。…","requirement":"1. 具备3年及以上的文案工作经验。…",
   "recruit_type":{"id":"101","name":"全职","en_name":"Full-time",
     "parent":{"id":"1","name":"社招","en_name":"Experienced"}},
   "publish_time":1790587689136,
   "job_function":{"id":"7233699008626133287","name":"游戏策划",
     "parent":{"name":"产品"}},
   "city_list":[{"code":"CT_125","name":"上海","en_name":"Shanghai"}],
   "job_process_id":"7055360233992374564"}
  ```
- Fields per row: title → `title`; location → `city_list[].en_name` (fallback
  `name`, zh); country → NOT present (city codes only; CT_11 Beijing, CT_125
  Shanghai, CT_163 Singapore, CT_75 San Francisco per the marketing page);
  department → `job_function.parent.name` ("产品"/"技术"); function →
  `job_function.name`; employment type → `recruit_type.en_name` ("Full-time");
  date → `publish_time` (epoch ms, exact); reqId → `id`.
- Pagination: `offset` + `limit` (10/page). VERIFIED LIVE: offset=10 → 10 new
  rows, offset=20/100 → 10 rows each, offset=110 → 0 rows (count stays 110);
  `data.count` = 110 total. Also verified by clicking pages 2/3 in a tier-6
  patchright render.
- Detail URL pattern: `https://lilithgames.jobs.feishu.cn/career/{id}`
  (SPA route, 200).
- Detail shape: `GET https://lilithgames.jobs.feishu.cn/api/v1/job/posts/{id}`
  (same portal headers + csrf cookie) → `{"code":0,"data":{"job_post_detail":{…}}}`
  with the SAME fields as a list row (title, description, requirement,
  publish_time, city_list). Filters endpoint (GET
  `/api/v1/config/job/filters/6`) returns city_list/job_function_list/
  recruitment_type_list.
- Counts: total 110 rows (walked all 11 pages in-browser, deduped); US rows 0
  (location_code_list=["CT_75"] → count 0, live-verified; the SF button on
  jobs.lilith.com is currently an empty filter); CN rows 109 (Shanghai 107,
  Beijing 2); non-CN rows 1 (Singapore, "海外法务BP" Overseas Legal BP).
- Sample titles (>= 6): "高级文案策划（都市生活模拟）"; "高级角色概念设计师（AFK
  Journey）"; "平台测试开发工程师"; "发行制作人/主营销（都市生活模拟）"; "2D动画设计师
  （AFK）"; "高级场景概念设计师（欧美现代写实-UE5）"; "English Localization
  Specialist"; "海外法务BP".
- Sample locations (>= 6): 上海 Shanghai ×107; 北京 Beijing ×2; 新加坡
  Singapore ×1.
- postedOn source: `publish_time` epoch-ms (e.g. 1790587689136 → 2026-09-27);
  exact ISO derivable.
- Adapter kind recommendation: NEW class `feishuhire` (generic — the same
  contract family will serve other jobs.feishu.cn tenants: portal headers,
  csrf-token dance, offset paging, tenant subdomain + website-path params).
  Given 0 US rows, priority is LOW for the US pipeline; the census's "US roles
  via LinkedIn only" is confirmed for Lilith.
- Raw evidence: samples/lilith.html (jobs.lilith.com marketing shell, 28KB),
  samples/lilith_feishu.html (board HTML + #js-websiteInfo island, 146KB),
  samples/lilith_feishu_jobs.json (row samples), samples/lilith_feishu_jobs_p2.json
  (offset=10 live response), samples/lilith_feishu_detail.json (detail response).
