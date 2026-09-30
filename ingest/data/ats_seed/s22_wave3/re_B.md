# S22-RE-B — Enterprise Job-Board API Reverse Engineering

Method: curl with browser UA, `Accept: application/json`, no auth. US counts computed from live API JSON via python.
Workspace: /tmp/s22. Raw samples: `re_B_<platform>.json`.

---

## 1. Oracle HCM (hcmRestApi / CandidateExperience)

### 1.1 LIST endpoint (minimal verified working query)

```
GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions
    ?onlyData=true
    &expand=requisitionList.secondaryLocations
    &finder=findReqs;siteNumber=CX_1,limit=100,offset=0
```

- `siteNumber` is a **finder param**, not a query param. `limit`/`offset` are also finder params (comma-separated inside `finder=findReqs;...`). URL-level `?limit=&offset=` are ignored for the req list (they page the outer resource, which always returns 1 item).
- Minimal working = just `onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=CX_1`. The long `facetsList=LOCATIONS%3BWORK_LOCATIONS...&expand=...,flexFieldsFacet.values` from the CE UI is NOT needed — facets only add `locationsFacet`/`workLocationsFacet` etc. to the single item.
- Optional finder params (comma-separated after siteNumber): `keyword=`, `limit=`, `offset=`, `sortBy=POSTING_DATES_DESC` (also RELEVANCY, TITLE_ASC), `selectedLocationsFacet=`, `postedDate=POSTED_WITHIN_7DAYS` style, `workplaceType=`. Keep adapter minimal: `siteNumber,limit,offset`.
- Auth: **none**. Anonymous GET, HTTP 200. No cookies/CSRF.

### 1.2 Response shape

Outer envelope: `{ items: [ <ONE search item> ], count, hasMore, limit, offset, links }` — real rows live at `items[0].requisitionList[]`.

Search-item level fields: `TotalJobsCount` (total for site/query — pagination total), `Limit`/`Offset` (echo of finder paging), `SearchId`, `SiteNumber`.

Row shape (trimmed, live Mattson response):

```json
{
  "Id": "3009",
  "Title": "Field Service Engineer 3",
  "PostedDate": "2026-09-28",
  "PostingEndDate": null,
  "PrimaryLocation": "Hillsboro, OR, United States",
  "PrimaryLocationCountry": "US",
  "GeographyId": 300000001553457,
  "Language": "US",
  "WorkplaceType": "", "WorkplaceTypeCode": null,
  "JobFamily": null, "JobFunction": null,
  "WorkerType": null, "ContractType": null,
  "JobSchedule": null, "JobShift": null, "JobType": null,
  "LegalEmployer": null, "BusinessUnit": null, "Department": null, "Organization": null,
  "ShortDescriptionStr": "",
  "ExternalQualificationsStr": null, "ExternalResponsibilitiesStr": null,
  "HotJobFlag": false, "TrendingFlag": false,
  "secondaryLocations": [
    { "RequisitionLocationId": 300001689579441, "Name": "Singapore, Singapore",
      "CountryCode": "SG", "Latitude": 1.31125, "Longitude": 103.894,
      "GeographyNodeId": 100000281448212, "GeographyId": 100000006416516 }
  ]
}
```

**US detection**: `PrimaryLocationCountry == "US"` OR any `secondaryLocations[].CountryCode == "US"`. (Virtuos req 1834 is US-only via a secondary location.)

**timeType/employmentType**: fields exist (`WorkerType`, `JobSchedule`, `ContractType`, `WorkplaceType`) but are **null on both boards** unless customer configures them. Don't rely.

### 1.3 Pagination

- Page size via finder `limit=N` (default 25 observed), cursor via finder `offset=N`. Verified: `limit=100,offset=0` -> 40/40 rows; `limit=15,offset=30` -> 10 rows (30-39), first Id 3123, last Id 3145.
- Total: `items[0].TotalJobsCount` (40 Mattson, 91 Virtuos). Loop `offset += limit` until `len(requisitionList) < limit` or offset >= TotalJobsCount.
- Default sort constant across calls; add `sortBy=POSTING_DATES_DESC` for incremental syncs.

