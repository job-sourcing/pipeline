# re_ugreen — Ugreen Electronics (SZ301606; charging/accessories; US ops)
VERDICT: WIRE-READY (single static Shopify page, tier-0 crawlable; 1 US row, email-apply)
- Company identity evidence: page `<title>Join Us &ndash; UGREEN US</title>`; body
  copy "On behalf of the UGREEN brand, carry out business development... NAS
  products... in the United States"; apply email @ugreen.com. Matches Ugreen.
  (The census "careers via own CN portal; US roles on LinkedIn only" is now
  outdated: www.ugreen.com/pages/careers 404s, but the US store keeps a live
  join-us page.)
- List URL: `https://us.ugreen.com/pages/join-us`
  (discovery: `https://www.ugreen.com/pages/join-us` → 301 → us.ugreen.com;
  `https://www.ugreen.com/pages/careers` → 404; Ugreen's US sitemap has 53
  pages, none career-related — the join-us page is reachable only via the
  redirect, not the sitemap).
- List mechanics: SSR HTML (Shopify page; 1.18MB DOM, mostly nav). Tier-0
  curl_cffi 200 — no WAF.
- Row shape: the page contains exactly ONE job, pasted as rich text inside
  `rte-formatter > article` (Shopify rte block, `data-clipboard-cangjie`
  attributes = pasted from a CN doc editor). There is no per-job markup —
  the whole posting is one text blob:
  ```html
  <rte-formatter>
    <article data-mce-fragment="1" data-clipboard-cangjie='[...]'>
      <p>Job description: On behalf of the UGREEN brand, carry out business
      development, mainly develop channels related to NAS products, ...</p>
      ... Responsibility: 1. Responsible for the channel development of NAS
      products in the United States ... Job requirements: ... 4. Accept
      business trips across the United States Recruit 2 people, send resume to
      email: linyejie@ugreen.com
  ```
- Fields per row: title = derive from the first "Job description:" line (no
  explicit title on page; role ≈ "NAS Channel Business Development (US)");
  location = "the United States" (from requirements; not a structured field);
  apply email = regex `send resume to email: ([a-zA-Z0-9._-]+@ugreen\.com)`;
  openings = `Recruit (\d+) people`. No dates, no links.
- Pagination: N/A — single static page, no pagination.
- Detail URL pattern: none — list page is the detail page.
- Detail shape: see row shape (full JD in the rte article).
- Counts: 1 row; US 1; non-CN 1; CN 0.
- Sample titles (only 1, derived): "NAS Channel Business Development (US)".
- Sample locations: "United States" (remote/travel, unstructured).
- postedOn source: none.
- Adapter kind recommendation: new class `ugreen_joinus` (or reuse a generic
  "shopify_static_email_apply" class): fetch page, regex the article text for
  "Job description:", "Recruit N people", "send resume to email: X".
- Raw evidence: samples/ugreen_joinus.html (6.8KB main-content segment).
