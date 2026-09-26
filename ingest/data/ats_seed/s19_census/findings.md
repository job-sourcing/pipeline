# S19 census corrections — re-audit of "closed" verdicts with the agent-fetch-kit

> Trigger: the user's directive to re-apply the vendored `tools/agent-fetch-kit`
> (wfetch ladder: local curl_cffi → supabase 15-region rotating AWS → firecrawl →
> zenrows → gha remote compute → patchright stealth browser) against the
> previously-closed blocked surfaces, after run #62 revealed the xiaohongshu
> GHA-egress block had silently LIFTED.

## 1. Run #62 audit (the first scheduled 30-watch steady state, 2026-09-25T12:16Z)

- Schedule fired ~5.5h late (12:16Z vs 06:45Z nominal) — within the known GHA
  slack window; run GREEN, all 30 watches `complete`.
- **xiaohongshu: egress block LIFTED** — the board served 20 rows from the GHA
  US runner (`[watch] xiaohongshu: 20 rows … total=20`). The declared
  `egress_restricted=gha_us` degraded gracefully during the block and
  auto-recovered the moment the TLS block lifted — exactly the designed
  contract (streak reset, 3 legs today, state untouched through the outage).
- INGEST_ENV secret materialized fine (107 lines; the "not set" warning in the
  raw log is the script's `else` branch echoed in the source dump, not executed).
- Board churn vs the S18-close CSVs: **-44 rows net across 14 companies**
  (nvidia -28, anthropic -7, netflix -5, tencent -3, bytedance -2, gea -2, and
  singles; gotion +5, faradayfuture +1, xpeng +1). unitedimaging's "Account
  Executive - Iowa" gone; gea REQ-26784 repost (startDate reset 09-10 → 09-25).
- Mirror: the fresh sandbox re-learned the `core.fileMode false` lesson (13
  pure-mode-change files in the sync commit); fixed on the new clone.

## 2. Kwai (Kuaishou) — the S18 "406 from both egresses, definitively closed"
## verdict is SUPERSEDED

The kit round proved the 406 is a **root-path-only WAF rule, not a board-level
block**:

| Surface | From local HK | From kit backends |
|---|---|---|
| `GET /` (root page) | **406** (any UA, any Accept; real Chromium too) | 406 (supabase AWS, GHA US), 200-with-0-bytes (firecrawl — a block dressed as success), 422 (zenrows, needs JS render) |
| `GET /wday/cxs/kwai/External` | SPA shell 200 | SPA shell 200 (supabase) |
| `POST /wday/cxs/kwai/{site}/jobs` | **API OPEN** — 404 `S21 not found: Job_Posting_Site_ID` (Workday's own API error, NOT the WAF) | n/a (POST can't ride the GET proxies) |

So the S18 board-probe failed at site-discovery (root fetch) and never reached
the API. The remaining blocker is ONLY the job-posting-site slug:

- slug dictionary sweep (35+ candidates, `External`, `Careers_at_Kwai`,
  `Kuaishou`, `Kwai_Brasil`, …) — all 404:233 invalid-URL (Workday's
  `community.workday.com/invalid-url` redirect = a clean slug VALIDATOR;
  control `jd.wd103/Careers_at_JD` → 200 shell).
- No indexed job-detail URLs anywhere: z-ai web search, Bing (decoded
  redirector links), DDG (202-challenged), Common Crawl (6 indexes, no
  captures), Wayback (CDX unreachable from this egress).
- Kwai's own properties point elsewhere: `usrdc.kuaishou.com` (US R&D center)
  says "submit your application at zhaopin.kuaishou.cn" — **US recruiting runs
  on the CN portal, not the workday board**; indexed workday titles ("Agency
  sales manager", "Commercial Operation", overseas-advertising JDs) match the
  Brazil/SEA commercial-ops profile.

**Corrected verdict:** root-WAF-blocked but CXS-API-open; site slug
undiscoverable from every public surface; US hiring intentionally on the CN
zhaopin portal (out of D2 scope). Census class: `no-structured-us-surface /
slug-locked`. A human sharing the board URL makes this a config-only wire
(tenant `kwai`, instance `wd3`).

**Generalized lesson (SKILL §22):** a WAF verdict on the ROOT page is NOT a
board verdict — probes must test the API path separately and record
`root_blocked_api_open` as a distinct class from `unreachable`.

## 3. Genspark — the S18 "CF-hardened even in browser" verdict is CORRECTED

- `https://www.genspark.ai/careers` → server 200 (56KB Nuxt SPA shell) to a
  **plain curl**; the SPA's client router then renders its own 404 view (the
  route does not exist). `/jobs`, `/about`, `/company` → server-side 404s.
- Rendered homepage (patchright stealth) sitemap: NO careers/jobs/join/team
  link anywhere in the nav or footer (tools pages only).
- **Corrected verdict:** the site is fully reachable (no CF challenge on any
  path we hit); Genspark simply has **no public careers page** — LinkedIn is
  the only jobs surface (the PixVerse/Manus class). The S18 "CF-hardened"
  reading conflated the SPA's client-side 404 view with a block page.

## 4. Kit health + kit bugs found this round

- Backends healthy: local (curl_cffi), supabase, firecrawl, zenrows, browser
  (patchright, after `pip install patchright curl_cffi` — fresh-sandbox gotcha
  again); `gha` mode unconfigured in the kit (needs GH_TOKEN env).
- Kit bug 1: `--mode zenrows --render` → API 400 `REQS004` (invalid params —
  the render flag combination is broken in this vendored build).
- Kit bug 2: `wfetch --json` never includes the body inline — use `--out` +
  `bytes` field (bites anyone testing with --json alone).
- Kit bug 3: firecrawl "200 with 0 bytes" (kwai root) is indistinguishable
  from success in metadata — always check `bytes > 0`, not `status == 200`.
- ZenRows is live again for normal URLs (the EXHAUSTED note is stale) but
  refuses kwai root with RESP001 "try enabling javascript rendering".