### 1.4 DETAIL endpoint

```
GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
    ?expand=all&onlyData=true
    &finder=ById;Id="<reqId>",siteNumber=CX_1        // Id must be URL-quoted: %223009%22
```

Response `{ items: [ detailItem ] }`:
- `Id`, `Title`, `PrimaryLocation`, `PrimaryLocationCountry`
- `ExternalPostedStartDate` — ISO 8601 datetime `"2026-09-02T04:04:26+00:00"` (list `PostedDate` is date-only)
- `ExternalDescriptionStr` — full HTML job description
- `ExternalQualificationsStr`, `ExternalResponsibilitiesStr` (HTML, may be null)
- `Category`, `RequisitionType`, `JobFunction`, `WorkerType`, `JobType`, `ContractType`, `JobSchedule`, `NumberOfOpenings`, `HiringManager`, `LegalEmployer`, `BusinessUnit`, `Department`, `WorkplaceType`
- `secondaryLocations[]`, `workLocation[]` (LocationId, LocationName, AddressLine1-4, TownOrCity, PostalCode, Country, Region1-3, Latitude/Longitude as strings), `otherWorkLocations`, `media`, `skills`, `requisitionFlexFields` (via expand=all)

Candidate detail URL: `https://<host>/hcmUI/CandidateExperience/en/sites/CX_1/job/<Id>` (verified HTTP 302 -> SPA page).

### 1.5 Boards verified live

| Board | Host | siteNumber | Total | US (primary) | US incl. secondary |
|---|---|---|---|---|---|
| Mattson Technology | eidg.fa.us6.oraclecloud.com | CX_1 | 40 | 24 | 24 |
| Virtuos | fa-exhj-saasfaprod1.fa.ocs.oraclecloud.com | CX_1 | 91 | 6 | 7 (req 1834) |

Task brief said Virtuos "6 US" — matches primary-only count. Mattson US locations include Fremont CA, Hillsboro OR, TX.

### 1.6 Sample rows (Mattson, US)

```json
[{"Id":"3009","Title":"Field Service Engineer 3","PrimaryLocation":"Hillsboro, OR, United States","PrimaryLocationCountry":"US","PostedDate":"2026-09-28"},
 {"Id":"2897","Title":"VP of Product","PrimaryLocation":"Fremont, CA, United States","PrimaryLocationCountry":"US","PostedDate":"2026-04-30"},
 {"Id":"2931","Title":"VP, Sales","PrimaryLocation":"Fremont, CA, United States","PrimaryLocationCountry":"US","PostedDate":"2026-05-04"}]
```

Sample rows (Virtuos, US):
```json
[{"Id":"2096","Title":"Management Trainee","PrimaryLocation":"Los Angeles, United States","PostedDate":"2026-06-16"},
 {"Id":"2256","Title":"Business Development Manager (Westcoast)","PrimaryLocation":"United States","PostedDate":"2026-09-02"}]
```

### 1.7 Quirks

- `Id` in finder must be double-quoted & URL-encoded: `Id=%223009%22`.
- `onlyData=true` required else huge $ref metadata; `expand=requisitionList.secondaryLocations` needed for multi-location US detection; detail needs `expand=all`.
- Envelope is a single-item list resource — don't iterate `items` expecting jobs.
- No auth/rate-limit observed; ~1.3s latency; 60KB/100 rows Mattson, 178KB/91 rows Virtuos.
- `Relevancy`/`Distance` fields are noise (Distance=9 without geo query).
- Raw samples: /tmp/s22/oracle_mattson_all.json, oracle_mattson_detail.json, oracle_virtuos_probe.json, oracle_virtuos_detail.json

---
## 2. SAP SuccessFactors Careers (Recruiting Marketing / Jobs2Web "j2w")

### 2.0 Engine identification (answer to the brief's question)

Both boards run **SAP SuccessFactors Recruiting Marketing (RMK)** career-center sites on the **Jobs2Web (j2w)** platform — **server-rendered HTML**, NOT Phenom, and NOT a JSON API.
Evidence: `/platform/js/j2w/min/j2w.core.min.js` etc. scripts; form `method="get" action="/search/"`; table `id="searchresults"`; scripts pulled from `hcm41.sapsf.com/verp/vmod_v1/...`.

