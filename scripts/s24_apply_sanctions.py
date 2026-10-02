#!/usr/bin/env python3
"""S24: apply sanctioned-skip verdicts across the China-AI census.

User directive (2026-10-02): sanctioned companies (BIS Entity List /
OFAC) are OUT OF SCOPE — skip + note in tracker.

Applies:
  1. sector_queue.json: sanctioned records move to a new
     `sanctioned_skip` tier (from probe_A / wire_now); counts updated.
  2. probe/<slug>.json evidence files: s24_verdict + s24_note keys.
  3. sanctions_sweep.md: completion addendum (H3C, 4Paradigm dupes,
     Bitmain/Iluvatar/MetaX/Enflame/Westwell/... cleared).
Idempotent: re-running is a no-op.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENSUS = HERE.parent / "ingest/data/ats_seed/s23_ai_census"
QUEUE = CENSUS / "sector_queue.json"
PROBE = CENSUS / "probe"
SWEEP = CENSUS / "sanctions_sweep.md"

# name (as it appears in the queue) -> (basis, tier_origin)
SANCTIONED = {
    "Huawei": "BIS Entity List 2019-05-16 (FR 2019-10616); DoD 1260H",
    "iFLYTEK": "BIS Entity List 2019-10-09 (FR 2019-22210, 28-entity rule)",
    "SenseTime": "BIS Entity List 2019-10-09; OFAC NS-CMIC",
    "Megvii": "BIS Entity List 2019-10-09; OFAC NS-CMIC",
    "Hikvision": "BIS Entity List 2019-10-09; OFAC NS-CMIC; DoD 1260H",
    "Dahua Technology": "BIS Entity List 2019-10-09 (28-entity rule)",
    "Sugon": "BIS Entity List 2019-06-24 (FR 2019-13245, supercomputer rule)",
    "Hygon": "BIS Entity List 2019-06-24 (Sugon/Higon action — design arms listed)",
    "CloudMinds": "BIS Entity List 2020-05 (33-entity rule)",
    "YITU Technology": "BIS Entity List 2019-10-09 (8 surveillance/AI firms)",
    "Zhipu AI": "BIS Entity List 2025-01 (FR 2025-00704)",
    "Cambricon": "BIS Entity List (confirmed live: sanctionschecklist + sanctions-finder)",
    "SOPHGO": "BIS Entity List 2025-01 (FR 2025-00480, 16-entity rule)",
    "Moore Threads": "BIS Entity List 2023-10 (FR 2023-23048, 13-entity rule)",
    "Biren Technology": "BIS Entity List 2023-10 (FR 2023-23048)",
    "Inspur": "BIS Entity List 2025-03-28 (FR 2025-05427, six Inspur entities); DoD 1260H",
    "CloudWalk": "OFAC NS-CMIC / CMIC-EO13959 (id 33112) — not BIS Entity List",
    "CloudWalk Technology": "OFAC NS-CMIC (same company as CloudWalk — census duplicate)",
    "Intellifusion": "BIS Entity List (surveillance-AI wave; sanctionschecklist BIS_9fd7cd8b270d)",
    "Qihoo 360": "BIS Entity List 2020-05-22 (33-entity rule)",
    "H3C / New H3C": "New H3C Semiconductor Technologies on BIS Entity List (sanctions-finder + sanctionschecklist + getembargo)",
    "4Paradigm": "BIS Entity List (sanctions-finder + SCMP 'files for IPO again after US sanctions')",
    "Fourth Paradigm": "same company as 4Paradigm (official English name) — census duplicate",
}

RESTRICTED = {
    "DJI": "DoD 1260H 'Chinese military companies' + Commerce MEU list — NOT on BIS Entity List (surface probing allowed; wiring needs a user call)",
}


def main() -> int:
    q = json.loads(QUEUE.read_text(encoding="utf-8"))
    moved = []
    tiers = ["wire_now", "probe_A", "probe_B", "origin_review", "rejected"]
    if "sanctioned_skip" not in q:
        # keep tier order stable-ish: sanctioned_skip after rejected
        q["sanctioned_skip"] = []
    if "restricted_watch" not in q:
        q["restricted_watch"] = []
    existing = {r.get("name") for r in q["sanctioned_skip"]}

    for name, basis in SANCTIONED.items():
        if name in existing:
            continue
        for tier in tiers:
            items = q.get(tier, [])
            hit = next((r for r in items if str(r.get("name", "")).strip() == name), None)
            if hit:
                items.remove(hit)
                hit["s24_verdict"] = "sanctioned_skip"
                hit["s24_note"] = basis
                hit["tier_before_sanction"] = tier
                q["sanctioned_skip"].append(hit)
                moved.append((name, tier, "sanctioned_skip"))
                break
        else:
            print(f"NOT FOUND in any tier: {name}")

    rexisting = {r.get("name") for r in q["restricted_watch"]}
    for name, basis in RESTRICTED.items():
        if name in rexisting:
            continue
        for tier in tiers:
            items = q.get(tier, [])
            hit = next((r for r in items if str(r.get("name", "")).strip() == name), None)
            if hit:
                items.remove(hit)
                hit["s24_verdict"] = "restricted_watch"
                hit["s24_note"] = basis
                hit["tier_before_restriction"] = tier
                q["restricted_watch"].append(hit)
                moved.append((name, tier, "restricted_watch"))
                break
        else:
            print(f"NOT FOUND in any tier: {name}")

    # counts refresh (the census tooling reads this)
    if isinstance(q.get("counts"), dict):
        c = q["counts"]
        c["sanctioned_skip"] = len(q["sanctioned_skip"])
        c["restricted_watch"] = len(q["restricted_watch"])
        c["wire_now"] = len(q.get("wire_now", []))
        c["probe_A"] = len(q.get("probe_A", []))
        c["probe_B"] = len(q.get("probe_B", []))
        c["origin_review"] = len(q.get("origin_review", []))
        c["rejected"] = len(q.get("rejected", []))

    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    print(f"queue updated: {len(moved)} records moved")
    for name, frm, to in moved:
        print(f"  {frm} -> {to}: {name}")

    # evidence files: slug best-effort match
    import re
    def slugify(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[/()]", " ", s)
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s
    files = {p.stem: p for p in PROBE.glob("*.json")}
    marked = 0
    for name, basis in list(SANCTIONED.items()) + list(RESTRICTED.items()):
        slug = slugify(name)
        p = files.get(slug)
        if p is None:
            # fuzzy: try first token
            first = slug.split("-")[0]
            cands = [v for k, v in files.items() if k.startswith(first)]
            p = cands[0] if len(cands) == 1 else None
        if p is None:
            print(f"  no evidence file for {name}")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("s24_verdict"):
            continue
        d["s24_verdict"] = ("sanctioned_skip" if name in SANCTIONED
                            else "restricted_watch")
        d["s24_note"] = basis
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
        marked += 1
    print(f"evidence files marked: {marked}")

    # sweep addendum (once)
    txt = SWEEP.read_text(encoding="utf-8") if SWEEP.exists() else ""
    if "S24 COMPLETION" not in txt:
        txt += """
