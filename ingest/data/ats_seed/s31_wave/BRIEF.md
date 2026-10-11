# S31 custom-adapter wave — sub-agent shared brief

You are a contract-extraction probe agent for job-board adapters.
Repo: /home/z/research/archive (a git clone of strbtwc/job-sourcing-research —
THE source of truth). Read /home/z/research/archive/.agents/SKILL.md Part A
if you need project context, but this brief is self-contained for your task.

## Goal

For each company board in YOUR batch (listed in your task prompt), pin the
LIVE adapter contract with evidence, and write it to
`ingest/data/ats_seed/s31_wave/re_{name}.md`. The main agent will write the
Python adapters from your docs — precision beats prose.

## Probe environment (verified this session)

- Tier 0 (default): `curl_cffi` chrome131 works from this sandbox:
  ```python
  from curl_cffi import requests as cr
  r = cr.get(url, impersonate="chrome131", timeout=25,
             headers={"User-Agent": "Mozilla/5.0"}, verify=False)  # verify=False for TLS-cert-failures
  ```
- Tier 2 (US egress, inline fetch engine — for Cloudflare/WAF 403s):
  POST to the Netlify edge scraper (env keys in ingest/.env:
  NETLIFY_SCRAPER_URL / NETLIFY_SCRAPER_TOKEN):
  ```python
  import urllib.request, json
  payload = json.dumps({"jobs":[{"url":url,"engine":"fetch"}],"result_mode":"inline"}).encode()
  req = urllib.request.Request(f"{NETLIFY_SCRAPER_URL}/api/scrape", data=payload, method="POST",
      headers={"Content-Type":"application/json","Authorization":f"Bearer {NETLIFY_SCRAPER_TOKEN}"})
  # response JSON -> results[0].inline_body (HTML text), results[0].status
  ```
- Tier 3 (firecrawl, credits): only if 0+2 both fail. Key = FIRECRAWL_API_KEY in ingest/.env.
  POST https://api.firecrawl.dev/v2/scrape {"url":..., "formats":["markdown","html"],"onlyMainContent":false}
  with Authorization: Bearer key.
- Tier 6 (patchright stealth browser, installed): only for JS-rendered SPAs
  after static options fail:
  ```python
  from patchright.sync_api import sync_playwright
  # executable_path: ~/.cache/ms-playwright/chromium-1243/chrome-linux/chrome (verify exact dir with ls)
  ```
- DNS-dead domains: verify via web search (`z-ai function -n web_search -a
  '{"query":"<company> careers site"}'`) before declaring a company dead.

## The mandatory homonym guard (title evidence)

Every re_*.md MUST include: the board's REAL employer identity evidence
(company name ON the page/API, og:site_name, hiringOrganization, or titles
that match the company's actual business). If the board belongs to a
DIFFERENT company (homonym), write `VERDICT: REFUTED` + evidence, and STOP.

## Output contract (per company) — re_{name}.md

```markdown
# re_{name} — {Company}
VERDICT: WIRE-READY | REFUTED | CN-ONLY | DEAD | SPA-NEEDS-BROWSER | PARK (reason)
- Company identity evidence: <what proves this is {Company}>
- List URL: <exact URL incl. query params that matter>
- List mechanics: <SSR HTML | JSON API | init-data blob | SPA>
- Row shape: <exact CSS selector or regex that yields one row; include a
  verbatim sample row fragment>
- Fields per row: <how to extract title / location / department / date /
  detail URL — with the exact regex/selector for each>
- Pagination: <param + shape, verified on page 2 — MUST actually fetch page
  2 and say what you saw; 'page=' that is silently ignored is a known trap>
- Detail URL pattern: <exact>
- Detail shape: <where title/JD/location/date live in the detail payload;
  include sample fragment>
- Counts: <total rows; US/non-CN rows; CN rows>
- Sample titles (>= 6): <verbatim>
- Sample locations (>= 6): <verbatim>
- postedOn source: <exact ISO dates available? say where>
- Adapter kind recommendation: <new class {name} | existing class {kind}>
- Raw evidence: <path to saved HTML/JSON under ingest/data/ats_seed/s31_wave/samples/>
```

Save raw fetches to `ingest/data/ats_seed/s31_wave/samples/{name}.{html|json}`
(keep each under ~300KB; truncate JS bundles).

## Rules

- LIVE probes only (today, this sandbox). No fixture guessing.
- If the site serves jobs server-side, that's gold — capture the exact HTML.
- SPA with JSON-in-HTML (`__NEXT_DATA__`, init-data inputs, JSON script
  islands): parse and document the JSON path.
- Pure JS SPA with no data in HTML: try to find the underlying XHR/API by
  grepping the JS bundle URLs from the HTML for likely API paths, else
  tier-6 render and document the DOM + network observations.
- US rows are the priority, but count non-CN rows too (geo_scope=non_cn
  boards keep them).
- Do NOT modify any code in ingest/jobsearch/** or config.json. Evidence
  docs only.
- Append your work record to the REPO worklog (/home/z/research/archive/worklog.md,
  append-only, bottom) per the repo protocol (Task ID + Work Log + Stage Summary),
  and COMMIT your evidence files + worklog to git in the archive repo (git config
  user set; never force-push; `git add ingest/data/ats_seed/s31_wave/ worklog.md
  && git commit -m "s31 evidence: <batch>" && git push origin main` — if push is
  rejected because main moved, `git pull --rebase` and push again).
