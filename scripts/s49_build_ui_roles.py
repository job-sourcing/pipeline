#!/usr/bin/env python3
"""s49_build_ui_roles.py — build the UI-ready role-family bundles for
job-explorer (S49-D: the family switcher ship).

Reads the archive-side role-family bundles from job-sourcing-research
(ingest/data/ui/roles-{pm,gen,dmm}.json — READ ONLY, never modified)
and writes UI-ready copies into the job-explorer clone's public/data/:

  roles-{pm,gen,dmm}.ui.json — the per-family bundles the /roles view
                              fetches lazily (one fetch per family)
  roles-families.json        — the ~1KB families index the view
                              fetches FIRST (family switcher; default
                              landing family = gen)

The desc policy (audit/s49/review-r1-design-audit.md F15 + the S49-D
brief):
  * gen/dmm: `desc` capped at --desc-cap chars (default 1,200 — the
    view slices the inline JD at 2,000 chars anyway, and F15 measured
    desc = 63% of the 32MB gen bundle); `descLen` recomputed.
  * pm: the identity doctrine — desc passes through untouched
    (already ≤4,000 upstream); the pm UI copy refreshes the DATA,
    byte-identical in FORMAT. (--cap-pm opts into the cap if the
    doctrine ever changes.)

Everything else passes through untouched: every row field (including
future additive fields — smb_signal / salaryMin / salaryMax when D4
ships them), the envelope, counters, snapshotDate. A top-level
`family` id+label is stamped onto each copy (additive; the switcher's
source of truth is roles-families.json).

Usage:
  python3 scripts/s49_build_ui_roles.py \
      [--from <jsr>/ingest/data/ui] \
      [--out /home/z/work/job-explorer/public/data] \
      [--desc-cap 1200] [--cap-pm]

Prints per-family rows / size / snapshot. Fails loudly if a source
bundle is missing, if the bundle's `rows` count disagrees with the
jobs array, or if a written file does not re-parse.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent          # job-sourcing-research
DEFAULT_FROM = REPO / "ingest" / "data" / "ui"
# NEVER point --out at /home/z/my-project (a separate watched copy).
DEFAULT_OUT = Path("/home/z/work/job-explorer/public/data")

DEFAULT_FAMILY = "gen"
# (id, label) — gen first: it is the default landing family (D3).
FAMILY_META = [
    ("gen", "All Roles (Gen)"),
    ("pm", "Tech PM"),
    ("dmm", "DMM (Ads)"),
]


def mb(p: Path) -> float:
    return p.stat().st_size / 1e6


def build_family(src: Path, fam: str, label: str, out_dir: Path,
                 desc_cap: int, cap_pm: bool) -> tuple[Path, dict]:
    """Write roles-{fam}.ui.json; return (out_path, per-family stats)."""
    if not src.is_file():
        sys.exit(f"FAIL: missing source bundle {src} — run the role "
                 f"sweep export first (the jsr ingest/data/ui/ files "
                 f"must exist before the UI ship).")
    with open(src, encoding="utf-8") as f:
        bundle = json.load(f)

    jobs = bundle.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        sys.exit(f"FAIL: {src} has no jobs array")
    if bundle.get("rows") != len(jobs):
        sys.exit(f"FAIL: {src} top-level rows={bundle.get('rows')!r} "
                 f"!= len(jobs)={len(jobs)} — refusing to ship a "
                 f"self-inconsistent bundle.")

    # pm passes through (identity doctrine); gen/dmm get the desc cap.
    do_cap = cap_pm or fam != "pm"
    n_capped = 0
    out_jobs = []
    for r in jobs:
        if do_cap:
            desc = r.get("desc")
            if isinstance(desc, str) and len(desc) > desc_cap:
                r = {**r, "desc": desc[:desc_cap]}
                n_capped += 1
            if "desc" in r or "descLen" in r:
                # recompute honestly against the shipped desc
                r = {**r, "descLen": len(r.get("desc") or "")}
        out_jobs.append(r)

    out = dict(bundle)  # snapshotDate / rows / envelope — untouched
    out["rows"] = len(out_jobs)
    out["jobs"] = out_jobs
    out["family"] = {"id": fam, "label": label}  # additive (S49-D (c))

    out_path = out_dir / f"roles-{fam}.ui.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    # verify: every shipped file must re-parse as valid JSON with the
    # same row count (the ship script json.loads again before pushing)
    with open(out_path, encoding="utf-8") as f:
        check = json.load(f)
    if check.get("rows") != len(check.get("jobs", [])) or \
            len(check["jobs"]) != len(jobs):
        sys.exit(f"FAIL: verification re-parse of {out_path} disagrees "
                 f"with the written bundle.")
    if check.get("snapshotDate") != bundle.get("snapshotDate"):
        sys.exit(f"FAIL: snapshotDate drifted while writing {out_path}")

    stats = {
        "family": fam, "label": label, "rows": len(out_jobs),
        "snapshotDate": str(bundle.get("snapshotDate", "")),
        "capped": n_capped,
        "src_mb": mb(src), "out_mb": mb(out_path),
    }
    return out_path, stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build UI-ready role-family bundles + the "
                    "families index for job-explorer (S49-D).")
    ap.add_argument("--from", default=str(DEFAULT_FROM),
                    help=f"jsr archive dir holding roles-*.json "
                         f"(default: {DEFAULT_FROM})")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"explorer public/data dir "
                         f"(default: {DEFAULT_OUT})")
    ap.add_argument("--desc-cap", type=int, default=1200,
                    help="desc char cap for gen/dmm copies "
                         "(default: 1200; pm is exempt — identity "
                         "doctrine — unless --cap-pm)")
    ap.add_argument("--cap-pm", action="store_true",
                    help="also cap pm desc (off by default: the pm "
                         "bundle stays byte-identical in format)")
    args = ap.parse_args()

    # NB: "from" is a Python keyword — argparse still stores the flag
    # under attribute name `from`; read it with getattr.
    src_dir = Path(getattr(args, "from"))
    out_dir = Path(args.out)
    if "/home/z/my-project" in str(out_dir.resolve()):
        sys.exit("FAIL: --out resolves into /home/z/my-project — the "
                 "watched copy. Point --out at the job-explorer clone "
                 "(/home/z/work/job-explorer/public/data).")
    if not src_dir.is_dir():
        sys.exit(f"FAIL: --from dir does not exist: {src_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_stats = []
    for fam, label in FAMILY_META:
        src = src_dir / f"roles-{fam}.json"
        _, stats = build_family(src, fam, label, out_dir,
                                args.desc_cap, args.cap_pm)
        all_stats.append(stats)

    # the families index (the switcher's first fetch)
    fam_index = {
        "default": DEFAULT_FAMILY,
        "families": [
            {
                "id": s["family"],
                "label": s["label"],
                "file": f"roles-{s['family']}.ui.json",
                "rows": s["rows"],
                "snapshotDate": s["snapshotDate"],
            }
            for s in all_stats
        ],
    }
    idx_path = out_dir / "roles-families.json"
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(fam_index, f, ensure_ascii=False, separators=(",", ":"))
    with open(idx_path, encoding="utf-8") as f:
        json.load(f)  # verify

    print(f"UI role families → {out_dir}")
    for s in all_stats:
        print(f"  {s['family']:4s} [{s['label']}] "
              f"{s['rows']:>6,} rows · snapshot {s['snapshotDate']} · "
              f"{s['src_mb']:.1f}MB → {s['out_mb']:.1f}MB "
              f"({s['capped']:,} desc capped @{args.desc_cap}"
              f"{' incl pm' if args.cap_pm else ''})")
    print(f"  index {idx_path.name} ({idx_path.stat().st_size} bytes): "
          f"default={DEFAULT_FAMILY}, "
          + ", ".join(f"{s['family']}={s['rows']:,}" for s in all_stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
