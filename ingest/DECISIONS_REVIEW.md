# PEER REVIEW — BUILD DECISIONS v1 (DECISIONS.md)

**Reviewer**: Senior software architect (peer-review sub-agent, Task 2-a)
**Date**: 2026-08-23
**Status**: Pre-build gate review. No files modified except this review.

**Files reviewed**:
- `DECISIONS.md` (review target, 8 decisions)
- `HANDOFF.md` §4 (8-week plan), §5 (original options), §7 (risks), §9 (appendix)
- `01_resumewing_apis/SUMMARY.md`, `02_ats_scoring/SUMMARY.md`, `10_career_ops_deep/SUMMARY.md`, `12_ai_job_search_deep/SUMMARY.md`
- Source evidence: `02_ats_scoring/score_jobs.mjs`, `04_hr_breaker/optimize_resume.mjs`, `.gitignore`, git log/ls-files

**Environment verification performed** (review-only, no project files touched):
- `bun --version` → 1.3.14; `node --version` → v24.18.0; `python3` → 3.12.13; SQLite lib 3.53.1
- `bun -e "import('node:sqlite')"` → **FAIL: "No such built-in module: node:sqlite"**; same import under `node` → OK (`DatabaseSync` etc.)
- `/home/z/repos/` → **does not exist** (D6 re-clone is unscheduled pre-build work)
- `docker` → **not installed** (Week 3 RSSHub self-host option is not executable in-container)
- `jobspy`, `weasyprint`, `apscheduler`, `loguru`, `aiolimiter`, `edgartools` → none installed yet
- `git ls-files` → `07_autopilot_jobhunt/workdir/.env` IS committed; `.gitignore` comment confirms policy: ".env IS tracked … git is our disk"

---

## Verdict table

| ID | Decision | Verdict | One-line reason |
|----|----------|---------|-----------------|
| D1 | Python + Bun hybrid | **ENDORSE-WITH-CHANGES** | Right call and the D1.1 single-seam rule is excellent, but the "validated subprocess pattern" claim is overstated and the error/output contract for the seam is unspecified. |
| D2 | Scope: skip J7, human-assist G1/G2, curl-cffi first, no web UI | **ENDORSE** | Ethically and economically correct; only minor distribution/e2e-framing caveats. |
| D3 | Free tiers + $1 spend | **ENDORSE-WITH-CHANGES** | Sound budget posture, but secrets policy contradicts itself and free-tier quota-exhaustion behavior is undefined. |
| D4 | CRM built from scratch, one SQLite file | **MODIFY** | Build-narrow is right, but "same DB file as the tracker" collides with an unresolved two-sources-of-truth problem and a multi-runtime concurrency policy that must be decided before Week 1 schema work. |
| D5 | Career-ops subprocess via `bun` | **MODIFY** | Factual error, verified: `tracker.mjs` needs `node:sqlite`, which Bun 1.3.14 does not provide — these scripts must run under Node. `pipeline-lock.mjs` is a library, not a CLI. |
| D6 | Repo/package structure | **ENDORSE-WITH-CHANGES** | Good hygiene; missing Day-0 re-clone task, data/backup policy, .env policy, CI, and SDK vendoring decision. |
| D7 | Testing strategy | **MODIFY** | The mock/HTTP strategy is right, but the highest-probability failure mode (malformed LLM JSON) has no specified behavior and no test; no mock seam exists for the z-ai SDK; subprocess timeout/signal/concurrency paths untested. |
| D8 | LLM caching + rate limiting | **MODIFY** | Caching is correct and cheap, but the TF-IDF pre-filter detail is refuted by exp 02's own data (absolute 30% cutoff would have dropped every job the LLM ranked top), the cache key omits prompt version, and 429/backoff behavior is undefined. |

---

## Detailed analysis

### D1 — Python + Bun hybrid: ENDORSE-WITH-CHANGES

The pick is correct: 4 of 5 key repos are Python, z-ai SDK is JS-only and sanctioned, and full-TS is a rewrite. The D1.1 boundary rule (ALL LLM calls through `jobsearch/llm.py`) is the single best idea in DECISIONS.md — keep it and enforce it in code review from Week 1.

