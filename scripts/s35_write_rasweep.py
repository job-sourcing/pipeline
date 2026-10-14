#!/usr/bin/env python3
"""s35_write_rasweep.py — emit the RemoteAggSweepAdapter block + config
entries + lane wiring for the S35 aggregator layer (DESIGN-S35 §2).

Generates (idempotent):
  1. the adapter source block -> ingest/jobsearch/sources/site_boards.py
     (inserted after GJOBS_CONTRACTS, before _ADAPTERS)
  2. 59 watch-config entries ({key}_ra_us_fulltime) -> mirror config.json
  3. .github/workflows/ra-board-watch.yml (weekly, free APIs)
  4. .github/workflows/gj-quarterly-watch.yml (quarterly probes)
Run from the repo root: python3 scripts/s35_write_rasweep.py [--config-only]
"""
import json
import re
import sys
from pathlib import Path

MIRROR = Path("/home/z/repos/pipeline-mirror")
ARCH = Path("/home/z/repos/job-sourcing-research")

ADAPTER_BLOCK = '''

# ── S35: remote-API aggregator sweep (custom:rasweep:{key}) ───────────
# D-S35-3 layer L2 (the sibling's remote-aggregator strategy applied):
# remoteok / jobicy / remotive / arbeitnow — free public APIs, ZERO
# firecrawl credits, remote-focused (the mission's own class). 4 HTTP
# requests TOTAL per run (the shared _RA_CACHE — every company filters
# the SAME fetches; the _GJ_SCRAPE_CACHE pattern). Measured yield
# 2026-10-14 (audit/s35_aggregator/probe_results.json, exact-variant
# matcher): ZERO US-geo rows for roster companies — the layer is free
# CLOSURE coverage + future-posting catch (the honest-zero doctrine,
# re-probed weekly for 4 requests). Contracts DERIVED from
# LICOMPANY_CONTRACTS (same keys = the same-entity owner rule; the
# li_variants ARE the company's aggregator name-strings).

_RA_SOURCES = {
    "remoteok": "https://remoteok.com/api",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs?count=200",
    "remotive": "https://remotive.com/api/remote-jobs?limit=1000",
    "arbeitnow": "https://www.arbeitnow.com/api/job-board-api",
}
_RA_TTL = 21600.0                  # shared-run cache (like _GJ_TTL)
_RA_CACHE: dict[str, tuple[float, list[dict]]] = {}
_RA_DESC_MAX = 12000


def _ra_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _ra_us_remote(loc: str) -> bool:
    """The mission filter: US-geo or remote. Non-US-geo rows are noise
    for the US-remote view (the A-prime rule at ingest time)."""
    l = (loc or "").lower()
    if not l.strip():
        return True                 # no signal — keep (honest)
    if "remote" in l:
        return True
    toks = set(re.split(r"[^a-z.]+", l))
    if {"us", "usa", "u.s.", "united", "states", "america"} & toks:
        return True
    from .sources.workday import _US_STATE_TOKENS
    for t in re.split(r"[^A-Za-z]+", l):
        if (len(t) == 2 and t.upper() != t and t in _US_STATE_TOKENS) \\
                or (len(t) == 2 and t.isupper()
                    and t.lower() in _US_STATE_TOKENS) \\
                or t.lower() in _US_STATE_TOKENS:
            return True
    return False


class RemoteAggSweepAdapter:
    """custom:rasweep:{key} — the free remote-API aggregator sweep.

    Honesty doctrine: per-SOURCE fail-soft (the sibling's F-contract:
    rows are NEVER discarded on partial failure — a dead API is
    skipped, reported; ALL sources dead raises fail-loudly, state
    untouched). Matching is EXACT normalized equality (the gjobs
    doctrine — "Engine" must never match "TDengine", the round-1
    review's P0-5). US/remote rows only (client-side country filter);
    rid = sha1(title|company|loc|source).
    """

    KIND = "rasweep"

    def __init__(self, org: str, cfg: Config):
        base = LICOMPANY_CONTRACTS.get(org)
        if base is None:
            raise ValueError(
                f"rasweep: unknown contract {org!r} (derived from "
                f"LICOMPANY_CONTRACTS; known: "
                f"{sorted(LICOMPANY_CONTRACTS)})")
        self.key = org
        self.company = base["company"]
        self.variants = {v.lower() for v in
                         [base["company"], *base.get("li_variants", [])]}
        self.ats = "rasweep"
        self.cfg = cfg

    def _fetch_all(self) -> list[dict]:
        ck = "rasweep:sources"
        hit = _RA_CACHE.get(ck)
        if hit and time.monotonic() - hit[0] < _RA_TTL:
            return hit[1]
        rows: list[dict] = []
        dead: list[str] = []
        for name, url in _RA_SOURCES.items():
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json"})
                with urllib.request.urlopen(
                        req, timeout=45) as r:
                    d = json.loads(r.read().decode("utf-8", "replace"))
            except Exception as exc:
                print(f"[rasweep] source {name} FAILED "
                      f"({type(exc).__name__}: {exc}) — skipped",
                      file=sys.stderr, flush=True)
                dead.append(name)
                continue
            if name == "remoteok":
                src = [x for x in d if isinstance(x, dict)
                       and x.get("company")]
                for x in src:
                    rows.append({"source": name,
                                 "company": str(x.get("company") or ""),
                                 "title": str(x.get("position") or ""),
                                 "loc": str(x.get("location") or ""),
                                 "url": str(x.get("url") or ""),
                                 "type": str(x.get("job_type") or ""),
                                 "posted": str(x.get("date") or "")[:10],
                                 "desc": str(x.get("description") or "")})
            elif name == "jobicy":
                for x in (d.get("jobs") or []):
                    rows.append({"source": name,
                                 "company": str(x.get("companyName") or ""),
                                 "title": str(x.get("jobTitle") or ""),
                                 "loc": str(x.get("jobGeo") or ""),
                                 "url": str(x.get("url") or ""),
                                 "type": str(x.get("jobType") or ""),
                                 "posted": str(x.get("pubDate") or "")[:10],
                                 "desc": str(x.get("jobDescription") or "")})
            elif name == "remotive":
                for x in (d.get("jobs") or []):
                    rows.append({"source": name,
                                 "company": str(x.get("company") or ""),
                                 "title": str(x.get("title") or ""),
                                 "loc": str(
                                     x.get("candidate_required_location")
                                     or ""),
                                 "url": str(x.get("url") or ""),
                                 "type": str(x.get("job_type") or ""),
                                 "posted": str(
                                     x.get("publication_date") or "")[:10],
                                 "desc": str(x.get("description") or "")})
            elif name == "arbeitnow":
                for x in (d.get("data") or []):
                    rows.append({"source": name,
                                 "company": str(x.get("company") or ""),
                                 "title": str(x.get("title") or ""),
                                 "loc": str(x.get("location") or ""),
                                 "url": str(x.get("url") or ""),
                                 "type": str(x.get("job_type") or ""),
                                 "posted": str(
                                     x.get("created_at") or "")[:10],
                                 "desc": str(x.get("description") or "")})
        if dead and not rows:
            raise RuntimeError(
                f"rasweep:{self.key}: ALL aggregator sources dead "
                f"({dead}) — failing loudly, state untouched")
        _RA_CACHE[ck] = (time.monotonic(), rows)
        return rows

    def list_board(self, *, country: Optional[str] = None,
                   time_type: Optional[str] = None,
                   progress_label: str = "list",
                   include_remote: bool = False,
                   geo_scope: Optional[str] = None,
                   ) -> tuple[dict[str, dict], dict]:
        all_rows = self._fetch_all()
        rows: dict[str, dict] = {}
        noise = 0
        for x in all_rows:
            if _ra_norm(x["company"]) not in \\
                    {_ra_norm(v) for v in self.variants}:
                continue
            if not _ra_us_remote(x["loc"]):
                noise += 1
                continue
            rid = hashlib.sha1(
                f"{x['title']}|{x['company']}|{x['loc']}|"
                f"{x['source']}".encode("utf-8")).hexdigest()[:16]
            label, iso = _posted_label(x["posted"] or None)
            rows[rid] = {
                "reqId": rid,
                "title": x["title"],
                "company": self.company,
                "url": x["url"],
                "externalPath": f"/{rid}",
                "locationsText": x["loc"],
                "postedOn": label,
                "timeType": "Full time" if "full" in x["type"].lower()
                else (x["type"] or ""),
                "bulletFields": [rid],
                "ats": self.ats,
                "departments": [],
                "countries": [],
                "remoteType": "remote" if "remote" in x["loc"].lower()
                else "",
                "_ra_row": x,
            }
        print(f"[{progress_label}] rasweep:{self.key}: {len(rows)} rows "
              f"(exact-variant; {noise} non-US-geo dropped; sources "
              f"{len(_RA_SOURCES)})", file=sys.stderr, flush=True)
        meta = {
            "complete": True, "total": len(rows), "pages": 1,
            "country_client": True,     # US/remote filter applied here
            "client_filtered": noise, "kept_remote": 0,
            "geo_scope": "", "ats": self.ats,
        }
        return rows, meta

    def detail_payload(self, external_path: str,
                       country: Optional[str] = None,
                       time_type: Optional[str] = None
                       ) -> Optional[dict]:
        rid = str(external_path or "").strip("/").split("?")[0]
        rows, _ = self.list_board()
        j = next((r for k, r in rows.items() if k == rid), None)
        if j is None:
            return None
        x = j.get("_ra_row") or {}
        desc = re.sub(r"<[^>]+>", " ", x.get("desc") or "")
        desc = re.sub(r"\\s+", " ", desc).strip()
        return {
            "jobPostingInfo": {
                "title": j["title"],
                "location": j["locationsText"] or "See posting",
                "additionalLocations": [],
                "jobDescription": desc[:_RA_DESC_MAX],
                "timeType": j["timeType"],
                "startDate": "",
                "externalUrl": j["url"],
                "jobReqId": rid,
                "postedOn": j["postedOn"],
                "country": None,
            },
            "hiringOrganization": {"name": self.company},
            "similarJobs": [],
            "firstPublishedIso": x.get("posted") or "",
            "workMode": "Remote" if "remote" in (
                x.get("loc") or "").lower() else "",
            "numApplicants": "",
            "closed": False,
        }

'''

