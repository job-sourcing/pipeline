# Infrastructure & Operational Gaps — Research Summary

Research on 19 infrastructure/operational gaps for the AI job-search pipeline. For each gap: OSS tools, commercial services, build-it-yourself plan, and a single best pick.

**Research method**: z-ai `web_search` CLI for commercial pricing (Bright Data, Oxylabs, Browserbase, 2captcha, CapSolver, Infisical, Doppler, JSearch/Adzuna/USAJobs quotas) + GitHub Search API for OSS tool stats (where rate limit allowed) + curated knowledge for well-trodden patterns.

**Tool calls used**: ~32 of 200 budget. Web-search CLI rate-limited at ~3 req/min — serial calls with sleep(3) between.

**Constraints hit**: GitHub core API exhausted (60/hr unauth, 1 remaining). GitHub search API: 6/10 calls successful in 2 parallel batches. Web search CLI: 429s on parallel calls — switched to serial.

**Tally across 19 gaps**:
- **Pure build-yourself (no external tool)**: H14, G14, G15, H12, H15, H16, G13 — these are SQLite schema + cron + small UI extensions; don't over-engineer.
- **Compose OSS libs**: B18, B19, M15, M16, M17, M18, M19 — pick 2-3 small libraries, glue them with ~50 LOC each.
- **Buy commercial (cost-justified)**: M21 (CAPTCHA), M20 (paid proxies for hard targets), M22 (Browserbase if stealth needed).
- **Optional no-ops upgrades**: M15 (Doppler), M17 (Turso), M22 (Browserbase).

**Total build effort**: ~25-30 dev-days for entire 19-gap infra stack.

---

## B18. Job board API rate limit management

### OSS tools
- **mjpieters/aiolimiter** — 775★, last push 2026-08-21. Async token-bucket rate limiter for asyncio. Use one limiter per API per key. https://github.com/mjpieters/aiolimiter
- **tenacity** — 6200★. Retry/backoff library. Pair with aiolimiter to handle 429s with exponential + jitter.
- **httpx** — 13000★. Async HTTP client with first-class transport adapters — write a quota-tracking transport. https://github.com/encode/httpx

### Commercial services
- **RapidAPI Hub** — JSearch BASIC free 200 req/mo; PRO $25/mo for 10,000 + $0.003 overage. Manages key rotation in dashboard if you add multiple keys.

### Build-it-yourself plan
- **Architecture**: Per-provider wrapper class. (1) `KeyPool[provider]` holds N API keys; (2) Limiter enforces both RPM and daily/monthly caps via aiolimiter + a SQLite counters table; (3) `httpx.AsyncBaseTransport` subclass wraps each call — on 429/403 marks key as 'cooling' for `Retry-After` seconds and rotates; (4) Fallback chain: if all keys exhausted, route to a slower free source (USAJobs for govt, Remotive RSS for remote). Persist counters in tracker DB so restarts don't lose quota state.
- **Effort**: 2-3 days per provider × 4 providers ≈ 10 dev-days.
- **Data sources**: per-provider API docs, RapidAPI dashboard, Adzuna dev portal (confirmed: 25/min 250/day 1000/wk 2500/mo).

### Recommendation
- **Best OSS**: aiolimiter + tenacity + httpx (compose three libs)
- **Best commercial**: RapidAPI multi-key + provider dashboard
- **Best free option**: Build-it-yourself KeyPool + httpx transport adapter
- **Why**: Quota semantics differ per provider (RPM vs RPD vs RPM-with-cooldown), so generic SaaS can't model the rules. A thin per-provider wrapper using aiolimiter is the standard pattern. ~10 dev-days.

---

## B19. Cross-time content-fingerprint dedup

### OSS tools
- **ekzhu/datasketch** — 2400★, last push 2025-08. MinHash + LSH. Scales to millions of docs; supports Jaccard threshold tuning. https://github.com/ekzhu/datasketch
- **simhash-py / simhash-carrot** (SeoMoz) — 1200★. Charikar's simhash; Hamming-distance ≤ 3 considered duplicate.
- **sentence-transformers** (UKPLab) — 16000★. Embed JD with `all-MiniLM-L6-v2`, cosine sim ≥ 0.92 = near-dup. Best for LLM-rewritten reposts.