- `/search/jobs.json` → **returns HTML** (200, text/html, same page size — server ignores the `.json` suffix). Same for POST + `X-Requested-With`. **No JSON list API.**
- `.../career?company=&service=` careercenter REST → not present (`/career` falls back to an HTML landing page).
- The data source is the HTML search page (+ `sitemap.xml` as a full-URL index). Parse HTML rows.

### 2.1 LIST endpoint

Joyson: `GET https://careers.joysonsafety.com/search/?q=&locale=en_US` (also plain `/search/`)
COFCO:  `GET https://careers.cofcointernational.com/search-jobs?q=` (path is `/search-jobs`, no trailing slash variant)

Query params:
- `q=` keyword (empty = all)
- `sortColumn=referencedate` (default) | `sort_title` | `sort_location`
- `sortDirection=desc` (default) | `asc`
- `startrow=<N>` — **pagination cursor** (0-based row offset; omit = 0)
- `locale=en_US` (Joyson) / site default en_GB (COFCO) — affects date format only

No auth, no cookies needed. Plain curl GET works.

### 2.2 Pagination

- **25 rows per page, fixed.** Cursor: `startrow=25`, `startrow=50`, ... (0-based offset).
- Total count: text in `.paginationLabel`: `Results <b>1 – 25</b> of <b>26</b>` (Joyson) / `of <b>63</b>` (COFCO). Also `aria-label="Search results ... Results 1 to 25 of 26"` on the results table.
- Page 2 link format: `?q=&sortColumn=referencedate&sortDirection=desc&startrow=25`.
- Alternative full enumeration: `/sitemap.xml` lists every job detail URL (Joyson: 26, COFCO: 63 — matches totals).

### 2.3 Row shape (HTML table `#searchresults > tbody > tr.data-row`)

Per-row parse (regex-verified):

| field | extraction | example |
|---|---|---|
| reqId | from `href="/job/<slug>/<reqId>/"` last path segment | `1423376900` |
| title | `a.jobTitle-link` text | `Sr. Product Engineer SW` |
| location | `span.jobLocation` text | `Auburn Hills, US` |
| posted date | `span.jobDate` text | `Sep 24, 2026` (en_US) / `21 Sept 2026` (en_GB) |
| detail URL | `https://<host><href>` | `https://careers.joysonsafety.com/job/Auburn-Hills-Sr_-Product-Engineer-SW-DE/1423376900/` |

- Country marker = **last comma-token of the location string**; inconsistent: ISO-2 (`US`, `BR`, `SG`), full names (`Portugal`, `Argentina`, `Netherlands`), or garbage for multi-loc rows (`+1 more…`, city names when location string overflows). US markers observed: `US`, `USA`.
- Multi-location rows render as `Chicago, Illinois, US\n+1 more…` — the "+1 more…" jobs have additional locations visible only on the detail page.
- timeType/employmentType: **absent from list rows**. Detail page shows a `Job Function:` token (department) but no worker type.

### 2.4 DETAIL endpoint

`GET https://<host>/job/<slug>/<reqId>/` — HTML with **schema.org microdata** (`itemprop`, not JSON-LD):

| field | path |
|---|---|
| title | `span[itemprop="title"]` (also `data-careersite-propertyid="title"`) |
| posted date | `meta[itemprop="datePosted"][content]` → `"Thu Sep 24 07:00:00 UTC 2026"` (java.util.Date toString format; parse as `EEE MMM dd HH:mm:ss zzz yyyy`) |
| validThrough | `meta[itemprop="validThrough"][content]` |
| description | `span[itemprop="description"] span.jobdescription` — **inner HTML** of the job description |
| location(s) | `p#job-location span.jobGeoLocation` (one per location — the multi-loc "+1 more" resolves here); geo microdata `meta[itemprop="addressLocality"|"addressRegion"|"addressCountry"]` per location, e.g. `Chicago` / `Illi` (truncated region!) / `US` |
| job function | `span[data-careersite-propertyid="department"]` → `R&D` |
| reqId | hidden `input#jobid` value |
| apply | email form posting to internal apply handler |

