# re_infold — Infold Games (Paper Games global studio brand)
VERDICT: WIRE-READY (1 active EN job, and it IS US-located: Los Angeles)
- Company identity evidence: board title "Infold Games Careers"
  (career.infoldgames.com/EN, assets at assets.infoldgames.com/nikkiweb/paper/…);
  the API's department for the live job is "Z1 Studio" (Infold's internal studio);
  requirement text "6+ years of 3D character modeling experience in the gaming or
  film/television industry… AAA console"; i18n locales EN/JP/CN with a Chinese
  privacy statement. Confirmed Infold Games (Love Nikki / Infinity Nikki house).
- List URL: `POST https://chaos.infoldgames.com/v2/engine` (the "ena/TDS" flow
  engine — domain from the bundle's API_MAP tds.prod). Body (verbatim, works with
  NO auth, NO cookies):
  ```json
  {"act_id":1803,"flow_alias":"8924","website_id":"7224436604932475191",
   "page_size":10,"page_token":"","city_code_list":[],
   "job_function_id_list":[],"keyword":""}
  ```
  Headers: `Content-Type: application/json` (+ Origin/Referer
  `https://career.infoldgames.com`; both optional in testing).
  website_id EN(prod) = "7224436604932475191", JP = "7225087345124559161"
  (constants `Pe` in bundle chunk index.9d3a56dd.js @19936). act_id = 1803.
- List mechanics: JSON API (React SPA, discovered by grepping
  assets.infoldgames.com/…/index.9d3a56dd.js for the jobs-fetch thunk
  `gt({page_size:10, page_token, city_code_list, job_function_id_list, keyword})`
  → flow_alias Ce="8924", and the SDK's `baseURL: Y(env)+"/v2/engine"` in
  custom.6de3b383.js).
- Row shape: one object in `data.data.items[]`. Verbatim (trimmed) row:
  ```json
  {"address":{...},"address_list":[{"city":{"code":"CT_163","name":{"en_us":"Singapore"}},
    "country":{"code":"CN_25","name":{"en_us":"Singapore"}}},
    {"city":{"code":"CT_94","name":{"en_us":"Los Angeles"}},
     "country":{"code":"CN_6","name":{"en_us":"United States of America"}},
     "state":{"code":"ST_31","name":{"en_us":"California"}}}],
   "create_time":"1778660393058","description":"1. Responsible for the high-fidelity…",
   "id":"7639286868983384370","job_active_status":1,"job_code":"A167711",
   "job_department":{"name":{"en_us":"Z1 Studio"}},
   "job_function":{"name":{"en_us":"Animation & CG"}},
   "job_id":"7639286868983220530","job_type":{"name":{"en_us":"Audio / Video / Graphics development"}},
   "job_recruitment_type":{"name":{"en_us":"Full-time"}},
   "modify_time":"1784019939366","requirement":"1. 6+ years…","title":"3D Character Modeler"}
  ```
- Fields per row: title → `title`; location → `address_list[].city.name.en_us` +
  `country.name.en_us` (STRUCTURED country); department →
  `job_department.name.en_us`; function → `job_function.name.en_us`;
  employment type → `job_recruitment_type.name.en_us` ("Full-time");
  date → `create_time` / `modify_time` (epoch ms strings); code → `job_code`;
  reqId → `id` (job_post id).
- Pagination: `page_token`-based cursor: pass the previous response's
  `data.data.page_token` and stop when `has_more` is false. Live-verified:
  current EN board has 1 item, `has_more:false`, `page_token:""` — there IS no
  page 2 today, so the cursor behavior could not be exercised on real data
  (documented as the SPA's own load-more mechanism, page_size=10).
  Filters flow: `{"act_id":1803,"flow_alias":"8925","website_id":...}` returns
  `{city_list:[{code,name:{en_us}}], job_function_list:[{id,name}]}` (live:
  cities = Singapore, Los Angeles; functions = Animation & CG).
- Detail URL pattern: `https://career.infoldgames.com/EN/job/{job_post_id}`
  (SPA route; the HTML shell is the same 5.9KB app — data only via API).
- Detail shape: `POST /v2/engine` body
  `{"act_id":1803,"flow_alias":"8926","website_id":"7224436604932475191",
  "job_post_id":"7639286868983384370"}` → `ret:0`,
  `data.data.job_post` = the SAME shape as a list row (address, description,
  requirement, job_code, job_type, create_time…). Sample fragment:
  `"job_recruitment_type":{"name":{"en_us":"Full-time"}}`.
- Counts: EN board total 1 row; US rows 1 (address_list includes "Los Angeles,
  United States of America" — same job also Singapore); CN rows 0; JP board 0.
- Sample titles: "3D Character Modeler" (the EN board's only current job).
- Sample locations: Singapore; Los Angeles, California, United States of America.
- postedOn source: `create_time` / `modify_time` — epoch-millis strings
  (create 1778660393058 ≈ 2026-05-13, modify 1784019939366 ≈ 2026-07-14);
  exact ISO derivable by /1000.
- Adapter kind recommendation: NEW class `infold` (custom "chaos engine" flow
  API; nothing else in the repo speaks flow_alias/website_id). Trivial to wire:
  one POST for the list (+ optional filters POST 8925, detail POST 8926).
- Raw evidence: samples/infold_en.html (SPA shell, 6KB),
  samples/infold_jobs_p1.json (live list response),
  samples/infold_filters.json (live filters response),
  samples/infold_job_detail.json (live detail response).
