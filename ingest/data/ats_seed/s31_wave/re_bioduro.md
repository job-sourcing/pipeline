# re_bioduro — BioDuro-Sundia
VERDICT: WIRE-READY
- Company identity evidence: `<title>Open Positions &amp; Vacancies | BioDuro</title>`; `og:site_name="BioDuro"`; inline JD states "BioDuro is one of the premier Contract Research, Development and Manufacturing Organizations (CRDMO) globally... Our US operations are US headquartered in Irvine, CA" (Chinese-owned CRDMO — Sundia acquired BioDuro 2017; US HQ now stated as Irvine, CA, not San Diego). No homonym conflict.
- List URL: `https://www.bioduro.com/open-positions/` (the `/careers/` page is marketing; it links "open positions" to this URL). `https://www.bioduro-sundia.com/careers/` and `/open-positions/` serve the SAME content (200, no redirect; canonical points to bioduro.com).
- List mechanics: SSR HTML (WordPress, theme `mmbio`; jobs are Elementor accordion items with schema.org Question/Answer markup).
- Row shape: `div.c-list-expandable__item` (attributes `itemscope itemprop="mainEntity" itemtype="https://schema.org/Question"`). Verbatim sample row fragment:
  ```html
  <div class="c-list-expandable__item" itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
      <div class="c-list-expandable__title-wrap js-item-expandable">
          <a class="c-list-expandable__link" data-expandable="expandable-3-1">
              <h3 class="c-list-expandable__title body-medium" itemprop="name">
                  Director/Senior Director, Business Development
              </h3>
              <div class="c-list-expandable__subline">Full time | Drug Discovery (EU)</div>
          </a>
  ```
  JD inline: `div.c-list-expandable__inner` (`id="expandable-3-{n}"`, `style="display: none;"`, itemprop="acceptedAnswer") → `div.c-content.c-list-expandable__content` with `<h4>What We Do</h4>…<h4>Job Overview/Summary</h4>…<h4>Essential Functions and Responsibilities</h4><ul>…`.
- Fields per row: title = `h3.c-list-expandable__title` (itemprop name); subline = `div.c-list-expandable__subline` = `{Full time|Part time} | {Department (Region)}` — employment type | department + region in parens; location = parse region from subline parentheses (e.g. "(EU)"); date = none; detail URL = none (no per-job links; `a.c-list-expandable__link` only toggles the accordion). Apply = `mailto:humanresources@bioduro.com` (page-level CTA).
- Pagination: none — 1 item today, no load-more/pagination controls (0 occurrences of load-more/pagination in HTML).
- Detail URL pattern: N/A (accordion-only).
- Detail shape: N/A — JD inline in `.c-list-expandable__inner .c-content`.
- Counts: total rows = 1. US rows = 0; non-CN rows = 1 (`Director/Senior Director, Business Development`, Full time | Drug Discovery (EU)); CN rows = 0. (Thin but non-CN → geo_scope=non_cn candidate.)
- Sample titles (>= 6): only 1 row today — `Director/Senior Director, Business Development`.
- Sample locations (>= 6): only 1 location value — `EU` (from subline "Drug Discovery (EU)").
- postedOn source: none — no posting dates in rows (page `article:modified_time` 2026-10-06T14:41:52+00:00 only).
- Adapter kind recommendation: new class `bioduro` (single SSR accordion page).
- Raw evidence: ingest/data/ats_seed/s31_wave/samples/bioduro_openpositions.html (board), bioduro.html (/careers/ marketing page).
