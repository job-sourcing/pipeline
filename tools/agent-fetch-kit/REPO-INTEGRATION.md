# Integration notes (job-sourcing-research vendoring, 2026-09-08)

This is a vendored copy of the user's agent-fetch-kit (v1.0), kept under
`tools/agent-fetch-kit/` per the git-is-the-disk policy. Full evaluation:
`docs/findings.md`, `docs/decision-matrix.md`, and the repo-side audit
`audit/findings-fetchkit.md`.

## What the repo actually uses from the kit
1. **`supabase_fetch_json`** — ported INTO the pipeline as
   `ingest/jobsearch/transport.py` (chain: direct → supabase → netlify →
   zenrows). GET-only, no custom-header forwarding, region-pinnable
   (x-region=us-east-1 verified live: Ashburn US egress). Free.
2. **`FIRECRAWL_API_KEY`** — consolidated in `ingest/.env` (also read by
   `scripts/careerjet_allowlist/06_register_firecrawl.py`).
3. **`bin/wfetch` / `bin/wprobe`** — manual ops/debug tools only. Run from
   this directory with `PYTHONPATH=lib python3 -m fetchkit.cli …` (or
   source the env per the .env header). NOT wired into the pipeline's HTTP
   seam: wfetch cannot forward custom headers and treats 402 as terminal,
   which would break the adapters' exception contract (S8 live tests).

## Gotchas (measured 2026-09-08)
- **Netlify queue engines are BROKEN on our deploy** (puppeteer AND
  chrome_impersonate submit 202 but stay pending forever — the worker only
  drains on preview deploys). Only the inline `fetch` engine works — that
  is the engine `ingest/jobsearch/transport.py::netlify_fetch_json` already
  uses. ⚠️ This invalidates PLAN Phase S5-1's original assumption.
- **The kit's ZENROWS key is the same exhausted key as the repo's** (402
  AUTH004) — the kit brings zero new ZenRows credits.
- **Tier 5 (GHA remote compute) needs its own repo + token**: the zip's
  GH_TOKEN was a placeholder (401 Bad credentials) and the strbtwc PAT
  cannot see `zmytone/agent-fetch-kit`. Configure GH_TOKEN/GH_REPO in this
  .env to enable; ~60 s latency per fetch.
- **wprobe burns 1 Firecrawl credit per run** (one live scrape probe).
- **Tier 6 (patchright browser)** needs `pip install patchright` + its
  chromium download; untested here.
- Kit env loader reads `$KIT_ROOT/.env` AND repo-root `.env` (repo root
  wins) — with this layout that is `tools/agent-fetch-kit/../../.env`,
  which does not exist, so the kit .env is authoritative here.
