# re_sphereex — SphereEx (Apache ShardingSphere commercial company)
VERDICT: WIRE-READY
- Company identity evidence: page copy "Participate in the design, development
  and maintenance of Apache ShardingSphere... Follow the Apache Way" + product
  names SphereEx-DBPlusEngine/Tools/Services; contact block biz@sphere-ex.com.
  Matches the ShardingSphere commercial entity.
- List URL: `https://www.sphere-ex.com/about/` ("Open Positions" section near
  the bottom; separate "Join Us" footer link also lands here).
- List mechanics: SSR HTML (React/emotion-styled static page; NO separate
  careers page — jobs live on the About page).
- Row shape: role card = `<span component="View">{Title}</span>` + Job
  Description / Job Requirements text blocks + Apply button; verbatim fragment:
  ```html
  <span class=" sphere-ex-16mlea6" component="View">Overseas Technical Operations</span>
  ... Job Description / Job Requirements lists ...
  <a target="_blank" rel="noreferrer"
     href="mailto:hr@sphere-ex.com?subject=Job Application: Infrastructure Engineer">
    <button class=" sphere-ex-2kvns1" ...> ... <div class=" sphere-ex-oosbdi" component="View">Apply Position</div>
  ```
  Row regex: split the "Open Positions" region on `component="View">` role-title
  spans (only 2 exist), or pair each `mailto:hr@sphere-ex.com?subject=Job
  Application: {title}` with the preceding description text.
- Fields per row:
  - title: `component="View">([^<]{3,60})<` within the Open Positions region
    (values: `Overseas Technical Operations`, `Infrastructure Engineer`)
  - JD: "Job Description" + "Job Requirements" lists following the title
  - apply URL: `href="(mailto:hr@sphere-ex.com\?subject=Job Application: [^"]+)"`
  - location: NOT stated per row (no location field at all)
  - date: NOT available
- Pagination: none — single About page, 2 rows, no page 2.
- Detail URL pattern: none — the card is the detail (email-apply; detail =
  card itself; anchor not provided).
- Detail shape: same card (Description + Requirements + mailto button).
- Counts: total 2 rows; US rows 0 (locations unspecified); non-CN rows:
  ambiguous — "Overseas Technical Operations" requires "English native speaker
  or native speaker level" and is overseas-facing (possibly non-CN or remote),
  "Infrastructure Engineer" is ShardingSphere/Java with "basic written English"
  (likely CN); CN rows: 0 explicitly labeled.
- Sample titles (verbatim, all 2): `Overseas Technical Operations`,
  `Infrastructure Engineer`.
- Sample locations: none published (no location strings on the page).
- postedOn source: none.
- Adapter kind recommendation: new class `static_email_apply_cards` (About-page
  section; same family as emqx/juicefs/greptime). geo_scope: ambiguous →
  non_cn conservative; low priority (2 rows, no locations).
- Raw evidence: samples/sphereex.html (truncated 300KB of 399KB live fetch —
  full text extracted above; full copy at /tmp/s31b/sphereex_full.html during
  probe; the positions section sits at ~offset 306K).