Note: `addressRegion` is often **truncated to 4 chars** (`Illi`, `Conn`, `Texa`) — use the list-row location string for state, or `jobGeoLocation` spans.

### 2.5 US counts verified live

| Board | Total | US rows | US details |
|---|---|---|---|
| Joyson Safety Systems | 26 (Results 1–25 of 26; 2 pages) | **7** | all `Auburn Hills, US` (MI) |
| COFCO International | 63 (3 pages: startrow 0/25/50) | **3** | Senior Legal Counsel NA (Chicago, IL **+ Stamford, CT** multi-loc, +1 more), Business Analyst (Memphis, TX), Head of Compliance NA (Stamford, CT) |

Brief said COFCO "3 US — Chicago/Stamford": confirmed 3 US jobs; locations Chicago + Stamford (+ Memphis TX).

### 2.6 Sample rows (trimmed)

Joyson:
```json
[{"reqId":"1423376900","title":"Sr. Product Engineer SW","loc":"Auburn Hills, US","date":"Sep 24, 2026","href":"/job/Auburn-Hills-Sr_-Product-Engineer-SW-DE/1423376900/"},
 {"reqId":"1432408600","title":"Program Manager Electronics / STW","loc":"Auburn Hills, US","date":"Sep 22, 2026","href":"/job/Auburn-Hills-Program-Manager-Electronics-STW-DE/1432408600/"}]
```
COFCO:
```json
[{"reqId":"1329380657","title":"Senior Legal Counsel, North America","loc":"Chicago, Illinois, US (+1 more: Stamford CT)","date":"21 Sept 2026","href":"/job/Chicago-Senior-Legal-Counsel%2C-North-America-Illi/1329380657/"},
 {"reqId":"1371554557","title":"Business Analyst","loc":"Memphis, Texas, US","date":"8 Sept 2026","href":"/job/Memphis-Business-Analyst-Texa/1371554557/"},
 {"reqId":"1368982957","title":"Head of Compliance for North America and Derivatives","loc":"Stamford, Connecticut, USA","date":"6 Sept 2026","href":"/job/Stamford-Head-of-Compliance-for-North-America-and-Derivatives-Conn/1368982957/"}]
```

### 2.7 Quirks

- No JSON anywhere — adapter must HTML-parse (rows are a stable table: `tr.data-row`, `a.jobTitle-link`, `span.jobLocation`, `span.jobDate`).
- Date format differs by site locale (`Sep 24, 2026` vs `21 Sept 2026`) — parse both (`%b %d, %Y` / `%d %b %Y`).
- `reqId` from URL is the SAP requisition posting id (numeric string; COFCO ids end in 57, Joyson in 00 — different tenants).
- Country token inconsistent (`US`/`USA`/full names); recommend mapping via a country-name/code table.
- `sortColumn=referencedate&sortDirection=desc` = newest-first (default) — good for incremental sync.
- Raw samples: re_B_sf_joyson_rows.json, re_B_sf_cofco_rows.json, joyson_detail.html, cofco_detail.html

---
## 3. UKG UltiPro (recruiting.ultipro.com "JobBoardView" SPA)

### 3.1 Board URL discovery

`https://meyerus.com/careers/` contains **NO link to the job board** (verified — no `recruiting`/`ultipro` hrefs in page HTML). The board is reachable via the tenant redirect:

```
GET https://recruiting.ultipro.com/MEY1000MEYER
  -> 302 -> https://recruiting.ultipro.com/MEY1000MEYER/JobBoardView
  -> 302 -> FULL BOARD URL:
https://recruiting.ultipro.com/MEY1000MEYER/JobBoard/7a970c3d-b076-4042-8735-673b5e5508ac
```