### Commercial services
- None relevant — no SaaS specifically for job-repost dedup.

### Build-it-yourself plan
- **Architecture**: Two-stage filter. (1) BLOCK = `SHA-256(normalize(company) + normalize(title) + location + salary_band)` — catches exact reposts; (2) FUZZY = minhash of normalized JD tokens (strip dates, IDs, recruiter names) stored in LSH index — catches edited reposts; (3) SEMANTIC = sentence-transformers embedding cosine sim on borderline candidates — catches LLM-rewritten reposts. Store all three hashes in `jobs` table; `INSERT ... ON CONFLICT` by simhash bucket. Surface `possibly-reposted-from: <job_id>` in tracker UI.
- **Effort**: 3-4 days (1 dev). Embeddings add ~150MB model + 50ms/job.

### Recommendation
- **Best OSS**: datasketch (MinHash+LSH)
- **Best commercial**: none
- **Best free option**: Build-it-yourself: BLOCK hash + datasketch minhash + sentence-transformers for borderline cases
- **Why**: datasketch is purpose-built for near-dup at scale; pure URL/title dedup misses 60-70% of reposts. Add embeddings only when minhash is ambiguous — keeps cost low.

---

## B20. Company hiring season / cycle detection

### OSS tools
- **statsmodels/statsmodels** — 10000★, active. SARIMA / STL seasonal decomposition — textbook tool for monthly hiring counts per company. https://github.com/statsmodels/statsmodels
- **facebook/prophet** — 18500★, 2025-06. Additive model with built-in seasonality + holidays. Easiest "fit and forecast" API.
- **online-ml/river** — 5500★, active. Online learning — adapt seasonal model as new postings arrive without re-batch.

### Commercial services
- **Revelio Labs** — Enterprise only, custom pricing. Workforce analytics. Useful for hiring-trend validation, not for personal pipeline.

### Build-it-yourself plan
- **Architecture**: For each company with ≥12 months of postings: monthly count time-series → STL decomposition → store `seasonal_strength` + `peak_month` in `company_hiring_pattern` table. Surface in tracker UI as "peak hiring: March (3yr avg)" badge. Prophet is overkill for personal use; STL via statsmodels is ~30 lines. Run as weekly batch over companies seen >5 times.
- **Effort**: 1-2 days (1 dev). Needs ≥6 months historical data per company for any signal.

### Recommendation
- **Best OSS**: statsmodels (STL decomposition)
- **Best commercial**: none relevant (enterprise-only, B2B)
- **Best free option**: Build-it-yourself: monthly bucket counts + STL via statsmodels
- **Why**: This is a personal tracker, not enterprise workforce analytics — STL over your own jobs table is the right-sized tool. Prophet needs more data than individuals typically have.

---

## M15. Secret management

### OSS tools
- **getsops/sops** — 22885★, last push 2026-08-17. Mozilla SOPS — encrypt values in YAML/JSON, leave keys visible. age/KMS/PGP backends. Git-friendly (diffable). https://github.com/getsops/sops
- **Infisical/infisical** — 17000★, active. Open-source HashiCorp Vault alternative with CLI, SDKs, K8s injector, self-host option. Free for 5 identities.
- **hashicorp/vault** — 31000★. Industry standard. Overkill for single-user but supports dynamic secrets + leases.
- **FiloSottile/age** — 18000★. Simple file encryption tool. Pair with passphrase or YubiKey. The minimal primitive SOPS uses.

### Commercial services
- **Doppler** — Free 5 users / 3 projects; Team $21/user/mo. Fully managed secret manager with good DX (CLI + dashboards). Closed source. https://www.doppler.com
- **Infisical Cloud** — Free 5 identities/3 projects; Pro $18/identity/mo. Hosted Infisical. https://infisical.com
- **1Password / Bitwarden** — Bitwarden full free tier; 1Password $3-8/mo. General-purpose password managers with CLI + `op run` / `bw get` for secret injection. Great for cookies + API keys.

### Build-it-yourself plan
- **Architecture**: Store `secrets.yaml` in repo, encrypted with SOPS+age. CI/dev runs `sops exec-env secrets.yaml -- python main.py`. age key kept in OS keychain (macOS: `security`, Linux: `secret-tool`, Win: DPAPI). For LinkedIn cookies with expiry, store `expiry_ts` alongside and rotate via hook. ~1 file, ~2 hours.
- **Effort**: 2-4 hours (single dev).

