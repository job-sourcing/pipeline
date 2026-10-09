#!/usr/bin/env python3
"""S29: aggregate the unwired census records' probe evidence →
the board-surface sweep worklist (ATS-class distribution + live-probe
queue)."""
import json
import pathlib
import collections
import re

PROBE = pathlib.Path("ingest/data/ats_seed/s25_census/probe")
rows = [json.loads(l) for l in open(
    "ingest/data/ats_seed/s25_census/s27_adjudication.jsonl")]
unwired = [r for r in rows if r.get("verdict") in ("china_hq", "china_ops")
           and not r.get("already_wired")]

def classify(url: str) -> str:
    pats = [
        (r"job-boards\.greenhouse\.io|boards\.greenhouse\.io|app\.greenhouse\.io", "greenhouse"),
        (r"ashbyhq\.com|jobs\.ashby", "ashby"),
        (r"jobs\.lever\.co|eu\.lever\.co", "lever"),
        (r"jobs\.workable\.com|apply\.workable", "workable"),
        (r"smartrecruiters\.com", "smartrecruiters"),
        (r"icims\.com", "icims"),
        (r"myworkdayjobs\.com|wd\d+\.myworkday", "workday"),
        (r"jobs\.feishu\.cn|\.feishu\.cn", "feishuhire"),
        (r"recruiting\.paylocity\.com", "paylocity"),
        (r"paycom", "paycom"),
        (r"trakstar", "trakstar"),
        (r"app\.moka\.cn|mokahr", "mokahr"),
        (r"breezy\.hr", "breezy"),
        (r"bamboohr\.com", "bamboohr"),
        (r"jobs\.jobvite\.com|apply\.jobvite", "jobvite"),
        (r"rippling", "rippling"),
        (r"jazzhr", "jazzhr"),
        (r"oraclecloud\.com", "oraclehcm"),
        (r"teamtailor", "teamtailor"),
        (r"workstream", "workstream"),
        (r"recruitee\.com", "recruitee"),
        (r"jobs\.ceipal|ceipal", "ceipal"),
        (r"jobs\.personio|personio\.de", "personio"),
        (r"rec\.zoho|zoho", "zoho"),
        (r"tam\.brassring|brassring", "brassring"),
        (r"successfactors", "successfactors"),
        (r"taleo\.net|taleo", "taleo"),
        (r"ultipro|ukg", "ultipro"),
        (r"jobpad|pineappl?", None),
        (r"jobs\.com |career.*\.com", None),
    ]
    for pat, label in pats:
        if label and re.search(pat, url, re.I):
            return label
    return "custom/other"

by_class = collections.defaultdict(list)   # class -> [(name, slug, url, spec)]
no_candidates = []
for r in unwired:
    pf = PROBE / f"{r.get('slug')}.json"
    if not pf.exists():
        no_candidates.append((r["name"], "NO-PROBE-FILE"))
        continue
    d = json.loads(pf.read_text(encoding="utf-8"))
    cands = d.get("candidates") or []
    if not cands:
        no_candidates.append((r["name"], "probe-no-candidates"))
        continue
    # best candidate: highest score with a real url
    ranked = sorted(cands, key=lambda c: -(c.get("score") or 0))
    best = None
    for c in ranked:
        u = c.get("url") or ""
        if u.startswith("http"):
            best = c
            break
    if best is None:
        no_candidates.append((r["name"], "no-url"))
        continue
    cls = classify(best.get("url", ""))
    by_class[cls].append((r["name"], r.get("slug"), best.get("url"),
                          best.get("spec") or "", best.get("score") or 0))

print("=== ATS-class distribution (unwired, best candidate) ===")
total = 0
for k in sorted(by_class, key=lambda x: -len(by_class[x])):
    print(f"{len(by_class[k]):4} {k}")
    total += len(by_class[k])
print(f"{total:4} with candidates; {len(no_candidates)} without")
print()
print("=== supported classes TODAY (wire-queue) ===")
for k in ("greenhouse", "ashby", "lever", "workable", "feishuhire",
          "paylocity", "workday", "adp"):
    if k in by_class:
        print(f"-- {k} ({len(by_class[k])}):")
        for name, slug, url, spec, score in by_class[k]:
            print(f"   {name:26} {url[:70]:70} spec={spec[:28]} score={score}")

out = {
    "by_class": {k: v for k, v in by_class.items()},
    "no_candidates": no_candidates,
}
pathlib.Path("ingest/data/ats_seed/s25_census/s29_worklist.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("\nworklist written → s29_worklist.json")
