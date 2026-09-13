# Development guide

## Running the tests

```bash
python3 -m pytest -q          # full suite — offline, ~35s (live auto-deselected)
python3 -m pytest -m live     # LIVE: real job-board APIs + real LLM (network + quota)
```

Default runs never touch the network: board clients replay
`tests/fixtures/sources/*.json` payloads, LLM paths replay
`tests/fixtures/llm/*.json` via `ZAI_MOCK`. Live tests are opt-in and spend
real API/LLM quota — run them consciously.

## ZAI_MOCK — the LLM replay seam

`llm/lib/runner.mjs` checks `process.env.ZAI_MOCK` before loading the SDK; if
set, it returns a fake client instead of calling `createClient`.

- Fixture format: a JSON **array of raw LLM response strings**, replayed in
  order, one per `chat.completions.create` call.
- An item `{"error": "..."}` makes that call **throw** (simulates SDK errors).
- If calls outnumber entries, the last entry repeats.

Python seam tests set `ZAI_MOCK` in the environment `jobsearch/llm.py` passes
to the Node child, so the real script and envelope contract get exercised
(`tests/test_llm_seam.py`, `tests/test_envelope_node.py`).

## Adding an LLM script (envelope contract, DECISIONS D1.1)

1. Create `llm/<name>.mjs` importing `main`/`createClient` from
   `./lib/runner.mjs`, ending with `main(async (payload) => { ...; return data; })`.
2. Contract (runner.mjs enforces it):
   - Payload arrives via `--payload <file>` (never argv; stdin also accepted).
   - stdout: exactly ONE JSON envelope — `{"ok": true, "schema_version": "1",
     "data": {...}}` or `{"ok": false, ..., "error": {code, message, retryable}}`.
   - Logs go to stderr only. Exit 0 whenever an envelope was produced;
     nonzero means crash.
   - Per-item failures stay per-item (D9); transient errors (429/timeout/5xx)
     should `throw` so runner.mjs classifies the envelope `llm_retryable`.
3. If it needs a prompt: add `jobsearch/prompts/<name>.md` with frontmatter
   `version:` + `source:`. The version feeds the cache key — bump it on edits.
4. Call it from Python only via `jobsearch/llm.py` (`LLMSeam`), never with a
   bare `subprocess` call.
5. Test it: add a fixture under `tests/fixtures/llm/` plus cases in
   `tests/test_llm_seam.py` / `tests/test_envelope_node.py`.

## Adding a job source

1. Write `jobsearch/sources/<name>.py` with
   `fetch(keywords, location="Remote", num_results=20, cfg=None) -> list[Job]`
   (see `jobicy.py`; helpers in `base.py`: `fetch_json`, `keyword_match`,
   `detect_h1b`, `extract_email`, `clean_html`).
2. Register it in `jobsearch/sources/__init__.py`:
   `REGISTRY["Name"] = Source("Name", <mod>.fetch, remote_only=...)` — the
   `Source` dataclass is `name, fetch, remote_only, configured, hint`.
3. Capture a real API payload as `tests/fixtures/sources/<name>.json` and test
   against it by monkeypatching `<mod>.fetch_json` — patch the client module's
   own binding, not `base.fetch_json` (each client imports it at import time).
   Pattern: `tests/test_sources.py`.

## DB migrations (D12)

`jobsearch/storage.py` defines `_MIGRATIONS: dict[int, str]` — numbered SQL
blocks applied additively in ascending order; the applied version is stamped
in the `schema_version` table (`SCHEMA_VERSION` is the latest).

To change the schema: append the NEXT `_MIGRATIONS[N]` block (additive only —
no drops or renames; v2 added trust + `source_stats`, v3 the categorization/
ops columns, v4 `sector`), bump `SCHEMA_VERSION`, and add
a test that a version-(N-1) DB with data migrates forward. Migrations run
inside explicit transactions with ALTER-guarding (a half-applied migration
recovers on the next open). Every connection sets `journal_mode=WAL`
+ `busy_timeout=5000`; Python is the sole writer (D4); the CLI constructs
`Store` inside the `PipelineLock` so two CLIs can't race `init_db`.

