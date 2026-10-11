# re_emqx — EMQ (EMQX, MQTT broker / IoT data platform)
VERDICT: WIRE-READY
- Company identity evidence: `<title>Careers | EMQ</title>`, og:site_name
  www.emqx.com; JD copy "EMQ is the team behind EMQX, the world's leading
  open-source messaging and streaming data platform for IoT, AI, and
  event-driven applications". Matches the MQTT broker company (Hangzhou roots,
  global remote).
- List URL: `https://www.emqx.com/en/careers`
- List mechanics: SSR HTML (Nuxt/Vue + Reka UI collapsible cards; full JD is in
  the HTML inside hidden accordion bodies — no browser needed).
- Row shape: `<div class="rounded-xl border border-default bg-default
  overflow-hidden">` per job; verbatim fragment:
  ```html
  <div class="rounded-xl border border-default bg-default overflow-hidden">
    <div data-state="closed" data-slot="root">
      <button type="button" ... data-slot="base" class="... w-full ... text-left rounded-none">
        <div><div class="mb-2 flex items-center">
          <svg ... aria-hidden="true" ...></svg>
          <span class="text-sm text-icon">Europe</span></div>
        <h2 class="text-xl font-semibold text-highlighted">Technical Support Engineer - EMQX Cloud</h2></div>
      ...
      <div data-slot="content" ... id="reka-collapsible-content-v-0-9-0" hidden data-state="closed">
        ... full JD text ...
        <a href="mailto:hr-eu@emqx.io?subject=Technical%20Support%20Engineer%20-%20EMQX%20Cloud" ...>Apply now → </a>
  ```
  Row regex: split on `<div class="rounded-xl border border-default bg-default overflow-hidden">`.
- Fields per row:
  - title: `<h2 class="text-xl font-semibold text-highlighted">([^<]+)</h2>`
  - location: `<span class="text-sm text-icon">([^<]+)</span>`
  - JD: text between `id="reka-collapsible-content..."` and the `mailto:` anchor
    (strip tags; ~1.9-3.8KB per card, includes About/Responsibilities/Qualifications)
  - apply URL: `href="(mailto:[^"]+)"` (per-card, subject-encoded; US rows cc
    `yanping.miao@emqx.io,elee@emqx.io`)
  - date: NOT available (no postedOn in cards or page)
- Pagination: none — all 6 cards on one page (single SSR page, no page-2).
- Detail URL pattern: none — the card IS the detail (accordion expansion);
  apply = mailto (email-apply rows are wire-able: detail = the card itself).
- Detail shape: same card (full JD in hidden collapsible body).
- Counts: total 6 rows; US rows 5 (2× Greater Boston Area, 2× Bay Area CA,
  1× North America); non-CN rows 6; CN rows 0 (CN hiring separate). No "remote"
  wording in any JD.
- Sample titles (verbatim, all 6): `Technical Support Engineer - EMQX Cloud`,
  `Regional Vice President of Sales`, `Sr. Strategic Sales Executive`,
  `Technical Account Executive, Commercial - West`, `Sales Representative`,
  `Associated Marketing Manager`.
- Sample locations (verbatim, all 6): `Europe`, `Greater Boston Area`,
  `Greater Boston Area`, `Bay Area, CA`, `North America`, `Bay Area, CA`.
- postedOn source: none (only blog/news dates exist in page payload, not jobs).
- Adapter kind recommendation: new class `static_collapse_cards` (SSR email-apply
  cards; shareable pattern) — geo_scope: us (5/6 rows US).
- Raw evidence: samples/emqx.html (live SSR page, all 6 cards).
