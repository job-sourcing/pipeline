# re_microportortho — MicroPort Orthopedics
VERDICT: WIRE-READY
- Company identity evidence: `<title>Careers North America | MicroPort Orthopedics</title>`; page copy "MicroPort Participates in E-Verify", "MicroPort Orthopedics is a Drug-Free Workplace", "© 2026 MicroPort Orthopedics, Inc. … 016223"; jobs are device-manufacturing roles at 5677 Airline Rd, Arlington, TN — matches MicroPort Orthopedics (Arlington TN; ex-Wright Medical manufacturing, MicroPort Scientific subsidiary). No homonym conflict.
- List URL: `https://recruiting.ultipro.com/MIC1008MICPT/JobBoard/9f087488-7494-c18e-5713-c97b9f3d8219` (External Applicants board). The corporate page `https://www.microportortho.com/about-us/microport-orthopedics-careers/careers-north-america` links both "External Applicants" (this board) and "Internal Applicants" → `https://recruiting.ultipro.com/MIC1008MICPT/JobBoard/ff42c70c-4085-2598-c58e-133f18d8a8fb` (internal; returns totalCount=0 without sign-in).
- List mechanics: UKG/UltiPro JobBoardView (KnockoutJS SPA) + JSON API. The API is directly callable (verified from sandbox egress):
  1. GET the board page (200) → scrape `__RequestVerificationToken` (`<input name="__RequestVerificationToken" value="…">`).
  2. POST `{board}/JobBoardView/LoadSearchResults` with header `X-RequestVerificationToken` and JSON body:
  ```json
  {"opportunitySearch":{"Top":100,"Skip":0,"QueryParamKeyword":"","QueryParamLocation":"","QueryParamJobCategoryIds":[],"QueryParamJobLocationIds":[],"QueryParamJobLocationTypeIds":[],"QueryParamScheduleIds":[],"QueryParamCompanyIds":[],"SortColumn":"postedDate","SortDirection":"desc","filters":[]}}
  ```
- Row shape: one element of `opportunities[]` in the response. Verbatim sample row fragment:
  ```json
  {"Id":"22c5b65c-3e5a-493d-96e0-37ccb654c3eb","Featured":false,
   "Title":"Metal Finisher (3rd shift)","RequisitionNumber":"METAL002817","FullTime":true,
   "JobCategoryName":"Manufacturing",
   "Locations":[{"Id":"0fd3de93-8368-5139-b991-e367071a639c","LocalizedDescription":"MicroPort Orthopedics",
     "Address":{"Line1":"5677 Airline Rd","City":"Arlington","PostalCode":"38002",
       "State":{"Code":"TN","Name":"Tennessee"},"Country":{"Code":"USA",...}}}],
   "PostedDate":"2026-10-02T15:38:23.902Z",
   "BriefDescription":"As a Metal Finisher, you will metal finish medical devices to meet specifications...",
   "JobCategoryName":"Manufacturing","JobLocationType":"...","OpportunityType":...}
  ```
- Fields per row: title = `Title`; req = `RequisitionNumber`; department = `JobCategoryName`; location = `Locations[].Address.{City,State.Code,Country.Code,PostalCode}`; type = `FullTime`/`JobLocationType`; date = `PostedDate` (ISO 8601 with Z); summary = `BriefDescription`; detail = `{board}/OpportunityDetail?opportunityId={Id}` (UltiPro standard) — rendered card also shows `PostedDateString` "Oct 2, 2026".
- Pagination: API `Top`/`Skip` (verified with Top=100 → all 7 rows in one call, `totalCount: 7`). No page 2 exists (7 jobs total; "Showing 7 of 7 opportunities" in rendered page).
- Detail URL pattern: UltiPro opportunity detail keyed on `Id` GUID (standard UltiPro route; the rendered list links each card title).
- Detail shape: rendered card shows full meta (Job Category / Requisition Number / Schedule / Job Location Type / location string `Arlington, TN 38002, USA`) + description; `BriefDescription` carries the JD summary in the API payload.
- Counts: total rows = 7. US rows = 7; non-CN rows = 7; CN rows = 0 — every job is `Arlington, TN 38002, USA`.
- Sample titles (>= 6): `Metal Finisher (3rd shift)`; `CNC Programmer`; `Sr. Machinist (1st Shift)`; `Maintenance Service Specialist (2nd Shift)`; `Machinist, Screws (2nd Shift)`; `Sr. Machinist, Screws (1st Shift)`; `Metal Finisher (2nd shift)`.
- Sample locations (>= 6): `Arlington, TN 38002, USA` ×7 (only value; employer LocalizedDescription "MicroPort Orthopedics").
- postedOn source: yes — `PostedDate` ISO 8601 (`2026-10-02T15:38:23.902Z`); rendered string `Oct 2, 2026`.
- Adapter kind recommendation: **existing class `ultipro`** (`UltiProAdapter`, KIND="ultipro" in ingest/jobsearch/sources/site_boards.py — S22 LoadSearchResults POST incl. silent-empty trap). Config: tenant `MIC1008MICPT`, external JobBoard id `9f087488-7494-c18e-5713-c97b9f3d8219`.
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/microportortho.html (corporate careers NA page), microportortho_ultipro1.html (board shell), microportortho_ultipro1_rendered.html (patchright render), microportortho_ultipro_loadsearch.json (full API response).
