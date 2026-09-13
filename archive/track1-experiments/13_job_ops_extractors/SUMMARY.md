# job-ops Deep Analysis

Repository: `DaKheera47/job-ops` @ `/home/z/my-project/repos/DaKheera47__job-ops/`
Tag-line in README: "3.9k-star TypeScript web app, self-hostable via `docker compose up`".

## Architecture Overview

### What this repo actually is

A **monolithic, single-container web application** (Express + React + better-sqlite3 + Drizzle ORM) that bundles:

- A React 18 SPA (orchestrator client, ~500+ component files under `orchestrator/src/components/`)
- An Express JSON API server (`orchestrator/src/server/`) with multi-tenant scoping, JWT auth, SSE, scheduler
- 13 job-board extractor workspaces (`extractors/*`) — each is a separate npm workspace
- 3 career-board adapters (`career-boards/{bamboohr,greenhouse,workday}`) — used for the "Watchlist" feature (polling a company's own ATS), NOT for pipeline discovery
- 2 visa-sponsor providers (`visa-sponsor-providers/{uk,nl}`)
- A shared library (`shared/`, the `job-ops-shared` workspace) of pure-function utilities/types
- A Python sidecar for the `jobspy` extractor (`extractors/jobspy/scrape_jobs.py`)
- A Docusaurus docs site (`docs-site/`)

### Monorepo / Workspaces

`package.json` uses npm workspaces:

```json
"workspaces": ["orchestrator", "docs-site", "career-boards/*", "extractors/*", "shared"]
```

CI parity (from `AGENTS.md`): biome + tsc per workspace + vitest in orchestrator only. Node 22.

### Docker stack

`docker-compose.yml` defines **one** service (`job-ops`). No service mesh, no separate workers — everything is in one image. `Dockerfile` is multi-stage and bundles:

- Node 22 + npm workspaces (production deps)
- Python 3 + `playwright` + `python-jobspy` + Playwright Firefox
- `camoufox-js` patched-Firefox binaries (anti-detection)
- Tectonic + Typst (for local LaTeX resume rendering)
- `xvfb` + `x11vnc` + `novnc` + `websockify` (so a Cloudflare challenge can be solved in a *headed* browser inside the container)
- `@openai/codex` CLI and `@anthropic-ai/claude-code` CLI (for the local "app-server" LLM provider)
- `better-sqlite3` native module (rebuilt per Node ABI)

This is a **gigantic image**. Healthcheck on `:3001/health`, port published on `:3005`.

### Pipeline flow

`orchestrator/src/server/pipeline/{orchestrator.ts, steps/*}` implements a fixed sequence:

1. `load-profile` → fetch user profile JSON
2. `discover-jobs` → run selected extractor manifests via `asyncPool` (concurrency 3, 10-min timeout per source), dedupe via `deduplicateJobsByTitleAndEmployer` (Levenshtein ≥ 90 title / ≥ 85 employer, merges fields across dups)
3. `import-jobs` → insert into SQLite
4. `score-jobs` → for each unscored job, call LLM (concurrency 4), also compute visa-sponsor fuzzy match
5. `select-jobs` → pick top N by score
6. `process-jobs` → per-job: AI generates tailored summary/headline/skills, project-pick, PDF gen (Typst/Tectonic)
7. `watchlist-jobs` → optional BambooHR/Greenhouse/Workday polling
8. `notify-webhook` → outbound webhook

### `skills-lock.json`

NOT part of the project. It's a manifest of marketing/design skills (`ab-test-setup`, `copywriting`, `frontend-design`, etc.) cloned from `coreyhaines31/marketingskills` and `pbakaus/impeccable` GitHub repos. This is tooling noise from the maintainer's own agent workflow — irrelevant to the extractor system. Ignore.

### `AGENTS.md`

Engineering hygiene rules: API response shape `{ok, data/error, meta.requestId}`, correlation IDs, multi-tenancy defaults, structured logging via `@infra/logger`, redaction, extractor deployment checklist (Dockerfile stages + `deployment.test.ts`), CI parity commands. Confirms repo is single-container, multi-tenant, with strict extractor deployment gates.

---

## Per-Extractor Analysis

All extractors implement the `ExtractorManifest` interface from `shared/src/types/extractors.ts`:

```ts
interface ExtractorManifest {
  id: string;
  displayName: string;
  providesSources: readonly string[];
  requiredEnvVars?: readonly string[];
  capabilities?: { locationEvidence?: boolean };
  locationCapabilities?: Partial<Record<string, ExtractorSourceLocationCapabilities>>;
  run: (context: ExtractorRuntimeContext) => Promise<ExtractorRunResult>;
}
```

The runtime context (`ExtractorRuntimeContext`) is **opinionated** — it expects `settings: Record<string, string|undefined>`, `searchTerms: string[]`, `selectedCountry: string`, an optional `locationIntent`/`sourceLocationPlan` (built by orchestrator-side location-domain logic), `getExistingJobUrls()`, `shouldCancel()`, `onProgress()`. So extractors are *not* pure standalone functions — they expect the orchestrator's settings schema (e.g. `settings.jobspyResultsWanted`, `settings.searchCities`, `settings.workplaceTypes` as JSON string).

There are 13 extractor workspaces. **LinkedIn / Indeed / Glassdoor are NOT standalone scrapers** — they are all provided by the single `jobspy` workspace which shells out to Python's `python-jobspy` library.

### 1. hiringcafe — Hiring Cafe

- **Implementation**: HTTP `fetch()` against `https://hiring.cafe/` SSR pages, regex-extract `<script id="__NEXT_DATA__">` JSON, plus a secondary fetch to `https://hiring.cafe/job/{requisitionId}` for jobs missing descriptions. Also calls `nominatim.openstreetmap.org` for city geocoding.
- **LOC**: 820 (`run.ts`) + 100 (`main.ts`) + 92 (`default-search-state.ts`) + 74 (`country-map.ts`) + 147 (`manifest.ts`) ≈ **1,233 LOC**
- **Auth required**: No
- **Output schema**: `CreateJobInput` with `source: "hiringcafe"`, `sourceJobId`, `title`, `employer`, `jobUrl`, `applicationLink`, `location`, `locationEvidence`, `salary`, `datePosted`, `jobDescription`, `jobType`, `skills`, `isRemote`
- **Quality**: 4/5 — Real job board, real search, real descriptions. Cloudflare challenge detected and surfaced as `challengeRequired` URL for human solve. No auth, no Playwright needed.
- **Standalone portable**: **Yes** — only depends on `@shared/location-support`, `@shared/search-cities`, `@shared/types/jobs`, `@shared/utils/type-conversion`. All pure functions.
- **Notes**: The manifest layer (`manifest.ts`) is what's wired into the orchestrator; the underlying `runHiringCafe()` function in `src/run.ts` is genuinely standalone and could be lifted by copying ~5 shared utility files.

### 2. startupjobs — startup.jobs

- **Implementation**: Imports `scrapeStartupJobsViaAlgolia` from external npm package [`startup-jobs-scraper`](https://www.npmjs.com/package/startup-jobs-scraper) (v0.3.0, published by DaKheera47 themselves). That package internally uses Crawlee + Playwright + Cheerio + Apify.
- **LOC**: 214 (`run.ts`) + 104 (`manifest.ts`) = **318 LOC** (plus ~112 KB of dependency code)
- **Auth required**: No, but requires `npx playwright install` to function
- **Output schema**: `source: "startupjobs"`, `title`, `employer`, `employerUrl`, `jobUrl`, `applicationLink`, `disciplines`, `deadline`, `salary`, `location`, `degreeRequired`, `starting`, `jobDescription`, `datePosted`, `jobType`, `isRemote`
- **Quality**: 3/5 — Wraps an external Playwright scraper of Algolia's private startup.jobs index. Works but adds significant dependency weight (Crawlee + Playwright).
- **Standalone portable**: **Partial** — you'd need to depend on the external npm package, install Playwright binaries, and lift 4 small shared utils. Functionally portable, but it's really just a thin mapper around a third-party Playwright scraper.

### 3. workingnomads — Working Nomads

- **Implementation**: HTTP `POST` to `https://www.workingnomads.com/jobsapi/_search` (Elasticsearch _search endpoint), JSON payload with bool query, filter by `locations` tokens, sort by premium+pub_date. Plain `fetch()`, no auth, no browser.
- **LOC**: 701 (`run.ts`) + 90 (`manifest.ts`) = **791 LOC**
- **Auth required**: No
- **Output schema**: `source: "workingnomads"`, `sourceJobId`, `title`, `employer`, `jobUrl`, `applicationLink`, `location`, `locationEvidence`, `jobDescription`, `datePosted`, `jobType`, `jobFunction`, `disciplines`, `skills`, `isRemote: true` (hardcoded — remote-only board)
- **Quality**: 5/5 — Cleanest extractor in the repo. Direct Elasticsearch query, real descriptions, comprehensive country/region/city token filtering logic (Europe/APAC/Africa/Middle East/Latin America sets baked in).
- **Standalone portable**: **Yes** — depends only on 3 shared utility modules, no external runtime deps beyond Node `fetch`.
- **Notes**: This is the gold standard for what a "lift this one extractor" looks like.

### 4. gradcracker — Gradcracker (UK STEM grads)

- **Implementation**: Spawns `npx tsx src/main.ts` as a subprocess. `main.ts` uses Crawlee's `PlaywrightCrawler` with Camoufox-patched Firefox, navigates `https://www.gradcracker.com/search/computing-technology/{role}-graduate-jobs-in-{location}?order=dateAdded`, intercepts Cloudflare challenges via `browser-utils`. `routes.ts` uses Cheerio to parse HTML list pages and detail pages. Cookie persistence to disk so `cf_clearance` survives runs.
- **LOC**: 970 (`run.ts`) + 147 (`main.ts`) + 365 (`routes.ts`) + 89 (`progress.ts`) + manifest ≈ **1,571 LOC**
- **Auth required**: No, but requires Playwright Firefox + Camoufox + cheerio + impit + browser-utils + ~1GB of browser binaries
- **Output schema**: custom local `CreateJobInput` (source `"gradcracker"`), fields: `title`, `employer`, `employerUrl`, `jobUrl`, `applicationLink`, `disciplines`, `deadline`, `salary`, `location`, `degreeRequired`, `starting`, `jobDescription`. Subset of shared `CreateJobInput`.
- **Quality**: 3/5 — Real UK grad board, well-tested, but heavily coupled to the `browser-utils` cookie/challenge infrastructure and Crawlee.
- **Standalone portable**: **No** — requires Camoufox, Playwright, Crawlee, the `browser-utils` workspace (~786 LOC across 6 files), cookie storage dir, and a Cloudflare challenge solver. Lifting this means lifting half the platform.
- **Notes**: Also spawns a child process unnecessarily (the parent `run.ts` reads JSON files written by the child). This pattern is repeated across several extractors.

### 5. golangjobs — Golang Jobs

- **Implementation**: HTTP `GET` against `https://mvjyjzestmcxxmmmakec.supabase.co/rest/v1/jobs` (Supabase REST API). Uses a **hardcoded public anon key** baked into the source (env-overridable). Paginates (200/page, max 10 pages).
- **LOC**: 454 (`run.ts`) + 89 (`manifest.ts`) = **543 LOC**
- **Auth required**: No (anon key is public, hardcoded as fallback)
- **Output schema**: `source: "golangjobs"`, `sourceJobId`, `title`, `employer`, `jobUrl` (constructed from slug), `applicationLink`, `location`, `datePosted`, `jobDescription`, `jobType`, `skills`, `isRemote`
- **Quality**: 4/5 — Real Go job board backed by Supabase. Clean. Limited to ~2,000 jobs total due to pagination cap.
- **Standalone portable**: **Yes** — only shared utils, no external runtime deps.
- **Notes**: Hardcoded anon key in source — fine for a public read-only board, but worth flagging.

### 6. adzuna — Adzuna

- **Implementation**: Two-layered. The orchestrator-facing `run.ts` spawns a child process (`npm run start` or `tsx src/main.ts`) with env vars. `main.ts` makes the real HTTP `GET` to `https://api.adzuna.com/v1/api/jobs/{country}/search/{page}` with `app_id`/`app_key` query params, paginates up to 100 pages × 50/page.
- **LOC**: 302 (`run.ts`) + 226 (`main.ts`) + manifest ≈ **635 LOC**
- **Auth required**: **Yes** — `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` (free tier from developer.adzuna.com)
- **Output schema**: `source: "adzuna"`, `sourceJobId`, `title`, `employer`, `jobUrl`, `applicationLink`, `location`, `locationEvidence`, `salary`, `datePosted`, `jobDescription`, `jobType`
- **Quality**: 4/5 — Real REST API, real auth, clean JSON output. The unnecessary subprocess indirection is awkward but the underlying API call is trivial.
- **Standalone portable**: **Yes** — `main.ts` is a ~226-line standalone script that needs only `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` env vars and writes JSON to disk.

### 7. ukvisajobs — UK Visa Jobs

- **Implementation**: Subprocess spawns `npx tsx src/main.ts`. `main.ts` uses **Playwright Firefox (Camoufox)** to: launch browser → sign in with email/password at `my.ukvisajobs.com/signin` → navigate to open-jobs page → intercept `https://my.ukvisajobs.com/ukvisa-api/api/fetch-jobs-data` API calls → cache auth session to disk → fall back to API + cached cookies on subsequent runs.
- **LOC**: 468 (`run.ts`) + 643 (`main.ts`) + manifest ≈ **1,135 LOC**
- **Auth required**: **Yes** — `UKVISAJOBS_EMAIL` + `UKVISAJOBS_PASSWORD` (paid account on my.ukvisajobs.com)
- **Output schema**: `source: "ukvisajobs"`, `sourceJobId`, `title`, `employer`, `employerUrl`, `jobUrl`, `applicationLink`, `location`, `deadline`, `salary`, `jobDescription`, `datePosted`, `degreeRequired`, `jobType`, `jobLevel`
- **Quality**: 3/5 — Valuable niche data (UK sponsorship jobs), but heavy Playwright+Camoufox dependency, subprocess pattern, and brittle auth flow.
- **Standalone portable**: **No** — requires Playwright+Camoufox+browser-utils+credentials. Lifting means lifting the entire browser stack.
- **Notes**: Has a per-process mutex (`isUkVisaJobsRunning`) to prevent concurrent runs. The richest schema of any extractor (visa_acceptance, likely_to_sponsor, definitely_sponsored fields available upstream but not all surfaced).

### 8. jobindex — Jobindex (Denmark)

- **Implementation**: HTTP `fetch()` of `https://www.jobindex.dk/jobsoegning` HTML pages, regex-extract a `<script>` block containing a `Stash` JSON payload (`jobsearch/result_app` storeData), parse `searchResponse.results`. No Playwright.
- **LOC**: 620 (`run.ts`) + 105 (`manifest.ts`) = **725 LOC**
- **Auth required**: No
- **Output schema**: Full `CreateJobInput` with `source: "jobindex"`, `sourceJobId`, `title`, `employer`, `employerUrl`, `jobUrl`, `applicationLink`, `location`, `locationEvidence` (with `evidenceQuality: "exact"|"approximate"`, lat/lon), `salary`, `datePosted`, `deadline`, `jobDescription`, `jobType`, `companyIndustry`, `companyLogo`, `companyUrlDirect`, `companyRating`, `companyReviewsCount`
- **Quality**: 5/5 — Cleanest "scrape-ish" extractor. Real board, real data, no Playwright, decent company enrichment.
- **Standalone portable**: **Yes** — only shared utils needed.

### 9. seek — Seek (Australia/NZ)

- **Implementation**: Uses the official `apify-client` npm package to call an Apify actor (`unfenced-group/seek-com-au-scraper`). The actor runs on Apify's cloud and returns a dataset; the extractor just maps results.
- **LOC**: 172 (`run.ts`) + 25 (`main.ts`) + manifest ≈ **270 LOC**
- **Auth required**: **Yes** — `APIFY_TOKEN` (Apify account with paid credits)
- **Output schema**: `source: "seek"`, `sourceJobId`, `title`, `employer`, `jobUrl`, `applicationLink`, `location`, `salary`, `salaryMinAmount`/`MaxAmount`/`Interval`/`Currency`, `datePosted`, `jobDescription`, `jobType`, `isRemote`
- **Quality**: 3/5 — Works but outsources the actual scraping to Apify's cloud. Adds a paid third-party dependency.
- **Standalone portable**: **Yes** — minimal code, but requires Apify account and tokens.

### 10. naukri — Naukri (India)

- **Implementation**: Uses **Playwright Firefox in-process** (not subprocess) to navigate `https://www.naukri.com/{keyword}-jobs[-in-{location}]`, intercept `https://www.naukri.com/jobapi/v3/search` JSON responses, extract `jobDetails` array. Includes Cloudflare challenge detection via `browser-utils`.
- **LOC**: 567 (`run.ts`) + 17 (`main.ts`) + manifest ≈ **684 LOC**
- **Auth required**: No
- **Output schema**: `source: "naukri"`, `sourceJobId`, `title`, `employer`, `employerUrl`, `jobUrl`, `applicationLink`, `location`, `locationEvidence`, `salary` (min/max/currency), `datePosted`, `jobDescription`, `jobType`, `jobLevel`, `jobFunction`, `companyIndustry`, `companyLogo`, `companyUrlDirect`, `companyNumEmployees`, `companyRating`, `companyReviewsCount`, `vacancyCount`, `workFromHomeType`, `experienceRange`, `skills`
- **Quality**: 4/5 — Rich data (India's largest board), but requires Playwright+Camoufox. Heavy.
- **Standalone portable**: **No** — in-process Playwright dependency.

### 11. fiveamsat — Khamsat (Egypt freelance)

- **Implementation**: HTTP `fetch()` of `https://khamsat.com/services/{query}` HTML page, Cheerio parse anchors with `href*="/service"` or `/services`, extract title/seller/price/description from card containers.
- **LOC**: 83 (`run.ts`) + 30 (`fetcher.ts`) + 125 (`parser.ts`) + 22 (`types.ts`) + manifest ≈ **285 LOC**
- **Auth required**: No
- **Output schema**: Minimal — `source: "fiveamsat"`, `sourceJobId`, `title`, `employer` (= seller), `jobUrl`, `applicationLink`, `salary`, `jobDescription`, `jobType: "Freelance / Project"`
- **Quality**: 2/5 — Fragile CSS-selector scraping of an Arabic-UI freelance micro-services marketplace. Misfit for a "jobs" pipeline (Khamsat sells $5 gigs, not software engineering roles).
- **Standalone portable**: **Yes** — clean, tiny, only Cheerio as external dep.

### 12. wazzuf — WUZZUF (Egypt)

- **Implementation**: HTTP `fetch()` of `https://wuzzuf.net/search/jobs/?q={query}` HTML, Cheerio parse anchors with `href*="/jobs/p/"`, extract title/employer/location/postedAt/salary/description/skills from card containers.
- **LOC**: 83 (`run.ts`) + 31 (`fetcher.ts`) + 153 (`parser.ts`) + 22 (`types.ts`) + manifest ≈ **319 LOC**
- **Auth required**: No
- **Output schema**: `source: "wazzuf"`, `sourceJobId`, `title`, `employer`, `jobUrl`, `applicationLink`, `location`, `datePosted`, `salary`, `jobDescription`, `jobType`, `skills`, `isRemote`
- **Quality**: 3/5 — Reasonable HTML scraping for a regional board. Fragile to UI redesigns.
- **Standalone portable**: **Yes** — clean, tiny, Cheerio only.

### 13. jobspy — LinkedIn + Indeed + Glassdoor

- **Implementation**: Subprocess spawns Python `scrape_jobs.py` which calls `from jobspy import scrape_jobs` (the [`python-jobspy`](https://github.com/Bunsly/JobSpy) library). python-jobspy internally uses Playwright/requests to scrape LinkedIn, Indeed, Glassdoor. Writes JSON+CSV to disk, parent TS reads JSON.
- **LOC**: 524 (`run.ts`) + 301 (`scrape_jobs.py`) + 122 (`verify_date_posted.py`) + manifest ≈ **947 LOC** of glue, plus the entire python-jobspy library as a dependency
- **Auth required**: No (but LinkedIn will rate-limit / Cloudflare-challenge aggressively without it; python-jobspy supports LinkedIn cookies)
- **Output schema**: Full JobSpy row mapped to `CreateJobInput` with all JobSpy-specific fields (salaryMinAmount/MaxAmount/Currency/Interval, companyIndustry, companyLogo, companyUrlDirect, companyAddresses, companyNumEmployees, companyRevenue, companyDescription, skills, experienceRange, companyRating, companyReviewsCount, vacancyCount, workFromHomeType, listingType, emails)
- **Quality**: 3/5 — This is the actual source of all "LinkedIn/Indeed/Glassdoor" jobs that the README advertises. They are NOT native TS extractors — they are wrapped by a Python library that uses its own scraping logic. Maintenance burden lives upstream in python-jobspy.
- **Standalone portable**: **Yes, but heavyweight** — requires Python 3 + `pip install python-jobspy pandas playwright` + `python3 -m playwright install firefox`. The TS wrapper is portable; the Python runtime is the friction.
- **Notes**: README "Special Thanks" credits jobspy. This is the linchpin extractor — the entire "LinkedIn + Indeed + Glassdoor" marketing claim depends on python-jobspy continuing to scrape those sites.

### `browser-utils` workspace (shared browser infrastructure)

Not an extractor. 786 LOC across `challenge.ts`, `cookies.ts`, `launch.ts`, `retry.ts`, `solver.ts`, `index.ts`. Depends on `camoufox-js` + `tough-cookie` + peer-deps `playwright`. Used by gradcracker, ukvisajobs, naukri, and startupjobs (transitively via startup-jobs-scraper). This is the anti-Cloudflare WAF infrastructure: cookie persistence, headless challenge detection, headed fallback via Xvfb+VNC.

---

## Orchestrator Analysis

- **Pattern**: `asyncPool` (custom bounded-concurrency pool, 1–10 workers, fail-fast on first error) drives `manifest.run()` calls for selected sources. Per-source 10-minute timeout. Watchlist sources polled separately. SSE pushes progress to the React UI.
- **Tied to web app**: **Yes, deeply.** The orchestrator imports `better-sqlite3` (native module), Drizzle ORM, JWT auth, multi-tenant scoping (`@server/tenancy/private-scope`), the settings repo, the profile service, the PDF service, the LLM service registry, the scheduler, the demo-mode service, backup scheduler, etc. It is the web app.
- **Runnable standalone**: **No.** There is no `pipeline:run` CLI that works without SQLite, the React UI built, profile JSON in DB, settings rows in DB, etc. Even `npm run pipeline:run` boots the full Express app context.
- **Dedup**: `deduplicateJobsByTitleAndEmployer` in `shared/src/job-matching.ts` — Levenshtein-based, title ≥ 90 / employer ≥ 85 similarity, with field-merging across near-duplicates (first non-null wins for each optional field). Better than MD5-of-URL dedup because it merges cross-source duplicates (LinkedIn "Senior SWE @ Acme Ltd" + Indeed "Senior Software Engineer at Acme Limited" → one row).

---

## Gmail Integration

- **Implementation**: ~1,254 LOC across 4 files:
  - `providers/gmail.ts` (295 LOC) — provider adapter, OAuth credential storage
  - `ingestion/gmail-api.ts` (450 LOC) — Gmail REST API calls, OAuth2 refresh-token flow against `oauth2.googleapis.com/token`, sophisticated `buildGmailQuery()` with 30+ subject keywords ("interview", "regret to inform", "not moving forward", "offer letter"…) and 15+ from-domain patterns (`careers@`, `no-reply@greenhouse.io`, `@smartrecruiters.com`, `@calendly.com`…)
  - `ingestion/gmail-sync.ts` (451 LOC) — sync orchestration, idempotency, message persistence, stage-transition triggering
  - `ingestion/email-router.ts` (214 LOC) — **LLM-powered smart router**: sends the email body (truncated to 12k chars) + compact list of active jobs to the configured LLM, which returns `{bestMatchIndex, confidence, stageTarget, isRelevant, stageEventPayload, reason}`. Stages include `recruiter_screen`, `technical_interview`, `offer`, `rejected`, etc.
- **Auth**: Requires `GMAIL_OAUTH_CLIENT_ID` + `GMAIL_OAUTH_CLIENT_SECRET` env vars + a stored `refreshToken` per user (OAuth2 installed-app flow, no Pub/Sub push — pure polling via `gmail.googleapis.com/gmail/v1/users/me/messages?q=…`).
- **Reusable**: **Partial.** The `gmail-api.ts` (token refresh + list + get + body extraction) is genuinely reusable as a standalone module. The sync/router logic is **tightly coupled** to Drizzle/SQLite repos (`post-application-messages`, `post-application-integrations`, `post-application-sync-runs`, `applicationTracking` stage transitions). To reuse outside job-ops you'd rewrite `gmail-sync.ts` and the LLM router stays portable (it's a single function with a JSON schema).
- **Adaptation effort**: ~2 days to lift `gmail-api.ts` and rewire `gmail-sync.ts` to your own persistence; ~1 day to lift the email-router if you already have an LLM client.

---

## AI Scoring

- **Prompt** (verbatim from `shared/src/prompt-template-definitions.ts`):
  ```
  You are evaluating a job listing for a candidate. Score how suitable this job is for the candidate on a scale of 0-100.

  SCORING CRITERIA:
  - Skills match (technologies, frameworks, languages): 0-30 points
  - Experience level match: 0-25 points
  - Location/remote work alignment: 0-15 points
  - Industry/domain fit: 0-15 points
  - Career growth potential: 0-15 points

  CANDIDATE PROFILE:
  {{profileJson}}

  SCORING INSTRUCTIONS:
  {{scoringInstructionsText}}
  ```
  Appended automatically by `scorer.ts`:
  ```
  JOB DATA (JSON):
  {minified job JSON with HTML stripped from description}

  Perform these tasks in one response:
  1. JOB FACT REVIEW (candidate-independent): compare JOB DATA with the original listing. Use only those sources, never the candidate profile. Propose a patch only for a missing or clearly incorrect whitelisted field with an exact supporting listing excerpt. Do not guess, infer from general knowledge, estimate, annualise compensation, or paraphrase/invent evidence. Use high confidence for clear corrections; medium may only fill missing values; omit ambiguous corrections. Candidate evaluation must use the proposed corrected facts.
  2. JOB BRIEF: use only stated job information, remain neutral, remove employer fluff, never judge candidate fit, and use "Not stated" for missing practical details.
  3. CANDIDATE EVALUATION: score the candidate against the listing, corrected facts, and scoring instructions.
  ...
  Respond with ONLY valid JSON in this exact shape:
  {
    "score": <integer 0-100>,
    "reason": "<1-2 sentence explanation>",
    "jobBrief": { "role_summary": ..., "they_want": [...], "specifics": [...], "company_offers": [...], "practical_details": [...], "missing_or_unclear": [...], "repeated_signals": [...] },
    "jobPatches": [{"field":"<whitelisted field>","value":<...>,"confidence":"high|medium|low","evidence":"<exact listing excerpt>"}],
    "jobWarnings": ["<unrepresentable or contradictory stated fact>"]
  }
  ```
- **Model**: User-configurable. `orchestrator/src/server/services/llm/providers/` has 12+ adapters: `anthropic.ts`, `claude_cli.ts`, `codex.ts` (OpenAI Codex app-server), `gemini.ts`, `gemini_cli.ts`, `glm.ts` (Zhipu), `lmstudio.ts`, `ollama.ts`, `openai.ts`, `openai-compatible.ts` (any OpenAI-compatible endpoint), `openrouter.ts`, `requesty.ts`. Selected via `resolveLlmModel("scoring")` and a settings registry.
- **JSON Schema enforcement**: `SCORING_SCHEMA` (a strict JSON schema, `additionalProperties: false`) is passed to the LLM via `llm.callJson()`. So the LLM is forced into structured output mode (where supported).
- **Post-processing**: Score clamped 0-100, optional `penalizeMissingSalary` (subtract N points if salary missing), `validateAndApplyJobPatches` applies only whitelisted field corrections with exact-evidence requirement.
- **Reusable**: **Yes, highly.** The prompt template (147 LOC in `prompt-template-definitions.ts`), the JSON schema, and the build-prompt logic (~80 LOC in `scorer.ts:buildScoringPrompt`) are trivially portable to any LLM client. The `LlmService` abstraction layer (12+ providers) is portable but heavyweight; you could substitute a single OpenAI/Anthropic call.

---

## Visa Sponsorship Check

- **Implementation**: Two-layer:
  1. **Provider layer** (`visa-sponsor-providers/{uk,nl}/manifest.ts`): downloads the official government register.
     - **UK**: Fetches `https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers` (Content API), finds the "Worker and Temporary Worker" CSV attachment URL, downloads CSV, parses 5-column rows (Organisation Name, Town/City, County, Type & Rating, Route).
     - **NL**: Fetches `https://ind.nl/en/public-register-recognised-sponsors/public-register-work` HTML, regex-parses `<th scope="row">` rows for organisation name + KvK number.
  2. **Service layer** (`orchestrator/src/server/services/visa-sponsors/index.ts`, 517 LOC): per-provider in-memory cache (1-hour TTL), CSV file persistence under `data/visa-sponsors/{provider}/`, daily scheduler (UK at 02:00, NL at 03:00), cleanup of old CSVs (keep 2).
  3. **Matching** (`shared/src/job-matching.ts`): `normalizeCompanyName` strips 20 corporate suffixes (Ltd, LLC, Inc, Corp, Group, Holdings, "Trading As", "&", "the"…). `calculateSimilarity` = Levenshtein-distance-based 0-100 score (substring match → proportional score; full edit distance otherwise). `searchSponsors(query, {minScore: 50, limit: 10})` returns top matches.
- **Per-job check** (in `score-jobs.ts`): `searchSponsors(job.employer, {minScore: 50, countryKey})` → `calculateSponsorMatchSummary` returns `sponsorMatchScore` (top score) + `sponsorMatchNames` (JSON array of perfect-match names).
- **Reusable**: **Yes.** The provider manifests (158 LOC UK + 83 LOC NL) and the CSV parser (54 LOC) are pure functions of `fetch()` + regex. The service layer adds a scheduler + SQLite-adjacent file storage but the *core* (download → parse → search) is genuinely standalone. ~300 LOC of code + ~50 LOC of similarity = easily portable.
- **Limitations**: Only UK and NL providers exist. Adding US (DOL-sponsored employers list), DE (Ausländerbehörde), CA (LMIA employers) etc. would each be ~100 LOC following the same manifest pattern.

---

## Comparison to ResumeWing

ResumeWing's `aggregator.py` (per the brief): 5 no-key APIs, ThreadPoolExecutor, MD5 dedup. We have validated those 5 APIs work.

| Dimension | ResumeWing `aggregator.py` | job-ops extractors |
|---|---|---|
| **# sources** | 5 no-key APIs (validated working) | 13 workspaces, but really 12 TS + 1 Python wrapper |
| **Auth burden** | 0 keys | Adzuna key, Apify token, UK Visa Jobs account, optional LinkedIn cookies |
| **Playwright dependency** | None | Required by 4 extractors (gradcracker, ukvisajobs, naukri, startupjobs) + transitively jobspy |
| **Python dependency** | Native Python | Required for 1 extractor (jobspy → LinkedIn/Indeed/Glassdoor) |
| **Container size** | Python slim | ~2-3 GB image (Node + Python + Playwright + Camoufox + Tectonic + Typst + Xvfb + VNC + Codex CLI + Claude CLI) |
| **Dedup** | MD5 of URL | Levenshtein on title+employer with field merging (strict ≥90/≥85 thresholds). **Better.** |
| **Concurrency** | ThreadPoolExecutor | `asyncPool` (concurrency 3 for discovery, 4 for scoring) |
| **Failure isolation** | Unknown | Per-source 10-min timeout, source-errors collected, partial success returned |
| **Output schema** | Likely flat dict | Rich `CreateJobInput` (28 fields) + `Job` (60+ fields) |

### Extractors: which is better?

**For the 5 APIs ResumeWing already validates**: ResumeWing wins on simplicity. If those 5 sources cover your target market (likely Adzuna + 4 others), there is **no reason to lift job-ops**. ResumeWing's pattern (one Python file, ThreadPoolExecutor, MD5 dedup) is ~10× simpler than even the simplest job-ops extractor (workingnomads at 791 LOC).

**For sources ResumeWing doesn't cover**: job-ops has unique value in:
- **Working Nomads** (remote-only, clean Elasticsearch API) — **worth lifting**
- **Hiring Cafe** (no-auth SSR scrape with Cloudflare detection) — **worth lifting**
- **Golang Jobs** (Supabase anon key, public) — **worth lifting** if you want Go-specific jobs
- **Jobindex** (Denmark-specific, clean Stash JSON) — **worth lifting** for DK market
- **WUZZUF** (Egypt) — niche, only worth it for MEA targeting
- **Khamsat** — skip, it's a $5 freelance marketplace, not a job board

**For LinkedIn/Indeed/Glassdoor**: job-ops gets these via `python-jobspy`. If ResumeWing doesn't already cover them, you could lift `jobspy` directly (it's a public PyPI package — `pip install python-jobspy`) without lifting any job-ops code at all.

### Dedup: which is better?

**job-ops wins.** `deduplicateJobsByTitleAndEmployer` (Levenshtein ≥ 90 title, ≥ 85 employer, with field-merging) catches "Senior SWE @ Acme Ltd" from LinkedIn and "Senior Software Engineer at Acme Limited" from Indeed as the same job, and merges their fields. MD5-of-URL dedup misses these entirely because each board has its own URL namespace. ResumeWing's MD5 approach is faster and simpler but loses real signal at the cross-source layer.

Recommendation: lift `shared/src/job-matching.ts` (337 LOC) — it's pure functions, no deps.

### Aggregator pattern: which is better?

**Mixed verdict.**
- ResumeWing's `ThreadPoolExecutor` is simpler than job-ops' `asyncPool` (87 LOC with hooks), but `asyncPool` supports `shouldStop`, `onTaskStarted`, `onTaskSettled` callbacks and fail-fast on first error — better for long-running pipelines with cancellation.
- job-ops' per-source timeout (10 min) is genuinely useful for hanging scrapers.
- ResumeWing's flat aggregator is better for a stateless batch job.
- Recommendation: keep ResumeWing's pattern; lift `asyncPool` only if you need cancellation.

---

## Recommendation

### Lift individual extractors? Or use whole Docker stack?

**Lift individual extractors. Do NOT run the whole Docker stack.**

Reasons:
1. The Docker image is ~2-3 GB and bundles Codex CLI, Claude CLI, Xvfb/VNC/novnc, Tectonic, Typst — none of which you need if you already have your own LLM client and resume renderer.
2. The orchestrator is a full multi-tenant Express+React+SQLite web app — running it just to scrape jobs is using a sledgehammer to crack a nut.
3. Each extractor workspace is a separate npm package with a clean `manifest.ts` + `src/run.ts` interface; the runtime contract (`ExtractorManifest`) is well-typed and documented.
4. The shared utilities are tiny (1,365 LOC total for all 6 shared modules extractors touch) and pure-function.

### Which specific extractors are worth lifting?

**Tier 1 — Lift immediately (clean HTTP, no auth, no browser):**
1. **workingnomads** — best of the bunch, direct Elasticsearch API
2. **hiringcafe** — broad remote-job coverage, Next.js SSR scrape with Cloudflare detection
3. **golangjobs** — public Supabase API, niche Go-specific jobs
4. **jobindex** — Denmark coverage, rich data
5. **wazzuf** — Egypt/MENA coverage, simple HTML scrape

**Tier 2 — Lift if you need the source (has friction):**
6. **adzuna** — requires free Adzuna API key, subprocess indirection is awkward but underlying API is clean
7. **seek** — requires Apify account (paid), but only 270 LOC of glue
8. **jobspy (LinkedIn/Indeed/Glassdoor)** — install `python-jobspy` directly via pip; do NOT lift the job-ops wrapper

**Tier 3 — Skip (heavy, brittle, or duplicative):**
9. **gradcracker** — UK-only grad board, requires Camoufox + Playwright + Crawlee + cookie infra. Not worth the dependency weight.
10. **ukvisajobs** — requires paid my.ukvisajobs.com account + Playwright + Camoufox + auth-session caching. Skip; use the visa-sponsor-providers/uk manifest + your own job search instead.
11. **naukri** — requires in-process Playwright Firefox. Skip unless India is a primary market.
12. **startupjobs** — wraps an external Playwright-based npm package. Skip unless startup.jobs is a primary source.
13. **fiveamsat (Khamsat)** — not actually a job board, it's a $5 freelance gig marketplace. Skip.

### Which are duplicative with ResumeWing?

If ResumeWing's 5 APIs are already validated and cover your core market, then:
- **adzuna** is likely duplicative (ResumeWing probably has Adzuna or similar general-purpose API).
- **jobspy (LinkedIn/Indeed/Glassdoor)** is likely duplicative if ResumeWing already covers those boards via another route.
- All Tier 1 extractors cover niche boards ResumeWing likely doesn't have.

### What else is worth lifting (beyond extractors)?

1. **`shared/src/job-matching.ts`** (337 LOC) — `deduplicateJobsByTitleAndEmployer` + `calculateSimilarity` + `normalizeCompanyName`. Better than MD5-of-URL dedup. Pure functions, no deps.
2. **Visa sponsorship check** (`visa-sponsor-providers/uk/manifest.ts` 158 LOC + `visa-sponsor-providers/nl/manifest.ts` 83 LOC + `shared/src/visa-sponsors/csv.ts` 54 LOC + similarity function above). Standalone, downloads official gov registers, fuzzy-matches employer names. High-value if you target UK/NL markets.
3. **AI scoring prompt + JSON schema** (`shared/src/prompt-template-definitions.ts` 147 LOC + `orchestrator/src/server/services/scorer.ts:buildScoringPrompt` ~80 LOC + `SCORING_SCHEMA` ~95 LOC). Trivially portable to any LLM client. The 5-criteria scoring rubric (Skills 30 / Experience 25 / Location 15 / Industry 15 / Growth 15) is a solid baseline.
4. **Gmail `buildGmailQuery()`** (~100 LOC in `gmail-api.ts:152-251`). The 30+ subject keywords + 15+ from-domain patterns + exclusion list is genuinely useful search-query engineering. Lift as a string template.
5. **`asyncPool`** (87 LOC) — only if you need cancellation/timeout hooks that `ThreadPoolExecutor` doesn't give you.
6. **Email router LLM prompt** (`email-router.ts:142-163`) — only if you build a post-application tracker.

### What is NOT worth lifting?

1. The orchestrator (Express server, React UI, SQLite schema, Drizzle ORM, multi-tenancy, JWT auth, SSE, scheduler, backup, demo mode, hosted-usage quotas, product analytics). Massive surface area for a scraping use case.
2. The `browser-utils` workspace (Camoufox + Playwright challenge solver + Xvfb/VNC headed fallback). Only needed for Tier 3 extractors you shouldn't lift anyway.
3. The PDF generation stack (Tectonic + Typst + theme generation + Reactive Resume integration). Use your own resume renderer.
4. The Codex CLI / Claude CLI integration. Use your own LLM client.
5. `skills-lock.json` — it's the maintainer's marketing-skills tooling, not part of the project.

### Bottom line

**Lift 5-8 extractors + the dedup logic + the visa-sponsor providers + the scoring prompt. Skip the Docker stack entirely.** You'll get ~80% of job-ops' value with ~5% of the operational complexity.