**Risks / blind spots**

1. **Evidence overclaim.** D1 says experiments 02+04 "already validated the Python↔Bun subprocess pattern." They did not. Both experiments are standalone `.mjs` scripts run directly under `bun`; no Python process ever spawned a Bun child in any experiment. What was validated is the z-ai SDK call from Bun — the Python→Bun seam itself is unvalidated. It is low-risk (~50 LOC), but it should be validated in Week 1's first hours, not asserted as proven.
2. **stdout pollution.** Both experiment scripts print progress tables to stdout (`console.log("✓ … → score")`) *and* emit results (to a file today; the build will want stdout JSON). The moment one `.mjs` logs to stdout while Python parses stdout as JSON, the seam breaks. This is the most likely Week-2 integration bug.
3. **No error envelope.** Exp 04 ends with `main().catch(e => { console.error(e); process.exit(1); })` — stderr text + exit code 1 is the entire error contract. At Week 4-8 scale (score batches, optimize, 11 LLM-template features, company-research 10-node pipeline) you need a machine-readable error channel to distinguish: transient (retry), rate-limit (backoff), content (quarantine record), and bug (crash loudly).
4. **Timeout/signal blind spot.** Neither experiment has a timeout on the z-ai call; a hung SDK call hangs the batch forever. Python must use `subprocess.run(..., timeout=)` with process-group kill (`start_new_session=True` + `os.killpg`) on expiry, and handle KeyboardInterrupt so Ctrl-C doesn't leave orphaned Bun children or half-written temp files.
5. **Three runtimes, not two.** After D5's fix (below), the system runs Python + Bun (z-ai scripts) + Node (career-ops). D1's "pin package.json" mitigation doesn't cover Node version or the globally-installed z-ai SDK.

**Recommended changes** (all Week 1, ~1 day total):
- Define the seam contract in D1.1: machine output = one JSON envelope on stdout (`{ok, schema_version, data | error:{code,message,retryable}}`); human logs → stderr; nonzero exit only on crash. ~2h to write into `jobsearch/llm.py` + one `.mjs` helper.
- Add `timeout=` + process-group kill + KeyboardInterrupt cleanup to the single subprocess helper; make it the only place `subprocess` is invoked for JS. ~2h.
- Pin all three runtimes (bun, node, python) + vendor the z-ai SDK into the repo's `package.json`/`bun.lock` instead of relying on the global install (which may not survive container recycle). ~2h.

### D2 — Scope: ENDORSE

Correct on all four picks; skipping J7 is the only defensible ethical call, and human-assist G1/G2 matches Risk 1's mitigation. Two minor caveats, neither blocking:

- **Extension distribution is unplanned.** "ResumeWing extension autofills" requires the user to load an unpacked MV3 extension in Chrome on their own machine — outside the container, outside the CLI, undocumented in D6. Add a 1-page setup note (Day 0, ~30min) or the G1/G2 deliverable silently has no delivery vehicle.
- **"E2E" framing.** With a mandatory human submit step, the pipeline is E2E *except* submission. Say so in MASTER_PLAN so Week 8's "e2e operational" claim isn't oversold.

### D3 — Budget: ENDORSE-WITH-CHANGES

Free tiers + $1 is the right posture; nothing in the 158-feature journey needs paid APIs, and Levels.fyi at $2k/mo was correctly rejected.

**Risks / blind spots**

