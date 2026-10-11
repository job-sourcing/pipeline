# re_biomere — Biomere (Biomedical Research Models; JOINN Laboratories' US preclinical CRO, Worcester MA + Richmond CA)
VERDICT: WIRE-READY (the real board is a JazzHR board at biomere.applytojob.com — tier-0 crawlable, no WAF; careers page itself is CF-walled → Netlify)
- Company identity evidence: JazzHR board `<title>Biomedical Research Models -
  Career Page</title>` (Biomere = Biomedical Research Models); WP careers page
  copy: "Biomere is a preclinical CRO based in Worcester, MA, and Joinn Labs is
  a preclinical CRO based in Richmond, CA"; dept label "Joinn Labs" on the job;
  apply emails @biomere.com. Matches JOINN's US CRO.
- List URL: `https://biomere.applytojob.com/` (JazzHR board — tier-0 curl_cffi
  200 from HK egress, plain SSR HTML, no challenge).
  Discovery chain: `https://biomere.com/careers/` is CF-walled from HK (403)
  but 200 via Netlify US egress (inline_body capped at 32KB — use
  /wp-json/wp/v2/pages?slug=careers for the full content); that WP page embeds
  `<script src="https://app.jazz.co/widgets/basic/create/biomere">` → the real
  jobs live on the JazzHR subdomain.
- List mechanics: SSR HTML (JazzHR standard job board markup).
- Row shape: `div.job-board-list-wrapper ... ul.list-group > li.list-group-item`;
  title+URL = `h3.list-group-item-heading > a`; location = the `<li>` following
  `i.fa-map-marker`; department = the `<li>` following `i.fa-sitemap`;
  department group header = `div.department-heading > h3[id]`.
  Verbatim sample row fragment:
  ```html
  <li class="list-group-item">
      <div class="department-heading">
          <h3 id="joinn-labs">Joinn Labs&nbsp;</h3>
      </div>
      <h3 class='list-group-item-heading'>
          <a href="https://biomere.applytojob.com/apply/xV7VfII3ME/Facility-Technician">
              Facility Technician                            </a>
      </h3>
      <ul class='list-inline list-group-item-text'>
          <li><i class='fa fa-map-marker'></i>Richmond, CA, CA</li>
          <li><i class='fa fa-sitemap'></i>Joinn Labs</li>
      </ul>
  </li>
  ```
- Fields per row: title = `h3.list-group-item-heading a` text; detail URL = same
  `a` href; location = text after `i.fa-map-marker`; department = text after
  `i.fa-sitemap` (or the department-heading). NOTE: location string has a
  duplicated-state quirk ("Richmond, CA, CA" verbatim).
- Pagination: `?page=2` (standard JazzHR). VERIFIED PAGE 2 (fetched live):
  `https://biomere.applytojob.com/?page=2` → 200, byte-identical HTML to page 1
  (96453 bytes) — board has a single page (1 job), so the param is inert today
  but is the correct mechanism when >1 page appears (JazzHR renders
  `.pagination` + "Next" links when needed).
- Detail URL pattern: `https://biomere.applytojob.com/apply/{shortId}/{Job-Title-Slug}`
  (e.g. /apply/xV7VfII3ME/Facility-Technician).
- Detail shape: SSR page; header block = title, "Richmond, CA, CA", "Full Time",
  "Joinn Labs", "Mid Level"; body = "GENERAL SUMMARY / MAJOR RESPONSIBILITIES /
  JOB REQUIREMENTS / PHYSICAL REQUIREMENTS" text sections; apply = JazzHR form
  on the same page. Details crawlable tier-0 (verified 200, 118KB).
- Counts: 1 row; US 1; non-CN 1; CN 0.
- Sample titles (only 1 exists): "Facility Technician".
- Sample locations (verbatim): "Richmond, CA, CA".
- postedOn source: none visible in the JazzHR list or detail markup.
- Adapter kind recommendation: existing class `jazzhr` (if the repo already has
  one — standard applytojob.com markup; hostname biomere); else new class
  `jazzhr`. Homepage careers page also documents email-apply
  careers@biomere.com but the board is the wire-able surface.
- Raw evidence: samples/biomere_jazzhr_list.html (96KB),
  samples/biomere_jazzhr_detail.html (15KB detail segment),
  samples/biomere_careers_wp_content.html (8KB WP REST rendered content showing
  the jazz.co widget embed).