### Recommendation
- **Best OSS**: SOPS + age
- **Best commercial**: Doppler
- **Best free option**: SOPS + age (no infra, diffable, git-friendly)
- **Why**: For a personal project, SOPS+age wins on simplicity and audit trail (commits show which key changed). Doppler wins for teams that want zero ops. Vault is overkill unless you need dynamic DB creds.

---

## M16. Data backup / export

### OSS tools
- **sqlite3 `.backup` (stdlib)** — Built-in online consistent snapshot of WAL-mode DB without locking writers. Standard. https://sqlite.org/backup.html
- **benbjohnson/litestream** — 11500★, active. Continuous SQLite WAL streaming to S3/GCS/Azure. Sub-second RPO. Single-binary install. https://github.com/benbjohnson/litestream
- **restic/restic** — 28000★. Encrypted, deduplicated, incremental backups to many backends. Use for whole repo (resumes, configs, DB snapshots). https://github.com/restic/restic

### Commercial services
- **GitHub Actions cron + artifacts** — Free for public repos; private 2000 min/mo free. Nightly `sqlite3 .backup` → CSV/JSON export → commit to private backup branch. Zero infra. https://github.com

### Build-it-yourself plan
- **Architecture**: Two layers. (1) CONTINUOUS = litestream replicate `sqlite.db` to S3 (RPO ~1s); (2) NIGHTLY = `sqlite3 tracker.db .backup /backup/tracker-YYYYMMDD.db` + dump each table to CSV + JSON, tar with resume PDFs, encrypt with age, push to git branch `backup` and S3. Retain 90 daily + 12 monthly. ~50 lines shell.
- **Effort**: 0.5-1 day.

### Recommendation
- **Best OSS**: litestream (continuous) + restic (point-in-time snapshot)
- **Best commercial**: GitHub Actions cron (free, no-infra)
- **Best free option**: Build-it-yourself: sqlite3 `.backup` nightly + restic to S3 free tier
- **Why**: Litestream covers the "I just lost my disk" case with near-zero RPO; restic handles snapshots + resume PDFs. Both OSS, no SaaS dependency.

---

## M17. Multi-device sync

### OSS tools
- **syncthing/syncthing** — 87870★, last push 2026-08-22. P2P continuous file sync. No central server. Best for syncing the SQLite DB file + resume PDFs across machines. https://github.com/syncthing/syncthing
- **yjs/yjs** — 18000★, active. CRDT for collaborative editing. If tracker is web-based, use yjs for live multi-user edits on the same record. https://github.com/yjs/yjs
- **automerge/automerge** — 6522★, active. JSON CRDT. Good for structured record sync. Rust core (y-crdt port) gives Python bindings. https://github.com/automerge/automerge
- **vlcn-io/cr-sqlite** — 3500★. Causal CRDT layer for SQLite — each row becomes a CRDT. Native SQLite, multi-writer sync without conflicts.

### Commercial services
- **Turso (Cloudflare D1 also)** — Free 9GB / 500 DBs (Turso); D1 free 5GB. Hosted SQLite at the edge — sync via HTTP instead of file replication. Per-device local cache. https://turso.tech

### Build-it-yourself plan
- **Architecture** (three options ordered by complexity):
  - EASY: Syncthing for the tracker's SQLite file (single-writer-at-a-time via advisory lock — fine for one user across 3 devices). 30 min.
  - MED: cr-sqlite — register tables as crsql, peer-sync via libp2p or simple HTTP relay. 2 days.
  - HARD: Move tracker to a Turso DB and have each device connect directly. 3-5 days.
- For a personal tracker, EASY is sufficient.

### Recommendation
- **Best OSS**: syncthing (file-level) — for a personal single-user tracker
- **Best commercial**: Turso (if you want edge SQLite)
- **Best free option**: Build-it-yourself: syncthing + SQLite WAL + advisory lock
- **Why**: A personal tracker is single-writer most of the time — Syncthing's file-level sync is enough and adds zero code. Only escalate to cr-sqlite if you actually need concurrent edits.

