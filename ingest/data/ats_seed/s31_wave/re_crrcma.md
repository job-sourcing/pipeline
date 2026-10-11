# re_crrcma — CRRC MA Corporation (US railcar manufacturer, Springfield MA)
VERDICT: WIRE-READY (plain WP SSR, tier-0 crawlable — the 404 was just a wrong path; real board = /opportunities/)
- Company identity evidence: og:site_name "CRRC"; detail page copy "CRRC MA
  Corporation, established in 2015, is a U.S. rail car manufacturer
  headquartered in Springfield, Massachusetts. Awarded contracts to supply 700+
  railcars to public transit agencies across the United States". LCA-active
  US manufacturer — matches.
- List URL: `https://www.crrcma.com/opportunities/` (nav label "Join Our Team";
  the task's `/careers` 404 was the wrong path — homepage nav points to
  /opportunities/).
- List mechanics: SSR HTML (WordPress + custom "job" plugin). Tier-0 curl_cffi
  200 — no WAF at all from HK egress.
- Row shape: jobs render inside `div.job_list` (attrs `data-page="1"
  data-posttype="job"`) as `div.newsroom_info` cards:
  title+URL = `a[href^="https://www.crrcma.com/job/"] > h3.newsroom_info_title`;
  excerpt = `p.newsroom_info_desc`.
  Verbatim sample row fragment:
  ```html
  <div class="job_list" data-page="1" data-posttype="job">
      <div class="newsroom_info">
          <div class="newsroom_info_holder">
              <div>
                  <a href="https://www.crrcma.com/job/electrical-assembler/"><h3 class="newsroom_info_title"> Electrical Assembler</h3></a>
                  <p class="newsroom_info_date"></p>
              </div>
              <div>
                  <p class="newsroom_info_desc">
                      TITLE Electrical Assembler Click here to apply for the Electrical Assembler REQ NUMBER PRO-20-00048 SALARY RANGE $17.50 per hour ABOUT THE ORGANIZATION CRRC MA Corporation, established in 2015, is a U.S. rail car manufacturer headquartered in Springfield, Massachusetts. ...
  ```
- Fields per row: title = `h3.newsroom_info_title` text (strip leading space);
  detail URL = the wrapping `<a href>`; excerpt (contains REQ NUMBER + salary) =
  `p.newsroom_info_desc`; date = `p.newsroom_info_date` (empty in both rows).
- Pagination: AJAX — plugin JS (`/wp-content/plugins/job/js/job.js?ver=1.0`)
  POSTs to `https://www.crrcma.com/wp-admin/admin-ajax.php` with
  `action=job&data=page=N&states=all&tags=`. VERIFIED PAGE 2 (fetched live):
  `{"html":"<ul class=\"baf-lists jobs-feed\" data-page = \"2\" data-posttype=\"job\"><p class=\"intro\">No matching Jobs.<\/p><\/div>","pagination":"","paginationCount":""}`
  — i.e. page 2 is real and empty (only 1 page of results exists). State/tag
  filters exist (`?states=...&tags=...` reflected in URL via pushState).
- Detail URL pattern: `https://www.crrcma.com/job/{slug}/`
  (e.g. /job/electrical-assembler/, /job/mechanical-assemblers/).
- Detail shape: WP post; `<h1>` = job title; a `<table>` with
  `<td class="smallheader">` label cells:
  `LOCATION` → `Springfield Office`; `FULL-TIME/PART-TIME` → `Full-Time`;
  JD text contains "REQ NUMBER PRO-20-00048", "SALARY RANGE $17.50 per hour",
  "Click here to apply" → `<a href="https://recruiting.myapps.paychex.com/appone/MainInfoReq.asp?R_ID=3220213">`
  (Paychex recruiting = the apply system). `article:modified_time` present
  (2021-11-05T15:29:45+00:00) — old posts.
- Counts: 2 rows; US 2; non-CN 2; CN 0.
- Sample titles (all 2, verbatim): "Electrical Assembler", "Mechanical
  Assemblers".
- Sample locations (verbatim): "Springfield Office" (both).
- postedOn source: none reliable — `article:modified_time` 2021 dates; the list
  `p.newsroom_info_date` is empty.
- Adapter kind recommendation: new class `crrc` (WP job plugin: list page +
  admin-ajax pagination + per-job WP detail with Paychex apply link).
- Raw evidence: samples/crrcma_opportunities.html (13KB list segment),
  samples/crrcma_job_electrical-assembler.html (10KB detail segment),
  samples/crrcma_ajax_page2.json (page-2 AJAX response, verified).
