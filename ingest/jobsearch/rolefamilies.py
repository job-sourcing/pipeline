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
    # S38 F21: noun-first program-mgmt head ("Manager, Product Program
    # Management") must route adjacent BEFORE PM_CORE's noun-first
    # branch short-circuits it (the anthropic PIP-267864 class).
    (re.compile(
        r"""\b(?:managers?|directors?|heads?|leads?)\s*,\s*
            product\s+program\b""",
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


# ── S38 G1B: physical-goods TITLE gate (fp-wave1 §2, TESTED 28 rows /
# 9 classes, 15/15 TP guards) — TITLE-ONLY, never JD/loc: `product
# line` and `instrumentation` are JD-unsafe (routine software-PM
# phrasing). T6 carries the software-PLM guard (F13): the
# platform/cloud/software/saas/api context of a product-line title is
# software portfolio language, not industrial PLM.
G1B = re.compile(
    r"""
      # T1 white-goods / HVAC / trade products
      \b(?:hvac|ductless|rooftop|rtu|dishwasher|robot\s+vacuum|appliance\w*|
          plumbing|abrasives?)\b
      # T2 medical devices / dental / instrumentation (devices&saas exc.)
    | \b(?:crown\s+and\s+bridge|computed\s+tomography|instrumentation|
          ophthalm\w*|orthodont\w*|surgical|dental)\b
    | \bmedical\s+devices?\b(?!\s*(?:&|and|/|\+)\s*
          (?:saas|software|cloud|app|platform))
      # T3 life-science reagents / enzymes
    | \b(?:enzymes?|reagents?|biotech\s+reagents|assay\w*)\b
      # T4 insurance product-line (business product, not software)
    | \binsurance\s+product\s+(?:manager|owner)\b
      # T5 power-generation equipment
    | \b(?:lean\s+burn|gas\s+power\s+generation|microgrid|
          solid[-\s]state\s+transformer|solar\s+plant|cell\s+product|
          genset\w*)\b
      # T6 physical product-line / segment management (software-PLM
      # guard: platform/cloud/software/saas/api context = keep)
    | \bproduct\s+(?:line|segment)\s+(?:manager|management|cost)\b
          (?!.{0,60}\b(?:platform|cloud|software|saas|api)\b)
    | \bdirector\s+of\s+product\s+line\s+management\b
      # T7 bank product-control function (not a PM role)
    | \bproduct\s+control\b
      # T8 talent-community pool postings (not open roles)
    | \b(?:talent\s+community|talent\s+pool)\b
      # T9 pharma commercial marketing
    | \bpatient\s+marketing\b
    """,
    re.I | re.VERBOSE)


def g1b_fires(title: str) -> bool:
    """S38: the physical-goods title gate (title-only; hit rows go to
    the RECOVERABLE bucket for wave-2 re-adjudication — reason
    'g1b_title', recoverable=True — per DESIGN-S38 v2 §3a/F3)."""
    return bool(isinstance(title, str) and G1B.search(title))


# ── S38: the staffing/repost detector (fp-wave1 §3, TESTED 218 rows /
# 155 companies; the naive Inc/LLC/Group suffix pattern over-fires 180
# rows incl. AWS/TransUnion — tokens + brands is the conjunction).
# ^-anchored brand match on the normalized company — known limitation:
# "Digital by Randstad" style inversions don't match (recorded).
STAFF_TOKENS = re.compile(
    r"""\b(?:staffing|recruiting|recruitment|recruiters?|talent|
        personnel|placements?|consulting|consultants?|consultancy|
        executive\s+search)\b""",
    re.I | re.VERBOSE)

STAFF_BRANDS = re.compile(
    r"""^(?:kforce|eliassen|judge\s+group|korn\s+ferry|robert\s+half|
        randstad|genesis10|apex\s+systems|insight\s+global|brooksource|
        actalent|mastech(?:\s+digital)?|mindlance|swoon|optomi|harnham|
        mitchell\s+martin|cypress\s+hcm|mmd\s+services|madison-davis|
        carrera\s+agency|gravity\s+it\s+resources|innovative\s+systems|
        stellent\s+it|vdart|accion\s+labs|techno-comp|ispace|
        innova\s+solutions|snapcode|aptara|hexacorp|merican|lorven|
        inficare|acunor|infovision|acadia\s+technologies|
        atlantic\s+partners|groupa|g3\s+business|kpg99|caler\s+group|
        purple\s+squirrel|established\s+search|outscout|oxenham|
        higher\s+people|hansell\s+tierney|creative\s+people|headfarmer|
        angove|epm\s+scientific|martyn\s+bassett|seattle\s+corporate|
        michael\s+page|kelly\s+services|lhh|akkodis|ledgent|
        new\s+york\s+technology\s+partners|prokatchers|
        pro\s+integrate|burtch\s+works|harrison\s+clarke|tech\s+observer|
        hmg\s+america|agile\s+resources|agility\s+partners|jcw\s+group|
        jobot|epic\s+placements|evona|biotalent|hour\s+consulting|
        jsr\s+tech|calculated\s+hire|critical\s+river|edify\s+tech|
        enfec|fild|echo\s+it|hire\s+it\s+people|hirekeyz|hirewell|
        hire\s+level|software\s+guidance|the\s+sage\s+group|
        the\s+ceres\s+group|the\s+brixton\s+group|widescope|
        strategic\s+resources|summit\s+group\s+solutions|tammina|
        vits\s+consulting|zaddy|peterson\s+technology\s+partners|
        ptr\s+global|pts\s+advance|venture\s+up|dolphin\s+solutions|
        methodhub|peoplentech|logicplanet|newt\s+global|sofratech|
        petadata|mroads|xaxis\s+solutions|theboss\.us|virtual\s+vocations|
        certitude|kimaru|talently|success\s+matcher|motion\s+recruitment|
        motion\s+partners|ispace|randstad\s+digital|sga|
        software\s+guidance\s+&\s+assistance)\b""",
    re.I | re.VERBOSE)


def is_staffing_company(company: str) -> bool:
    """S38: the staffing/repost detector — a FLAG (staffing_evidence
    = 'agency'), never a drop: real employer unknown ≠ not a real PM
    opening (DESIGN-S38 v2 §3a/F11)."""
    c = (company or "").lower().strip()
    return bool(c and (STAFF_TOKENS.search(c) or STAFF_BRANDS.search(c)))


# ── S38: the anonymized-employer detector (fp-wave1 — the
# Confidential/stealth/mangled-name class; flagged, kept; their
# company_keys are EXCLUDED from merge eligibility per F14).
ANON_COMPANY = re.compile(
    r"""^(?:confidential|company\s+confidential|stealth(?:\s+startup)?|
         hidden| undisclosed|anonymous|private\s+company|
         a\s+vinyl\s+bar\s+in\s+shibuya)$""",
    re.I | re.VERBOSE)


def is_anonymized_employer(company: str) -> bool:
    return bool(ANON_COMPANY.search((company or "").lower().strip()))


# ── S38: G3 exact-key physical-goods seeds (F12) — normalized
# full-name EQUALITY after legal-suffix stripping, never substring
# ("quest" must not kill Quest Software). Seeds from fp-wave1 §4's
# measured 63-row class: grocery/CPG + consumer/industrial hard goods
# + diagnostics/pharma service lines + insurance/annuity/title +
# agency employers. Hardware/semiconductor companies are DELIBERATELY
# NOT seeded (the over-filter guard class — NVIDIA/Lenovo/Micron stay).
PHYSICAL_GOODS_SEED = [
    # grocery / CPG distribution
    "mclane company", "mclane", "procter & gamble", "p&g",
    # consumer / industrial hard goods
    "a.o. smith", "pella", "oldcastle apg", "delta light group",
    "gator co", "interstate batteries", "penn-plax", "buckhorn",
    "thermon", "gc america", "radians", "regal rexnord", "hubbell",
    "westinghouse electric", "vehicle service group",
    "tempo communications", "global finishing solutions", "calnetix",
    "braun corporation", "yibi group", "everpure", "ecoflow",
    "intellian", "waters corporation", "waters", "te connectivity",
    "coats", "cimpress", "lem products", "seek thermal",
    "immersivetouch", "osteocentric technologies", "aalberts",
    "joyson safety systems",
    # diagnostics / pharma service lines
    "quest diagnostics", "genentech", "amgen", "baylor genetics",
    "genscript",
    # F&B services
    "sage dining services",
    # insurance / annuity / title business products
    "security benefit", "fct", "group 1001",
    # marketing / agency employers (marketing-side product roles)
    "fingerpaint", "wild marketing group", "the people brand",
    "productwind", "semify",
]

_LEGAL_SUFFIX = re.compile(
    r"""\s+(?:inc|incorporated|corp|corporation|llc|ltd|limited|
          co|company|usa|us|americas|america|holdings|group|
          technologies|international)\.?$""",
    re.I | re.VERBOSE)


def company_seed_key(company: str) -> str:
    """The seed-matching key: lowercase, legal/corporate suffix tail
    stripped (iteratively — 'Waters Corporation Inc' → 'waters'),
    punctuation → single spaces (periods glued: 'A. O.' ≡ 'A.O.'),
    trimmed."""
    s = re.sub(r"[^a-z0-9&.]+", " ", (company or "").lower()).strip()
    s = re.sub(r"\.\s+", ".", s)             # 'A. O. Smith' ≡ 'A.O. Smith'
    for _ in range(3):                     # strip chained suffixes
        s2 = _LEGAL_SUFFIX.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    return re.sub(r"\s+", " ", s)


def physical_goods_seed_hit(company: str) -> bool:
    """True when the company's seed-key equals a known physical-goods
    seed's seed-key (exact, both sides normalized the same way)."""
    k = company_seed_key(company)
    if not k:
        return False
    return k in _PG_SEED_KEYS


_PG_SEED_KEYS = {company_seed_key(s) for s in PHYSICAL_GOODS_SEED}


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
    # G3 deny match? (grocery seed substring OR the S38 physical-goods
    # exact-key seeds OR the division-allowlist keys)
    deny_key = None
    for seed in G3_GROCERY_SEED:
        if seed in org:
            deny_key = seed
            break
    if deny_key is None and physical_goods_seed_hit(company_str or ""):
        deny_key = company_seed_key(company_str or "")
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