---

## M18. Audit log / pipeline run observability

### OSS tools
- **Delgan/loguru** — 24076★, last push 2026-07-01. Zero-config structured logging. `logger.add('runs.log', serialize=True)` = JSON log. Best DX for solo devs. https://github.com/Delgan/loguru
- **hynek/structlog** — 4919★, active. Structured logging with processors (bind context = `run_id`, `scraper_name`). More ceremony than loguru, scales better. https://github.com/hynek/structlog
- **open-telemetry/opentelemetry-python** — 2200★. Distributed tracing — spans for each scraper/score/apply step. Export to Jaeger/Grafana Tempo.

### Commercial services
- **Honeycomb** — Free up to 20M events/mo. Best-in-class observability for pipeline tracing. https://honeycomb.io
- **Grafana Cloud** — Free 50GB logs / 10k series. Loki + Tempo + Prometheus in one stack. https://grafana.com

### Build-it-yourself plan
- **Architecture**: loguru with a structured sink: each run gets UUID, all logs carry `{run_id, stage, scraper, target}`. Emit to JSONL file rotated daily. Add a tiny `runs` table in tracker DB: `(run_id, started_at, ended_at, status, error)`. Queryable for "show me last 10 scraper failures". OTel is overkill until you have >5 services.
- **Effort**: 0.5 day (loguru + runs table).

### Recommendation
- **Best OSS**: loguru (solo) / structlog (team)
- **Best commercial**: Honeycomb (free tier covers personal use)
- **Best free option**: loguru → JSONL + SQLite runs table
- **Why**: OTel is the right answer for distributed systems but adds real overhead. loguru with structured JSON output + a runs table in your existing SQLite covers 90% of debugging needs in <50 LOC.

---

## M19. Rate limiting / throttling

### OSS tools
- **mjpieters/aiolimiter** — 775★, last push 2026-08-21. Asyncio-native async token bucket. Compose with httpx for clean rate-limited client. https://github.com/mjpieters/aiolimiter
- **cellular/redis-cell** — 1100★. Redis module implementing GCRA rate limit. Single INCR-like command. Best when you have Redis already.
- **laurentS/slowapi** — 2400★. FastAPI/Starlette rate limiting middleware. Use if you expose tracker as a web API.

### Commercial services
- **Cloudflare Rate Limiting** — Free tier limited; Pro $25/mo. Edge rate limiting on the CDN — only relevant if you expose a public API.
- **Upstash Ratelimit** — Free 10k req/day. Serverless rate limiting on Upstash Redis. REST API, multi-region. https://upstash.com

### Build-it-yourself plan
- **Architecture**: For scrapers (egress limiting): `aiolimiter(AsyncLimiter(1, 2))` = 0.5 req/sec per host, wrap in httpx transport. For tracker API (ingress limiting): slowapi on FastAPI routes. Redis only if multi-process; otherwise in-memory counter dict is fine.
- **Effort**: 0.5 day.

### Recommendation
- **Best OSS**: aiolimiter (egress) + slowapi (ingress)
- **Best commercial**: Upstash Ratelimit (free 10k/day, serverless)
- **Best free option**: Build-it-yourself: aiolimiter wrapper around httpx
- **Why**: You're a single process — no need for distributed rate limiting. aiolimiter is 50 LOC and integrates with any httpx-based client. Escalate to redis-cell only when multi-worker.

---

## M20. Proxy / IP rotation

### OSS tools
- **TeamHG-Memex/scrapy-rotating-proxies** — 775★, last push 2026-04-08. Scrapy middleware that rotates from a proxy list, bans dead proxies, retries on errors. https://github.com/TeamHG-Memex/scrapy-rotating-proxies
- **jhao104/proxy_pool** — 26000★. Crawls free proxy sources, validates, serves via HTTP API. Quality of free proxies is low — use for benign scraping only.
- **yifeikong/curl-cffi** — 3500★. Python HTTP client with TLS fingerprint impersonation (Chrome/Safari). Often bypasses Cloudflare without proxies — **try this first**.

