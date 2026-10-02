#!/usr/bin/env python3
"""S25 — apply the loosened-origin-bar verdicts to the AI census records.

User directive (2026-10-02):
  1. Chinese-founder origin ALONE qualifies (the major-CN-ops test is
     retired as a gate) — the rationale: these companies likely favor
     Chinese nationals in US-based roles.
  2. Remote-OK roles are US-eligible ("any 'remote ok' roles are
     basically US-based — I can still do it, in a different timezone").
  3. MyShell/Creatify-class (China-remote workforces) = "the best"
     candidates if they have roles.

Outcome classes written as s25_verdict on the origin_review records:
  s25_wire            — board live, wired this session
  s25_park_no_board   — verified NO live board (recheck cadence noted)
  s25_park_empty      — board live but empty
  s25_out             — fails even the loose bar (non-Chinese founders)
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
CENSUS = HERE / "ingest/data/ats_seed/s23_ai_census"

VERDICTS = {
    "Creatify": ("s25_wire",
        "LOOSE BAR: user called this class 'the best'. ashby:creatify live "
        "(5 US roles, Mountain View; 2026-10-02 verified). CN-remote rounds "
        "continue via V2EX but the board carries the US side. "
        "LCA needle: CREATIFY (CREATIFY LAB INC x18 2026)."),
    "MyShell": ("s25_park_no_board",
        "LOOSE BAR: user class 'the best', but NO live board: homepage "
        "greenhouse link job-boards.greenhouse.io/myshell = 404 (stale; "
        "API 404 too), jobs.myshell.ai DNS-dead (2026-10-02). Hiring runs "
        "via Zhihu/V2EX CN-remote rounds — no ATS surface to wire. "
        "Recheck monthly; if a board appears, wire immediately per policy."),
    "Pika": ("s25_wire",
        "LOOSE BAR (Chinese-founder, US-only ops now qualifies): "
        "ashby:pika live 14 roles (9 'US remote' + 5 Palo Alto). "
        "No LCA rows in 2023-2026 — honest-0 band expected. "
        "LCA needle: PIKA LABS (specific; PIKACARD/PIKA INTL homonyms)."),
    "World Labs": ("s25_wire",
        "LOOSE BAR: Fei-Fei Li (Chinese-founder class). ashby:worldlabs "
        "live 11 SF roles. LCA needle: WORLD LABS "
        "(WORLD LABS TECHNOLOGIES INC x10 2026, MTS roles)."),
    "Fireworks AI": ("s25_wire",
        "LOOSE BAR: founder Lin Qiao (Fudan, ex-Meta PyTorch). "
        "ashby:fireworks live 86 roles (San Mateo/SF/NY + 3 US-Remote). "
        "LCA needle: FIREWORKSAI,FIREWORKS.AI (FIREWORKSAI INC x31 2026; "
        "FIREWORKS GALLERIES LLC is a homonym — needle excludes it)."),
    "Together AI": ("s25_wire",
        "LOOSE BAR: co-founder/CTO Ce Zhang (Chinese-origin, ETH/UChicago). "
        "greenhouse:togetherai live 77 roles (50 SF). "
        "LCA needle: TOGETHER COMPUTER,TOGETHER AI "
        "(TOGETHER COMPUTER INC x39 2026, AI/ML titles)."),
    "Cognition": ("s25_wire",
        "LOOSE BAR: founders Steven Hao + Walden Yan (Chinese-descent, "
        "US-based). ashby:cognition live 103 roles global (38 SF). "
        "LCA needle: COGNITION AI (COGNITION AI INC x9 2026 NY; "
        "COGNITION FINANCIAL/COGNITION LLC homonyms excluded)."),
    "Genspark": ("s25_wire",
        "LOOSE BAR: founders Eric Jing + Zhu Kaihua (ex-Baidu Xiaodu). "
        "ashby:genspark live 8 roles (NYC & Palo Alto). "
        "LCA needle: GENSPARK,MAINFUNC (GENSPARK INC x2 2026; "
        "mainfunc.cn Xi'an mining co is a HOMONYM — never wire that)."),
    "Hyperbolic Labs": ("s25_wire",
        "LOOSE BAR: CEO Jasper Zhang (Berkeley math PhD) + CTO Yuchen Jin "
        "(Chinese-origin founders, US-based). ashby:hyperbolic live 15 "
        "(11 SF + 4 remote). LCA needle: HYPERBOLIC LABS "
        "(HYPERBOLIC LABS INC x8 2025-26)."),
    "Sunday Robotics": ("s25_wire",
        "LOOSE BAR resolves the S22 origin question: founders Tony Zhao + "
        "Cheng Chi (Stanford, Chinese-descent) qualify as "
        "Chinese-founder. ashby:sunday live 24 (23 Redwood City + 1 "
        "remote; robotics eng roles). LCA needle: SUNDAY ROBOTICS "
        "(SUNDAY ROBOTICS INC x21 2026; SUNDAY APP INC PBC homonym)."),
    "LMArena": ("s25_out",
        "LOOSE BAR still fails: co-founder Wei-Lin Chiang is "
        "Taiwanese-descent, not a mainland-Chinese-founder case; no "
        "China ops. Stays out."),
    "Sonauto": ("s25_out",
        "LOOSE BAR still fails: founders Hayden Housen & Ryan Tremblay "
        "(Cornell, ex-Meta) — not Chinese-founded. Stays out."),
    "Nexa AI": ("s25_out",
        "Acquired by Qualcomm (derivative entity) — out regardless of "
        "origin bar."),
    "Aizip": ("s25_park_no_board",
        "LOOSE BAR: Chinese-founder plausible (Saratoga/SZ edge-AI), but "
        "aizip.ai /careers = 404, no ATS surface found. "
        "Park; recheck if a board appears."),
    "Haiper": ("s25_park_no_board",
        "haiper.ai/careers 404 (2026-10-02), no ATS board; founder "
        "departed to Microsoft AI. Park."),
    "CAMEL-AI": ("s25_park_no_board",
        "OSS community (Guohao Li, UK-based Chinese founder); no hiring "
        "board. Park."),
    "AutoX": ("s25_park_empty",
        "consider.com board live but 'No jobs found' (needs CSRF capture "
        "per re_E contract); autox.ai careers 404. Recheck when jobs "
        "appear."),
    "Akool": ("s25_park_no_surface",
        "No direct board (LinkedIn ~3 roles). Park unchanged."),
    "Curacloud Corporation": ("s25_park_no_surface",
        "Keya Medical dual-HQ; LinkedIn-only US hiring. Park unchanged."),
}

WIRE_LABELS = {
    "Creatify": "creatify_us_fulltime",
    "Pika": "pika_us_fulltime",
    "World Labs": "worldlabs_us_fulltime",
    "Fireworks AI": "fireworks_us_fulltime",
    "Together AI": "togetherai_us_fulltime",
    "Cognition": "cognition_us_fulltime",
    "Genspark": "genspark_us_fulltime",
    "Hyperbolic Labs": "hyperbolic_us_fulltime",
    "Sunday Robotics": "sundayrobotics_us_fulltime",
}


def main() -> int:
    queue_p = CENSUS / "sector_queue.json"
    q = json.loads(queue_p.read_text())
    applied = 0
    for tier in ("origin_review",):
        for e in q.get(tier, []):
            v = VERDICTS.get(e.get("brand") or e.get("name"))
            if not v:
                continue
            e["s25_verdict"], e["s25_note"] = v
            if e["brand"] in WIRE_LABELS:
                e["s25_watch_label"] = WIRE_LABELS[e["brand"]]
            applied += 1
    # also annotate the wire_now records that were already wired in S24
    for e in q.get("wire_now", []):
        if e.get("s24_verdict", "").startswith("s24_wire"):
            e["s25_verdict"] = "s25_wired_prior"
    queue_p.write_text(json.dumps(q, indent=2, ensure_ascii=False) + "\n")

    # companies.jsonl — same verdict fields, key on brand
    comp_p = CENSUS / "companies.jsonl"
    if comp_p.exists():
        lines = []
        hit = 0
        for line in comp_p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            v = VERDICTS.get(rec.get("brand") or rec.get("name"))
            if v:
                rec["s25_verdict"], rec["s25_note"] = v
                if rec.get("brand") in WIRE_LABELS:
                    rec["s25_watch_label"] = WIRE_LABELS[rec["brand"]]
                hit += 1
            lines.append(json.dumps(rec, ensure_ascii=False))
        comp_p.write_text("\n".join(lines) + "\n")
        print(f"companies.jsonl: {hit} records updated")
    print(f"sector_queue.json: {applied} origin_review records updated")
    print(f"wire wave: {len(WIRE_LABELS)} boards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
