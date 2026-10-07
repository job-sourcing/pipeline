#!/usr/bin/env python3
"""S28: close the 11 gray_review census records (final verdicts, live-
evidence cited). D-S27-1 strict reading: founder descent means NOTHING;
include only China-sited ops / Chinese-owned+operated entities.

Verdicts (live evidence 2026-10-07):
  KEEP  alluxio  china_ops  — lever live: 9 postings, 5 China-sited
        (3 'China', Beijing, Shanghai) + 4 Foster City → ops gravitate
        to China; dual-HQ; wired + non_cn already.
  KEEP  pingcap  china_ops  — Chinese-owned dual entity (Beijing + US
        Inc); live board: 5 Tokyo Hybrid + 2 remote US/KR — global
        roles, the exact collection target; wired + non_cn.
  OUT   affine, aisa, automq, databend, fish-audio, llamaindex, rss3,
        tabbyml, zingage — Singapore/US remote-first, no China-siting
        evidence (descent excluded); none wired → roster unchanged.
"""
import json

PATH = "ingest/data/ats_seed/s25_census/s27_adjudication.jsonl"

VERDICTS = {
    "AFFiNE": (
        "us_only_out",
        "S28 live: Singapore-incorporated remote-first (all-mainland-"
        "founders — descent excluded D-S27-1); no China-siting evidence; "
        "smartrecruiters surface EMPTY (api totalFound=0, 2026-10-07)"),
    "AIsa": (
        "us_only_out",
        "S28: SF-incorporated MyShell-class US AI startup; founder "
        "descent only — excluded D-S27-1; no China-ops evidence, no "
        "surface"),
    "Alluxio": (
        "china_ops",
        "S28 live lever board (2026-10-07): 9 postings — 5 China-sited "
        "(3 'China', Beijing, Shanghai) + 4 Foster City → ops gravitate "
        "to China; dual-HQ US Inc + CN R&D; wired, non_cn flagged"),
    "AutoMQ": (
        "us_only_out",
        "S28 live: US entity + global remote; ex-Alibaba founders — "
        "descent excluded; no CN entity documented; careers page is "
        "form-only (no ATS)"),
    "Databend": (
        "us_only_out",
        "S28: US Las Vegas entity, remote-first global; descent "
        "excluded. HOMONYM TRAP: greenhouse 'databento' = Databento "
        "(NY market-data firm) — never wire"),
    "Fish Audio": (
        "us_only_out",
        "S28: US-incorporated remote-first; core-team-Chinese = descent "
        "only — excluded D-S27-1; no ATS ('watch' class)"),
    "LlamaIndex": (
        "us_only_out",
        "S28 close-out: SF remote-first; board removed at S27 "
        "(D-S27-1); census verdict now closed (was gray_review)"),
    "PingCAP": (
        "china_ops",
        "S28 live greenhouse board (2026-10-07): 5 Tokyo Hybrid + 2 "
        "remote (US Bay Area/KR) — global roles, zero CN-sited; "
        "Chinese-owned/operated dual entity (PingCAP Beijing + PingCAP "
        "US Inc); wired, non_cn + include_remote"),
    "RSS3": (
        "us_only_out",
        "S28 live: careers pages 404 (no surface); remote-first "
        "Singapore-linked; founder descent only — excluded D-S27-1"),
    "TabbyML": (
        "us_only_out",
        "S28 live: no careers page (404s); US remote-first; ex-Google "
        "Chinese founders — descent excluded. HOMONYM: Workable 'Tabby' "
        "(0 jobs) — never wire"),
    "Zingage": (
        "us_only_out",
        "S28 close-out: company defunct (board 404 — removed S27); "
        "US NY hq; census verdict now closed (was gray_review)"),
}

rows = [json.loads(l) for l in open(PATH, encoding="utf-8")]
changed = 0
for r in rows:
    if r.get("verdict") == "gray_review":
        v = VERDICTS.get(r["name"])
        if v is None:
            raise SystemExit(f"unhandled gray: {r['name']}")
        r["verdict"], r["origin_citation"] = v
        r["s28_closed"] = True
        changed += 1

with open(PATH, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

import collections
c = collections.Counter(r["verdict"] for r in rows)
print(f"updated {changed} records; verdict dist: {dict(c)}")
assert changed == 11, "expected all 11 grays closed"
assert not any(r.get("verdict") == "gray_review" for r in rows)
print("ZERO grays remain — census fully adjudicated")