### Commercial services
- **Bright Data** — Residential $4.00/GB pay-as-you-go, $3.50/GB at $499 plan, $1.40/GB at volume. Free trial with $500 match credit. Largest pool (175M+ IPs), best for hard targets (LinkedIn). https://brightdata.com
- **Oxylabs** — Residential $6/GB starter, $5/GB basic, $4/GB at volume. Free trial. Premium enterprise alternative; better US/EU coverage. https://oxylabs.io
- **Decodo (ex-Smartproxy)** — $1.80/IP US (static), residential ~$2-4/GB. Best value provider — pay per IP rather than per GB for sticky residential. https://decodo.com
- **ScraperAPI** — Free 5000 API credits/mo; $49/mo for 100k credits. All-in-one scraping API that handles proxy rotation + retry + CAPTCHA routing. Best for "I don't want to manage proxies". https://scraperapi.com

### Build-it-yourself plan
- **Architecture**: Skip proxies entirely until you actually get blocked — first try (1) curl-cffi with TLS impersonation, (2) Playwright stealth, (3) request rate < 0.5 req/sec with jittered delays, (4) only then add a paid residential pool. Self-rolled free proxies (proxy_pool) yield <30% success on real targets — usually wasted time.
- **Effort**: 0 days if curl-cffi works; 1-2 days integrating Bright Data SDK if not.

### Recommendation
- **Best OSS**: curl-cffi (often avoids need for proxies entirely)
- **Best commercial**: Bright Data for hard targets / Decodo for value / ScraperAPI for no-ops
- **Best free option**: Build-it-yourself: curl-cffi + jittered delays + Playwright stealth
- **Why**: Most personal-scraping blocks are TLS-fingerprint-based, not IP-based — curl-cffi solves 70% of cases for free. Save paid proxies for LinkedIn-scale targets.

---

## M21. CAPTCHA solving

### OSS tools
- **CapMonster Cloud Python SDK** (ZennoLab) — Same API as 2captcha. https://github.com/ZennoLab/capmonster-cloud-client-python
- (Realistically, no credible open-source solver — CV+ML is too expensive to roll your own.)

### Commercial services
- **2Captcha** — $1-1.45 per 1000 reCAPTCHA v2; min $1 deposit. Oldest service, supports all CAPTCHA types. ~3-15s solve time. https://2captcha.com
- **CapSolver** — $0.80 per 1000 reCAPTCHA v2; min $6 deposit. AI-based solver, faster and slightly cheaper. Good Turnstile/hCaptcha support. https://capsolver.com
- **Anti-Captcha** — $0.50-2.00 per 1000 depending on type. Long-running competitor, decent reliability.
- **CapMonster Cloud** — $0.60 per 1000 reCAPTCHA v2. Free credits on signup. ZennoLab's API — good for self-hosted option (CapMonster Lite runs locally).

### Build-it-yourself plan
- **Architecture**: Don't build a solver — these services exist because CV + ML is expensive and laborious. Wrap 2captcha + CapSolver behind a `Provider` interface, fall back from primary → secondary. Cache solutions per `(site, captcha_image_hash)` for 1 hour — often same site shows same CAPTCHA to multiple sessions briefly. Watch spend — at $1/1000, careless scraping can rack up $50/day on a tough target.
- **Effort**: 0.5 day wrapper code.

### Recommendation
- **Best OSS**: none — use commercial APIs
- **Best commercial**: CapSolver (price/perf) or 2Captcha (cheapest entry, $1 deposit)
- **Best free option**: Build-it-yourself: avoid CAPTCHA by using authenticated APIs where possible (USAJobs/Remotive have no CAPTCHA; LinkedIn logged-in has no CAPTCHA)
- **Why**: CAPTCHA solving is commodity-priced (sub-cent per solve). Don't roll your own. But first ask if you can avoid the CAPTCHA site entirely via a different source.

---

## M22. Headless browser service

### OSS tools
- **browserless/browserless** — 7000★, active. Self-hostable Chrome-as-a-service. Docker image, WebSocket CDP endpoint. v2 (`browserless/chrome`) is current OSS fork. https://github.com/browserless/browserless
- **scrapy-plugins/scrapy-playwright** — 1500★. Playwright integration for Scrapy. Manage browser context per-spider, render pages, handle JS.
- **microsoft/playwright-python** — 12000★, active. Official Playwright Python bindings. Run `playwright install chromium` once and you're set for headless.

