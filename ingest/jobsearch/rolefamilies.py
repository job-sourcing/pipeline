"""rolefamilies.py — the S36 role-keyed campaign taxonomy (D-S36-1).

DESIGN-S36 §2 (v2.1, FINAL-APPROVED) — corpus-tested contracts adopted
from the R2 review (85/247-corpus ambiguity resolved by the
position-aware grammar) + R3's mechanical amendments (re.VERBOSE pin,
the 2-of-4 noun-first FP closure).

Contract surface (consumed by scripts/role_sweep.py):
  classify_family(title, query_is_pm) -> (family, evidence)
      family: 'pm' | 'adjacent' | 'other'
      evidence: 'grammar' | 'qp' (query provenance, L1-L3 bare-PM)
  pm_seniority(title) -> 'director+' | 'senior' | 'mid' | 'junior'
      classify_tier + PM overlay (no fork of experience_level
      semantics — mapping documented in pm_seniority docstring).
  g1_fires(title) -> bool            (R2's amended title markers)
  sector_verdict(...) -> (action, sector_evidence, reason)
      action: 'keep' | 'bucket' | 'pending'
      (G2 tech-evidence-required chain + G3 recoverable bucket)
  title_core(title) -> str           (cross-layer merge key)

All multi-line patterns compile with re.I | re.VERBOSE — pinned in
test_rolefamilies.py (a naive compile matches almost nothing: the
newline between alternatives must be whitespace).
"""
from __future__ import annotations

import re
from typing import Optional

from .categorize import classify_tier
from .dedup import normalize_job_title

# ── §2.1 position-aware family grammar ─────────────────────────────────────
# REDIRECT runs FIRST: "product <function>" adjacency routes to that
# function's family. Function poison is POSITIONAL — never
# anywhere-in-title (R2 tested: global poison kills 5 real PM rows
# whose SPECIALIZATION tail is a finance/security domain).
REDIRECT: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"""\bproduct\s+analys(?:t|ts)\b|\bproduct\s+analytics\b""",
        re.I | re.VERBOSE), "adjacent"),
    (re.compile(
        r"""\bproduct\s+program\s+manager\b|\btechnical\s+program\s+manager\b""",
        re.I | re.VERBOSE), "adjacent"),
    (re.compile(
        r"""
        \bproduct\s*(?:&|and|,)?\s*
        (?:legal|counsel|security|sales|engineering|talent|
           recruit(?:ing|er|ment)|acquisition|design(?:er)?|finance|
           quality|test(?:ing|s)?|safety|integrity|reliability|
           communications|comms|development)\b
        """,
        re.I | re.VERBOSE), "other"),
]

# PMM / product-ops adjacency requires a manager-class head in the
# title (R2 P1-4 closure: "Product Marketing Specialist" and
# "Product Operations Engineer" are other, not adjacent). The
# noun-first form ("Manager, Product Marketing") carries the head on
# the other side of the comma — the _ROLE_HEAD check is
# position-independent for exactly this reason.
_PMM_MARK = re.compile(
    r"""\bproduct\s*(?:&|and|,)?\s*(?:marketing|marketer)\b""",
    re.I | re.VERBOSE)
_OPS_MARK = re.compile(
    r"""\bproduct\s*(?:&|and|,)?\s*(?:operations|ops)\b""",
    re.I | re.VERBOSE)
_ROLE_HEAD = re.compile(
    r"""\b(?:manager|lead|director|head|vp|vice\s+president|marketer)\b""",
    re.I | re.VERBOSE)

# PM CORE: product adjacent to the role head, with abbreviation forms,
# count-noun bridges, noun-first reversal, X-of-product, GPM.
PM_CORE = re.compile(
    r"""
      \b(?:product)\s*,?\s*(?:mgr\b|managers?|management|mgmt\b|owners?|
         leads?|directors?|gm\b|general\s+managers?)
    | \bproduct\s+(?:line|segment|platform|portfolio)\s+manager\b
    | \b(?:managers?|directors?|heads?|presidents?|vp|vice\s+president|leads?)\s*,?\s*
        (?:of\s+)?\s*product(?:\s+management)?\b
    | \b(?:director|vp|vice\s+president|head)\s+of\s+(?:product|pm)\b
    | \b(?:chief\s+product\s+officer|cpo)\b
    | \bGPM\b|\bgroup\s+product\s+manager\b
    """,
    re.I | re.VERBOSE)