RA_WORKFLOW = '''name: RA Board Watch (weekly)

# S35 (D-S35-3) — the remote-API aggregator sweep lane
# (custom:rasweep:*, tags=["ra"]): remoteok / jobicy / remotive /
# arbeitnow — FREE public APIs, zero firecrawl credits. 4 HTTP
# requests per RUN total (shared fetch cache; every company filters
# the same responses). Measured yield for the roster: zero US-geo
# rows today (audit/s35_aggregator/probe_results.json) — the layer
# is free closure coverage + future-posting catch, re-probed weekly
# (the honest-zero doctrine). Runtime ~2 min on the public runner.

on:
  workflow_dispatch:
  schedule:
    - cron: "23 11 * * 3"   # Wed 11:23Z (off-hour; after the gj lane)

permissions:
  contents: write
  actions: write

concurrency:
  group: ra-board-watch
  cancel-in-progress: false

jobs:
  watch:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install pipeline (no dev extras)
        run: pip install -e ingest

      - name: Run RA watch (ra tag only)
        env:
          WATCH_ONLY_TAGS: ra
          WATCH_LEGS_MAX_PER_DAY: "6"
        run: |
          set -e
          python3 scripts/gha_board_watch.py
          if [ "${WATCH_RESULT}" = "failed" ]; then
            echo "::error::ra-board-watch FAILED"
            exit 1
          fi

      - name: Checkpoint state to git (race-proof)
        if: always()
        run: |
          set -e
          mkdir -p /tmp/watch_state
          FILES=$(git status --porcelain ingest/data/board_watch/ \\
            | awk '{print $2}' | grep -E '\\.(state\\.jsonl|newposts\\.jsonl|alerts\\.log|legs\\.json|reposts\\.jsonl|runs\\.jsonl)$' || true)
          echo "checkpointing: ${FILES:-<none>}"
          for f in $FILES; do
            mkdir -p "/tmp/watch_state/$(dirname $f)"
            cp "$f" "/tmp/watch_state/$f" 2>/dev/null || true
          done
          git config user.name "ra-watch-bot"
          git config user.email "actions@github.com"
          push_ok=0
          for i in 1 2 3; do
            git fetch origin || true
            git reset --hard origin/main
            if ls /tmp/watch_state/ingest/data/board_watch/ >/dev/null 2>&1; then
              cp -r /tmp/watch_state/ingest/data/board_watch/. ingest/data/board_watch/
            fi
            git add ingest/data/board_watch
            if git diff --cached --quiet; then
              push_ok=1; break
            fi
            git commit -m "ra-board-watch checkpoint [skip ci]"
            if git push; then push_ok=1; break; fi
            sleep 5
          done
          if [ "$push_ok" != "1" ]; then
            echo "::error::ra checkpoint push failed after 3 attempts"
            exit 1
          fi
'''