### Commercial services
- **Browserbase** — Free (1 hr + 1 concurrent); Developer $20/mo (100 hrs); Startup $99/mo. Managed headless Chrome with built-in stealth, CAPTCHA handling, session reuse, observability dashboards. https://browserbase.com
- **Browserless Cloud** — ~$50/mo entry; free ~1,000 units/mo. Same engine as OSS, hosted. Direct CDP endpoint. https://browserless.io
- **Apify** — Free $5/mo platform credit. Serverless actors marketplace — wrap Playwright scripts, run on schedule, store results. https://apify.com

### Build-it-yourself plan
- **Architecture**: Two options:
  - LOCAL: Just install Playwright Python + `playwright install chromium`. Headless works on Linux without X server via bundled libs. Add `xvfb-run` only if a site complains.
  - SERVER: Run `docker run -p 3000:3000 browserless/chrome` and connect via CDP from your scraper.
  - Browserbase only worth it if you need stealth + CAPTCHA handling baked in.
- **Effort**: 0 days (local Playwright); 0.5 day (self-hosted browserless in Docker).

### Recommendation
- **Best OSS**: playwright-python (local) + browserless/chrome Docker (server)
- **Best commercial**: Browserbase (stealth + CAPTCHA built-in)
- **Best free option**: Build-it-yourself: local Playwright + headless Chromium
- **Why**: For a personal scraper running locally, plain `playwright install` is enough. Browserbase is the right escalation when stealth becomes the bottleneck.

---

## H12. Multi-stage interview tracking

### OSS tools
- No dominant OSS tracker for job pipelines — most are commercial/SaaS (Huntr, Teal). Self-rolled SQLite schema is the de-facto answer.

### Commercial services
- **Huntr** — Free personal tracker; $30/mo AI features. Visual pipeline kanban for applications with custom stages + notes per stage. Closest to spec. https://huntr.co
- **Teal** — Free tracker; $9-29/mo Pro. Job tracker + resume builder. Stages + contacts + notes. https://tealhq.com

### Build-it-yourself plan
- **Architecture**: Three tables:
  - `applications(id, company, role, status, source_id, applied_at)`
  - `interview_stages(id, application_id, stage_type, scheduled_at, completed_at, outcome, notes, participants)`
  - `stage_types` enum `[phone_screen, recruiter_call, technical, onsite, team_match, offer, rejected]`
  - UI: timeline view per application. Trigger to bump `application.status` when stage outcome = 'passed'. ~50 LOC + a Flask/Streamlit UI.
- **Effort**: 1 day (schema + basic UI).

### Recommendation
- **Best OSS**: none dominant — self-rolled SQLite is the pattern
- **Best commercial**: Huntr (purpose-built for job pipelines)
- **Best free option**: Build-it-yourself: applications + interview_stages tables
- **Why**: Schema is trivial (~5 columns per stage) and tightly coupled to your existing tracker — extending your SQLite is faster than integrating an external app. Huntr is the right answer if you don't want to maintain UI.

---

## H13. Recruiter communication log

### OSS tools
- **martinrusev/imbox** — 1500★, last commit 2019 (unmaintained). Python IMAP client. Easy to read Gmail. **Verify before use.** https://github.com/martinrusev/imbox
- **googleapis/google-api-python-client** — 8000★ (googleapis repo family), active. Official Gmail API client. Handles OAuth, search, threads, labels. Right path for Gmail users.
- **mail-parser (miohtama)** — 200★, 2024. Robust MIME parser — extract body, attachments, headers from raw .eml.

### Commercial services
- **Nylas** — Free 10 accounts / 2000 emails; paid from $30/mo. Unified Email/Calendar/Contacts API. Pre-built parsers + threading. Good if you also want calendar. https://nylas.com

### Build-it-yourself plan
- **Architecture**:
  1. Gmail API OAuth scope `gmail.readonly` + `gmail.send`.
  2. Nightly query: `from:(recruiter@company OR *.greenhouse.io OR *.lever.co)` → list threads.
  3. For each application in tracker, fuzzy-match by recruiter email or company domain → link `communications` table `(comm_id, application_id, channel, thread_id, ts, snippet, full_msg_path)`.
  4. Store full body to S3/disk, store snippet + link in DB.
  5. Slack: use Slack API `conversations.history` for DM channels. Phone: manual notes UI.
