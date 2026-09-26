#!/usr/bin/env python3
"""build_ui_bundle.py — the CSV corpus → UI data bundle exporter (S19).

Reads the 30-company csv-v2 corpus (ingest/data/workday/*.csv) + the
watch config (board/adapter metadata) and emits the two-tier bundle the
job-explorer UI serves:

  index.json   — companies meta + corpus facets + snapshot date (tiny)
  jobs.json    — ALL rows, every filter/sort field, a 240-char
                 description excerpt, NO full descriptions (~4-5MB)
  desc/{cid}.json — per-company full descriptions (lazy-loaded by the
                 UI only when a row detail opens or desc-search turns on)

Also mirrors the bundle into ingest/data/ui/ (durable, git-tracked).

Usage:
  python3 scripts/build_ui_bundle.py [--out /home/z/my-project/public/data]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "ingest" / "data" / "workday"
CFG = REPO / "ingest" / "data" / "board_watch" / "config.json"

ADAPTER_LABEL = {
    "workday": "Workday", "greenhouse": "Greenhouse", "ashby": "Ashby",
    "lever": "Lever", "workable": "Workable", "feishuhire": "Feishu Hire",
    "paylocity": "Paylocity", "smartrecruiters": "SmartRecruiters",
    "bytedance": "ByteDance (custom)", "alibaba": "Alibaba (custom)",
    "tripcom": "Trip.com (custom)", "xiaohongshu": "Xiaohongshu (custom)",
}

STATUS_COLORS = {  # for the UI badge palette
    "matched": "green", "no_match": "gray", "blocked": "red",
    "not_checked": "amber",
}


def adapter_class(board_spec: str) -> str:
    if board_spec.startswith("ats:"):
        return board_spec.split(":")[1]
    if board_spec.startswith("custom:"):
        return board_spec.split(":")[1]
    # workday tenant|instance|site
    return "workday"


def city_of(primary: str) -> str:
    """'US, CA, Santa Clara' → 'Santa Clara, CA' (display form)."""
    if not primary:
        return ""
    parts = [p.strip() for p in primary.split(",")]
    if len(parts) >= 3 and parts[0].upper() in ("US", "USA", "UNITED STATES"):
        return f"{parts[-1]}, {parts[-2]}"
    if len(parts) == 2:
        return f"{parts[0]}, {parts[1]}"
    return primary


def clean_excerpt(desc: str, limit: int = 240) -> str:
    if not desc:
        return ""
    txt = re.sub(r"<[^>]+>", " ", desc)
    txt = re.sub(r"&[a-z]+;", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit] + ("…" if len(txt) > limit else "")


def num(v: str):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/home/z/my-project/public/data")
    args = ap.parse_args()

    watches = {}
    if CFG.exists():
        cfg = json.loads(CFG.read_text(encoding="utf-8"))
        for w in cfg.get("watches", []):
            watches[w["label"].removesuffix("_us_fulltime")] = w

    jobs: list[dict] = []
    companies: list[dict] = []
    descs: dict[str, dict[str, str]] = {}
    facets: dict[str, set] = {
        "jobFamilyGroup": set(), "corroborationStatus": set(),
        "timeType": set(), "remoteFlag": set(), "workerSubType": set(),
        "stateCodes": set(), "matchMethod": set(),
    }
    snapshot = ""

    for p in sorted(DATA.glob("*_us_fulltime.csv")):
        cid = p.name.removesuffix("_us_fulltime.csv")
        w = watches.get(cid, {})
        with open(p, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        n_li = 0
        dump_date = ""
        for r in rows:
            rid = r.get("reqId") or ""
            uid = f"{cid}:{rid}"
            status = r.get("corroborationStatus") or "not_checked"
            if status == "matched":
                n_li += 1
            states = [s for s in (r.get("stateCodes") or "").split(";") if s]
            job = {
                "id": uid,
                "reqId": rid,
                "companyId": cid,
                "company": r.get("company") or w.get("company") or cid,
                "hiringOrg": r.get("hiringOrg") or "",
                "title": r.get("title") or "",
                "timeType": r.get("timeType") or "",
                "postedOn": r.get("postedOn") or "",
                "startDate": r.get("startDate") or "",
                "age": num(r.get("postingAgeDays")),
                "primaryLocation": r.get("primaryLocation") or "",
                "city": city_of(r.get("primaryLocation") or ""),
                "nLocations": num(r.get("nLocations")),
                "remoteFlag": (r.get("remoteFlag") or "").lower() == "true",
                "states": states,
                "status": status,
                "matchMethod": r.get("matchMethod") or "",
                "linkedinUrl": r.get("linkedinUrl") or "",
                "linkedinPostedDate": r.get("linkedinPostedDate") or "",
                "numApplicants": num(r.get("numApplicants")),
                "applicantLabel": r.get("applicantLabel") or "",
                "dateDeltaDays": num(r.get("dateDeltaDays")),
                "daysOnMarket": num(r.get("daysOnMarket")),
                "repostCount": num(r.get("repostCount")) or 0,
                "lastResetDate": r.get("lastResetDate") or "",
                "applicationDeadline": r.get("applicationDeadline") or "",
                "daysLeftToApply": num(r.get("daysLeftToApply")),
                "workerSubType": r.get("workerSubType") or "",
                "jobFamilyGroup": r.get("jobFamilyGroup") or "",
                "earliestEvidenceDate": r.get("earliestEvidenceDate") or "",
                "crossSourceRepostEvidence":
                    r.get("crossSourceRepostEvidence") or "",
                "h1bFilings": num(r.get("h1bFilings")),
                "h1bWageP25": num(r.get("h1bWageP25")),
                "h1bWageP50": num(r.get("h1bWageP50")),
                "h1bWageP75": num(r.get("h1bWageP75")),
                "h1bMatchBasis": r.get("h1bMatchBasis") or "",
                "url": r.get("url") or "",
                "excerpt": clean_excerpt(r.get("description") or ""),
                "descLen": num(r.get("descriptionLength")) or 0,
                "detailError": r.get("detailError") or "",
            }
            jobs.append(job)
            descs.setdefault(cid, {})[uid] = r.get("description") or ""
            for k in ("jobFamilyGroup", "corroborationStatus", "timeType",
                      "workerSubType", "matchMethod"):
                if job.get(k if k != "corroborationStatus" else "status"):
                    facets[k].add(job[k if k != "corroborationStatus"
                                       else "status"])
            facets["remoteFlag"].add(job["remoteFlag"])
            for s in states:
                facets["stateCodes"].add(s)
            dump_date = max(dump_date, r.get("dumpDate") or "")
            snapshot = max(snapshot, r.get("dumpDate") or "")

        board = w.get("board", "")
        companies.append({
            "id": cid,
            "name": w.get("company") or (rows and rows[0].get("company")) or cid,
            "board": board,
            "adapter": adapter_class(board),
            "adapterLabel": ADAPTER_LABEL.get(adapter_class(board),
                                              adapter_class(board)),
            "rows": len(rows),
            "liMatched": n_li,
        })

    out = Path(args.out)
    (out / "desc").mkdir(parents=True, exist_ok=True)
    index = {
        "snapshotDate": snapshot or dump_date,
        "totalRows": len(jobs),
        "companies": sorted(companies, key=lambda c: -c["rows"]),
        "facets": {k: sorted(v) for k, v in facets.items()},
    }
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (out / "jobs.json").write_text(
        json.dumps({"snapshotDate": index["snapshotDate"], "jobs": jobs},
                   ensure_ascii=False), encoding="utf-8")
    for cid, rows in descs.items():
        (out / "desc" / f"{cid}.json").write_text(
            json.dumps({"companyId": cid, "rows": rows},
                       ensure_ascii=False), encoding="utf-8")

    # durable mirror into the research repo
    ui_dir = REPO / "ingest" / "data" / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (ui_dir / "jobs.json").write_text(
        json.dumps({"snapshotDate": index["snapshotDate"], "jobs": jobs},
                   ensure_ascii=False), encoding="utf-8")
    (ui_dir / "desc").mkdir(exist_ok=True)
    for cid, rows in descs.items():
        (ui_dir / "desc" / f"{cid}.json").write_text(
            json.dumps({"companyId": cid, "rows": rows},
                       ensure_ascii=False), encoding="utf-8")

    mb = (out / "jobs.json").stat().st_size / 1e6
    dmb = sum((out / "desc" / f).stat().st_size
              for f in (out / "desc").glob("*.json")) / 1e6
    print(f"bundle: {len(jobs)} rows, {len(companies)} companies, "
          f"jobs.json {mb:.1f}MB, desc/ {dmb:.1f}MB, snapshot "
          f"{index['snapshotDate']}")
    print(f"output: {out}  + durable mirror at ingest/data/ui/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
