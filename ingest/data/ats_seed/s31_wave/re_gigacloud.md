# re_gigacloud — GigaCloud Technology (Nasdaq: GCT; B2B furniture logistics, El Monte CA / Savannah GA)
VERDICT: WIRE-READY (CF-walled — needs stealth tier (firecrawl) for both list and detail; 1 live US row)
- Company identity evidence: detail page says `Employer name: GigaCloud Technology
  (USA) Inc.`; careers page heading "JOIN THE GIGA FAMILY"; site footer
  "©Copyright 2026 GIGACLOUD TECHNOLOGY (USA) INC." with links to
  investors.gigacloudtech.com. All matches the Nasdaq-listed GigaCloud Technology.
- List URL: `https://www.gigacloudtech.com/careers/`
- List mechanics: SSR HTML (Nuxt/Vue SSR, `data-v-*` attrs) — but served only
  after Cloudflare JS check. Tier-0 curl_cffi AND Netlify US egress both get
  403 CF challenge (domain-level, not path-scoped: `/` is 403 too).
  WORKAROUND VERIFIED: firecrawl stealth (formats:["html"], waitFor 3000)
  returns the rendered SSR DOM. Patchright not needed.
- Row shape: each opening renders a heading span + LEARN MORE link:
  `span[role="heading"][aria-level="2"]` (title) inside a card div; detail URL
  from `a[href^="https://www.gigacloudtech.com/careers/"]`.
  Verbatim sample row fragment (from firecrawl DOM):
  ```html
  <div class="box-border w-27.5 flex flex-shrink-0 px-10px py-18px" data-v-ce0f9d8f="">
    <div un-border="1px solid brown-orange" class="w-full flex flex-col items-center gap-1.875 py-5.5" data-v-ce0f9d8f="">
      <span class="max-w-77% flex-1 text-center lh-[1] font-poppins text-h5" role="heading" aria-level="2" data-v-ce0f9d8f="">Inventory Analyst</span>
      <nav aria-label="undefined detail" data-v-ce0f9d8f="">
        <a href="https://www.gigacloudtech.com/careers/inventoryanalyst" ...>
          <button class="giga-button giga-button-primary medium" ...> LEARN MORE </button>
  ```
- Fields per row: title = `span[role="heading"][aria-level="2"]` text; detail
  URL = the sibling `<a href>` LEARN MORE link; department/date = not present
  in the list card. Everything else (location, salary, apply) is on the detail page.
- Pagination: N/A — 1 opening on the page, no pagination control in DOM
  (checked: the "JOB OPENINGS" flex-wrap container has a single card).
- Detail URL pattern: `https://www.gigacloudtech.com/careers/{slug}` (e.g.
  `/careers/inventoryanalyst` — no hyphens in current slug).
- Detail shape: SSR page, plain label/value text under "Job Opening":
  `Job title: Inventory Analyst`, `Employer name: GigaCloud Technology (USA) Inc.`,
  `Minimum Requirement: Bachelor's degree in Actuarial Science or Business Analytics required.`,
  `Salary: $51,085 to $69,600 per year.`,
  `Work Location: 133 Coleman Blvd, Suite #200, Savannah, GA 31408.`,
  `How to apply: Interested candidates should contact Human Resources at GigaCloud
  Technology (USA) Inc. Human Resources Department 4388 Shirley Ave El Monte, CA 91731`.
  This is a PWD-style (PERM prevailing-wage) posting — location+salary+minimum
  requirements are present but no postedOn date.
- Counts: 1 row; US 1 (Savannah, GA); non-CN 1; CN 0.
- Sample titles (only 1 exists): "Inventory Analyst".
- Sample locations: "133 Coleman Blvd, Suite #200, Savannah, GA 31408".
- postedOn source: none on page (no dates in list or detail).
- Adapter kind recommendation: new class `gigacloud` (static-ish SSR page behind
  CF; needs stealth transport; single-page, no pagination; detail = label/value
  text scrape).
- Raw evidence: samples/gigacloud_careers_firecrawl.html (20.6KB),
  samples/gigacloud_detail_firecrawl.html (14.8KB).