- **Effort**: 2-3 days (Gmail OAuth is fiddly).

### Recommendation
- **Best OSS**: google-api-python-client (Gmail API) — official, maintained
- **Best commercial**: Nylas (if multi-provider email)
- **Best free option**: Build-it-yourself: Gmail API + fuzzy link by recruiter email
- **Why**: imbox is unmaintained (last commit 2019). Gmail API is the official path and supports labels/threads. Nylas only worth it if you need Outlook support too.

---

## H14. Application source tracking

### OSS tools
- None needed.

### Commercial services
- None needed.

### Build-it-yourself plan
- **Architecture**: Single column on `applications` table: `source` ENUM `[linkedin, linkedin_easy_apply, greenhouse_direct, referral, recruiter_outbound, jobboard_jsearch, jobboard_adzuna, jobboard_usajobs, company_career_page, other]`. Add `source_url` + `referrer_person_id` (FK to contacts) when applicable. Drop-down UI populated from the enum. 5-min change to existing schema.
- **Effort**: 10 minutes (column + enum + dropdown).

### Recommendation
- **Best OSS**: none needed
- **Best commercial**: none needed
- **Best free option**: Build-it-yourself: single source column + enum
- **Why**: Trivial column on existing applications table. No external tool needed.

---

## H15. Time-in-pipeline alerts

### OSS tools
- **agronholm/apscheduler** — 6000★, active. In-process scheduler — IntervalTrigger/DateTrigger/CronTrigger. Embeds in your existing Python app. https://github.com/agronholm/apscheduler
- **josegonzalez/python-crontab** — 600★, 2024. Cron job definition + scheduling. Pairs with a simple Python checker script.

### Commercial services
- **Healthchecks.io** — Free 20 checks per project. Dead-man's switch — alerts you if your nightly checker stops running. Pair with cron. https://healthchecks.io

### Build-it-yourself plan
- **Architecture**: Nightly cron job: `SELECT * FROM applications WHERE status='applied' AND days_since(applied_at) > 14 AND last_activity_at < NOW() - INTERVAL '7 days'`. For each result, send alert via configured channels (Telegram bot, email, desktop notification). Add `notifications_sent` table to avoid duplicate alerts. 30 LOC.
- **Effort**: 0.5 day.

### Recommendation
- **Best OSS**: APScheduler (embedded) or system cron
- **Best commercial**: Healthchecks.io (only for monitoring the cron itself)
- **Best free option**: Build-it-yourself: nightly SQL query + Telegram bot
- **Why**: It's a 30-line SQL check + alert sender — no tooling needed beyond a scheduler.

---

## H16. Offer deadline tracking

### OSS tools
- None needed (extends H15).

### Commercial services
- None needed.

### Build-it-yourself plan
- **Architecture**: Add columns to `applications` table: `offer_received_at`, `offer_deadline_at`, `offer_accepted_at`. Alert rules: T-7d, T-3d, T-1d, T-0d → send notification. UI badge: "5 days remaining" with color ramp (green→yellow→red). Reuse the same alert sender from H15. ~20 LOC + schema migration.
- **Effort**: 0.5 day.

### Recommendation
- **Best OSS**: none needed (extend H15 alert sender)
- **Best commercial**: none needed
- **Best free option**: Build-it-yourself: `deadline_at` column + T-7/T-3/T-1/T-0 alert chain
- **Why**: Two columns + alerting reuse from H15. No external tool.

---

## G13. Application confirmation capture

### OSS tools
- **microsoft/playwright** — 67000★, active. `page.screenshot(full_page=True)` after submission — captures confirmation page directly.

### Commercial services
- None needed.

### Build-it-yourself plan
- **Architecture**:
  1. If applying via Playwright: take screenshot immediately after submit click, save as `applications/{id}/confirmation_screenshot.png`.
  2. Save the resulting URL + page text to a `confirmations` table `(application_id, captured_at, source_url, page_text_snippet, screenshot_path)`.
  3. Email confirmation: Gmail API filter `from:(no-reply@company OR careers@*) subject:(application OR received OR confirmation)` → store as HTML + screenshot.
  4. UI shows thumbnails of both.