# Noun-first FP closure (R3): (a) GRC/governance/risk-head or
# Program-head titles are NOT PM heads; (b) the comma-phrase after a
# noun-first head must terminate as product( management)?( numeral)?
# — an "&|and"-continuation chain is a different family.
_HEAD_DENY = re.compile(
    r"""^\s*(?:grc|governance|risk|program|administrative\s+assistant|
        specialist|sr\.?\s+specialist)\b""",
    re.I | re.VERBOSE)
_NOUN_FIRST_CHAIN = re.compile(
    r"""\b(?:managers?|directors?|heads?|vp|vice\s+president|leads?)\s*,\s*
        product\s*(?:&|and)\s*\w+""",
    re.I | re.VERBOSE)

# Bare-PM (R2 P0-2): only via query provenance (L1-L3 cards).
_BARE_PM = re.compile(
    r"""\b(?:[A-Z][a-z]*\s)?(?:Sr\.?|Jr\.?|Staff|Principal|Distinguished|
        Group|Associate|Senior)?\s*PM\b(?:\s+(?:I{1,3}|IV|V))?\b""",
    re.I | re.VERBOSE)

# Adjacent-set closure (R2 P1-4): role nouns required — the loose
# "product marketing" form leaks Specialist/Engineer/GRC rows.
_ADJACENT_CLOSED = re.compile(
    r"""
      \bproduct\s+marketing\s+(?:manager|lead|director|marketer)\b
    | \bhead\s+of\s+product\s+marketing\b
    | \bproduct\s+marketing\s+assistant\s+manager\b
    | \bproduct\s*(?:&|and|,)?\s*(?:operations|ops)\s+(?:manager|lead|director)\b
    | \bproduct\s+analys(?:t|ts)\b
    | \b(?:product\s+program\s+manager|technical\s+program\s+manager)\b
    """,
    re.I | re.VERBOSE)


def classify_family(title: str,
                    query_is_pm: bool = False) -> tuple[str, str]:
    """family ∈ {pm, adjacent, other}; evidence ∈ {grammar, qp}.

    query_is_pm: the row's sweep query was a PM-family variant
    (§2.5 core list) — enables the bare-PM query-provenance rule for
    L1-L3 cards only (L0 census: zero bare-PM rows — do not set it).
    """
    if not isinstance(title, str) or not title.strip():
        return "other", "grammar"
    # 1. PMM / product-ops adjacency (role-head required)
    if _PMM_MARK.search(title):
        return ("adjacent" if _ROLE_HEAD.search(title) else "other"), "grammar"
    if _OPS_MARK.search(title):
        return ("adjacent" if _ROLE_HEAD.search(title) else "other"), "grammar"
    # 2. redirects (positional function-adjacency)
    for pat, fam in REDIRECT:
        if pat.search(title):
            return fam, "grammar"
    # 3. head-denies (R3 noun-first FP closure, part a)
    if _HEAD_DENY.search(title):
        return "other", "grammar"
    # 4. noun-first "&|and"-chain (R3 closure, part b)
    if _NOUN_FIRST_CHAIN.search(title):
        return "other", "grammar"
    # 5. adjacent closed set BEFORE core (TPM/analyst forms that
    #    would otherwise fall through)
    if _ADJACENT_CLOSED.search(title):
        return "adjacent", "grammar"
    # 6. PM core
    if PM_CORE.search(title):
        return "pm", "grammar"
    # 7. bare-PM via query provenance (L1-L3 only)
    if query_is_pm and _BARE_PM.search(title):
        return "pm", "qp"
    return "other", "grammar"


# ── §2.3 seniority — classify_tier + PM overlay ────────────────────────────

