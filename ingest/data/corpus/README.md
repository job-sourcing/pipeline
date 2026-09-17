# Durable jobs corpus — facet-01 export (Wave-R R2)

**What this is:** a committed, sandbox-independent corpus of live job
postings in the facet-01 JSONL contract (spec: `../../docs/jsonl-export-spec.md`
— consumer: the MAIN repo's facet-01 loader, `load_postings_jsonl`).
This is the durable successor to "the corpus lives in `data/tracker.db`
and dies with the sandbox" — same whitelist precedent as
`data/workday/` (the NVIDIA deliverable).

## Contents

| File | Query | Rows | Bytes |
|---|---|---|---|
| `software_engineer.jsonl` | "software engineer" | 529 | ~1.4MB |
| `platform_engineer.jsonl` | "platform engineer" | 220 | ~0.6MB |
| `product_manager.jsonl` | "product manager" | 359 | ~0.9MB |
| `manifest.json` | — | run log, per-source counts, degraded list | |

Run date: **2026-09-16**, location `Remote`, `--num 50` per source,
22/23 default sources attempted (18 ok + 4 degraded; the 23rd — JobSpy —
skipped unconfigured). Full numbers in `manifest.json`.

**Currency contract (S15-R4 P1-1, applied to this committed corpus
2026-09-16):** WTTJ bands are EUR-marked (the facet-01 loader excludes
non-USD bands); unknown-currency boards' bands (Adzuna/Ashby/Careerjet)
are DROPPED (`salary_min/max` null, `salary_text` kept) — no number ever
rides the consumer's default-USD assumption. Regeneration emits this
shape natively. **Regen note:** run before the Jooble native-id fix —
Jooble `job_id`s in this snapshot are URL-hash (rank-unstable); regenerate
to pick up stable `jooble-<id>` ids.

## How to regenerate

```bash
cd ingest
for q in "software engineer" "platform engineer" "product manager"; do
  slug=$(echo "$q" | tr ' ' '_')
  search-jobs "$q" --location Remote --num 50 --no-score \
    --jsonl "data/corpus/${slug}.jsonl" --jsonl-desc-max 4000
done
python3 scripts/corpus_manifest.py
```

`--no-score` is deliberate: the corpus run burns ZERO LLM calls
(z-ai rate-window discipline). `llm_score`/`sector` can be re-run later
against the tracker DB without re-fetching.

## Honest caveats

- **Degraded sources (all 3 runs):** JSearch (endpoints 404 — adapter
  dead since 2026-09-13), The Muse (HTTP 403), Wellfound (needs
  `playwright-stealth`, not installed in this sandbox), USAJobs
  (geo-blocked 403 from HK egress; ZenRows fallback credits exhausted).
- **Description truncation:** exported descriptions are capped at 4000
  chars (`--jsonl-desc-max 4000`) to keep the committed corpus small;
  `tracker.db` keeps full text and re-exporting without the flag restores
  it. Rows carry `description_truncated: true` when cut.
- **job_id:** ~85% board-native (`<board>-<id>` recovered from the apply
  URL — see spec §job_id); the rest are stable `u-<sha1(url)[:16]>`
  hashes (Jooble/Careerjet redirect URLs carry no native id). All 1,108
  job_ids are unique within their query file.
- **Salary currency:** `salary_min/max` are raw source-native figures.
  `currency` is emitted only for known-USD boards (USAJobs — which was
  degraded this run, so effectively none). Bands WITHOUT `currency` are
  of unknown currency (WTTJ bands are EUR) — do not aggregate them as
  USD (the facet-01 loader's default-USD assumption does not hold).
- **`experience_level`** carries the ingest tier enum
  (`intern|entry|mid|senior` — plain titles default to `mid`), and
  **`work_type`** carries the work MODE (`remote|hybrid|onsite`), not an
  employment type — the model has neither LinkedIn-style labels nor
  Full-time/Contract data.
