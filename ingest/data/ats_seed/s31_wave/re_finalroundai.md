# re_finalroundai — Final Round AI (Interview CoPilot)
VERDICT: DEAD (the ashby board referenced by the careers page no longer exists on
ashby's side — embed 400, posting-api 404, board shell renders organization:null)
- Company identity evidence: page is www.finalroundai.com/careers (title
  "Careers", product copy "Do The Best Work Of Your Life", Interview CoPilot
  marketing; Trustpilot shows "Final Round AI … 456 University Ave, Palo Alto").
  No homonym issue — the page belongs to Final Round AI; the referenced job board
  is simply gone.
- List URL (referenced, NOT working): the Next.js RSC payload contains
  `{"id":"ashby-script","src":"https://jobs.ashbyhq.com/finalroundai/embed"}`
  and a preload link for the same URL, plus `<div id="ashby_embed">`. The org
  slug in the page is literally `finalroundai` — this IS the "real slug" the
  census recorded; it is not wrong, it is DEAD.
- Live evidence of death (all checked today from this sandbox):
  - `GET https://jobs.ashbyhq.com/finalroundai/embed` → 400 "Bad Request"
    (for valid orgs like openai/linear/notion the same path returns JS, 200 —
    sanity-checked live).
  - `GET https://api.ashbyhq.com/posting-api/job-board/finalroundai` → 404
    "Not Found" (the repo's AshbyAdapter URL).
  - Slug variants tried live, all 404: final-round-ai, FinalRoundAI,
    finalround-ai, finalroundai.com, finalround, final-round, fra,
    finalroundai-inc, finalroundaiinc, finalround-ai-inc, finalroundai1.
  - `GET https://jobs.ashbyhq.com/finalroundai` → 200 but the SPA bootstrap
    carries `window.__appData = {...,"organization":null,"posting":null,
    "jobBoard":null...}` — ashby serves the shell with NO org resolved.
  - Browser render of https://www.finalroundai.com/careers: the embed area
    renders nothing (network request fails with net::ERR_BLOCKED_BY_ORB because
    the 400 text/plain body is not a script); the page shows only culture
    content (Async / Flexible Hours / Remote First), zero job listings.
  - Web search: a third-party board (careers.ritualcapital.com) says Final Round
    AI "does not have jobs relevant to this job board at this time"; instahyre
    carries an aggregator page.
- List mechanics: n/a (no live list). If the org ever resurfaces, the wire is the
  repo's EXISTING class `ashby` with org `finalroundai` (posting-api returns
  everything in one call — the AshbyAdapter contract, live-verified on other
  orgs this session).
- Row shape / fields / pagination / detail: n/a — no rows exist to observe.
- Counts: 0 rows (board dead).
- Sample titles: none.
- Sample locations: none.
- postedOn source: n/a.
- Adapter kind recommendation: EXISTING class `ashby` (org `finalroundai`) —
  but DO NOT WIRE until the org resolves again; add a B1-style guard (posting-api
  404 = dead). The careers page itself is Next.js with no fallback listing, so
  there is no alternative own-board contract to pin.
- Raw evidence: samples/finalroundai_careers.html (Next.js shell + RSC payload
  with the embed reference, 63KB), samples/finalroundai_ashby.html (ashby board
  shell with organization:null, 9KB).