def pm_seniority(title: str) -> str:
    """director+ | senior | mid | junior.

    Built as categorize.classify_tier + a thin PM overlay (R2 P0-3: no
    silent fork of experience_level semantics). Mapping to the shipped
    tiers (documented, not forked): director+ ↔ classify_tier 'senior'
    band above senior-IC; junior ↔ 'entry'+'intern' (the intern→junior
    fold is PINNED here). Overlay fixes classify_tier's GPM/GM/numeral
    gaps — the same 3 rows R2 measured it mis-tiering.
    """
    if not isinstance(title, str) or not title.strip():
        return "mid"
    t = title
    if re.search(
            r"\b(?:chief\s+product\s+officer|cpo|president,?\s*product)\b",
            t, re.I):
        return "director+"                     # exec band
    if re.search(r"\b(?:vp|vice\s+president)\b", t, re.I):
        return "director+"
    if re.search(r"\bdirector\b|\bhead\s+of\b", t, re.I):
        return "director+"                     # Associate Director guard
    if re.search(r"\bGM\b|\bgeneral\s+manager\b", t):
        return "director+"                     # Product GM (airwallex row)
    if re.search(
            r"\b(?:distinguished|principal|staff|group\s+product\s+manager|"
            r"group\s+pm|gpm)\b", t, re.I):
        return "senior"                        # GPM + distinguished
    if re.search(r"\b(?:senior|sr\.?)\b", t, re.I):
        return "senior"
    if re.search(r"\b[a-z]{2,}[\s-](?:iii|iv|v)\b", t, re.I):
        return "senior"                        # numeral III parity
    if re.search(
            r"\b(?:product|pm)\s+lead\b|\blead,?\s*(?:product|pm)\b|"
            r"\blead\s+product\b", t, re.I):
        return "senior"                        # bare-lead pin
    if re.search(r"\b[a-z]{2,}[\s-]i\b|\b(?:l1|l2)\b", t, re.I):
        return "junior"                        # numeral I parity
    if re.search(
            r"\b(?:junior|jr\.?|entry(?:\s+level)?|intern(?:ship)?|apm|"
            r"new\s+grad|graduate(?:\s+(?:program|scheme))?)\b", t, re.I):
        return "junior"                        # intern folds to junior
    if re.search(r"\bassociate\b", t, re.I):
        return "junior"                        # APM junior
    return "mid"


# ── §2.2 the non-tech-PM exclusion gates ───────────────────────────────────

# G1 (R2 amended — fintech/app-store/adtech FPs fixed, tested 11/11)
G1 = re.compile(
    r"""
      \b(?:grocery|categor(?:y|ies)|merchandis\w*|private\s+label|planogram|
         shelf|cpg|f&b|apparel|e-?commerce\s+merchandising)\b
    | \bretail\b(?!\s+(?:banking|bank|finance|financial|fintech|services|
         tech|technology|media|commerce|partnerships))
    | \b(?:store|brand)\s+(?:operations?|clusters?|formats?|manager|management)\b
    | \bbrand\s+(?:manager|management)\b
    | \bskus?\s+rationaliz\w*|\bsku\s+count\b
    """,
    re.I | re.VERBOSE)


def g1_fires(title: str) -> bool:
    return bool(isinstance(title, str) and G1.search(title))


# G2 evidence: STRONG ≥1 or WEAK ≥2, AND zero G1 markers in the JD.
_JD_STRONG = re.compile(
    r"""\b(?:roadmap|user\s+stories|a/?b\s+test\w*|apis?|saas|sprint\w*)\b""",
    re.I | re.VERBOSE)
_JD_WEAK = re.compile(
    r"""\b(?:stakeholders?|platform|okrs?|cross-functional|agile|scrum)\b""",
    re.I | re.VERBOSE)