1. **Secrets policy contradiction (real, verified).** `.gitignore` states ".env IS tracked … git is our disk," and `07_autopilot_jobhunt/workdir/.env` is already committed to a GitHub repo — while HANDOFF discipline says the GH PAT is "never written to disk." D3 now adds keys for Hunter, Apollo, Adzuna, Affinda, Apify, 2captcha. Committing live API keys even to a private repo contradicts the HANDOFF's own secret hygiene and makes rotation after the project (already pledged for the PAT) materially harder.
2. **Quota exhaustion is undefined behavior.** Hunter free = 50 emails/**month**. A real job search sends 5–10 outreach emails/week; you will exhaust Hunter in week 2 of use. D7 says HTTP is mocked and D3 says nothing about the 429/quota-exceeded path. This will surface as an unhandled exception mid-pipeline.
3. **Verify the Apify "$5/mo free credit"** — Apify's free plan historically gives $5 one-time trial credit, not monthly; if so, Glassdoor (D10/D12) coverage quietly dies after the first month.

**Recommended changes**: commit `.env.example` with placeholders only; gitignore `.env.local` for live keys (keeps "git is our disk" for structure, not secrets) — ~1h. Define quota-exhaustion as a first-class error code in the D1 envelope with a degrade-to-manual fallback — ~1h. Confirm Apify credit terms before Week 3 — 15min.

### D4 — CRM build-from-scratch: MODIFY

Build-narrow over fork-monica/twenty is clearly right (a PHP/Laravel or full TS CRM would cost more to strip than to write). The 10-file scope guard is good; add a schema-complexity guard (≤ ~12 tables) too. **The problems are in the data model, not the build choice:**

1. **Two sources of truth, unresolved.** D4 puts CRM tables in "the same DB file as the tracker." But *which* tracker? Week 1 (HANDOFF §4) builds a Python SQLite tracker (ai-job-search CSV schema + extensions) as source of truth, while D5 adopts career-ops `tracker.mjs` — whose architecture is the *opposite*: `data/applications.md` is the source of truth and its SQLite DB is a **derived, deletable index** (exp 10: "SQLite is a derived index that can be deleted and rebuilt any time"). If both ship, you have two tracker systems with different schemas, different write paths, and a markdown↔SQLite sync problem nobody owns. This is the single biggest architectural blind spot in the decision set, and it must be resolved **before** the Week 1 schema is written — otherwise Week 4 ("tracker integration") becomes a rewrite week.
2. **Multi-runtime concurrent writes.** Python (`sqlite3`), Node (`node:sqlite` via tracker.mjs), and a future APScheduler cron (H15) can all hold the file. In default rollback-journal mode, a Python write txn blocks/`SQLITE_BUSY`-es a Node writer; node:sqlite has no meaningful default busy timeout, so concurrent writes throw immediately. WAL mode fixes reader/writer overlap but is a persistent file setting both sides must handle, and it does **not** permit two simultaneous writers.
3. **Is single-file SQLite a scaling trap? No — but it is a concurrency trap.** At job-search scale (10³–10⁴ rows) SQLite is overkill-proof. The risk is not volume; it's (a) two runtimes writing one file, and (b) the file being simultaneously treated as source of truth (Python) and derived cache (tracker.mjs).

**Recommended changes** (decide in a 1-hour session before Week 1, ~1 day to implement):
- **Pick one source of truth.** My recommendation: SQLite (Python-owned) as the single source; do not adopt tracker.mjs as a writer — port its genuinely valuable ~570 LOC of normalization/status-alias/history logic to Python (2–3 days, exp 10 confirms it's clean), or adopt markdown-as-truth wholesale and make Python the derived view. Either is fine; *both* is not.
- If any .mjs must touch the DB: WAL + `busy_timeout=5000` on **both** connection helpers + single-writer rule (all writes go through Python; Node opens read-only). ~2h.
- Add a DB backup/export policy (D6 gap — see below).

### D5 — Career-ops subprocess: MODIFY

The 5 picks are the right 5 files (matches exp 10's "Top 5 most valuable"). But the decision contains a **verified factual error** and two integration gaps:

1. **`bun` cannot run `tracker.mjs` — verified in this container.** tracker.mjs depends on `node:sqlite` (exp 10: "requires Node 22.5+"). `bun -e "import('node:sqlite')"` fails with "No such built-in module" on Bun 1.3.14; Node v24.18.0 has it. D5's "called via subprocess with `bun`" will crash on the flagship file. This also falsifies D5's rationale "Bun runtime is already a dependency (D1), so no new runtime cost" — you need Node ≥22.5 as a pinned third runtime. **Fix**: run career-ops `.mjs` under `node`; reserve `bun` for z-ai SDK scripts. (scan/scan-ats-full/verify-cv-facts/pipeline-lock use only fs/path/crypto/fetch/js-yaml and would run under either, but standardize on `node` for the whole career-ops set.)
2. **`pipeline-lock.mjs` is a library, not a CLI.** It exports `acquirePipelineLock()` and returns a `{lockDir, release()}` handle. You cannot "call it via subprocess" without writing a lock-holder wrapper whose child-process lifetime *is* the lock lifetime — awkward, and stale-reclaim then depends on Python crash behavior. The mkdir-based protocol (atomic `mkdir` + `owner.json` + two-stage stale reclaim) is trivially portable: reimplement in Python (~60–80 LOC) at the storage layer and drop the cross-runtime locking dependency entirely. ~3h.
3. **"Standalone" ≠ "drop-in."** scan.mjs is 3,039 LOC coupled to ~10 sibling modules + `providers/` (~95 plugins) and a file-based contract: `portals.yml`, `config/profile.yml`, `data/scan-history.tsv`, `data/blacklist.md`, `data/pipeline.md`. Provisioning that layout (plus `js-yaml` install in the career-ops tree) is ~1 day of unestimated Week-1/4 work, and scan.mjs *appends markdown rows to pipeline.md* — feeding straight back into the D4 source-of-truth conflict.

**Keep**: the per-file Week-1 smoke test — but make it a **blocking gate run with `node`** before any feature depends on the file, and include the provisioning fixture in the test.

### D6 — Repo structure: ENDORSE-WITH-CHANGES

Freezing `experiments/`, provenance headers, and keeping third-party repos outside the repo are all right. Gaps:

1. **Day-0 re-clone is unscheduled.** `/home/z/repos/` does not exist. Week 1 step 1 lifts code from `repos/vageesh-kudutini-ramesh__resume-wing/...` — the build cannot start until 4 repos are re-cloned (worklog Task 0 already flagged this; D6 should own it as a scripted, verifiable pre-build step, ~30min).
2. **No data/backup policy.** "Git is our disk" is the stated policy, but the SQLite DB (and career-ops markdown/TSV data) location and commit cadence are undecided. Committing binary SQLite is tolerable single-user; at minimum decide: DB path, gitignore or commit, and a nightly JSON/markdown export for diffability. ~1h to decide + 1h to script.
3. **No CI decision.** Even a one-line policy ("pytest -m 'not live' must pass before each weekly PR merges, run in-container") — D7's mock-only default makes this cheap and it's currently implicit.
4. **SDK vendoring** (see D1 change 3) — global-install reliance is a container-recycle hazard.

### D7 — Testing strategy: MODIFY

The skeleton is right: mock HTTP by default, `live` marker excluded by default, golden files for subprocess output, test-before-depend. Three material gaps:

1. **Malformed LLM JSON is the highest-probability failure mode and has no specified behavior.** The existing pattern (exp 02 `score_jobs.mjs:103-124`) is: greedy regex `/\{[\s\S]*\}/` → `JSON.parse` → on failure return `{score:-1, raw}` sentinel, **no retry, no quarantine, sentinel then leaks into ranking** (`(b.llm_result.score||0)` sorts -1 below everything, silently). Known concrete breakages with this pattern: two JSON objects in one response (greedy regex spans both → parse fails); fenced ```` ```json ```` blocks with trailing prose; truncated output; leading BOM/smart quotes. None is tested; none has defined behavior. **Required spec in `jobsearch/llm.py`**: (a) salvage parse (strip fences, balanced-brace extraction, one targeted re-ask with "return ONLY JSON" suffix), (b) N=2 retries, (c) on final failure write a structured `llm_errors` row (prompt_hash, raw truncated to ~2KB) and continue the batch, (d) never emit sentinel scores into ranking. Tests: fixture corpus of ≥8 malformed-output shapes as golden inputs — ~4h.
2. **No mock seam for the z-ai SDK.** The `.mjs` scripts hard-import `z-ai-web-dev-sdk`. Golden-file mocks of *subprocess output* test the Python side only; the Bun side is untestable without network. Add a documented injection point (e.g. `ZAI_MOCK_PATH` env or `--mock` flag swapping the import) so the actual `.mjs` logic runs deterministically in CI — ~2h.
3. **The Week 4–8 failure modes are untested anywhere**: subprocess timeout kill, KeyboardInterrupt cleanup, and Python-writer/Node-reader SQLite concurrency (see Angle 3). Add one test each in Week 1–2 — ~3h.

### D8 — Caching + rate limiting: MODIFY

Caching in SQLite keyed by (prompt_hash, model) is correct, cheap, and directly mitigates Risks 3+6. Three fixes:

1. **The TF-IDF pre-filter, as specified, is refuted by experiment 02's own data.** Exp 02 results: TF-IDF scored all 10 jobs **5–12/100** (compressed), while the LLM scored the same jobs up to **85/100** — the SUMMARY even concludes "drop jobs scoring <30% before spending LLM tokens," and D8 inherits "only top-N get LLM treatment." An absolute 30% TF-IDF cutoff would have discarded **every** job the LLM ranked 75–85 — including both top picks. TF-IDF's compression (5–12 range) makes absolute thresholds meaningless. **Fix**: use a *relative* rule — top-N by TF-IDF, or TF-IDF z-score/percentile — and validate the cut against LLM scores in Week 1 before trusting it. (The tercile-agreement of 2/10 is itself a warning that the pre-filter is weak; consider making N generous.)
2. **Cache key must include prompt-template version.** Prompts *will* change across 8 weeks; without a version in the key, stale-cache responses silently poison scoring/optimization after every prompt edit. Add `prompt_template_version` (or hash the full template, not just the variable payload) — ~30min.
3. **Rate-limit *response* handling is undefined.** "Batches of 10, 1s between batches" is pacing, not resilience. Specify: exponential backoff with jitter on 429/5xx (honor Retry-After if present), circuit-break after K consecutive failures, and record the event in the cache DB for observability — ~2h. Also note pacing lives at the Python layer (llm.py controls batch spawn), which is consistent with D1.1 — good.

---

## Angle 1 — Integration risk: Python + Bun at Week 4–8 scale

The hybrid is workable *because* of D1.1's single seam, but by Week 4–8 there are **three subprocess categories**: llm.py→Bun (z-ai), Python→Node (career-ops 5 files), Python→Bun (ai-job-search portal CLIs, per exp 12's "keep portal CLIs as Bun subprocesses"). Specific hidden couplings:

- **JSON schema drift** between `.mjs` producers and Python consumers is mitigated only if the envelope (D1 change 1) is adopted *first* and every script routes through it. Ad-hoc per-script JSON (the exp 02/04 style) will drift; with `schema_version` in the envelope, drift becomes a detectable error, not a mysterious KeyError.
- **Error propagation**: exit code 1 + stderr prose (current pattern) cannot express "retryable vs fatal." Without the error envelope, Week 7's batch template-LLM features (11 of them) will each invent their own failure dialect.
- **Signal handling**: Ctrl-C in Python propagates SIGINT to the foreground process group (fine), but Python must catch KeyboardInterrupt, reap children, and clean temp files, or you get orphaned Bun processes holding the DB lock.
- **Timeout management**: no experiment ever timed out an SDK call. Every subprocess call needs `timeout=` and kill-on-expiry; the 10-node company-research pipeline (Week 3) multiplies the hang risk 10×.
- **Env propagation & temp files**: exp scripts hardcode absolute paths and rely on ambient env; the build must pass payloads via temp files/stdin (argv overflows on resume+JD batches) and whitelist env vars per child.
- **Latency stacking** (minor): company-research-agent's 10-node pipeline rerouted through llm.py→Bun adds ~10 subprocess spawns per report — negligible vs LLM latency; just confirm exp 11's 2–3 day estimate assumed this hop (it assumed direct SDK calls in Python `base.py`, which is impossible — the SDK is JS-only; the estimate still holds, but the architecture note belongs in MASTER_PLAN).

**Net**: no fatal coupling problem, but the seam contract is load-bearing and currently unspecified. One day of Week-1 work (envelope + timeout + signal + vendored SDK) removes the whole class.

## Angle 2 — Testing sufficiency for LLM-dependent features

Answer: **insufficient as written, fixably so.** What specifically breaks on malformed JSON, per the current code:

1. Greedy `/\{[\s\S]*\}/` spanning two objects → `JSON.parse` throws → sentinel `{score:-1}` → ranking silently degrades (no error surfaces anywhere).
2. Fenced output (```` ```json … ``` ````) with prose → regex captures braces across fence+prose → parse throws → same silent sentinel.
3. Truncated response (max_tokens hit) → unbalanced braces → same path.
4. Batch-level: one `ZAI.create()` failure or one hang kills the *entire* batch (no per-item isolation at init, no timeout at call).
5. Downstream: `score:-1` passes `if (j.llm_result.reasoning)`-style guards, lands in `results.json`, and would land in the tracker as a real score if unvalidated.

None of these paths is tested (no fixture corpus, no mock SDK seam, no retry logic exists to test). Required additions (detailed in D7): malformed-output fixture corpus, salvage-parse + bounded retry + quarantine table in llm.py, mock-SDK injection in `.mjs` scripts, and one live smoke test that deliberately elicits a bad response (e.g., ask for 10k tokens of prose) to prove the resilient path end-to-end. Total ~1 day.

## Angle 3 — Data model risk: one SQLite file, two runtimes

- **Concurrent access reality**: Python `sqlite3` (5s default busy timeout) + Node `node:sqlite` (effectively no busy-wait) + a future APScheduler cron writer. Default journal mode = whole-file locking; a Python write txn during a Node write → immediate `SQLITE_BUSY` crash in the Node child, which surfaces as an opaque subprocess failure. **WAL mode matters a lot here**: it enables concurrent readers with one writer and is a persistent, per-file setting both runtimes honor (SQLite 3.53.1 supports it fine). Enable WAL once at DB creation; set `busy_timeout` on both connection helpers.
- **But WAL does not solve the real problem**: two writers. career-ops's own answer is `pipeline-lock.mjs` — a *file-level* mkdir lock that knows nothing about SQLite transactions; bolting it across runtimes (see D5) adds a second, orthogonal locking regime. The correct policy is **single-writer**: all writes through Python; Node opens read-only (`node:sqlite` / `sqlite3` both support `mode=ro`); career-ops's own derived-index DB stays a *separate file* (or is dropped per D4's source-of-truth fix).
- **Is single-file SQLite a scaling trap?** No — at this data volume SQLite is the right database; splitting files buys nothing. The trap is *concurrent multi-runtime writes + mixed source-of-truth semantics*, both policy problems, both cheap to fix if decided before Week 1 (see D4 changes; ~1 day total).

## Angle 4 — Missing decisions

Decisions that should exist before build start, roughly by severity:

1. **Single source of truth for application state** (SQLite vs markdown) — the unstated D4/D5 conflict. Highest priority.
2. **Error-handling policy** — exit codes, the cross-runtime error envelope, retry/backoff/quota-exhaustion semantics, batch partial-failure behavior. (D1/D3/D7 changes fold into this.)
3. **Logging & observability** — `loguru` is listed in HANDOFF §6 infrastructure but never decided. Add: structured logs per pipeline run + LLM call telemetry (latency, tokens, cache hit/miss, error rate) — the cache DB can double as the telemetry store. ~half day.
4. **Secrets handling** — resolve the ".env is committed" vs "PAT never on disk" contradiction before D3 keys exist (see D3).
5. **Config management** — lifted components bring three config styles (career-ops YAML `profile.yml`/`portals.yml`, ResumeWing Python env/config, ai-job-search markdown+CSV). One loader + documented mapping, or config drift becomes the Week 3 tax. ~half day.
6. **Versioning/pinning & bootstrap** — pin bun+node+python, vendor the SDK, write a `bootstrap.sh` that recreates the environment after container recycle (worklog Task 0 shows recycles are expected). ~2h.
7. **CI policy** — one sentence + a pre-PR test command (see D6).
8. **Schema migration policy** — 8 weeks of additive schema evolution on the tracker DB; adopt ai-job-search's additive-only + version-stamp discipline (exp 12 documents it as a strength). ~1h to decide.
9. **Prompt asset management** — prompts are the core IP (exp 12: "the spec IS the implementation"); decide where prompts live (`jobsearch/prompts/`), how they're versioned, and that the version feeds the D8 cache key.

## Angle 5 — Week 7 load and hidden critical paths

**Week 7 is overloaded.** Item 4 alone (L6–L10, Q1–Q4, F6, F7 — 11 true-gap features) is ~41h by HANDOFF's own appendix table (35–50h stated in §4). Add four more workstreams (career-ops salary/negotiation tools, salary_lookup.py + Levels MCP integration, comp-calc fork + options simulator, H1B MCP) and integration/testing overhead — realistically 60h+ in a plan whose other weeks assume ~30–40h. Two fixes:

- **Build a generic template+LLM engine once** (~1 day): inputs = template pack + jurisdictional lookup table + LLM call via llm.py. The 11 features become content packs (~1–2h each) → ~20h instead of 41h, and the engine is reusable for cover letters (F6) and outreach (Week 5).
- **Spread the packs across Weeks 5–7** — they depend only on llm.py (Week 1) + templates, not on Weeks 5–6 output. Keep Week 7 focused on the offer-analysis core (comp breakdown, negotiation, onboarding plan).

**Hidden critical paths**:
- **Week 1** — everything depends on the data layer + the llm.py seam + the *unresolved* tracker schema (D4). The tracker decision is the true critical-path item and it's currently ambiguous.
- **Week 3** — RSSHub self-host requires Docker, **which is not installed**; only the rate-limited public instance is viable (or drop RSSHub, use feedparser directly against company PR feeds). Also the heaviest single adaptation (company-research-agent, 2–3 days by its own estimate) plus 5 other integrations — tight but feasible if the 2–3 day estimate survives the llm.py rerouting.
- **Week 4** — becomes a rewrite week if the source-of-truth conflict isn't resolved at Week 1 (data model + Gmail sync + H12 schema all land here).
- **Week 8** — CLI + slash commands + dashboard + MCP + docs is integration-heavy and always under-estimated; it's fine *only if* Weeks 5–7 shed load per above.

---

## Final summary — top 3 changes before build starts

1. **Resolve the tracker source-of-truth conflict (D4×D5) and fix D5's runtime error before any schema is written.** Verified fact: Bun 1.3.14 cannot run `tracker.mjs` (`node:sqlite` missing) — career-ops subprocesses must run under Node. Decide SQLite-single-source (recommended; port tracker.mjs's normalization logic to Python, 2–3 days) or markdown-single-source; adopt WAL + busy_timeout + single-writer policy; drop cross-runtime `pipeline-lock.mjs` usage by porting its ~70 LOC mkdir protocol to Python. *Effort: ~1h decision + ~1 day implementation; blocks Week 1 schema.*

2. **Specify and test the cross-runtime seam contract in Week 1.** One JSON envelope (`ok/schema_version/data|error{code,message,retryable}`) on stdout, logs to stderr, `timeout=` + process-group kill, KeyboardInterrupt cleanup, vendored SDK + pinned runtimes, and a malformed-LLM-JSON resilience spec (salvage parse → bounded retry → quarantine row → never a sentinel score in rankings) with a fixture-corpus test and a mock-SDK injection seam. *Effort: ~1.5 days; prevents the dominant Week 2–8 failure classes.*

3. **Rebalance Week 7 and correct the D8 pre-filter.** Build the generic template+LLM engine and spread the 11 true-gap packs across Weeks 5–7 (~20h vs 41h); replace the absolute TF-IDF 30% cutoff with a relative top-N rule (exp 02's own data shows the absolute cutoff would have discarded every job the LLM scored 75–85); add prompt-template version to the cache key and define 429/backoff behavior. *Effort: planning 1h + engine ~1 day; the D8 fixes are hours.*

**Overall**: proceed — with the three changes above. The decision set is unusually evidence-grounded (exp 01/02/10/12 are genuinely strong validation artifacts), the ethical scoping in D2 is right, and D1.1's single-seam rule is the correct architectural instinct. None of my findings reverses a decision; the two MODIFY-with-fact findings (D5 runtime, D8 threshold) are cheap fixes now and expensive reworks at Week 4.
