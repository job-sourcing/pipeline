# re_mce — MedChemExpress / HaoYuan MedChemExpress (MCE, life-science reagents; US HQ Monmouth Junction NJ)
VERDICT: WIRE-READY (CF-walled — firecrawl stealth verified; 8 rows, 5 US; email-apply board)
- Company identity evidence: page `<title>Careers | MedChemExpress</title>`;
  page copy "Headquartered in the United States, MedChemExpress LLC ("MCE")
  operates branch offices and warehouses in Europe and China"; jobs mention
  "Monmouth Junction, NJ 08852" and `hr@medchemexpress.com`. Matches the
  MedChemExpress (HaoYuan) company.
- List URL: `https://www.medchemexpress.com/careers.html`
- List mechanics: SSR HTML, one static page containing ALL job listings inline
  (no per-job detail pages). Domain is Cloudflare-walled at domain level:
  tier-0 curl_cffi 403, Netlify US fetch 403. WORKAROUND VERIFIED: firecrawl
  stealth (formats:["html"], waitFor 3000) → 200, 1.86MB DOM. (The census said
  careers.html live from US egress with browser UA; from this sandbox only
  firecrawl passed.)
- Row shape: each job is an `<h2>` (job title) followed by a
  `div.pages_sub_tit > span.second-tree-title` "Full Job Description" label and
  the full JD text in `div.pages_text`.
  Selector: `<h2 class="text-xl[^"]*"[^>]*>([^<]+)</h2>` (exactly 8 matches in
  the careers segment).
  Verbatim sample row fragment:
  ```html
  <h2 class="text-xl text-lg-5xl font-500 text-190C39 pt-24 w-full">Sales Representative/Manager</h2>
  <div class="pages_sub_tit mt-20 font-500"><span class="second-tree-title">Full Job Description</span></div>
  <div class="pages_text mt-10"><p class="text-sm text-lg-base leading-7 leading-lg-8">This is a full-time position with career advancement opportunities ...
  ```
- Fields per row: title = the `<h2>` text; location = first line matching
  `Location based ([^<]+)` inside the job's pages_text block (e.g. "Location
  based Monmouth Junction, NJ 08852"); JD = the pages_text block; apply email =
  `hr@medchemexpress.com` (per-job, appears after "Send Resume to:" in some
  rows); no dates, no per-row links.
- Pagination: N/A — single static page with all 8 openings, no pagination
  controls anywhere in the DOM.
- Detail URL pattern: none — detail lives on the list page itself. (The only
  in-domain links on the page are careers.html self-link and
  calculator-application.html.)
- Detail shape: N/A (see row shape above). Apply instruction per row:
  "Please submit a cover letter and resume to apply for this position (MS Word
  or pdf files) ... Send Resume to: hr@medchemexpress.com".
- Counts: 8 rows; US 5; non-US 3 (EU/global sales, EU marketing, Toronto); CN 0
  (Hong Kong appears inside a multi-country location string of one row).
- Sample titles (all 8, verbatim):
  "Sales Representative/Manager", "EU Marketing Support Manager",
  "Mandarin Bi-lingual Purchasing & Logistics Specialist",
  "Mandarin Bilingual Shipping/Warehouse Associate",
  "Sales Specialists in the Recombinant Protein Product Line",
  "Sales Specialist in the Antibody Product Line",
  "Regional Sales Specialist/Manager", "Regional Sales Manager".
- Sample locations (verbatim): "Belgium, France, Germany, India, Italy, Japan,
  Netherlands, Spain, UK, Hong Kong, Switzerland"; "UK, France, Germany or
  central area of Europe"; "Monmouth Junction, NJ 08852"; "California, Arizona,
  Utah, Colorado// Maryland, Pennsylvania, Delaware, New Jersey";
  "California, Massachusetts"; "Toronto, Canada (Work from home)".
- postedOn source: none — no dates anywhere on the page.
- Adapter kind recommendation: new class `mce_static` (single-page email-apply
  board; requires stealth transport for CF; parse h2 + Location based regex).
- Raw evidence: samples/mce_careers_firecrawl.html (53KB careers segment cut
  from the 1.86MB firecrawl DOM).