(Tenant code `MEY1000MEYER`; board hash `7a970c3d-b076-4042-8735-673b5e5508ac` — matches the brief's "7a970c3...".)

### 3.2 LIST endpoint (the data API)

```
POST https://recruiting.ultipro.com/MEY1000MEYER/JobBoard/7a970c3d-b076-4042-8735-673b5e5508ac/JobBoardView/LoadSearchResults
Content-Type: application/json; charset=utf-8
Accept: application/json

{"opportunitySearch":{"Top":50,"Skip":0,"QueryString":"","OrderBy":null,"OrderByKey":null,
                       "Filters":[],"Coordinates":null,"Extent":null,"ProximitySearchType":0}}
```

**CRITICAL quirk**: `"ProximitySearchType": 0` (integer) is REQUIRED — with `null` the endpoint returns HTTP 200 with `{"opportunities":[],"totalCount":0,"locations":[]}` (silent empty, not an error). `Filters` may be `[]` or `null`. Keys are PascalCase (camelCase body → silent empty). GET → HTTP 415.

No cookies/CSRF needed for the search POST (verified cookieless). The page sets an `.AspNetCore.Antiforgery.*` cookie + `__RequestVerificationToken` but the LoadSearchResults endpoint doesn't enforce it (needed only for apply/agent actions).

Response envelope:

```json
{"opportunities":[...], "totalCount":8, "locations":[...]}
```

- `totalCount` = total jobs on the board (8). `locations` = distinct location objects for map pins.
- Also `initialFeaturedOpportunities` (7 rows) + `pageSize: 50` are server-embedded in the board HTML (`var opportunityModel = new US.Opportunity.OpportunitiesViewModel({...})` in inline script).

### 3.3 Pagination

- `Top` = page size, `Skip` = 0-based row offset (finder-style cursor). Verified: `Top:3, Skip:5` → 3 rows = list positions 5-7 (SRMAN002330, SRMAN002331, AUTOM002339). Default page size 50.
- Loop `Skip += Top` while `len(opportunities) < totalCount`. Default sort = featured-first then PostedDate desc (observed order).

### 3.4 Row shape (opportunities[] — trimmed live row)

```json
{
  "Id": "33b666d6-4043-42e6-922f-717c17ea2fee",     // GUID — detail key
  "Featured": false,
  "Title": "Content Creator",
  "RequisitionNumber": "CONTE002357",                // reqId
  "FullTime": true,                                  // timeType marker (FT flag only)
  "JobCategoryName": "Staff",                        // category (Manager/Staff)
  "Locations": [
    { "Id": "ab75f7b0-332c-501a-8008-5a6f585fc8ea",
      "LocalizedName": "Vallejo",                    // display name ("Remote - USA" for remote)
      "LocalizedDescription": "Vallejo",
      "Address": { "Line1": "One Meyer Plaza", "Line2": null, "City": "Vallejo",
                   "PostalCode": "94590",
                   "State": {"Code": "CA", "Name": "California"},
                   "Country": {"Code": "USA", "Name": "United States"} },
      "DisplayName": false, "DisplayAddress": true, "SourceOfTruth": 1,
      "Coordinates": {"Latitude": 38.09682630241579, "Longitude": -122.25175474962919} }
  ],
  "PostedDate": "2026-09-04T17:05:51.214Z",          // ISO 8601 UTC
  "BriefDescription": "Your mission is to turn our products...",
  "MatchScore": 1.0, "MatchedLocations": [], "Distance": null,
  "JobLocationType": 0,                              // 0=onsite, 1=?, 2=remote (observed 2 on remote rows)
  "OpportunityType": 0
}
```

Field map: reqId=`RequisitionNumber`; title=`Title`; locations=`Locations[].LocalizedName` or `Address.City+State.Code`; country=`Locations[].Address.Country.Code` (**"USA"**, not "US"); timeType=`FullTime` bool + `JobCategoryName` (no part/full text); posted=`PostedDate`; detail URL = `https://recruiting.ultipro.com/MEY1000MEYER/JobBoard/<hash>/OpportunityDetail?opportunityId=<Id>` (from inline `opportunityLinkUrl`).

### 3.5 DETAIL endpoint

```
GET https://recruiting.ultipro.com/MEY1000MEYER/JobBoard/<hash>/OpportunityDetail?opportunityId=<GUID>
```

HTML page (88KB) embedding the full model as inline JS: `var opportunity = new US.Opportunity.CandidateOpportunityDetail({ ... })` — a single JSON object (string-aware brace scan needed; escaped quotes break naive regex).

Key fields beyond the list row: `Description` (**full HTML** job description), `UpdatedDate`, `HoursPerWeek`, `Salaried`, `CompensationAnnualMinimum/Maximum`, `CompensationHourlyMinimum/Maximum`, `CompensationCurrencyCode`, `PayRange{PayRangeMinimum,PayRangeMaximum}`, `TravelRequired`, `SupervisorName`, `EqualOpportunityEmployerDescription`, `PayTransparencyPolicyStatement`, `SkillCriteria`, `BehaviorCriteria`, `EducationCriteria`, `WorkExperienceCriteria`, `LicenseAndCertificationCriteria`, `JobBoardMemberships`, `AssessmentUri`, `PublishingStatus`, `OpportunityIsClosed`.

Note: detail `PostedDate` differs from list `PostedDate` (list 2026-09-11T21:50:09.576Z vs detail 2026-09-11T20:14:13.795Z) — use detail for authoritative posting time, or either for date-level granularity.

### 3.6 Counts verified live

**Meyer Manufacturing (MEY1000MEYER): 8 total jobs, 8 US** (Vallejo CA x2, Fairfield CA, Pompano Beach FL, 4x "Remote - USA" with Country.Code=USA, empty City/State).

US rows:
```
REGIO002359 Regional Sales Manager Residential (Florida Homebase)-HCC | Pompano Beach, FL
CONTE002357 Content Creator | Vallejo, CA
IMPOR002355 Import Compliance Manager | Remote - USA
HOSPI002349 Hospitality Account Manager | Remote - USA
SALES002348 Sales Operations Coordinator | Vallejo, CA
SRMAN002330 Sr. Manager Demand & Supply Planning | Remote - USA
SRMAN002331 Sr. Manager Demand Planning | Remote - USA
AUTOM002339 Automated Systems & Facilities Manager | Fairfield, CA
```

### 3.7 Quirks

- Silent-empty on wrong body (ProximitySearchType null / camelCase / missing wrapper key) — always assert `totalCount > 0` in the adapter as a sanity check.
- `Top`/`Skip` in `opportunitySearch` wrapper; PascalCase mandatory.
- No auth; 6 rapid POSTs → all 200 (no rate limit observed). GET → 415.
- Board discovery: follow `https://recruiting.ultipro.com/<TENANT>` 302 chain; hash is board-specific (stable).
- Raw samples: re_B_ultipro_meyer.json (full page 1), meyer_detail_model.json (detail model), meyer_featured.json (embedded featured rows)

---
## 4. TalentAdore (ats.talentadore.com JSON feed + WP career site)

### 4.1 Engine identification (answer to the brief's question)

Not `/jobs.json`, not `/api/jobs` (both 404 on the careers host). The WordPress career site (`amersports.careers.talentadore.com`, Enfold theme) renders jobs **client-side** from a widget:

```html
<div id="ta-json-careers"
     data-url="https://ats.talentadore.com/positions/mwRcjSn/json?v=2&language=&display_description=job_description&categories=tags_and_extras https://ats.talentadore.com/positions/7Veu1S9/json?v=2&language=&display_description=job_description&categories=tags_and_extras"
     data-lang="en" data-layout="center"
     data-filters='[{"filter":"country","label":"Country"},{"filter":"city","label":"City"},{"filter":"industry:list","label":"Job type"}]'
     data-tags='city,country'></div>
```

`data-url` is a **space-separated list of feed URLs** (one per TalentAdore "business unit"): `mwRcjSn` = active Amer Sports feed (44 jobs), `7Veu1S9` = "Amer Sports (inactive)" (0 jobs). Adapter: fetch ALL urls in `data-url`, concat `jobs[]`.

### 4.2 LIST endpoint (the feed)

```
GET https://ats.talentadore.com/positions/<token>/json
    ?v=2&language=&display_description=job_description&categories=tags_and_extras
```

- No auth, no cookies, no Referer needed; works with empty UA. 5 rapid requests → all 200 (no rate limit).
- Query params are effectively **optional** — plain `/json` returns the identical 44 rows with the same keys. `language=<code>` DOES filter (e.g. `language=fi` → 0 jobs since all are `en`); `language=` empty = all. `limit`/`page` are **ignored — the feed is a single unpaginated full dump**.
- Response: `{"version":"1.0","company":"Amer Sports","generated_at":"2026-09-30T05:14:00.044771Z","jobs":[...]}` — `generated_at` is the sync cursor.

### 4.3 Pagination

**None** — one GET returns the entire board (44 rows, 750KB with full descriptions). `limit`/`page` params ignored (verified).

### 4.4 Row shape (jobs[] — trimmed live row)

```json
{
  "id": "4nyL2",
  "job_token": "D4vNd0",                       // detail/apply key
  "name": "Solution Architect – SAP Sales – Fashion (m/f/d)",
  "business_unit_id": "gz9q",
  "business_unit_name": "Amer Sports",
  "link": "https://ats.talentadore.com/apply/solution-architect---sap-sales---fashion-m-f-d/D4vNd0",
  "description_html": "<div><p>Hybrid/Garching near Munich...</p>...",
  "description_text": "Hybrid/Garching near Munich\n\nJoin Amer Sports...",
  "updated": "2026-07-17T05:29:31Z",           // last-modified
  "start_date": "2026-04-29T13:22:53Z",        // POSTED DATE
  "due_date": "",                              // application deadline
  "location": "Parkring 15-17, 85748 Garching bei München",
  "county": "Finland",                          // (sic — recruiter-entered, unreliable)
  "country": "Germany",                         // country marker — FULL NAME, e.g. "United States"
  "city": "Garching bei München",
  "tags": [],
  "categories": [ {"domain":"language","term":"en","label":"en"},
                  {"domain":"industry","term":"Information Technology","label":"Information Technology"} ],
  "image": "https://s3-eu-west-1.amazonaws.com/feedback-production/jiuploads/header-image_...jpg",
  "logo": null,
  "employment_type": null                       // "Full Time" when set (free-text: "Full Time"/"Full time")
}
```

Field map: reqId=`job_token` (short id; `id` is a secondary key); title=`name`; location text=`city` + `location` (address); country marker=`country` (full name "United States"); timeType=`employment_type` (string, nullable); posted date=`start_date` (ISO 8601 UTC); detail URL=`link`. **Descriptions are embedded in the feed** (`description_html` full HTML, `description_text` plain text) — no per-job fetch needed.

US detection: `country == "United States"` (full name, not code).

### 4.5 DETAIL endpoint (optional)

`GET <link>` = `https://ats.talentadore.com/apply/<slug>/<job_token>` — candidate-facing HTML (113KB) with **schema.org JSON-LD**:

```json
{"@type":"JobPosting",
 "datePosted":"2026-08-27T14:14:00",
 "employmentType":["FULL_TIME"],
 "identifier":{"@type":"PropertyValue","name":"Amer Sports","value":"Zk4KMo"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressCountry":"United States","addressLocality":"New York City","addressRegion":"NY","postalCode":"NY 10010"}},
 "title":"IT Field Services & Support (FSS) Specialist",
 "url":"https://ats.talentadore.com/apply/it-field-services-support-fss-specialist/Zk4KMo",
 "description":"<full HTML>",
 "hiringOrganization":{...}}
```

Note: detail `datePosted` (2026-08-27T14:14) differs from feed `start_date` — use feed for list; detail adds `addressRegion` (state) + postal code. **URL must use `job_token`, not `id`** (id → 404).

### 4.6 Counts verified live

**Amer Sports: 44 total jobs, 12 US** (New York City x8, Chicago x2, Pittston Township PA x2 — matches brief "NYC/Chicago/Pittston PA").
Country distribution: Poland 14, Germany 12, United States 12, Austria 2, Italy 2, Bulgaria 1, Finland 1.

### 4.7 Sample rows (US)

```json
[{"id":"Ab8nz","job_token":"Zk4KMo","name":"IT Field Services & Support (FSS) Specialist","city":"New York City","country":"United States","employment_type":"Full Time","start_date":"2026-08-27T14:22:53Z","link":"https://ats.talentadore.com/apply/it-field-services-support-fss-specialist/Zk4KMo"},
 {"id":"dEbdb","name":"Senior Analyst, SAP FI/CO","city":"Chicago","country":"United States","employment_type":"Full Time"},
 {"id":"qaVn9","name":"IT Field Services & Support Technician","city":"Pittston Township","country":"United States","employment_type":"Full Time"}]
```

### 4.8 Quirks

- Feed tokens per business unit; the careers page may list several (space-separated in `data-url`) — fetch all, dedupe by `job_token`.
- `county` field is unreliable recruiter free-text (job in Germany had county "Finland").
- `employment_type` is free-cased ("Full Time" vs "Full time") and sometimes null.
- Feed already contains full descriptions — skip detail fetches for the pipeline; use JSON-LD detail only when state/postalCode needed.
- `generated_at` at feed top = server dump timestamp (incremental-sync watermark; pair with `updated` per row).
- Raw samples: re_B_talentadore_amersports.json (44 rows), re_B_talentadore_detail.jsonld, talentadore_feed1.json, talentadore_feed2.json (inactive unit)

---
# Summary — Cross-platform quick reference

| Platform | Board | List endpoint (minimal) | Pagination | Total | US | Detail |
|---|---|---|---|---|---|---|
| Oracle HCM CE | Mattson | `GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=CX_1,limit=100,offset=0` (host eidg.fa.us6.oraclecloud.com) | finder limit/offset; total=items[0].TotalJobsCount | 40 | 24 | `.../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="<Id>",siteNumber=CX_1`; desc=ExternalDescriptionStr |
| Oracle HCM CE | Virtuos | same, host fa-exhj-saasfaprod1.fa.ocs.oraclecloud.com, siteNumber=CX_1 | same | 91 | 6 (7 w/ secondary) | same |
| SAP SF RMK (j2w) | Joyson | `GET https://careers.joysonsafety.com/search/?q=` (HTML) | `startrow=N`, 25/page, total from .paginationLabel | 26 | 7 | `/job/<slug>/<reqId>/` HTML w/ itemprop microdata; desc=itemprop=description |
| SAP SF RMK (j2w) | COFCO | `GET https://careers.cofcointernational.com/search-jobs?q=` (HTML) | same | 63 | 3 | same |
| UKG UltiPro | Meyer | `POST /MEY1000MEYER/JobBoard/7a970c3d-b076-4042-8735-673b5e5508ac/JobBoardView/LoadSearchResults` body `{"opportunitySearch":{Top,Skip,QueryString,OrderBy,OrderByKey,Filters,Coordinates,Extent,ProximitySearchType:0}}` (ProximitySearchType must be 0) | Top/Skip; total=totalCount | 8 | 8 | `.../OpportunityDetail?opportunityId=<GUID>` HTML embed `CandidateOpportunityDetail({...})`; desc=Description |
| TalentAdore | Amer Sports | `GET https://ats.talentadore.com/positions/mwRcjSn/json` (full dump w/ descriptions) | none | 44 | 12 | descriptions inline; page link=jobs[].link w/ JSON-LD |

US-count rule per platform: Oracle `PrimaryLocationCountry=="US"` (+secondaryLocations CountryCode); SF location-string last token in (US, USA) — check detail for "+1 more…" multi-loc rows; UltiPro `Locations[].Address.Country.Code=="USA"`; TalentAdore `country=="United States"`.

Next actions for adapter implementation:
1. Oracle: single GET loop over finder offset until TotalJobsCount; quote Id in detail finder.
2. SF: HTML-parse 25-row pages (or sitemap.xml enumeration), follow detail pages for multi-loc rows + country confirmation; map country tokens.
3. UltiPro: remember PascalCase body + ProximitySearchType=0 (silent empty otherwise); detail needs string-aware JSON extraction from inline JS.
4. TalentAdore: one GET per business-unit token scraped from `#ta-json-careers[data-url]` on the WP careers page; feed has everything incl. description.
