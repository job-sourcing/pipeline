# re_juicefs — JuiceFS (Juicedata, cloud-native distributed filesystem)
VERDICT: WIRE-READY
- Company identity evidence: `<title>Careers - JuiceFS</title>`; JD copy "JuiceFS
  is looking for a customer-focused Solutions Architect ... cloud-native data
  infrastructure for AI, Kubernetes, analytics" — matches the JuiceFS
  open-source/enterprise filesystem company. Email apply: `team@juicedata.io`
  (Cloudflare-email-obfuscated on page; decoded from `data-cfemail`).
- List URL: `https://www.juicefs.com/en/careers` (canonical: juicefs.com)
- List mechanics: SSR HTML (Alpine.js page, static accordion cards).
- Row shape: `section#career` → `div.grid.divide-y > div.py-5[id="{slug}"]` →
  `<details class="group">`; verbatim fragment:
  ```html
  <div class="py-5" id="solution-architect">
    <details class="group">
      <summary class="flex justify-between items-center cursor-pointer list-none">
        <span class="text-2xl">Solutions Architect, North America</span>
        ...
      </summary>
      <div class="text-neutral-600 mt-3 group-open:animate-fadeIn jd">
        <h2 id="about-the-role">About the Role</h2><p>JuiceFS is looking for ...</p>
        ... <h2 id="compensation">Compensation</h2>
        ... <h2 id="location-and-work-authorization">Location and Work Authorization</h2>
        <p>This is a remote position within the United States.</p>
  ```
  Row regex: `<div class="py-5" id="([^"]+)">(.*?)</div>\s*(?=<div class="py-5"|$)`.
- Fields per row:
  - title: `<summary[^>]*>.*?<span class="text-2xl">([^<]+)</span>`
  - slug/anchor: the row `id` (`solution-architect`) → detail URL
    `https://www.juicefs.com/en/careers#solution-architect`
  - JD: `div.jd` inner HTML (About the Role / What You Will Do /
    Compensation / Location and Work Authorization sections)
  - salary: `The On-Target Earnings range for this position is USD 130,000–180,000
    per year` (in JD text)
  - apply email: `team@juicedata.io` (page-level, "send your resume to ...")
  - date: NOT available
- Pagination: none — 1 row on the page (no page-2 concept).
- Detail URL pattern: `https://www.juicefs.com/en/careers#{row-id}` (the card is
  the detail; the page JS auto-expands `details` for hash links).
- Detail shape: same card (full JD HTML with headings/`data-block-key` attrs).
- Counts: total 1 row; US rows 1 (remote within the United States; Bay Area
  preferred); non-CN 1; CN 0.
- Sample titles: `Solutions Architect, North America` (only row).
- Sample locations: `remote position within the United States` (Bay Area
  preferred; Mandarin+English fluency required).
- postedOn source: none (no dates on page).
- Adapter kind recommendation: new class `static_accordion_cards` (SSR
  email-apply accordion; same family as emqx/greptime) — geo_scope us.
- Raw evidence: samples/juicefs.html (live SSR page with the full job card).
