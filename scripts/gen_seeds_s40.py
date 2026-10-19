#!/usr/bin/env python3
"""S40: generate the company_seeds.py S40 fold section from
audit/s40/fold-ready.json, applying the DESIGN-S40 v2 resolutions
(flips, variant resolutions, drops, division re-derivation).

Appends a literal set section to jobsearch/company_seeds.py (idempotent:
refuses to append twice) and recomputes the *_KEYS both-forms sets.
"""
import json
import re
from pathlib import Path

REPO = Path("/home/z/jsr")
FOLD = json.load(open(REPO / "audit/s40/fold-ready.json"))
TARGET = REPO / "ingest/jobsearch/company_seeds.py"

src = TARGET.read_text()
if "S40 fold section" in src:
    raise SystemExit("S40 section already present — refusing to double-append")

tech = sorted(set(FOLD["fold"]["TECH_SEED"]))
nontech = sorted(set(FOLD["fold"]["NONTECH_SEED"]))
bank = sorted(set(FOLD["fold"]["BANK_SEED"]))
payer = sorted(set(FOLD["fold"]["PAYER_SEED"]))
staff = sorted(set(FOLD["fold"]["STAFF_BRANDS"]))

# ── v2 resolutions ──────────────────────────────────────────────────
# A6/F8 + A5/F7 flips and variant resolutions
flip_tech = {"dell", "dell-technologies", "hewlett-packard-enterprise",
             "tp-link", "samsung"}
tech = sorted(set(tech) | flip_tech)
nontech = sorted(set(nontech) - flip_tech)
bank = sorted(set(bank) - {"zip-co-limited"})          # D-S40-3
tech = sorted(set(tech) | {"ripple", "mercury"})        # A8 guard removal
staff = [k for k in staff if k]                         # hygiene

DIVISION_S40 = {
    # A5: re-derived from actual company+title row text (review F5)
    "samsung": ["mobile payment"],  # only if samsung stayed NONTECH — it
    # didn't (F8 flip) — kept for tp-link-usa-corp family safety, inert.
    "hp": ["software product", "hp solutions"],
    "epson": ["subscription program"],
    "logitech": ["l4b"],
    "pure-storage": ["data management"],
    "northrop-grumman": ["software product", "sw product",
                         "agile product owner"],
    "rockwell-automation": ["plex", "data integration", "ai product owner"],
    "marathon-petroleum": ["cybersecurity", "fusion center"],
    "mckesson": ["analytics"],
    "pga-tour": ["shotlink"],
    "pearson": ["technical product"],
    "pilot-thomas-logistics": ["onshape", "cpq"],
    "3m": ["ecommerce", "digital"],
    "applied-materials": ["enterprise ai"],
    "intuitive-surgical": ["downstream software", "digital solutions"],
    "getinge": ["act digital", "digital solutions"],
    "caesars-entertainment": ["poker"],
    "hard-rock-international": ["casino games", "game producer"],
    "7-eleven": ["digital product management"],
    "autozone": ["b2c"],
    "hilton": ["digital"],
    "avis-budget-group": ["web and ai"],
}


def setlit(name, keys, comment):
    lines = [f"{name} = {{  # {comment} ({len(keys)} keys)"]
    for i in range(0, len(keys), 4):
        lines.append("    " + ", ".join(repr(k) for k in keys[i:i + 4]) + ",")
    lines.append("}")
    return "\n".join(lines)


def dictlit(name, d, comment):
    lines = [f"{name} = {{  # {comment} ({len(d)} companies)"]
    for k, v in d.items():
        lines.append(f"    {k!r}: {v!r},")
    lines.append("}")
    return "\n".join(lines)


section = f'''

# ════════════════════════════════════════════════════════════════════
# S40 fold section (DESIGN-S40 v2 — audit/s40/DESIGN-S40.md; generated
# by scripts/gen_seeds_s40.py from audit/s40/fold-ready.json over the
# 6 triage waves: TAIL-A 801 / TAIL-B 543 / BANK-159 / MIDA 184 /
# MIDB1 93 / MIDB2 92. Confidence-gated: high+med only; 291 low/UNKNOWN
# deliberately left pending. v2 resolutions: OEM flips (dell family,
# hpe, tp-link, samsung → TECH per F7/F8 — row evidence; hp/epson/
# logitech/pure-storage stay NONTECH with division rescues), zip-co-
# limited dropped from BANK (D-S40-3), ripple+mercury guard-removal
# (A8), STAFF_SEED as slug keys (A3/F3 — NOT the display-name regex).
# ════════════════════════════════════════════════════════════════════

{setlit("TECH_SEED_S40", tech, "S40 TECH fold + flips")}
{setlit("NONTECH_SEED_S40", nontech, "S40 NONTECH fold minus flips")}
{setlit("BANK_SEED_S40", bank, "S40 BANK fold minus zip-co-limited")}
{setlit("PAYER_SEED_S40", payer, "S40 PAYER fold")}
{setlit("STAFF_SEED_S40", staff, "S40 staffing/agency fold — companyKey slugs (A3)")}
{dictlit("DIVISION_ALLOWLIST_S40", DIVISION_S40, "S40 division rescues — re-derived row text (A5)")}

# merges (precedence unchanged: TECH first in sector_verdict)
TECH_SEED = TECH_SEED | TECH_SEED_S40
NONTECH_SEED = NONTECH_SEED | NONTECH_SEED_S40
BANK_SEED = BANK_SEED | BANK_SEED_S40
PAYER_SEED = PAYER_SEED | PAYER_SEED_S40
FINTECH_GUARD = FINTECH_GUARD - {{"ripple", "mercury"}}  # A8 explicit
DIVISION_ALLOWLIST_ADD.update(DIVISION_ALLOWLIST_S40)

# recompute the both-forms key spaces over the merged seeds
TECH_KEYS = _both_forms(TECH_SEED)
NONTECH_KEYS = _both_forms(NONTECH_SEED)
BANK_KEYS = _both_forms(BANK_SEED)
FINTECH_KEYS = _both_forms(FINTECH_GUARD)
PAYER_KEYS = _both_forms(PAYER_SEED)
STAFF_KEYS = _both_forms(STAFF_SEED_S40)


def staff_seed_hit(company_key: str, company_str: str) -> bool:
    """A3/F3: the S40 agency fold matches on key/slug/name identity
    (the display-name STAFF_BRANDS regex cannot see slug keys)."""
    return seed_hit(company_key, company_str, STAFF_KEYS)
'''

TARGET.write_text(src + section)
print(f"appended S40 section: TECH +{len(tech)}, NONTECH +{len(nontech)}, "
      f"BANK +{len(bank)}, PAYER +{len(payer)}, STAFF +{len(staff)}, "
      f"division entries +{len(DIVISION_S40)}")
