# re_amcrest — Amcrest Industries (security cameras; Houston TX)
VERDICT: WIRE-READY (CF-walled — firecrawl stealth verified for list + detail; 3 US rows; Magento jobposts module)
- Company identity evidence: board lives at `https://amcrest.com/careers/` (the
  company's own Magento storefront; support phone 1-888-212-7538 on every page;
  nav = IP Cameras/NVR/DVR product catalog matching Amcrest). All 3 jobs are in
  "Houston,Texas" = Amcrest HQ. Matches the census LCA context (2 filings, Houston).
- List URL: `https://amcrest.com/careers/`
- List mechanics: SSR HTML (Magento CMS + custom "jobposts" module). Domain is
  Cloudflare-walled at DOMAIN level (tier-0 curl 403 on `/` and `/careers/`;
  Netlify US fetch 403) — so the S30 "path-scoped" hypothesis is REFUTED: the
  block is domain-wide, and only firecrawl stealth passed (200, real DOM).
- Row shape: `ul.jobpost_list > li.item`; title+URL = `li.item .left h2 > a`;
  location = `li.item .left h3`; action buttons (Email/View/Apply) all point at
  the same detail URL.
  Verbatim sample row fragment:
  ```html
  <div class="job-list">
      <ul class="jobpost_list">
          <li class="item">
              <div class="left">
                  <h2><a href="https://amcrest.com/careers/index/detail/id/3">Product Tester</a></h2>
                  <h3>Houtson,Texas</h3>
              </div>
              <div class="right">
                  <a href="https://amcrest.com/careers/index/detail/id/3"><span>...<strong>Email</strong></span></a>
                  <a href="https://amcrest.com/careers/index/detail/id/3"><span>...<strong>View</strong></span></a>
                  <a href="https://amcrest.com/careers/index/detail/id/3"><span>...<strong>Apply</strong></span></a>
  ```
  (NOTE: "Houtson,Texas" typo is verbatim on the page.)
- Fields per row: title = `li.item h2 a` text; detail URL = same `a` href;
  location = `li.item h3` text; department/date = not present in list.
- Pagination: N/A — 3 rows, single page, no pagination module in the DOM (the
  only "pager" found is the Yotpo reviews widget, unrelated).
- Detail URL pattern: `https://amcrest.com/careers/index/detail/id/{n}` (n = 1..3 today).
- Detail shape: `div.job-detail.jobpost_details`:
  ```html
  <div class="job-detail jobpost_details">
      <div class="jobpost_details_left">
          <h2>NetSuite Developer</h2>
          <h4><span>Location</span>: Houston,Texas</h4>
          <h4><span>Job Code</span>: 002</h4>
          <h4><span># of Openings</span>: 1</h4>
          <a href="https://amcrest.com/careers/index/detail/id/2#jobpost_details">Apply</a>
      </div>
      <div class="jobpost_details_right">
          <h2>NetSuite Developer</h2>
          <p><span>The NetSuite Developer will design and implement ...</span></p>
  ```
  Apply = on-page multipart form POSTing to
  `https://amcrest.com/careers/index/applypost/id/{n}/` (form id
  `job-application-from`, enctype multipart/form-data) — email/file upload form.
- Counts: 3 rows; US 3; non-CN 3; CN 0.
- Sample titles (all 3, verbatim): "Product Tester", "NetSuite Developer",
  "ECommerce Associate".
- Sample locations (verbatim): "Houtson,Texas", "Houston,Texas", "Houston,Texas".
- postedOn source: none — no dates in list or detail.
- Adapter kind recommendation: new class `amcrest` (Magento jobposts module
  behind CF; needs stealth transport; rows = li.jobpost_list; details at
  /careers/index/detail/id/N).
- Raw evidence: samples/amcrest_careers_firecrawl.html (163KB),
  samples/amcrest_detail_firecrawl.html (12KB detail segment).