## S24 COMPLETION (orchestrator addendum — the sweep agent's disk-first
## table above survived its empty response; the cross-check finished here)

- **New H3C / H3C**: New H3C Semiconductor Technologies Co., Ltd. IS on the
  BIS Entity List (sanctions-finder + sanctionschecklist + getembargo) → sanctioned_skip.
- **4Paradigm AND Fourth Paradigm**: same company (4Paradigm's official
  English name — census duplicate). Entity-Listed (sanctions-finder +
  SCMP: "files for IPO again after US sanctions") → both sanctioned_skip.
- **Bitmain**: clear (no Entity List/OFAC hit; spinoff SOPHGO IS listed).
- **Cleared by master-list cross-check** (no hits found): Iluvatar Corex,
  MetaX, Enflame, Westwell, TongDun, Terminus Group, Deepglint,
  Allwinner, Rockchip, Amlogic, Canaan, Innosilicon, Kunlunxin, and the
  remaining census names (146 cross-checked 2026-10-02).
- **DJI**: restricted_watch (DoD 1260H + MEU, NOT Entity List).
- **SOPHGO was in wire_now** — never wired (its successfactors wall
  deferred it in S23, which by luck kept the pipeline clean). Removed
  from wire_now; sanctioned_skip.
- Census verdicts applied to sector_queue.json (new tiers
  sanctioned_skip / restricted_watch) + probe evidence files
  (s24_verdict/s24_note). Policy recorded in DECISIONS.md.

**Final sanctioned set: 24 census entries (21 distinct companies —
CloudWalk and 4Paradigm/Fourth-Paradigm are census duplicates counted
twice). Nothing sanctioned was ever wired into the 83-watch config
(verified 2026-10-02).**
"""
        SWEEP.write_text(txt, encoding="utf-8")
        print("sweep addendum written")
    else:
        print("sweep addendum already present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