GJQ_WORKFLOW = '''name: GJ Quarterly Watch

# S35 (D-S35-3) — the gj catch-all QUARTERLY tier: NOISE-WALL
# companies from the 2026-10-14 gap probe (baseus, lianlian, notta,
# timekettle, xgimi — cards exist but none variant-match; the honest
# re-probe class). 1 firecrawl credit/company/quarter detects the
# 0->k flip at minimal burn (DESIGN-S35 §2.3: 2 REAL monthly + 5
# quarterly + 26 parked).

on:
  workflow_dispatch:
  schedule:
    - cron: "55 10 1 */3 *"   # 1st of Jan/Apr/Jul/Oct 10:55Z

permissions:
  contents: write
  actions: write

concurrency:
  group: gj-quarterly-watch
  cancel-in-progress: false

jobs:
  watch:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Materialize runtime env
        run: echo "${INGEST_ENV}" > ingest/.env
        env:
          INGEST_ENV: ${{ secrets.INGEST_ENV }}

      - name: Install pipeline (no dev extras)
        run: pip install -e ingest

      - name: Run GJ-quarterly watch (gj_quarterly tag only)
        env:
          WATCH_ONLY_TAGS: gj_quarterly
          WATCH_LEGS_MAX_PER_DAY: "6"
        run: |
          set -e
          python3 scripts/gha_board_watch.py
          if [ "${WATCH_RESULT}" = "failed" ]; then
            echo "::error::gj-quarterly-watch FAILED"
            exit 1
          fi

      - name: Checkpoint state to git (race-proof)
        if: always()
        run: |
          set -e
          mkdir -p /tmp/watch_state
          FILES=$(git status --porcelain ingest/data/board_watch/ \\
            | awk '{print $2}' | grep -E '\\.(state\\.jsonl|newposts\\.jsonl|alerts\\.log|legs\\.json|reposts\\.jsonl|runs\\.jsonl)$' || true)
          for f in $FILES; do
            mkdir -p "/tmp/watch_state/$(dirname $f)"
            cp "$f" "/tmp/watch_state/$f" 2>/dev/null || true
          done
          git config user.name "gjq-watch-bot"
          git config user.email "actions@github.com"
          push_ok=0
          for i in 1 2 3; do
            git fetch origin || true
            git reset --hard origin/main
            if ls /tmp/watch_state/ingest/data/board_watch/ >/dev/null 2>&1; then
              cp -r /tmp/watch_state/ingest/data/board_watch/. ingest/data/board_watch/
            fi
            git add ingest/data/board_watch
            if git diff --cached --quiet; then
              push_ok=1; break
            fi
            git commit -m "gj-quarterly-watch checkpoint [skip ci]"
            if git push; then push_ok=1; break; fi
            sleep 5
          done
          if [ "$push_ok" != "1" ]; then
            echo "::error::gjq checkpoint push failed"
            exit 1
          fi
'''


