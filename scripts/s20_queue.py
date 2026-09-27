#!/usr/bin/env python3
"""S20-D queue builder — the probe/wire priority queue from master_census v3.

Three modes:
  candidates  — census -> excluded/collapsed candidate JSONL (pre-LLM)
  finalize    — candidates + LLM verdicts -> s20_queue.json (ranked, tiered)

The queue tiering:
  WIRE-NOW   : verified chinese-origin + valid ATS board spec in hand
  PROBE-A    : verified chinese-origin + valid LCA evidence (US hiring real,
               careers surface unknown) -> s20_surface_probe.py
  PROBE-B    : verified chinese-origin, no LCA (brand-only: Temu/Manus class)
  REJECTED   : LLM-rejected (MNC arms, token collisions, non-CN origin)

Exclusions (pre-LLM, structural):
  - roster-covered (the 30 wired companies + brand aliases)
  - suspect_mnc_arm flag / Category:Chinese subsidiaries of foreign companies
  - person/noise wiki categories

Collapse: token-prefix families (JD.com / JD Health -> one JD record; the
US-hiring entity is shared). Family representative = max-evidence member.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CENSUS = REPO / "ingest/data/ats_seed/s20_census/master_census.json"
OUTDIR = REPO / "ingest/data/ats_seed/s20_census"
CAND = OUTDIR / "queue_candidates.jsonl"
VERDICTS = OUTDIR / "queue_verdicts.jsonl"
QUEUE = OUTDIR / "s20_queue.json"

# --- roster: DERIVED from the watch config at runtime (companies +
# li_variants — the wired set is the config, never a stale copy) ---
WATCH_CFG = (REPO / "ingest/data/board_watch/config.json")


def _load_roster() -> set[str]:
    import json as _json
    base = {"tiktok", "douyin", "toutiao", "taobao", "tmall", "lazada",
            "aliyun", "alibaba cloud", "ernie bot", "netease games",
            "mihoyo", "genshin impact", "moonshot ai", "kimi", "vidu",
            "horizon robotics", "rednote", "tplink", "tp link", "pony ai",
            "pony.ai", "ctrip", "united imaging healthcare", "aizip",
            "nvidia", "netflix", "openai", "anthropic", "haier", "gea"}
    try:
        cfg = _json.loads(WATCH_CFG.read_text(encoding="utf-8"))
        for w in cfg.get("watches", []):
            base.add((w.get("company") or "").lower())
            for v in w.get("li_variants") or []:
                base.add(str(v).lower())
    except Exception:
        pass
    return base


ROSTER = _load_roster()

# wiki noise categories (person articles, non-company scopes)
NOISE_CAT_RE = re.compile(
    r"people|persons|articles|templates|families|album|song|film|"
    r"software|video games|brands|products|fictional|defunct web|"
    r"mass media|newspapers|television|radio|music|bands|"
    r"diplomatic missions|government ministries|"
    r"universities and colleges|schools|hospitals|museums|"
    r"political parties|secretariats|legislatures|"
    r"lawsuits|litigation|legal cases|court cases|disputes",
    re.I,
)

# article names that are NOT companies (court cases like "360 v. Tencent",
# device models like "Honor 10" / "Honor 30 Pro")
NOISE_NAME_RE = re.compile(r"^\S+\s+v\.?\s+\S+", re.I)
DEVICE_MODEL_RE = re.compile(
    r"\b\d+\b|\b(pro|plus|max|ultra|lite|mini|se)\b", re.I)

LEGAL_TAIL = re.compile(
    r"\b(incorporated|inc|ltd|limited|corp|corporation|co|company|"
    r"plc|llc|group|holding|holdings|holding[s]? (?:group|company)?)\b\.?$",
    re.I,
)


def raw_tokens(name: str) -> list[str]:
    """Tokenize WITHOUT stripping legal tails (the prefix-collapse
    comparator: 'Legend Holdings' vs 'Legend Biotech' differ here)."""
    s = name.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().split()


def norm_tokens(name: str) -> list[str]:
    s = name.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    toks = s.split()
    while toks and LEGAL_TAIL.search(" ".join(toks[-1:])):
        toks = toks[:-1]
    return toks


def is_prefix_family(a: list[str], b: list[str]) -> bool:
    """True when one token list is a prefix of the other (>=1 token) or
    they share the first two tokens. S20 review fix: callers pass RAW
    (un-stripped) tokens so legal tails disambiguate — "Legend
    Holdings" ["legend","holdings"] vs "Legend Biotech" ["legend",
    "biotech"] share only token 1 and do NOT merge (they are different
    companies: the HK conglomerate vs the NASDAQ biotech)."""
    if not a or not b:
        return False
    n = min(len(a), len(b))
    if n == 1:
        # single-token overlap: only merge when BOTH sides are
        # single-token (JD.com / JD.com, Inc. after legal-tail strip is
        # handled by norm; raw single tokens = same short brand)
        return len(a) == 1 and len(b) == 1
    if a[:n] == b[:n]:
        return True
    if len(a) >= 2 and len(b) >= 2 and a[:2] == b[:2]:
        return True
    return False


def evidence_score(x: dict) -> int:
    lca = x.get("lca") or {}
    ats = x.get("ats") or []
    jobs = max((a.get("job_count") or 0) for a in ats) if ats else 0
    return (lca.get("filings") or 0) * 3 + jobs


def roster_hit(name: str) -> str | None:
    toks = norm_tokens(name)
    n2 = " ".join(toks[:2])
    n1 = toks[0] if toks else ""
    for alias in ROSTER:
        if n2 == alias or n1 == alias:
            return alias
        # brand-in-name (e.g. "Tencent Music Entertainment" -> tencent)
        if alias in n2 and len(alias) > 4:
            return alias
    return None


def candidates():
    census = json.load(open(CENSUS))
    comps = census["companies"]
    out, skipped = [], {"roster": 0, "mnc": 0, "noise": 0, "no_evidence": 0}

    for x in comps:
        name = x["name"]
        lca, ats = x.get("lca"), x.get("ats")
        if not lca and not ats:
            skipped["no_evidence"] += 1
            continue
        # roster exclusion
        hit = roster_hit(name)
        if hit:
            skipped["roster"] += 1
            continue
        # MNC arm exclusion
        cat = x.get("category") or ""
        flags = set(x.get("flags") or [])
        if "suspect_mnc_arm" in flags or re.search(
                r"subsidiaries of foreign", cat, re.I):
            skipped["mnc"] += 1
            continue
        # noise categories
        if NOISE_CAT_RE.search(cat):
            skipped["noise"] += 1
            continue
        # court-case article names ("360 v. Tencent" class)
        if NOISE_NAME_RE.search(name):
            skipped["noise"] += 1
            continue
        # device-model articles ("Honor 10"/"Honor 30 Pro" class — wiki
        # writes an article per phone model; the brand article is the
        # company record)
        toks0 = norm_tokens(name)
        if len(toks0) >= 2 and DEVICE_MODEL_RE.search(name):
            skipped["noise"] += 1
            continue
        out.append(x)

    # --- prefix-collapse into families (RAW tokens: legal tails kept) ---
    fams: list[list[dict]] = []
    for x in out:
        toks = raw_tokens(x["name"])
        placed = False
        for fam in fams:
            rep_toks = raw_tokens(fam[0]["name"])
            if is_prefix_family(toks, rep_toks):
                fam.append(x)
                placed = True
                break
        if not placed:
            fams.append([x])

    merged = []
    for fam in fams:
        rep = max(fam, key=evidence_score)
        merged.append({
            "name": rep["name"],
            "family": sorted({m["name"] for m in fam}),
            "sources": sorted({s for m in fam for s in m.get("sources", [])}),
            "category": rep.get("category") or "",
            "lca": rep.get("lca"),
            "lca_employers": sorted({(m.get("lca") or {}).get("employer")
                                     for m in fam if m.get("lca")} - {None}),
            "ats": rep.get("ats") or [],
            "n_family": len(fam),
        })

    merged.sort(key=lambda x: -evidence_score(x))
    with open(CAND, "w") as f:
        for m in merged:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"candidates: {len(merged)} (skipped {skipped}) -> {CAND}")


def finalize():
    cands = [json.loads(l) for l in open(CAND) if l.strip()]
    ver = {}
    if VERDICTS.exists():
        for l in open(VERDICTS):
            if l.strip():
                v = json.loads(l)
                ver[v["name"]] = v

    wire, probeA, probeB, rejected, unverified = [], [], [], [], []
    seen_brands = set()  # post-verdict dedupe by LLM brand (Honor-10 class)
    for c in cands:
        v = ver.get(c["name"])
        if v is None:
            unverified.append(c["name"])
            continue
        if not v.get("chinese_origin"):
            rejected.append({**c, "verdict": v})
            continue
        brand = (v.get("brand") or c["name"]).strip()
        # BRAND-CONSISTENCY GATE (the Noah-Holdings->"Noah Medical"
        # hallucination class): the LLM's brand must be a token-variant
        # of the census name or a family member; a freestanding
        # different-company brand goes to MANUAL adjudication, never a
        # silent wire
        btoks = set(norm_tokens(brand)) | set(raw_tokens(brand))
        ntoks = set(norm_tokens(c["name"])) | set(raw_tokens(c["name"]))
        fam_toks = set()
        for fam_name in c.get("family") or []:
            fam_toks |= set(norm_tokens(fam_name)) | set(raw_tokens(fam_name))
        if not (btoks & (ntoks | fam_toks)):
            rejected.append({**c, "verdict": v,
                             "reject_reason": "brand-inconsistent "
                             f"(LLM brand '{brand}' shares no token with "
                             f"'{c['name']}' family — hallucination risk)"})
            continue
        # brand-level roster check (the "360 v. Tencent" lesson: a
        # lawsuit article whose LLM brand is a roster company)
        if roster_hit(brand):
            rejected.append({**c, "verdict": v,
                             "reject_reason": "brand roster-covered"})
            continue
        if brand.lower() in seen_brands:
            continue  # brand already queued from a richer family record
        seen_brands.add(brand.lower())
        # the LLM's ats_valid is the gate: a token-collision board
        # (JD.com -> jdsports) must NOT become a wire. Even an accepted
        # verdict is only PRE-verdict — the probe stage confirms every
        # wire board by its self-reported company name (live fetch).
        ats_entry = None
        if v.get("ats_valid") and c.get("ats"):
            ats_entry = c["ats"][0]
        rec = {
            "name": c["name"], "family": c["family"],
            "sector": v.get("sector"), "brand": brand,
            "note": v.get("note"),
            "lca": c.get("lca") if v.get("lca_valid") else None,
            "lca_employers": c["lca_employers"] if v.get("lca_valid") else [],
            "ats": c.get("ats") if ats_entry else [],
            "evidence": "lca+ats" if (c.get("lca") and v.get("lca_valid")
                                      and ats_entry) else
                        ("lca" if c.get("lca") and v.get("lca_valid")
                         else "ats"),
        }
        if ats_entry:
            wire.append(rec)
        elif c.get("lca") and v.get("lca_valid"):
            probeA.append(rec)
        else:
            probeB.append(rec)

    def rank_key(r):
        lca = r.get("lca") or {}
        jobs = max((a.get("job_count") or 0) for a in r.get("ats") or [{}]
                   ) if r.get("ats") else 0
        return -((lca.get("filings") or 0) * 3 + jobs)

    # --- probe feedback loop (the review #10 fix): probe/*.json verdicts
    # demote their brands in the queue — a queue consumer must never
    # wire an identity-refuted board even if the LLM accepted it
    PROBE_DIR = OUTDIR / "probe"
    probe_verdicts = {}
    if PROBE_DIR.exists():
        for f in PROBE_DIR.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            b = d.get("brand") or ""
            if not b:
                continue
            verdict = d.get("verdict") or d.get("err") or ""
            manual = str(d.get("manual_adjudication") or "")
            if verdict == "mismatch" or "REFUTED" in manual:
                probe_verdicts[b] = "identity_refuted"
            elif verdict == "dict_collision":
                probe_verdicts[b] = "dict_collision"
            elif verdict in ("ats_surface", "custom_surface",
                             "marker_surface"):
                probe_verdicts[b] = "surface_found"
    demoted = []
    for tier_name in ("wire_now",):
        kept = []
        for r in wire:
            pv = probe_verdicts.get(r["brand"])
            if pv in ("identity_refuted", "dict_collision"):
                r["probe_verdict"] = pv
                demoted.append(r)
            else:
                r["probe_verdict"] = pv
                kept.append(r)
        wire = kept
    for r in probeA + probeB:
        r["probe_verdict"] = probe_verdicts.get(r["brand"])

    wire.sort(key=rank_key)
    probeA.sort(key=rank_key)
    probeB.sort(key=rank_key)
    queue = {
        "built": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {
            "wire_now": len(wire), "probe_A": len(probeA),
            "probe_B": len(probeB), "rejected": len(rejected),
            "unverified": len(unverified),
            "demoted_by_probe": len(demoted),
        },
        "wire_now": wire, "probe_A": probeA, "probe_B": probeB,
        "rejected": rejected, "unverified": unverified,
    }
    with open(QUEUE, "w") as f:
        json.dump(queue, f, indent=1, ensure_ascii=False)
    print(f"queue: {queue['counts']} -> {QUEUE}")
    if unverified:
        print(f"UNVERIFIED ({len(unverified)}): run s20_queue_verify.mjs")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "candidates"
    if mode == "candidates":
        candidates()
    elif mode == "finalize":
        finalize()
    else:
        sys.exit(f"unknown mode {mode} (candidates|finalize)")