## New modules map (2026-08-27 Steps A–E)

- `jobsearch/transport.py` — geo-block transports: **Netlify edge
  scraper** (`netlify_fetch_json` — US egress, free; transparent blob-store
  follow for non-HTML bodies; the primary USAJobs fallback) + **ZenRows
  premium proxy** (`zenrows_fetch_json` for JSON APIs — USAJobs secondary;
  `zenrows_fetch_text` for js_render'd HTML — ZipRecruiter/Glassdoor,
  currently dormant: ZenRows credits exhausted 2026-08-29). Netlify gated
  on `NETLIFY_SCRAPER_TOKEN`; ZenRows on `ZENROWS_API_KEY` (credits).
- `jobsearch/trust.py` — per-job 4-rule trust scoring (flag, never drop)
  + per-source 4-dimension trust score. The aggregator enriches every
  fetched Job; `Store.record_source_result` rolls per-source stats.
- `jobsearch/net.py` — `parse_retry_after_ms` (clamped Retry-After),
  jitter backoff, and the process-wide `DnsCache` (installed by the CLI —
  patches `socket.getaddrinfo`).
- `jobsearch/sources/wttj.py|ashby.py|personio.py|ziprecruiter.py|glassdoor.py`
  — the Step-B/C adapters (see SOURCE_AUDIT.md for the per-adapter
  verification notes).
- `jobsearch/dedup.py` — trust-scored precedence: the higher-ladder source
  becomes the merge identity (methodology §objective-I ladder).

## New modules map (2026-08-29 Sprint 3)

- `jobsearch/categorize.py` — T2 classify-tier port (leftmost-marker rule
  + associate/program-bridge guards), skill-extract port (exact aliases
  only; Go/SAFe case-sensitive side passes), and the central work-mode
  heuristic. Pure functions; the aggregator enriches every Job.
- `jobsearch/reposts.py` — T2 role-matcher + detect-reposts ports
  (90-day sliding-window clusters, same-URL collapse) and the §708 ghost
  heuristic (2+ aggregators, no own-ATS sighting, >30d). The CLI ops pass
  runs these on the PRE-dedup rows — dedup merges the multi-URL repost
  evidence away — and persists signals onto the surviving stored rows.
- `jobsearch/ats_resolve.py` — link→(platform, slug) detection for 13 ATS
  URL shapes + `AtsDirectory`, the company→board resolver over
  `data/ats_directory.db` (drained by `scripts/build_ats_directory.py`;
  missing DB resolves [] — flag, never drop).
- Sector axis — prompt v3 + `storage` v4: the LLM scoring pass also infers
  a fixed-taxonomy sector for each scored (top-N) job.

## Runtime knobs (env)

| Knob | Effect |
|------|--------|
| `CAREERJET_REFERER` | registered publisher site — the Referer header sent with Careerjet API calls (the IP allowlist is the primary auth; re-submit IPs after every container recycle) |
| `NETLIFY_SCRAPER_TOKEN` / `NETLIFY_SCRAPER_URL` | enables the Netlify edge transport (US egress — the free USAJobs fallback) |
| `ZENROWS_API_KEY` | enables the ZenRows transports (USAJobs secondary fallback + ZipRecruiter/Glassdoor adapters; credits per request) |
| `ASHBY_ORGS` / `PERSONIO_SLUGS` | comma-separated tenant lists (defaults: 5 verified Ashby orgs / personio+kiwigrid) |
| `JOBSEARCH_NO_DNS_CACHE=1` | disables the DNS cache + pacing entirely |
| `JOBSEARCH_DNS_LOOKUPS_PER_MIN` | resolver-bound lookup cap (default 400; 0 = no cap) |

CLI shape additions (Sprint 3): `search-jobs ... --ops/--no-ops` (repost +
ghost pass; default on) and `--jsonl PATH` (export the run's stored jobs as
JSONL — real JSON arrays, one object per line).

## Inspecting the runtime DB

```bash
python3 -c "import sqlite3; print(sqlite3.connect('data/tracker.db').execute('select * from runs').fetchall())"
```

`runs` holds run summaries, `telemetry` LLM call records, `quarantine` malformed LLM outputs.