def emit_code() -> None:
    p = ARCH / "ingest/jobsearch/sources/site_boards.py"
    t = p.read_text()
    if "RemoteAggSweepAdapter" in t:
        print("adapter already present")
        return
    anchor = "_ADAPTERS = {"
    assert anchor in t
    t = t.replace(anchor, ADAPTER_BLOCK.strip("\n") + "\n\n\n" + anchor, 1)
    # register the kind
    t = t.replace('"gjobs": GoogleJobsAdapter}',
                  '"gjobs": GoogleJobsAdapter,\n'
                  '             "rasweep": RemoteAggSweepAdapter}')
    p.write_text(t)
    import ast
    ast.parse(t)
    print("adapter emitted + registered; syntax OK")


def emit_config() -> None:
    p = MIRROR / "ingest/data/board_watch/config.json"
    cfg = json.loads(p.read_text())
    watches = cfg["watches"]
    have = {w["label"] for w in watches}
    sys.path.insert(0, str(ARCH / "ingest"))
    from jobsearch.sources import site_boards as sb
    added = 0
    for key, c in sorted(sb.LICOMPANY_CONTRACTS.items()):
        label = f"{key}_ra_us_fulltime"
        if label in have:
            continue
        watches.append({
            "label": label,
            "board": f"custom:rasweep:{key}",
            "company": c["company"],
            "country": "United States",
            "tags": ["ra"],
            "skip_li_corroboration": True,
        })
        added += 1
    p.write_text(json.dumps(cfg, indent=1) + "\n")
    print(f"config: +{added} ra watches (total {len(watches)})")
    # main watch must skip the new tags
    bw = MIRROR / ".github/workflows/board-watch.yml"
    t = bw.read_text()
    t2 = t.replace(
        "WATCH_SKIP_TAGS: weekly,li,li_dormant,gj,gj_dormant",
        "WATCH_SKIP_TAGS: weekly,li,li_dormant,gj,gj_dormant,ra,"
        "gj_monthly,gj_quarterly")
    if t2 != t:
        bw.write_text(t2)
        print("main watch skip-tags extended")
    # gj-dormant lane gains gj_monthly
    gd = MIRROR / ".github/workflows/gj-dormant-watch.yml"
    t = gd.read_text()
    t2 = t.replace("WATCH_ONLY_TAGS: gj_dormant",
                   "WATCH_ONLY_TAGS: gj_dormant,gj_monthly")
    if t2 != t:
        gd.write_text(t2)
        print("gj-dormant lane gains gj_monthly")
    (MIRROR / ".github/workflows/ra-board-watch.yml").write_text(
        RA_WORKFLOW)
    (MIRROR / ".github/workflows/gj-quarterly-watch.yml").write_text(
        GJQ_WORKFLOW)
    print("workflows emitted: ra-board-watch.yml, gj-quarterly-watch.yml")


if __name__ == "__main__":
    if "--config-only" not in sys.argv:
        emit_code()
    emit_config()