# G3: v1 seed = pure-grocery/retail chains ONLY (grown from G1-hit
# triage per D-S36-1). Division examples documented, added when the
# companies actually surface.
G3_GROCERY_SEED = [
    "albertsons", "ahold delhaize", "publix", "wegmans", "lidl", "aldi",
    "tesco", "carrefour",
]
DIVISION_ALLOWLIST: dict[str, list[str]] = {
    # company (normalized substr) → division patterns in the org string
    "walmart": ["global tech", "walmart connect"],
    "target": ["roundel", "target tech", "technology"],
    "kroger": ["84.51", "kroger technology"],
    "nike": ["nike digital"],
}


def _norm_company(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def sector_verdict(
    title: str,
    company_str: str,
    jd_text: str = "",
    company_key: Optional[str] = None,
) -> tuple[str, str, str]:
    """G2 (tech-evidence-required) + G3 (recoverable bucket).

    Returns (action, sector_evidence, reason):
      action 'keep'    — evidence found (division > jd) or no deny match
      action 'pending' — no sector evidence AND not G3-deniable: the row
                         ships flagged, never silently dropped
      action 'bucket'  — G3-denied with no division/JD rescue → the
                         recoverable review bucket (dropped-by-nontech.csv)
    sector_evidence: 'division' | 'jd' | '' (blank = pending review)
    """
    org = _norm_company(company_str)
    # G3 deny match? (company substring in the grocery seed or the
    # division-allowlist keys)
    deny_key = None
    for seed in G3_GROCERY_SEED:
        if seed in org:
            deny_key = seed
            break
    if deny_key is None:
        for comp in DIVISION_ALLOWLIST:
            if comp in org:
                deny_key = comp
                break
    if deny_key is None:
        # not deniable: G2 evidence optional (roster/neutral policy —
        # company membership is NOT tech evidence, D-S36-1)
        if jd_text and _JD_STRONG.search(jd_text) and not g1_fires(jd_text):
            return "keep", "jd", "jd_strong_marker"
        weak = len(_JD_WEAK.findall(jd_text or "")) if jd_text else 0
        if weak >= 2 and not g1_fires(jd_text or ""):
            return "keep", "jd", "jd_weak_x2"
        return "pending", "", "no_sector_evidence"
    # deniable company: division rescue > JD override > bucket
    for div in DIVISION_ALLOWLIST.get(deny_key, []):
        if div in org:
            return "keep", "division", f"division:{div}"
    if jd_text and _JD_STRONG.search(jd_text) and not g1_fires(jd_text):
        return "keep", "jd", "g3_jd_override"
    return "bucket", "", f"g3_deny:{deny_key}"


# ── §2.4 identity — title_core for the cross-layer merge ───────────────────

def title_core(title: str) -> str:
    """Cross-layer merge key: specialization tail stripped, sr/jr/mgr
    expanded (seniority tokens SURVIVE — senior≠non-senior = different
    req, the #2009 doctrine), pm→product manager (sound ONLY inside
    the pm-family sweep — query-provenance context, never generic).

    The tail split runs on the RAW title BEFORE normalization — the
    repo normalizer erases ALL punctuation (commas/dashes→spaces), so
    a post-normalization split can never fire (found by the test
    suite; R2's spec assumed separator survival)."""
    t = re.split(r"\s+[-–—]\s+|,\s+|\(\s*|\s+—\s*|:\s+", title)[0]
    t = normalize_job_title(t)
    t = re.sub(r"\bsr\.?\b", "senior", t, flags=re.I)
    t = re.sub(r"\bjr\.?\b", "junior", t, flags=re.I)
    t = re.sub(r"\bmgr\b\.?", "manager", t, flags=re.I)
    t = re.sub(r"\bpm\b", "product manager", t)
    return re.sub(r"\s+", " ", t)


# ── §2.5 query census (tiered) ─────────────────────────────────────────────

PM_QUERY_CORE = [
    "product manager", "senior product manager", "technical product manager",
    "group product manager", "director of product", "head of product",
    "product owner",
]
PM_QUERY_TAIL = [
    "vp product", "principal product manager", "associate product manager",
    "junior product manager", "product marketing manager",
    "technical program manager", "product management",
]
PM_QUERY_VARIANTS = PM_QUERY_CORE + PM_QUERY_TAIL
