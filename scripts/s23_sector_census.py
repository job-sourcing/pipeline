#!/usr/bin/env python3
"""S23 sector-census tooling — the standardized expansion instrument.

The beachhead directive (China AI companies): standardize the sector
expansion process so future "job sourcing cases" (any sector, any
origin) run the SAME instrumented flow instead of ad-hoc scripts:

    1. CENSUS      sub-agents build seg_*.jsonl per segment (the S23
                   pattern: 3 parallel agents, incremental writes)
    2. --merge     this tool: dedupe segments -> companies.jsonl
    3. --triage    this tool: census + roster -> sector_queue.json in
                   the EXACT s20_queue.json record shape, so the
                   existing probe instruments run unchanged:
                   node scripts/s20_surface_probe.mjs --mode surface \
                        --dir <sector_dir> --queue sector_queue.json
    4. PROBE       the surface-probe wave (unchanged instrument)
    5. ADJUDICATE  sub-agent batches over probe/<slug>.json evidence
    6. WIRE        re_*.md contracts -> adapters -> pins -> chains
                   (the S22/S23 assembly line)

Usage:
  python3 scripts/s23_sector_census.py --sector s23_ai_census --merge
  python3 scripts/s23_sector_census.py --sector s23_ai_census --triage
  python3 scripts/s23_sector_census.py --sector s23_ai_census --report
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "ingest" / "data" / "ats_seed"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80]


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"WARN: bad jsonl line in {path.name}: "
                  f"{line[:80]!r}", file=sys.stderr)
    return out


def load_roster() -> dict[str, str]:
    """watch label (short) -> company name, from the live config."""
    cfg_p = REPO / "ingest" / "data" / "board_watch" / "config.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
    return {w["label"].removesuffix("_us_fulltime"): w["company"]
            for w in cfg.get("watches", [])}


# ── merge ───────────────────────────────────────────────────────────────────

def merge(sector: str) -> int:
    sdir = SEED / sector
    segs = sorted(sdir.glob("seg_*.jsonl"))
    if not segs:
        print(f"no seg_*.jsonl under {sdir}")
        return 1
    by_key: dict[str, dict] = {}
    collisions: list[str] = []
    n_records = 0
    for seg in segs:
        for rec in load_jsonl(seg):
            n_records += 1
            key = _slug(rec.get("name") or "")
            if not key:
                continue
            prior = by_key.get(key)
            if prior is None:
                by_key[key] = rec
                continue
            # same company from two segments — keep the richer record,
            # union the aliases, note the collision
            collisions.append(f"{rec['name']} (in {seg.name} + "
                              f"{prior.get('_seg', '?')})")
            merged = dict(prior)
            for f in ("aliases",):
                a = set(prior.get(f) or []) | set(rec.get(f) or [])
                merged[f] = sorted(a)
            if len(json.dumps(rec)) > len(json.dumps(prior)):
                merged.update({k: v for k, v in rec.items()
                               if k not in ("aliases",)})
            merged["_seg"] = f"{prior.get('_seg', '')}+{seg.stem}"
            by_key[key] = merged
    companies = sorted(by_key.values(), key=lambda r: r.get("name") or "")
    out = sdir / "companies.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for c in companies:
            c.pop("_seg", None)
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"merge: {n_records} records → {len(companies)} unique "
          f"companies → {out}")
    if collisions:
        print(f"collisions resolved ({len(collisions)}):")
        for c in collisions:
            print(f"  - {c}")
    return 0


# ── triage ──────────────────────────────────────────────────────────────────

def triage(sector: str) -> int:
    sdir = SEED / sector
    comp_p = sdir / "companies.jsonl"
    if not comp_p.exists():
        print("run --merge first")
        return 1
    companies = load_jsonl(comp_p)
    roster = load_roster()
    roster_companies = {v.lower() for v in roster.values()}

    wire_now, probe_a, probe_b, rejected, origin_review = [], [], [], [], []
    n_wired = 0
    for c in companies:
        name = c.get("name") or ""
        if not name:
            continue
        brand = (name.split(" (")[0]).strip()
        status = c.get("status") or "active"
        origin = c.get("origin") or "china"
        confidence = c.get("confidence") or "medium"
        already = bool(c.get("already_wired")) or \
            name.lower() in roster_companies or \
            brand.lower() in roster_companies
        # probe record = the s20_queue shape the instruments expect
        rec = {
            "brand": brand,
            "name": name,
            "cn": c.get("cn") or "",
            "aliases": c.get("aliases") or [],
            "sector": c.get("segment") or "ai",
            "evidence": "probe_A",
            "ats_hint": c.get("ats_hint") or None,
            "careers_hint": c.get("careers_hint") or None,
            "us_signal": c.get("us_signal") or "",
            "lca_employers": [c["lca"]["employer"]]
            if isinstance(c.get("lca"), dict) and c["lca"].get("employer")
            else [],
            "origin": origin,
            "hq": c.get("hq") or "",
        }
        if already:
            n_wired += 1
            continue
        if status in ("defunct", "merged"):
            rejected.append({**rec, "reject_reason": f"status={status}"})
            continue
        if origin == "us_inc_chinese_founders":
            # the Sunday-Robotics class — the origin-policy call is the
            # USER's; surface, never silently wire
            origin_review.append(rec)
            continue
        ats = (rec["ats_hint"] or "")
        has_lca = bool(rec["lca_employers"])
        strong_us = bool((c.get("us_signal") or "").strip()) or has_lca
        if ats and re.match(r"^(greenhouse|ashby|lever|workable|jobvite|"
                            r"bamboohr|breezy|rippling|teamtailor|"
                            r"jazzhr|adp|ultipro|oraclehcm|j2w|"
                            r"talentadore|workstream|feishuhire|"
                            r"paylocity|radancy|sanity|wpjobboard|"
                            r"ttiproxy|successfactors|icims|"
                            r"smartrecruiters|myworkdayjobs):", ats):
            # a KNOWN-platform slug hint: the identity probe confirms
            # (homonyms are exactly what identity-mode catches)
            rec["evidence"] = "wire_now"
            wire_now.append(rec)
        elif strong_us:
            rec["evidence"] = "probe_A"
            probe_a.append(rec)
        elif (c.get("careers_hint") or "").strip() or \
                confidence in ("high", "medium"):
            rec["evidence"] = "probe_B"
            probe_b.append(rec)
        else:
            rejected.append({**rec, "reject_reason":
                            "no US signal + low confidence"})

    queue = {
        "built": __import__("datetime").datetime.now().astimezone().isoformat(),
        "counts": {
            "wired_already": n_wired,
            "wire_now": len(wire_now),
            "probe_A": len(probe_a),
            "probe_B": len(probe_b),
            "origin_review": len(origin_review),
            "rejected": len(rejected),
        },
        "wire_now": wire_now,
        "probe_A": probe_a,
        "probe_B": probe_b,
        "origin_review": origin_review,
        "rejected": rejected,
    }
    out = sdir / "sector_queue.json"
    out.write_text(json.dumps(queue, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"triage: {len(companies)} companies → "
          f"{n_wired} wired-already, {len(wire_now)} wire_now, "
          f"{len(probe_a)} probe_A, {len(probe_b)} probe_B, "
          f"{len(origin_review)} origin_review (user call), "
          f"{len(rejected)} rejected → {out}")
    return 0


# ── report ──────────────────────────────────────────────────────────────────

def report(sector: str) -> int:
    sdir = SEED / sector
    comp_p = sdir / "companies.jsonl"
    queue_p = sdir / "sector_queue.json"
    if not comp_p.exists():
        print("run --merge first")
        return 1
    companies = load_jsonl(comp_p)
    n_lca = sum(1 for c in companies if isinstance(c.get("lca"), dict)
                and c["lca"].get("filings"))
    n_ats = sum(1 for c in companies if c.get("ats_hint"))
    segs: dict[str, int] = {}
    for c in companies:
        segs[c.get("segment") or "?"] = segs.get(c.get("segment") or "?", 0) + 1
    print(f"=== {sector} census report ===")
    print(f"companies: {len(companies)}  |  LCA-evidenced: {n_lca}  |  "
          f"ATS-hinted: {n_ats}")
    print("segments:", ", ".join(f"{k}×{v}" for k, v in
                                 sorted(segs.items())))
    if queue_p.exists():
        q = json.loads(queue_p.read_text(encoding="utf-8"))
        print("queue:", json.dumps(q["counts"]))
        print("\nTop wire_now (identity-probe next):")
        for r in q.get("wire_now", [])[:20]:
            print(f"  - {r['brand']} [{r['ats_hint']}] "
                  f"LCA={r['lca_employers'][:1]}")
        print("\nOrigin-review (user policy call):")
        for r in q.get("origin_review", [])[:15]:
            print(f"  - {r['brand']} — {(r.get('us_signal') or '')[:70]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sector", required=True,
                    help="census dir name under ingest/data/ats_seed/")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--triage", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    rc = 0
    if a.merge:
        rc = merge(a.sector) or rc
    if a.triage:
        rc = triage(a.sector) or rc
    if a.report or not (a.merge or a.triage):
        rc = report(a.sector) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
