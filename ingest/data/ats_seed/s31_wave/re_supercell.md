# re_supercell — Supercell (Tencent-majority-owned game studio)
VERDICT: WIRE-READY
- Company identity evidence: `<title>Careers × Supercell</title>`, `og:site_name:
  Supercell`; job titles are all Supercell game franchises (Clash of Clans, Clash
  Royale, Brawl Stars, Hay Day, Boom Beach, "Project R.I.S.E"); Ashby payload
  mentions "Supercell's proprietary game engine ... reaching 300 million players a
  month". Tencent owns ~81.4% — matches brief.
- SURPRISE (the valuable finding): supercell.com is a Next.js SSR front-end whose
  data comes from ASHBY. Every posting row in `__NEXT_DATA__` has
  `"source": "ashby"`, and the canonical feed is the public Ashby posting API:
  `https://api.ashbyhq.com/posting-api/job-board/supercell` → 200 JSON, 32 jobs.
  => wire with the EXISTING ashby adapter class (org `supercell`); no new class.
- List URL (canonical): `https://api.ashbyhq.com/posting-api/job-board/supercell`
  (Human fallback/SSR: `https://supercell.com/en/careers/`).
- List mechanics: JSON API (Ashby posting-api; single payload, no pagination).
- Row shape (supercell.com SSR, if scraping HTML instead): table
  `JobPostings_positionsTable__Oc174` rows `tr.JobPostings_jobRow__YmHuy`
  (`data-test-id="open-position"`); verbatim fragment:
  ```html
  <tr class="JobPostings_jobRow__YmHuy" data-test-id="open-position">
    <td class="JobPostings_title__8xlB0"><a href="/en/careers/art-director-clash-of-clans/f5eddac0-3d89-4224-8301-bde715e2dde2/?source=Supercell.com">Art Director, Clash of Clans</a></td>
    <td class="JobPostings_location__qKvpw"><span>Helsinki<!-- -->/<wbr/></span><span>London</span><span class="JobPostings_mobileOnlyJobType__fxrO5"> - <!-- -->hybrid</span></td>
    <td class="JobPostings_category___NF6T">Art, Animation &amp; Design</td>
    ...
  ```
- Fields per row:
  - JSON (preferred): `jobs[]` with `id`, `title`, `department`, `team`,
    `employmentType`, `location`, `secondaryLocations[]`, `publishedAt` (ISO,
    e.g. `2026-08-14T09:00:30.670+00:00`), `isListed`, `isRemote`,
    `workplaceType` (OnSite|Hybrid|Remote), `address.postalAddress.addressCountry`
    (Finland/United Kingdom/China), `jobUrl`, `applyUrl`, `descriptionHtml`.
  - SSR HTML: title/URL from `JobPostings_title__ a`, location
    `JobPostings_location__` spans, category `JobPostings_category__`, type
    `JobPostings_type__`.
- Pagination: none needed — single JSON payload (32 jobs, ~630KB with
  descriptionHtml). Site table says "Showing 30 positions" (2 unlisted jobs are
  excluded: `Unlisted TEST job - do not delete`, one duplicate).
- Detail URL pattern: `https://jobs.ashbyhq.com/supercell/{id}` (verified 200);
  apply: `https://jobs.ashbyhq.com/supercell/{id}/application`.
  Site-mirror detail: `https://supercell.com/en/careers/{slug}/{id}/?source=Supercell.com`.
- Detail shape: JSON row already carries full `descriptionHtml` (JD); the
  posting-api returns everything in one call (no detail fetch needed).
- Counts: 32 total (30 listed). US rows: 0. CN rows: 5 (Shanghai, Project
  R.I.S.E). non-CN rows: 27 (23 Helsinki, 4 London; of which 5 Hybrid
  Helsinki/London, rest OnSite). Remote-eligibility: `isRemote` = false for ALL
  32; `workplaceType`: 28 OnSite (incl. unlisted), 5 Hybrid, 0 Remote — no
  US-remote-eligible rows today.
- Sample titles (verbatim, 8): `Art Director, Clash of Clans`,
  `Competitive Experience Manager, Clash Royale`, `Game Lead, Boom Beach`,
  `Senior Software Engineer, Supercell ID (Client SDK)`, `MarTech Engineer`,
  `Senior Match-3 Designer, New Game`, `Product Lead, Project R.I.S.E`,
  `Quality Engineering Lead`.
- Sample locations (verbatim): `Helsinki` (23), `London` (4), `Shanghai` (5);
  displayed combos: `Helsinki/London`, `London/Helsinki`.
- postedOn source: `publishedAt` ISO 8601 with offset (range observed
  2026-01-16 → 2026-10-08).
- Adapter kind recommendation: EXISTING class `ashby` (board `supercell`);
  geo_scope non_cn, honest counts (0 US, 27 non-CN non-remote).
- Raw evidence: samples/supercell.html (SSR page), samples/supercell_ashby.json
  (posting-api payload, descriptionHtml truncated to 200 chars/row),
  samples/supercell_detail.html (jobs.ashbyhq.com detail page).
