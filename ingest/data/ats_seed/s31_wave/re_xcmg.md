# re_xcmg — XCMG (Xuzhou Construction Machinery Group)
VERDICT: WIRE-READY (marginal — 1 row/page regional cards; only NA page has a US row)
- Company identity evidence: `<title>...Xuzhou Construction Machinery Group Global`
  (site-wide), Employer line in job card: "XCMG America Inc." (US subsidiary),
  social links to facebook.com/XCMGGroup. Matches the Xuzhou-based #3 global
  construction-machinery maker.
- List URL: NOT a list. `/career/north-america-region/` renders a single-job card.
  Career root `https://www.xcmgglobal.com/career/` links 7 regional pages:
  `/career/north-america-region/`, `/career/-europe-region/`,
  `/career/-oceania-region/`, `/career/indonesia-region/`,
  `/career/south-america-region/`, `/career/south-asia-region/`,
  `/career/west-asia-and-north-africa-region/` (each 1 job card, some empty).
  The real CN recruiting portal (Moka-style, jobs.xcmg.com) is separate — NOT here.
- List mechanics: SSR HTML, static CMS "appDetail" pages (one job per region page,
  content hand-rotated by CMS; `<title>` is a stale template "XCMG Apprentice S9").
- Row shape: one row per page, inside `div.pt2 > div.wrap > div.infor`; verbatim
  fragment (NA page, live 2026-10-11):
  ```html
  <div class="pt2 pt_item">
    <div class="wrap">
      <div class="infor">
        <div class="tit"> IT Director</div>
        <div class="con"><div class="box mCustomScrollbar">
          <dl class="dl01"><dt>Responsibility</dt><dd>...</dd></dl>
          <dl class="dl03">
            <p><strong>Location:</strong> Atlanta, USA</p><p><strong>Employer:</strong> XCMG America Inc.</p>
          </dl>
        </div></div>
      </div>
      <div class="app_join"><a href="https://www.linkedin.com/jobs/view/4033333726/?capColoOverride=true">join us</a></div>
  ```
- Fields per row (per region page):
  - title: `<div class="pt2 ... .infor .tit">([^<]+)` (" IT Director")
  - location: `<strong>Location:</strong>\s*([^<]+)` (" Atlanta, USA")
  - employer: `<strong>Employer:</strong>\s*([^<]+)` ("XCMG America Inc.")
  - JD: `dl.dl01` (Responsibility) + `dl.dl02` (Qualifications) lists
  - apply/detail URL: `.app_join a` href (LinkedIn job view URL — the "join us" button)
  - date: NOT present anywhere (no postedOn data)
- Pagination: none (7 fixed URLs; each fetched independently). "Page 2" concept
  does not exist. Verified live: all 7 region pages fetched 200 today; only NA
  has a US employer row; south-america (Brasil address), south-asia (India),
  indonesia, europe, west-asia pages carry 1 row each with LinkedIn apply;
  -oceania-region/ currently has no job card (empty).
- Detail URL pattern: the region page IS the detail (no per-job URL). Apply =
  `https://www.linkedin.com/jobs/view/{id}/?capColoOverride=true`.
- Detail shape: same card (Responsibility/Qualifications lists + Location/Employer).
- Counts: total rows today: 6 of 7 region pages populated (NA 1). US rows: 1
  (IT Director, Atlanta, USA, XCMG America Inc.). non-CN rows: 6. CN rows: 0
  (CN portal separate).
- Sample titles (verbatim, across regions): `IT Director` (NA), plus region-page
  job cards (titles vary; NA title tag is stale "XCMG Apprentice S9" — always
  trust the card `.tit`, never the <title>).
- Sample locations (verbatim): `Atlanta, USA`; `BR-040, km 519 - Chácara
  Campestre, Contagem - MG, 32145-480, Brasil`; `India`; other region pages
  show empty Location cells.
- postedOn source: none (no dates on page; LinkedIn target may have one).
- Adapter kind recommendation: new class `xcmg_regional_static` (7 fixed URLs,
  1 card each; treat each card as a row; apply_url = LinkedIn link). Priority
  low: 1 US row.
- Raw evidence: samples/xcmg.html (NA region page, live),
  samples/xcmg_career_root.html (region link list).
