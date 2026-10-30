# ats_seed provenance ledger [S50-T2 / RA18]

All lists from Feashliaa/job-board-aggregator `data/` (the same source as
the original 3; the repo replaces its job chunks daily, the *_companies
lists are near-static). Downloaded 2026-10-30 from
https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/

| file | rows | shape | imported since |
|---|---|---|---|
| ashby_companies.json | 3,161 | list[str] slug | S38 (pre-S50) |
| greenhouse_companies.json | 8,333 | list[str] slug | S38 (pre-S50) |
| lever_companies.json | 4,368 | list[str] slug | S38 (pre-S50) |
| bamboohr_companies.json | 11,316 | list[str] slug | **S50** (this session) |
| icims_companies.json | 10,108 | list[str] slug (incl. 'careers-' prefixed hosts) | **S50** |
| paylocity_companies_clean.json | 10,252 | list[dict] {guid, name, jobs} | **S50** |
| workday_companies.json | 12,884 | list[str] 'tenant|instance|site' | seed was 7,617 (S38); **refreshed to 12,884 in S50** (+5,267) |
| smartrecruiters | 10 curated | (no upstream list exists) | S38 |

Refresh cadence: re-download on the weekly drain cadence when a platform's
probe campaign completes; the lists rarely churn (greenhouse/lever/ashby
verified byte-identical vs the S38 copies on 2026-10-30).
