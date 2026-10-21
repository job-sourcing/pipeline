# Adhoc company test — starbucks

_Ran 2026-10-21 00:53Z. Region filter: SF Bay Area ∨ Remote. Level: corp (excl. hourly retail). Sources: 9 aggregator adapters + the Phenom direct probe matrix._

## Headline — corp-level ∧ (Bay Area ∨ Remote)

**0 rows** of 654 total starbucks rows.

_(none — see the per-source coverage below)_

## Region × level composition (all company rows)

| region | n | | level | n |
|---|---|---|---|---|
| bay_area | 5 | | corp | 179 |
| remote | 0 | | retail_hourly | 470 |
| other | 649 | | retail_mgmt | 5 |

## Aggregator coverage (company-keyword query, US-wide)

| source | status | raw | company hits | secs |
|---|---|---|---|---|
| adzuna | ok | 500 | 498 | 19.7 |
| careerjet | error:starbucks:HTTPError | 0 | 0 | 0.4 |
| jooble | ok | 150 | 104 | 8.6 |
| findwork | ok | 1 | 0 | 0.4 |
| dice | ok | 62 | 46 | 17.7 |
| himalayas | ok | 7 | 0 | 32.2 |
| talent | ok | 19 | 0 | 0.6 |
| muse | ok | 43 | 0 | 4.3 |
| indeed | ok | 6 | 6 | 3.0 |

## Phenom direct probe

- method that answered: `None`
- tried `A:GET /api/apply/v2/jobs` → HTTP 403
- tried `B:POST /widgets` → HTTP 401
- tried `C:GET /ph-api/jobs` → HTTP 404

## Interpretation

- 4/9 aggregators surfaced starbucks rows; the direct probe `did not answer`.
- Phenom endpoints are widget-auth-gated (lane-1: A→403, B→401, C→404) — a direct ATS read needs the site's own refNum/session; aggregators are the evidence base today.
- Corp∧(BA∨remote) = 0 — no corp-level Bay Area or remote postings surfaced this run.