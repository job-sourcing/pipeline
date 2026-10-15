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

## The refresher — the cadence doctrine (S37 / D-S37-1 R2)

The watch lanes ARE the refresher: the postings corpus refreshes via
the daily watch passes (list fetches + the presence diff) and the
nightly csv-export state-merge (the CSVs + the UI bundle). The cadence
doctrine — **daily for bigger companies, weekly for smaller** — is
formalized as churn tiering (the 2026-10-15 tag-audit measured
new-postings/28d per label from the committed state):

| Lane | Cadence | n | Churn (new/28d) | What it covers |
|---|---|---|---|---|
| `board-watch` | daily 06:45Z | 121 | median ~13 | the strategic high-churn set (the majors, the AI labs, the Chinese-tech US arms) |
| `li-board-watch` | daily 07:45Z | 20 | median 2 | the LinkedIn-guest company boards (dedicated lane — correlated 429s; the S37 `source_blocked` degrade) |
| `weekly-board-watch` | weekly Wed 10:35Z | 28 | median 1 | the churn-demoted tail (healthy-but-zero-churn US boards — the standard driver, no firecrawl) |
| `gj-board-watch` | weekly Wed 10:25Z | 4 | median 4 | the Google-Jobs gap-fillers |
| `cf-board-watch` | weekly Tue 12:30Z | 3 | median 1 | the Cloudflare-walled boards (firecrawl credits — kept weekly on purpose) |
| `ra-board-watch` | weekly Wed 11:23Z | 59 | 0 | the remote-aggregator boards (free APIs, the honest-zero closure layer) |
| `li-dormant-watch` | weekly | 39 | 0 | dormant LinkedIn boards |
| `gj-dormant` / `gj_monthly` / `gj_quarterly` | monthly–quarterly | 9 | 0 | the deep-dormant tail |
| `ats-drain` | weekly Wed 05:07Z + chain | 15,872 boards | — | the board-UNIVERSE refresh (live >30d re-probe + dead >95d quarterly resurrection) |
| `refresh-watchdog` | 2×/day | — | — | the daily guarantee (dispatches the main lane iff no successful run in 30h / none today) |
| `csv-export` | nightly 22:17Z + post-leg | — | — | the state-merge export (the CSVs + the UI bundle) |
| `sector-probe` | daily-ish | — | — | the sector-census discovery expansion |

The re-audit duty: re-run the churn script at each drift absorption —
promote `weekly_std` labels that churn above the daily median, demote
daily labels that flatline over 28d (the niu retirement record in
`ingest/data/board_watch/config.json`'s `retired_watches` is the
dead-board disposition convention).

The health read: `python3 <canon>/scripts/refresher-health.py` (the
session-ritual pull over these lanes' last-run states; drift_alert.py
is the committed-state PUSH channel — both, by design).
