# job-sourcing pipeline

Job-source ingestion pipeline + **board-watch**: a daily diff-and-alert layer
over company job boards (Workday CXS boards today; config-as-data — one JSON
line per company in `ingest/data/board_watch/config.json`).

What's here:
- `ingest/` — the Python pipeline: 25 registered job sources (ATS-direct +
  aggregators), SQLite storage, dedup, scoring seam, ghost/repost detection,
  corroboration module (LinkedIn guest applicant-count signals joined 1:1 to
  Workday requisitions).
- `scripts/` — operational runners: `board_dump.py` (exhaustive list →
  details → corroborate → finish CSV), `gha_board_watch.py` (the daily watch
  runner used by the GitHub Actions workflow).
- `.github/workflows/board-watch.yml` — the durable daily watch chain
  (race-proof checkpointed state committed back to this repo).
- `ingest/data/` — committed operational state: watch state, the NVIDIA v2
  reference dump, ATS directory snapshot.

Secrets are **never committed here** — `ingest/.env` is materialized at
runtime from the `INGEST_ENV` repo secret (see the workflow). Local dev:
`cp ingest/.env.example ingest/.env` and fill keys as needed (most sources
work keyless).

Quick start:
```bash
pip install -e ingest
python3 -m pytest -q          # offline suite
python3 scripts/gha_board_watch.py   # run the watch locally (idempotent)
```

This is the public runtime mirror of a private research archive; sensitive
credentials, internal research docs, and scraped third-party HTML stay in the
private copy.
