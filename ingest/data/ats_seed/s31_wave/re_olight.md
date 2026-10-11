# re_olight — Olight (flashlight maker, Shenzhen; US ops Lorton VA)
VERDICT: PARK (careers path is broken — redirects to the storefront homepage; no job rows anywhere on olight.com)
- Company identity evidence: og:site_name "Olight Official Store", `<title>Olight
  Official Premium Flashlights & Illumination Tools - Olight</title>`; product
  copy (ArkPro/Marauder flashlights) matches the Shenzhen flashlight maker.
  NOTE: the t0 sample (`audit/s31_probe/samples/olight.html`, 348KB) is
  actually the REDIRECTED STOREFRONT HOMEPAGE — the t0 "static-listing" label
  was a misread; there were never job rows in it.
- List URL: `https://www.olightworld.com/pages/careers` → 301 →
  `https://www.olight.com` (storefront home; VERIFIED LIVE with
  allow_redirects=False). `https://olight.com/pages/careers` → 302 loop on
  itself (curl error 47: max redirects). `https://www.olight.com/pages/careers`
  → 404. `/about-us` (200) contains ZERO career/job links.
- List mechanics: none — no jobs page exists on the domain.
- Row shape / fields / pagination / detail: N/A.
- Counts: 0 rows (US 0, non-CN 0, CN 0). Web search shows Olight US hiring
  happens via Indeed postings (e.g. "Warehouse Supervisor, olight, Lorton VA
  $5,000-6,500/mo") — no owned board.
- Sample titles/locations: none on this domain (Indeed has 2+ Olight US posts
  but that is Indeed's corpus, not a wire-able board).
- postedOn source: none.
- Adapter kind recommendation: none — PARK (board dead). If revival needed,
  watch olight.com for a careers path or find their ATS (none detected today).
- Raw evidence: samples/olight.html (redirect-target storefront home,
  truncated), samples/olight_about.html (about-us page, no career links).