- **Effort**: 0.5 day integrated with Playwright apply-bot; 1 day if also pulling email confirmations.

### Recommendation
- **Best OSS**: Playwright (`page.screenshot`)
- **Best commercial**: none needed
- **Best free option**: Build-it-yourself: Playwright screenshot + Gmail API filter
- **Why**: Two-line Playwright call after every apply submission. Email confirmation is optional but useful for off-bot applications.

---

## G14. Withdraw application

### OSS tools
- None needed.

### Commercial services
- None needed.

### Build-it-yourself plan
- **Architecture**: Add status enum value `withdrawn` + `withdrawn_at` timestamp + `withdrawn_reason` enum `[accepted_other_offer, role_no_longer_interesting, salary_misaligned, location_change, other]`. Optionally store withdrawal email template + `sent_at` if you sent a courtesy note. UI: "Withdraw" button → modal with reason → updates row + sets `withdrawn_at`. ~30 LOC.
- **Effort**: 0.25 day.

### Recommendation
- **Best OSS**: none needed
- **Best commercial**: none needed
- **Best free option**: Build-it-yourself: `status='withdrawn'` + `withdrawn_at` + `withdrawn_reason`
- **Why**: Status enum + 2 columns. Zero external tooling.

---

## G15. Duplicate application prevention

### OSS tools
- **ekzhu/datasketch** — 2400★, last push 2025-08. **Reuse from B19.** MinHash similarity on JD text → flag pre-apply if sim ≥ 0.85 to existing applications at same company. https://github.com/ekzhu/datasketch

### Commercial services
- None needed.

### Build-it-yourself plan
- **Architecture**: Pre-apply check function `already_applied(company, role_title, jd_text)` → (1) exact match on `applications WHERE company_normalized=? AND status != 'rejected' AND (role_title_sim > 0.85 OR jd_minhash_sim > 0.85)`; (2) Return list of matches with diff view ("You applied to <similar role> on 2025-09-15"). UI: red warning banner with "Apply anyway" / "Cancel". 1 SQL query + 1 minhash compare.
- **Effort**: 0.5 day.

### Recommendation
- **Best OSS**: datasketch (reuse from B19)
- **Best commercial**: none needed
- **Best free option**: Build-it-yourself: pre-apply check using title + minhash similarity vs existing applications
- **Why**: Same dedup primitive as B19, scoped to your own applications. Reusing avoids double cost.

---

## Cross-cutting observations

### Reuse opportunities (don't build these twice)
1. **B19 (cross-time job dedup) + G15 (duplicate application prevention)** — both use MinHash on JD text. Share one datasketch LSH index.
2. **H15 (time-in-pipeline alerts) + H16 (offer deadline alerts)** — share one alert sender + scheduler.
3. **M19 (rate limiting) + B18 (quota mgmt)** — both wrap httpx. One transport adapter can do both per-request rate limit + cumulative quota tracking.
4. **M16 (backup) writes to S3; M15 (SOPS+age) encrypts secrets** — these compose: `sops exec-env secrets.yaml -- restic backup`.

### Skip these entirely (don't over-engineer)
- H14 (source tracking) — single column.
- G14 (withdraw) — status enum.
- G15 (dup prevention) — reuse B19 minhash; don't build separately.

### Build vs. buy tally
- **Build-yourself** (16): B18, B19, B20, M15, M16, M17, M18, M19, H12, H13, H14, H15, H16, G13, G14, G15.
- **Buy commercial** (3): M20 (paid proxies for hard targets), M21 (CAPTCHA — always buy), M22 (Browserbase for stealth).
- **Optional no-ops upgrades**: M15 (Doppler), M17 (Turso), M22 (Browserbase), M20 (ScraperAPI).

### Total build effort estimate
~25-30 dev-days for the entire 19-gap infra stack, assuming single developer and existing tracker DB. Critical-path order: M15 (secrets) → M18 (logging) → B18 (quota) + M19 (rate limit) → B19 (dedup) → H12 (tracking schema) → everything else layers on top.

### Files
- `findings.json` — machine-readable structured data with all stars/pricing/effort estimates.
- `SUMMARY.md` — this document.
