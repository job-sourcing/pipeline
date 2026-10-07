#!/usr/bin/env python3
"""s27_census_adjudicate.py — D-S27-1 census adjudication at scale.

Finalizes the company list under the recalibrated origin criteria
(Chinese-descent founders alone = OUT; china-based / china-owned /
china-operated / china-remote-workforce = IN), then extracts the
board-surface wiring candidates from the drained probe_A evidence
(203 files, S25).

Rule-based, evidence-cited, reversible (verdicts are recorded INTO the
census records + a summary sheet; nothing is deleted).

Verdict classes (company-level):
  china_hq / china_ops / cn_owned      — census origin=china, or
                                         us_chinese_founder with a
                                         China-sited hq string
  us_only_out                          — us_chinese_founder + US/foreign
                                         hq, no China-ops note evidence
  gray_review                          — us_chinese_founder + US hq BUT
                                         notes/hq mention China entity/
                                         team/remote (needs one live
                                         look before wiring)

Board-surface classes (row-level, from probe evidence):
  wire:{kind}:{org}                    — a supported adapter class and
                                         a live candidate URL
  surface_unsupported                  — a live board exists but no
                                         adapter class (RE wave backlog)
  no_surface                           — no live board found

Outputs:
  ingest/data/ats_seed/s25_census/s27_adjudication.jsonl — one record
    per census company: verdict + surface + citation
  stdout report — the counts + the WIRE LIST (the wave-3 candidates)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CENSUS = ROOT / "ingest/data/ats_seed/s25_census/companies.jsonl"
PROBE = ROOT / "ingest/data/ats_seed/s25_census/probe"
CFG = ROOT / "ingest/data/board_watch/config.json"
OUT = ROOT / "ingest/data/ats_seed/s25_census/s27_adjudication.jsonl"

# wired labels → company-name keys (from the live watch config)
_WIRED_COMPANY_NAMES: set[str] = set()

# China markers in an hq string (city/country words)
_HQ_CN = re.compile(
    r"china|shenzhen|beijing|shanghai|hangzhou|guangzhou|chengdu|"
    r"suzhou|nanjing|wuhan|xiamen|tianjin|chongqing|hefei|changsha|"
    r"qingdao|dalian|ningbo|wuxi|dongguan|foshan|zhuhai|zhengzhou|"
    r"shenyang|harbin|kunming|guiyang|fuzhou|jinan|changzhou|nantong|"
    r"jiaxing|shaoxing|mianyang|huzhou|luoyang|shijiazhuang|"
    r"[\u4e00-\u9fff]", re.I)

# notes/hq phrases that indicate REAL China operations for a US-hq
# company (the gray class)
_CN_OPS_PHRASES = (
    "cn entity", "china entity", "cn team", "china team",
    "chinese team", "cn office", "china office", "shanghai office",
    "beijing office", "shenzhen office", "hangzhou office",
    "china-remote", "cn-remote", "remote-first", "china ops",
    "suzhou", "shanghai", "beijing", "shenzhen", "hangzhou",
    "china-registered", "wfoe",
)

# ATS kind detection from a board URL / spec / markers
_URL_KIND = [
    (re.compile(r"(?:job-boards|boards)\.greenhouse\.io/embed/([^/?#]+)", re.I),
     "greenhouse"),
    (re.compile(r"job-boards\.greenhouse\.io/([^/?#]+)", re.I), "greenhouse"),
    (re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.I), "ashby"),
    (re.compile(r"jobs\.lever\.co/([^/?#]+)", re.I), "lever"),
    (re.compile(r"apply\.workable\.com/([^/?#]+)", re.I), "workable"),
    (re.compile(r"(?:[^/]+\.)?myworkdayjobs\.com/([^/?#]+)", re.I),
     "workday"),
    (re.compile(r"recruiting\.paylocity\.com/([^/?#]+)", re.I), "paylocity"),
    (re.compile(r"boards\.eu\.lever\.co/([^/?#]+)", re.I), "lever"),
    (re.compile(r"(?:[^/]+\.)?feishu\.cn/hire/([^/?#]+)", re.I), "feishuhire"),
    (re.compile(r"(?:[^/]+\.)?moka\.com", re.I), "moka"),
    (re.compile(r"(?:[^/]+\.)?smartrecruiters\.com/([^/?#]+)", re.I),
     "smartrecruiters"),
    (re.compile(r"(?:[^/]+\.)?icims\.com", re.I), "icims"),
]

# adapter classes we can wire TODAY (46-class library)
_SUPPORTED = {
    "greenhouse", "ashby", "lever", "workable", "feishuhire", "paylocity",
    "workday", "adp", "jazzhr", "radancy", "teamtailor", "rippling",
    "breezy", "jobvite", "oraclehcm", "bamboohr", "j2w", "ultipro",
    "talentadore", "workstream", "ttiproxy", "sanity", "wpjobboard",
    "wuxibio", "antintl", "greenland", "jereh", "autelenergy", "aden",
    "mandarinoriental", "wuxiapptec", "blacksesame", "ecovacsus",
    "accutar", "hitgen", "insilico", "orbbec", "visionnav", "verisilicon",
    "uniview", "dify",
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _wired_keys() -> set[str]:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    keys = set()
    for w in cfg["watches"]:
        for nm in [w.get("company", "")] + (w.get("li_variants") or []):
            keys.add(_slug(nm))
        keys.add(w["label"].removesuffix("_us_fulltime"))
        # token-level containment (alibaba-group ↔ alibaba)
        for tok in re.split(r"[^a-z0-9]+",
                            (w.get("company") or "").lower()):
            if len(tok) > 3:
                keys.add(tok)
    return keys


# generic company-word tokens — never sufficient for a wired match
_GENERIC_TOKENS = frozenset("""
industries industrial laboratories labs laboratory pharmaceutical
international innovations innovation technology technologies
holdings global worldwide group america american usa systems
science sciences biotech biosciences games robotics energy pharma
therapeutics medicines healthcare networks solutions software
hardware electronics appliances motors automotive electric
international corporation incorporated company companies
imaging biologics diagnostics medical medicine health data
cloud digital media internet finance financial bank banking
capital logistics supply trading commerce retail brand brands
solar battery batteries power automation instruments devices
medical diagnostics clinics research development services
""".split())


def _slug_in_wired(slug: str, wired: set[str]) -> bool:
    if slug in wired:
        return True
    toks = [t for t in slug.split("-")
            if len(t) > 3 and t not in _GENERIC_TOKENS]
    return any(t in wired for t in toks)


def _origin_verdict(rec: dict) -> tuple[str, str]:
    """(verdict, citation) per D-S27-1."""
    origin = rec.get("origin")
    hq = str(rec.get("hq") or "")
    notes = str(rec.get("notes") or "") + " " + str(
        rec.get("us_signal") or "")
    if origin == "china":
        m = "china_hq" if _HQ_CN.search(hq) else "china_ops"
        return m, f"census origin=china; hq={hq!r}"
    # us_chinese_founder
    if _HQ_CN.search(hq):
        return "china_hq", f"origin=us_chinese_founder but hq is " \
                           f"China-sited: {hq!r}"
    blob = (hq + " " + notes).lower()
    if any(p in blob for p in _CN_OPS_PHRASES):
        return "gray_review", \
            f"US hq {hq!r} + China-ops phrases in notes"
    return "us_only_out", \
        f"US/foreign hq {hq!r}; founder-descent only (D-S27-1)"


def _board_surface(rec: dict, probe: dict | None) -> tuple[str, str]:
    """(surface_class, citation) — probe candidates + ats_hint."""
    hint = str(rec.get("ats_hint") or "")
    if ":" in hint:
        kind, org = hint.split(":", 1)
        if kind in _SUPPORTED:
            return f"wire:{kind}:{org}", f"ats_hint {hint!r}"
        return f"surface_unsupported:{kind}", f"ats_hint {hint!r}"
    if not probe:
        return "no_surface", "no probe evidence file"
    for cand in probe.get("candidates") or []:
        url = str(cand.get("url") or "")
        spec = str(cand.get("spec") or "")
        status = int(cand.get("status") or 0)
        for text in (spec, url):
            for rx, kind in _URL_KIND:
                m = rx.search(text)
                if m and status in (200, 0):
                    org = m.group(1) if m.groups() else ""
                    if kind in _SUPPORTED:
                        return (f"wire:{kind}:{org}",
                                f"probe candidate {url} ({kind})")
                    return (f"surface_unsupported:{kind}",
                            f"probe candidate {url}")
    return "surface_unsupported" if probe.get("candidates") \
        else "no_surface", "probe has no live candidates"


def main() -> int:
    recs = [json.loads(l) for l in CENSUS.read_text(encoding="utf-8")
            .splitlines() if l.strip() and not l.strip().startswith("***")]
    probes = {f.stem: json.loads(f.read_text(encoding="utf-8"))
              for f in PROBE.glob("*.json")} if PROBE.exists() else {}
    wired = _wired_keys()

    out = []
    counts: dict[str, int] = {}
    wire_list = []
    for rec in recs:
        name = rec.get("name") or ""
        slug = _slug(name)
        verdict, cite = _origin_verdict(rec)
        probe = probes.get(slug)
        surface, scite = _board_surface(rec, probe)
        is_wired = _slug_in_wired(slug, wired) or any(
            _slug_in_wired(_slug(a), wired)
            for a in (rec.get("aliases") or []))
        entry = {
            "name": name, "slug": slug, "segment": rec.get("segment"),
            "origin_field": rec.get("origin"), "verdict": verdict,
            "origin_citation": cite, "surface": surface,
            "surface_citation": scite, "already_wired": is_wired,
            "ts": "2026-10-07",
        }
        out.append(entry)
        key = ("wired" if is_wired else verdict)
        counts[key] = counts.get(key, 0) + 1
        if verdict.startswith(("china_", "cn_")) and not is_wired \
                and surface.startswith("wire:"):
            wire_list.append(entry)

    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n",
        encoding="utf-8")
    print(f"[adjudication] {len(out)} records -> {OUT.name}")
    print("counts:", json.dumps(counts, indent=1))
    print(f"\nWIRE LIST (qualifying + unwired + supported board): "
          f"{len(wire_list)}")
    for w in wire_list:
        print(f"  {w['slug']:36s} {w['surface']:32s} {w['verdict']}")
    grays = [r for r in out if r["verdict"] == "gray_review"]
    print(f"\nGRAY REVIEW (needs a live look): {len(grays)}")
    for g in grays[:20]:
        print(f"  {g['slug']:36s} {g['origin_citation'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
