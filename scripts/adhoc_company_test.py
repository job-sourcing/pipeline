#!/usr/bin/env python3
"""Adhoc company test — the region/level-filtered single-company probe.

S41 (the user directive, 2026-10-21): "add one more adhoc company test,
check starbucks (regional / corp / remote) jobs include any that are in
my region (SF Bay area, or are remote, not the barista hourly job corp
job level)". First of its kind — parameterized so future company tests
reuse the same lane (`--company <slug>`).

What it measures (the test value, not just the data):
  1. AGGREGATOR COVERAGE — which of the pipeline's source adapters
     surface rows for the company keyword (queries=["<company>"],
     US-wide, client-side region filter after).
  2. ATS-DIRECT COVERAGE — a Phenom People probe matrix against the
     company's careers site (public self-serve endpoints only; the
     official phenom.com API is OAuth-gated). Starbucks = Phenom at
     apply.starbucks.com (microsite=Retail vs Corp split observed in
     the site's URL grammar). Endpoints tried in order, the FIRST that
     answers becomes the recorded method — the probe is evidence, not
     assumption (HK sandbox egress is blocked; only the US GHA runner
     can actually reach the site).
  3. REGION/LEVEL FILTER — the user-relevant cut: rows are tagged
     region ∈ {bay_area, remote, other} and level ∈ {corp,
     retail_hourly, retail_mgmt}. The report head-lines corp ∧
     (bay_area ∨ remote); retail rows are counted for coverage math
     but never head-lined.

Rows land in ingest/data/adhoc/<company>.json (+ .report.md) — read by
the lane's checkpoint step, mirrored back to the archive by the
existing mirror back-syncs (the adhoc/ dir rides the ui/ sync tree).

Never crashes the lane: every source failure is recorded, never raised
(the D3 degradation policy). Exit 0 with a summary even when sources
fail — the JSON is the artifact.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "scripts")          # role_sweep fetchers
import role_sweep  # noqa: E402

# ---------------------------------------------------------------------------
# Region + level classification (the user's cut)
# ---------------------------------------------------------------------------

# Core SF Bay Area place names (9 counties: SF, San Mateo, Santa Clara,
# Alameda, Contra Costa, Marin, Sonoma, Napa, Solano — city-level list,
# deliberately broad on the commuter belt, kept to names that actually
# appear in job loc strings).
BAY_AREA = (
    "san francisco", "south san francisco", "daly city", "burlingame",
    "san mateo", "foster city", "redwood city", "menlo park", "palo alto",
    "mountain view", "sunnyvale", "santa clara", "san jose", "milpitas",
    "cupertino", "campbell", "los gatos", "saratoga", "los altos",
    "oakland", "berkeley", "emeryville", "alameda", "richmond",
    "san ramon", "danville", "dublin", "pleasanton", "livermore",
    "fremont", "newark", "union city", "hayward", "castro valley",
    "walnut creek", "concord", "pleasant hill", "lafayette", "orinda",
    "moraga", "martinez", "pittsburg", "antioch", "brentwood",
    "san rafael", "novato", "petaluma", "santa rosa", "sonoma",
    "vallejo", "fairfield", "napa", "suisun", "vacaville",
    "bay area", "silicon valley",
)

REMOTE_PAT = re.compile(
    r"\bremote\b|\bvirtual\b|work\s*-?\s*from\s*-?\s*home|wfh|anywhere\s+in",
    re.I,
)

# The lane-1 false-positive class (observed 2026-10-21): Antioch TN,
# Dublin OH, Richmond KY, Newark NJ, Brentwood TN, Lafayette MS — city
# names shared with Bay Area cities in OTHER states. A city match alone
# is NOT Bay Area evidence: require CA context (state abbr or
# 'California') in the same loc string, or one of the unambiguous
# whole-region markers.
UNAMBIG_BAY = (
    "san francisco", "south san francisco", "silicon valley",
    "bay area", "san jose", "palo alto", "mountain view", "sunnyvale",
    "cupertino", "menlo park", "redwood city", "san mateo",
    "foster city", "milpitas", "santa clara", "south bay",
)
CA_PAT = re.compile(r"\bca\b|\bcalif\b|california", re.I)

# Starbucks hourly-retail ladder (the excluded tier). Store manager is
# salaried at Starbucks but still retail-side — bucketed retail_mgmt,
# never head-lined as corp. Lane-1 addition: the LICENSED-STORE class —
# 'Starbucks Supervisor'/'Food Service Supervisor'/'Team Leader' rows
# are kiosks inside Target/Safeway/universities (the title carries the
# Starbucks brand but the employer is the host retailer) — hourly-tier
# food service, never corp.
HOURLY_RETAIL_PAT = re.compile(
    r"\bbarista\b|shift\s*supervisor|team\s*supervisor|"
    r"retail\s*associat|hourly\b|part\s*-?\s*time\b|"
    r"\bsupervisor\b|team\s*leader|team\s*member|food\s*service|"
    r"cashier|crew\s*member|store\s*partner",
    re.I,
)
RETAIL_MGMT_PAT = re.compile(
    r"\bstore\s*manager\b|assistant\s*store\s*manager|district\s*manager|"
    r"\bdistrict\s*manager\b|retail\s*manager|store\s*leader",
    re.I,
)


def region_tag(loc: str, title: str = "") -> str:
    """Region cut with the CA-context gate (lane-1 lesson).

    Remote first (title can carry it: 'work from home'), then the
    unambiguous whole-region markers, then city-in-list ∧ CA-context.
    A city match WITHOUT CA context is 'other' — Brentwood TN and
    Brentwood CA must never conflate.
    """
    blob = f"{loc} {title}"
    if REMOTE_PAT.search(blob):
        return "remote"
    loc_l = loc.lower()
    for marker in UNAMBIG_BAY:
        if marker in loc_l:
            return "bay_area"
    ca = bool(CA_PAT.search(loc))
    if ca:
        for city in BAY_AREA:
            if city in loc_l:
                return "bay_area"
    return "other"


def level_tag(title: str, microsite: str = "", desc: str = "") -> str:
    blob = f"{title} {microsite}"
    if HOURLY_RETAIL_PAT.search(blob):
        return "retail_hourly"
    if RETAIL_MGMT_PAT.search(blob):
        return "retail_mgmt"
    # Phenom microsite is authoritative when present (Retail vs Corp)
    if microsite and "retail" in microsite.lower():
        return "retail_hourly"
    return "corp"


# ---------------------------------------------------------------------------
# 1. Aggregator pass — reuse the role_sweep fetchers (queries=["<company>"])
# ---------------------------------------------------------------------------

def aggregator_pass(company: str) -> tuple[list[dict], list[dict]]:
    """Query the pipeline's source adapters with the company as keyword.

    Returns (rows, source_notes) — every fetcher failure is a note,
    never an exception (D3). Each fetcher returns US-wide results; the
    region filter is applied client-side afterwards.
    """
    fetchers = [
        ("adzuna", role_sweep.fetch_adzuna, True),
        ("careerjet", role_sweep.fetch_careerjet, True),
        ("jooble", role_sweep.fetch_jooble, True),
        ("findwork", role_sweep.fetch_findwork, True),
        ("dice", role_sweep.fetch_dice, True),
        # lane-1: these three take NO queries arg (whole-index fetchers)
        ("himalayas", role_sweep.fetch_himalayas, False),
        ("talent", role_sweep.fetch_talent, False),
        ("muse", role_sweep.fetch_muse, False),
        ("indeed", role_sweep.fetch_jobspy_indeed, True),
    ]
    rows: list[dict] = []
    notes: list[dict] = []
    queries = [company]                # company-as-keyword, US-wide
    for name, fn, keyed in fetchers:
        t0 = time.time()
        try:
            src_rows, status = (fn(queries) if keyed else fn())
            hit = [r for r in src_rows
                   if company in (r.get("company") or "").lower()
                   or company in (r.get("title") or "").lower()]
            notes.append({"source": name, "status": status,
                          "raw": len(src_rows), "company_hits": len(hit),
                          "secs": round(time.time() - t0, 1)})
            rows.extend(hit)
        except Exception as exc:                 # D3: degrade, never crash
            notes.append({"source": name, "status": f"error:{type(exc).__name__}",
                          "raw": 0, "company_hits": 0,
                          "secs": round(time.time() - t0, 1)})
    return rows, notes


# ---------------------------------------------------------------------------
# 2. Phenom direct probe — apply.<company>.com (candidate endpoint matrix)
# ---------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _http(url: str, data=None, timeout: int = 25) -> tuple[object, int]:
    req = urllib.request.Request(
        url, data=(json.dumps(data).encode() if data is not None else None),
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), r.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception:
        return None, 0


def _parse_phenom_v2(d: dict) -> list[dict]:
    """The /api/apply/v2/jobs shape: {'jobs': [...], 'count': N}."""
    out = []
    for j in d.get("jobs", []) or []:
        locs = ", ".join(l.get("name", "") for l in (j.get("locations") or [])
                         if isinstance(l, dict)) or str(j.get("location", ""))
        out.append({
            "title": j.get("title") or j.get("name") or "",
            "company": "Starbucks",
            "loc": locs,
            "url": j.get("link") or j.get("apply_url") or "",
            "posted": (j.get("posted_date") or "")[:10],
            "desc": "",
            "microsite": (j.get("microsite") or ""),
            "rid": f"ph:{j.get('jobId') or j.get('id') or ''}",
        })
    return out


def _parse_phenom_widgets(d: dict) -> list[dict]:
    """The /widgets POST shape: {'content': [{'listData': {'jobs': [...]}}]}."""
    out = []
    try:
        blocks = d.get("content") or []
        for blk in blocks:
            jobs = ((blk.get("listData") or {}).get("jobs")) or []
            for j in jobs:
                out.append({
                    "title": j.get("title", ""),
                    "company": "Starbucks",
                    "loc": j.get("location", "") or j.get("city", ""),
                    "url": j.get("jobUrl") or j.get("job_page_url") or "",
                    "posted": (j.get("posted_date") or "")[:10],
                    "desc": "",
                    "microsite": (j.get("microsite")
                                  or (j.get("tags") or {}).get("microsite", "")),
                    "rid": f"pw:{j.get('jobId') or j.get('job_id') or ''}",
                })
    except Exception:
        pass
    return out


def phenom_probe(company: str, domain: str, site: str,
                 pages: int = 10) -> tuple[list[dict], dict]:
    """Candidate endpoint matrix, tried in order; first responder wins.

    Candidates (Phenom self-serve patterns, no OAuth):
      A. GET  https://<site>/api/apply/v2/jobs?domain=<domain>&start=N&num=50
      B. POST https://<site>/widgets  (search widget body)
      C. GET  https://<site>/ph-api/jobs?...  (older tenant variant)
    """
    evidence: dict = {"tried": [], "method": None}
    rows: list[dict] = []

    # --- A: the v2 jobs REST ---
    for start in range(0, pages * 50, 50):
        url = (f"https://{site}/api/apply/v2/jobs?domain={domain}"
               f"&start={start}&num=50&sort_by=new")
        d, code = _http(url)
        if start == 0:
            evidence["tried"].append({"endpoint": "A:GET /api/apply/v2/jobs",
                                      "http": code})
            if code != 200:
                break
            evidence["method"] = "A"
        if not d:
            break
        got = _parse_phenom_v2(d)
        rows.extend(got)
        if len(got) < 50:
            break
        time.sleep(0.8)
    if rows:
        return rows, evidence

    # --- B: the widgets POST (search frontend's own endpoint) ---
    body = {
        "lang": "en_us", "deviceType": "desktop",
        "country": "United States", "distance": 40,
        "from": 0, "size": 50, "keyword": [company],
        "sort": "relevance", "userId": None, "phToken": "",
        "tags": ["asynchronousSearch"],
    }
    for from_ in range(0, pages * 50, 50):
        body["from"] = from_
        d, code = _http(f"https://{site}/widgets", data=body)
        if from_ == 0:
            evidence["tried"].append({"endpoint": "B:POST /widgets",
                                      "http": code})
            if code != 200:
                break
            evidence["method"] = "B"
        if not d:
            break
        got = _parse_phenom_widgets(d)
        rows.extend(got)
        if len(got) < 50:
            break
        time.sleep(0.8)
    if rows:
        return rows, evidence

    # --- C: the ph-api legacy variant ---
    url = (f"https://{site}/ph-api/jobs?domain={domain}"
           f"&start=0&num=50&sort_by=new")
    d, code = _http(url)
    evidence["tried"].append({"endpoint": "C:GET /ph-api/jobs", "http": code})
    if code == 200 and d:
        evidence["method"] = "C"
        rows = _parse_phenom_v2(d)          # same shape family
    return rows, evidence


# ---------------------------------------------------------------------------
# 3. Classify + report
# ---------------------------------------------------------------------------

def classify_rows(rows: list[dict], company: str) -> list[dict]:
    out = []
    seen: set[str] = set()
    for r in rows:
        key = (r.get("url") or r.get("rid") or "").strip()[:200]
        if not key or key in seen:
            continue
        seen.add(key)
        region = region_tag(r.get("loc", ""), r.get("title", ""))
        level = level_tag(r.get("title", ""), r.get("microsite", ""),
                          r.get("desc", ""))
        out.append({
            "title": (r.get("title") or "")[:160],
            "company": r.get("company") or "",
            "loc": (r.get("loc") or "")[:120],
            "region": region,
            "level": level,
            "posted": r.get("posted") or "",
            "source": r.get("source") or "phenom",
            "url": (r.get("url") or "")[:300],
        })
    return out


def write_report(company: str, rows: list[dict], src_notes: list[dict],
                 phenom_ev: dict, out_dir: Path) -> None:
    corp = [r for r in rows if r["level"] == "corp"]
    corp_user = [r for r in corp
                 if r["region"] in ("bay_area", "remote")]
    by_region: dict[str, int] = {}
    by_level: dict[str, int] = {}
    for r in rows:
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    lines = [
        f"# Adhoc company test — {company}",
        f"",
        f"_Ran {now}. Region filter: SF Bay Area ∨ Remote. "
        f"Level: corp (excl. hourly retail). Sources: 9 aggregator "
        f"adapters + the Phenom direct probe matrix._",
        "",
        "## Headline — corp-level ∧ (Bay Area ∨ Remote)",
        "",
        f"**{len(corp_user)} rows** of {len(rows)} total {company} rows.",
        "",
    ]
    for r in sorted(corp_user, key=lambda x: (x["region"], x["title"])):
        lines.append(f"- [{r['region']}] **{r['title']}** — {r['loc']}"
                     f" — {r['source']}"
                     f"{(' — ' + r['posted']) if r['posted'] else ''}")
    if not corp_user:
        lines.append("_(none — see the per-source coverage below)_")

    lines += [
        "",
        "## Region × level composition (all company rows)",
        "",
        "| region | n | | level | n |",
        "|---|---|---|---|---|",
        f"| bay_area | {by_region.get('bay_area', 0)} | "
        f"| corp | {by_level.get('corp', 0)} |",
        f"| remote | {by_region.get('remote', 0)} | "
        f"| retail_hourly | {by_level.get('retail_hourly', 0)} |",
        f"| other | {by_region.get('other', 0)} | "
        f"| retail_mgmt | {by_level.get('retail_mgmt', 0)} |",
        "",
        "## Aggregator coverage (company-keyword query, US-wide)",
        "",
        "| source | status | raw | company hits | secs |",
        "|---|---|---|---|---|",
    ]
    for n in src_notes:
        lines.append(f"| {n['source']} | {n['status']} | {n['raw']} "
                     f"| {n['company_hits']} | {n['secs']} |")
    lines += [
        "",
        "## Phenom direct probe",
        "",
        f"- method that answered: `{phenom_ev.get('method')}`",
    ]
    for t in phenom_ev.get("tried", []):
        lines.append(f"- tried `{t['endpoint']}` → HTTP {t['http']}")
    lines += ["", "## Interpretation", ""]
    agg_hit = sum(1 for n in src_notes if n["company_hits"] > 0)
    lines.append(
        f"- {agg_hit}/9 aggregators surfaced {company} rows; the direct "
        f"probe `{'answered' if phenom_ev.get('method') else 'did not answer'}`."
    )
    if phenom_ev.get("tried"):
        lines.append("- Phenom endpoints are widget-auth-gated (lane-1: "
                     "A→403, B→401, C→404) — a direct ATS read needs the "
                     "site's own refNum/session; aggregators are the "
                     "evidence base today.")
    lines.append(
        f"- Corp∧(BA∨remote) = {len(corp_user)}. Region tags are "
        "CA-context-gated (lane-1 false-positive class: Antioch TN / "
        "Dublin OH / Newark NJ are NOT Bay Area despite the name "
        "collision)." if corp_user else
        "- Corp∧(BA∨remote) = 0 — no corp-level Bay Area or remote "
        "postings surfaced this run.")
    (out_dir / f"{company}.report.md").write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="starbucks")
    ap.add_argument("--phenom-site", default="apply.starbucks.com")
    ap.add_argument("--phenom-domain", default="starbucks.com")
    ap.add_argument("--out", default="ingest/data/adhoc")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    company = args.company.lower()

    print(f"[adhoc] company={company} — aggregator pass (9 adapters)…")
    agg_rows, src_notes = aggregator_pass(company)
    print(f"[adhoc] aggregators: {len(agg_rows)} company rows")

    print(f"[adhoc] Phenom probe matrix on https://{args.phenom_site}…")
    ph_rows, phenom_ev = phenom_probe(company, args.phenom_domain,
                                      args.phenom_site)
    print(f"[adhoc] phenom: {len(ph_rows)} rows via {phenom_ev.get('method')}")

    rows = classify_rows(agg_rows + ph_rows, company)
    payload = {
        "company": company,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "regions": ["bay_area", "remote"],
        "n_total": len(rows),
        "n_corp_user_cut": sum(
            1 for r in rows
            if r["level"] == "corp" and r["region"] in ("bay_area", "remote")),
        "aggregator_notes": src_notes,
        "phenom_evidence": phenom_ev,
        "rows": rows,
    }
    (out_dir / f"{company}.json").write_text(json.dumps(payload, indent=1))

    write_report(company, rows, src_notes, phenom_ev, out_dir)

    n_cut = payload["n_corp_user_cut"]
    print(f"[adhoc] DONE: {len(rows)} total, {n_cut} corp∧(BA∨remote)")
    print(f"[adhoc] artifacts: {out_dir}/{company}.json "
          f"+ {company}.report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
