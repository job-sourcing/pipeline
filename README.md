# Job Sourcing Research — Methodology + Working Ingestion Pipeline

> **The single go-forward repo for the job-sourcing stream** (consolidated 2026-08-27 from
> two parallel research streams — see [`CONSOLIDATION.md`](CONSOLIDATION.md) for the full
> cross-walk). The wider e2e job-search research (resume/interview/networking/offer facets)
> remains frozen in the [`ai-job-search-experiments`](https://github.com/strbtwc/ai-job-search-experiments)
> archive repo.

Two halves, one repo:

1. **The spec** — [`job_sourcing_methodology.md`](job_sourcing_methodology.md): WHERE the
   jobs are (6-tier source map), HOW to get each (per-source method), HOW MUCH before
   failure (ceilings/rate limits), WHAT strategy combines them (5-layer trust-scored posture).
2. **The implementation** — [`ingest/`](ingest/): the `jobsearch` Python package — 25
   registered source adapters (23 default + 2 opt-in ZenRows-transport boards, currently
   dormant: credits exhausted; 2026-09-08: **Workday CXS** added with the NVIDIA pilot),
   **board-dump v2 + GHA board-watch** (2026-09-10: exhaustive per-company snapshots
   with ALL locations, full descriptions, metadata + LinkedIn corroboration signals;
   daily diff+alert watch — `ingest/data/workday/` + `ingest/data/board_watch/`),
   trust-scored dedup + per-source trust stats, Netlify + ZenRows + **Supabase
   edge-proxy** transports (agent-fetch-kit integration, `tools/agent-fetch-kit/`),
   SQLite storage (schema v5), TF-IDF + LLM scoring + sector
   inference, categorization (tier/skills/work-mode), ghost-job/repost detection, Telegram
   alerts + scheduler, JSONL export, `search-jobs` CLI, **test suite green
   offline** (live-marked tests auto-deselected — run `cd ingest &&
   python3 -m pytest -q`),
   live-verified end-to-end.
3. **The living plan** — [`PLAN.md`](PLAN.md): where the work stands and what's next
   (phases + exit criteria), maintained every session.

## Quick navigation

| Goal | Read this |
|------|-----------|
| Understand the sourcing strategy | [`job_sourcing_methodology.md`](job_sourcing_methodology.md) — start at §1 TL;DR |
| Where we are / what's next | [`PLAN.md`](PLAN.md) — the living execution plan |
| Run a job search right now | `cd ingest && pip install --break-system-packages -e . && search-jobs "software engineer" --num 10` |
| See per-source live-verification status | [`ingest/SOURCE_AUDIT.md`](ingest/SOURCE_AUDIT.md) (+ §7 consolidation deltas) |
| Pick up the work in a new session | [`HANDOFF.md`](HANDOFF.md) — bootstrap + recovery checklist |
| Why was X decided? | [`DECISIONS.md`](DECISIONS.md) (strategic S1–S13) · [`ingest/DECISIONS.md`](ingest/DECISIONS.md) (engineering D1–D13) |
| Understand the 2026-08-27 consolidation | [`CONSOLIDATION.md`](CONSOLIDATION.md) — full cross-walk inventory |
| Agent process + generalized know-how | [`.agents/SKILL.md`](.agents/SKILL.md) — Part A: how to run sessions; Part B: domain know-how |
| Re-run validation probes | `scripts/validators/` (8 scripts) → outputs in `validation_results/` |
| Netlify + ZenRows transports | Both are IN the package: [`ingest/jobsearch/transport.py`](ingest/jobsearch/transport.py) — `netlify_fetch_json` (free US-egress edge scraper; the primary USAJobs 403-fallback as of 2026-08-29) and the ZenRows pair `zenrows_fetch_json`/`zenrows_fetch_text` (credits; USAJobs secondary + ZipRecruiter/Glassdoor — dormant while ZenRows credits are exhausted). The Supabase edge-proxy transport still lives only in [`scripts/job_sourcer.py`](scripts/job_sourcer.py) (historical reference — port it if the IP-rotation use case returns) |
| Careerjet publisher-account automation | [`scripts/careerjet_allowlist/`](scripts/careerjet_allowlist/) (Turnstile bypass via ZenRows Browser Sessions, IP-allowlist submitter) |
| ATS directory (slug→ATS→job-count) | `ingest/scripts/build_ats_directory.py` + `.github/workflows/ats-drain.yml` (durable GHA drain; committed DB + snapshot) + `scripts/export_ats_directory.py` |
| Sourcing-relevant experiment history | `archive/track1-experiments/` (6 summaries) |

## The pipeline (`ingest/`)

```bash
cd ingest
python3 -m pytest -q                # offline by default (live-marked tests deselected)
search-jobs "Python Developer" --num 10          # or: python3 -m jobsearch.cli ...
search-jobs "software engineer" --sources Greenhouse,Lever --no-score
search-jobs "python" --jsonl out.jsonl           # enriched JSONL export (ops pass + categorization inline)
```

- **25 registered sources**: 7 no-key (Remotive, Arbeitnow, TheMuse, RemoteOK, Jobicy,
  LinkedIn Guest, HN Who's Hiring) + 6 free-key (Adzuna¹, JSearch², USAJobs¹, Findwork¹,
  Jooble², Careerjet¹) + 6 ATS-direct (Greenhouse, Lever, SmartRecruiters, Ashby, Workable,
  Personio) + WTTJ (public Algolia) + Wellfound + Workday CXS (per-company boards — the
  board-dump/watch surface) + 2 opt-in ZenRows boards (ZipRecruiter,
  Glassdoor) + 1 aggregator wrapper (python-jobspy → Indeed).
  ¹ live-verified ² keyed-API works but quota-capped/benchmark-only
- **Board-dump deliverable (S9)**: `ingest/data/workday/nvidia_us_fulltime.csv` — CSV v2.3,
  43 columns (full column semantics + enums: [`docs/csv-v2-spec.md`](docs/csv-v2-spec.md)),
  1,406 rows, engagement-corroborated against LinkedIn guest cards (matched 77.9% in the
  pre-join-fix snapshot — an honest verbatim-only regeneration was in flight at doc time).
  Built by `scripts/board_dump.py --phase list → details → tagfacets → corroborate →
  titlesearch → finish` (titlesearch = targeted exact-title guest search, MANDATORY for a
  complete refresh); ships a role-similarity graph (`similar_edges.jsonl`) and cross-source
  repost evidence (v2.3 timing floor).
- Keys live in `ingest/.env` — **committed deliberately** (no durable disk; git IS the
  disk; external reviewers: waive the credential P0). GH PAT is never committed.
- All LLM calls cross one Node seam (`ingest/llm/`, SDK vendored — no npm install).
- Engineering decisions: `ingest/DECISIONS.md` (D1–D13). Dev guide: `ingest/docs/DEVELOPMENT.md`.
- Audit findings (2026-09-08 hardening round): `audit/findings-*.md` (pipeline /
  infra / docs / data / fetch-kit — 5 parallel sub-agent audits).
- Resilient-fetch kit (vendored): `tools/agent-fetch-kit/` (`bin/wfetch`, `bin/wprobe`; 
  integration notes + gotchas in `tools/agent-fetch-kit/REPO-INTEGRATION.md`).

## Key findings (TL;DR)

1. **No universal job sourcing API exists.** The market fragments into 6 tiers; a layered hybrid is the only complete picture.
2. **Free no-auth APIs cover ~50–100k jobs/day**: Greenhouse/Lever/Ashby/SmartRecruiters (ATS-direct), 5+ remote-tech aggregators, LinkedIn Guest API (`curl_cffi` Chrome131, ~500 jobs/query), WTTJ Algolia flow (~50–100k EU jobs; creds rotate ~monthly).
3. **The 5 hardened sites need 4 different bypasses** — LinkedIn=Guest API, ZipRecruiter+Glassdoor=ZenRows, Indeed=Apify, Workday=plain requests + per-tenant site-ID discovery. One stealth stack does NOT fit all.
4. **Skip list**: Jooble (500 lifetime cap), Careerjet for bulk (120-char excerpts), SeleniumBase UC Mode (doesn't bypass CF), personal VPNs (datacenter IPs flagged), Otta (dead → WTTJ).
5. **Trust-scored posture**: company-ATS > regional-ATS > WTTJ > LinkedIn-Guest > Indeed-via-Apify > Adzuna > … (full ladder + 9-objective winner matrix: methodology §6.5).

## Cost reference

| Tier | Cost | Coverage |
|------|------|----------|
| Free DIY | $0/mo | ATS-direct + LinkedIn Guest + aggregators + WTTJ (~50–100k jobs/day) |
| + Apify Indeed | $0–10/mo | + Indeed (90–95% success) |
| + ZenRows B1/B2 | $19–49/mo | + ZipRecruiter + Glassdoor (1.8k–6k req/mo) |
| Production | $50–150/mo | + TheirStack broad coverage |
| Enterprise | $500+/mo | + Bright Data Web Unlocker |

## Repository state

- Consolidated 2026-08-27 (see [`CONSOLIDATION.md`](CONSOLIDATION.md)); ~700 files; PR #3 merged to main 2026-08-30
- Multi-session worklog: [`worklog.md`](worklog.md) (append-only, bottom = latest)
- Daily board-watch (diff + alert + repost detector) runs on GHA on the **public org mirror
  `job-sourcing/pipeline`** since 2026-09-13 — free unlimited public-repo minutes; the
  workflow commits its own checkpoint state; mirror sync via `scripts/build_public_mirror.sh`
  (bidirectional, host-side — see HANDOFF §3)
- Sibling repos: `career-ops-research` (T2 — gold-standard 85+ provider `.mjs` implementations, reference for porting), `ai-job-search-experiments` (T1 — frozen e2e research archive)

## License

Personal research artifact; pipeline code MIT (`ingest/LICENSE`) with lifted-code
provenance headers — note the job-ops dedup port is AGPL-3.0 + Commons Clause (verify
before redistribution). Respect every site's ToS.
