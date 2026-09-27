# JSONL export spec — the facet-01 corpus contract (Wave-R R2)

Status: **LIVE** (branch `wave-r-corpus-export`). The default output of
`Store.export_jsonl` / `search-jobs --jsonl` since Wave-R R2.

## Consumer

The MAIN repo's facet-01 loader:
`facets/01_profile_foundation/build/profile_foundation/corpus.py`
(`load_postings_jsonl`). One JSON object per line; `title` required —
a line without it fails the load. Unknown keys are dropped by that
consumer; they are kept in the export for provenance (below).

## Field mapping (ingest `Job` → JSONL row)

| JSONL key | Job field | Notes |
|---|---|---|
| `title` | `title` | required |
| `job_id` | derived | see **job_id** below — never empty |
| `description` | `description` | export-only truncation via `--jsonl-desc-max N` / `max_description_chars`; DB never modified; `description_truncated` extra flags it |
| `company` | `company` | |
| `location` | `location` | |
| `experience_level` | `tier` | ingest enum `intern\|entry\|mid\|senior` — NOT LinkedIn-style labels; plain titles default to `mid` (classify_tier) |
| `work_type` | `work_mode` | `remote\|hybrid\|onsite`; `unknown` → null. The model has no employment-type field — work_type carries the work MODE |
| `remote_allowed` | `remote` | always a real JSON bool |
| `salary_min` / `salary_max` | `salary_min` / `salary_max` | raw source-native figures |
| `currency` | — | emitted ONLY when the board is known-USD (USAJobs). A band without `currency` is of UNKNOWN currency — the facet-01 loader's default-USD assumption does not hold for it (e.g. WTTJ bands are EUR) |
| `pay_period` | — | never emitted (model does not carry it) |
| `skills` | `skills` | real JSON array, `[]` when absent |
| `listed_time` | `date_posted` | `YYYY-MM-DD` or null |
| `source` | `source` | |

**Provenance extras** (dropped by the facet-01 loader, kept for other
readers): `link`, `tier`, `work_mode`, `date_posted`, `salary_text`,
`ats_platform`, `remote`, `row_id` (tracker.db rowid — NOT stable across
DB rebuilds; use `job_id` for identity), `scraped_at`, `search_query`,
`ghost_candidate`, `repost_count`, `description_truncated`.

Run-local scoring artifacts (`llm_*`, `trust_*`, `tfidf_score`,
`scored_at`) are deliberately NOT exported — per-resume opinions, not
corpus data. The legacy full-model dump remains available for debugging:
`store.export_jsonl(path, contract=None)`.

## job_id derivation

The Job model carries no separate native-id column; the native id, when
one exists, lives embedded in the apply URL. Precedence (implemented in
`jobsearch/export.py::stable_job_id`):

1. **URL-pattern extraction** → `<board>-<native id>`: LinkedIn,
   Greenhouse, Lever, Ashby, Personio, SmartRecruiters, Wellfound,
   Remotive, RemoteOK, Careerjet, Adzuna, USAJobs, HN (`item?id=`),
   Workday (`/job/<reqId>`), WTTJ (job slug). Namespaced so equal numeric
   ids from different boards never collide in a merged corpus.
2. **URL hash** `u-<sha1(link)[:16]>`: sources whose apply URLs carry no
   stable id (Jooble redirects, Findwork/TheMuse referrer links, …).
   Stable across runs, NOT board-native.
3. **Content fingerprint** `c-<sha1(source|company|title)[:16]>`:
   linkless rows — the same fingerprint basis `upsert_jobs` uses for
   linkless identity. Stable across runs, NOT board-native.

A `job_id` is ALWAYS emitted: the facet-01 loader synthesizes `line-N`
for missing ids, which defeats cross-run dedup.

## CLI

```bash
search-jobs "software engineer" --location Remote --num 50 --no-score \
  --jsonl out.jsonl [--jsonl-desc-max 4000]
```

The committed corpus lives at `data/corpus/` (see its README) — the
whitelist precedent is `data/workday/` (NVIDIA deliverable).
