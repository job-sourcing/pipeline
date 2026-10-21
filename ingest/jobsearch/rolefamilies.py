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

import html as _html
import re
from typing import Optional

from .categorize import classify_tier
from .dedup import normalize_job_title
from . import company_seeds as _seeds   # S39: no import-time cycle
                                         # (seed_hit imports rolefamilies
                                         # lazily)

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
         (?!\s+(?:analyst|specialist|trainer|trainee|coordinator|assistant)\b)
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
# S38-C3 N1 guard: the PM token must sit in the title's HEAD segment
# (before the first comma) — HN/findwork role-enumeration comments
# ("Sr/Staff Engineers, Data Scientist, FDE, PM") are not PM rows.
_BARE_PM = re.compile(
    r"""\b(?:[A-Z][a-z]*\s)?(?:Sr\.?|Jr\.?|Staff|Principal|Distinguished|
        Group|Associate|Senior)?\s*PM\b(?:\s+(?:I{1,3}|IV|V))?\b""",
    re.I | re.VERBOSE)


def _bare_pm_head_only(title: str) -> bool:
    head = title.split(",")[0]
    return bool(_BARE_PM.search(head))

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
    # 7. bare-PM via query provenance (L1-L3 only; S38-C3 N1: head
    #    segment only — enumeration comments don't qualify)
    if query_is_pm and _bare_pm_head_only(title):
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
      # T7 bank product-control function (not a PM role) — S39 T7'
      # amendment: control\w* deliberately catches 'Product Controller'
    | \bproduct\s+control\w*\b
      # T8 talent-community pool postings (not open roles)
    | \b(?:talent\s+community|talent\s+pool)\b
      # T9 pharma commercial marketing
    | \bpatient\s+marketing\b
      # ── S39 G1B' extensions (DESIGN-S39 v2 §B4, F10 guards) ──
      # T1' industrial/chemical/aerospace/packaging (software-context
      # guard incl. 'systems' — the Sandalwood guard; packaging guarded
      # against co-packaged optics — the NVIDIA CPO FP)
    | \b(?:furnitur\w*|textil\w*|packag(?:ing|e)|chemical\w*|automotive|
          aerospace|composites?|engineered\s+hardware)\b
          (?!.{0,60}\b(?:platform|software|saas|marketplace|cloud|api|
          data|systems)\b)
          (?!.{0,30}\boptics?\b)
      # T2' medical specialties (bare 'device' NEVER added — TP-Link/
      # Okta/Netflix guards; x-ray gated on imaging context)
    | \b(?:cardiolog\w*|oncolog\w*|radiolog\w*|orthoped\w*|implant\w*|
          ultrasound)\b
    | \bdiagnostic\s+(?:x-?ray|imaging)\b
    | \bx-?ray\b(?=.{0,30}\b(?:radiolog\w*|imaging|medical)\b)
      # T4' insurance product-line (business product, not software);
      # plural head — Majesco/Zip 'P&C Insurance Technology' guards
    | \binsurance\s+products?\b(?!.{0,40}\b(?:technolog\w*|software|
          platform|underwriting\s+platform)\b)
      # T6' product-group engineering (J&J device PLM class)
    | \bproduct\s+group\s+engineering\b
      # T9' patient support/engagement programs (Neumeric HUB class)
    | \bpatient\s+(?:support|engagement)\s+programs?\b
      # T5n Copeland grammar (Lead Product ENGINEER, not manager)
    | \blead\s+product\s+engineer\b
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
        software\s+guidance\s+&\s+assistance|
        # S39 §B2/F5: T2b's 17 unflagged consultancies + T2a referrals
        aditi\s+consulting|agile\s+resources|bc\s+forward|
        blue\s+signal\s+search|chg\s+healthcare|
        clearbridge\s+technology|compri\s+consulting|elite\s+technical|
        govcio|incedo|inspyr\s+solutions|j\s+vers|jsr\s+tech|
        lumen\s+solutions\s+group|ltimindtree|ltm|
        manpowergroup|next\s+step\s+systems|photon\s+interactive|
        pinnacle\s+technical\s+resources|raft|sincera|
        coforge|perficient|hexaware|
        prominent\s+technology|fstone\s+technologies|
        informatic\s+technologies|netserv\s+applications|
        iris\s+software|reuben\s+cooley|cosmic-i)\b""",
    re.I | re.VERBOSE)


def is_staffing_company(company: str) -> bool:
    """S38: the staffing/repost detector — a FLAG (staffing_evidence
    = 'agency'), never a drop: real employer unknown ≠ not a real PM
    opening (DESIGN-S38 v2 §3a/F11).

    S39 §B2/F5: STAFF_BRANDS extended with T2b's 17 unflagged
    consultancies — the agency evidence-grade coverage pin depends on
    them (triage-midtail.md §2)."""
    c = (company or "").lower().strip()
    return bool(c and (STAFF_TOKENS.search(c) or STAFF_BRANDS.search(c)))


# ── S39 §B5/F8: the screening-post (N7) title-shape detector —
# company-independent; routed to a RECOVERABLE bucket (reason
# 'screening_post'), never a silent drop. COMPILE CONTRACT: re.I only,
# NO re.VERBOSE (the wave-2 regex is whitespace-sensitive — under VERBOSE
# it compiles to a 3× undercount; pinned in the test battery).
SCREENING_POST = re.compile(
    r"\b(?:product\s+(?:owner|manager)|po|pm)\s*[/&-]\s*(?:business\s+analyst|project\s+manager|scrum\s+master|ba)\b"
    r"|\b(?:business\s+analyst|scrum\s+master)\s*(?:and\s*|/)\s*(?:senior\s+)?product\s+(?:owner|manager)\b"
    r"|\bbusiness\s+analyst\s+and\s+project\s+manager,\s*product\s+(?:owner|manager)\b"
    r"|\b(?:ba|po)\s*[/&-]\s*(?:ba|po)\b"
    r"|\bwith\s+[^,|]{3,50}\b(?:experience|skills?|background)\b"
    r"|\b(?:experience|background)\s+required\b"
    r"|\(\s*\d+\+?\s*(?:years|yrs|yoe)\s+only\s*\)"
    r"|\(3-5\+\s*yoe\)"
    r"|\bproduct\s+manager\s*-\s*product\s+manager\b"
    r"|\bproduct\s+owner,\s*3\s+senior\b"
    r"|\bw2\s+only\b",
    re.I)


def is_screening_post(title: str) -> bool:
    """S39 N7: skill-screen / multi-role combo titles (agency bench
    postings, resume-fishing) — the 13 flagged + 12 unflagged wave-2
    rows. Guards (pinned): Motion 'Senior PM / Payments & Agentic
    Commerce / NYC' and 'Principal/Senior PM' style level/domain slashes
    do NOT fire (the combo arms need role-noun pairs)."""
    return bool(isinstance(title, str) and SCREENING_POST.search(title))


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


# ── S38-C3 N9: the job-board-as-employer class (Jobgether rows carry
# the BOARD as the company — the real employer lives in the title/loc;
# flagged as employer_anonymized + merge-excluded, kept, never dropped)
BOARD_EMPLOYER_SEED = {
    "jobgether", "us government jobs", "ziprecruiter", "adzuna",
    "jooble", "careerjet", "indeed", "linkedin",
    # S41-R1 F5: 43 pending rows surfaced as "JobLeads US" via jooble —
    # a job-board-as-employer shell whose redirect URLs can never yield
    # employer JD evidence. The N9 flag class, extended.
    "jobleads us", "jobleads",
}


def is_board_employer(company: str) -> bool:
    c = (company or "").lower().strip()
    return bool(c) and (c in BOARD_EMPLOYER_SEED or
                        c.startswith("jobgether"))


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
    trimmed.

    S39 §B7 hardening (T2a's guard finding): html-unescape FIRST
    ('Procter &amp; Gamble' ≡ 'procter & gamble' — the entity form
    escaped its own deny seed), '&'-spacing collapsed
    ('Procter&Gamble' ≡ 'procter & gamble'), leading 'the ' stripped
    ('The Cigna Group' ≡ 'cigna'; 'The Vanguard Group' ≡ 'vanguard'
    — load-bearing for the BANK_SEED vanguard entry), and ' and ' ≡
    ' & ' in the seed-key space ('Stanley Black and Decker' ≡
    'Stanley Black & Decker')."""
    s = _html.unescape(company or "").lower()
    s = re.sub(r"\s*&\s*", " & ", s)          # 'Procter&Gamble' ≡ spaced
    s = re.sub(r"\s+and\s+", " & ", s)        # 'Black and Decker' ≡ '&'
    s = re.sub(r"^the\s+", "", s)             # 'The Cigna Group' ≡ 'cigna'
    s = re.sub(r"[^a-z0-9&.]+", " ", s).strip()
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


_SERP_HEAD = re.compile(r"google\.com/websearch|go to google home",
                       re.I)


def _clean_digest(jd: str) -> str:
    """B-S40-5 (S40 review F1-note-1): a digest whose head carries
    Google-SERP chrome ('Skip to main content [Accessibility help]…'
    + google.com/websearch links) captured the SEARCH PAGE, not the
    JD — treat as empty. Measured: ~6 bank-review rows + wider
    pending queue; title/company tokens then carry the row."""
    t = (jd or "").strip()
    if not t:
        return ""
    head = t[:600]
    if _SERP_HEAD.search(head) and (
            "skip to main content" in head.lower()
            or "accessibility help" in head.lower()):
        return ""
    return t


def sector_verdict(
    title: str,
    company_str: str,
    jd_text: str = "",
    company_key: Optional[str] = None,
) -> tuple[str, str, str]:
    """G2 (tech-evidence-required) + G3 (recoverable bucket) + the S39
    sector-grade machinery (DESIGN-S39 v2 §B1-B3).

    Returns (action, sector_evidence, reason):
      action 'keep'    — evidence found (ranking: division > jd > roster)
      action 'pending' — no sector evidence AND not deniable: the row
                         ships flagged (▲), never silently dropped
      action 'bucket'  — denied with no rescue → the recoverable review
                         bucket (dropped-by-nontech.csv)
    sector_evidence: 'division' | 'jd' | 'roster' | '' (blank = pending)

    S39 order (F6 precedence, rescue-first):
      0. TECH_SEED membership overrides ALL deny seeds → roster keep
      1. N3B bank rule (BANK_SEED ∧ ¬FINTECH_GUARD): tech-division token
         → keep; bank-product token → bucket; else pending 'bank_review'
      2. N6B payer rule (PAYER_SEED): division kw → keep; business token
         → bucket; tech token → keep; else pending 'payer_review'
      3. G3 deny path (grocery substring / physical-goods seeds /
         NONTECH seeds / new division-allowlist keys) → division rescue
         on company+title haystack > jd override > bucket
      4. jd evidence → keep; else pending
    """
    org = _norm_company(company_str)
    hay = f"{org} { _norm_company(title)}"      # F6/T3: company+title
    ck = company_key or ""
    jd_text = _clean_digest(jd_text)   # B-S40-5: SERP-chrome = empty
    jd_c = "" if g1_fires(jd_text) else (jd_text or "")
    hay_f = hay + " " + jd_c.lower()    # company+title+desc haystack
    hay_t = _norm_company(title) + " " + jd_c.lower()  # title+desc
    pair_primary_bank = _phrase_hit(_seeds.BANK_PAIR_PRIMARIES, hay_f)
    pair_primary_payer = _phrase_hit(_seeds.PAYER_PAIR_PRIMARIES, hay_f)

    # 0. TECH_SEED precedence (the D-S36-1 curated exception) — but the
    #    evidence GRADE ranking still applies (division > jd > roster):
    #    a tech-seed row with JD markers earns 'jd', not 'roster'
    if _seeds.tech_seed_hit(ck, company_str or ""):
        if jd_text and _JD_STRONG.search(jd_text) and not g1_fires(jd_text):
            return "keep", "jd", "tech_seed_jd_strong"
        weak = len(_JD_WEAK.findall(jd_text or "")) if jd_text else 0
        if weak >= 2 and not g1_fires(jd_text or ""):
            return "keep", "jd", "tech_seed_jd_weak"
        return "keep", "roster", "tech_seed"

    # 1. N3B bank rule — S40 v2 A1: the phases consult the CLEANED desc
    #    too (the bank wave's 122 decided rows were JD-digest-decided),
    #    with the g1 guard; the DESC tier matches SPECIFIC tool/craft
    #    phrases only (boilerplate-proof: the BNY 'cutting-edge AI'
    #    culture-blurb trap, review F1 note-3). A2: platform/agentic/
    #    aml/fraud primaries are pair-ruled — solo 'platform' no longer
    #    auto-techs ('Global Clearing Platform' measured BUSINESS).
    #    B-S40-2: per-company orderings (citi/citigroup/michigan-farm-
    #    bureau business_first — the ≥80%-decided override rule).
    if _seeds.bank_seed_hit(ck, company_str or "") and \
            not _seeds.fintech_guard_hit(ck, company_str or ""):
        biz_first = (
            _seeds.BANK_OVERRIDES.get(ck) == "business_first"
            or _seeds.BANK_OVERRIDES.get(_seeds._slugify(org))
            == "business_first")

        def _bank_tech() -> bool:
            if pair_primary_bank and _phrase_hit(
                    _seeds.BANK_PAIR_TECH_CO, hay_f):
                return True
            if _phrase_hit(_seeds.BANK_TECH_TOKENS, hay):
                return True
            return _phrase_hit(_seeds.BANK_TECH_DESC_TOKENS, jd_c.lower())

        def _bank_biz() -> bool:
            if pair_primary_bank and _phrase_hit(
                    _seeds.BANK_PAIR_BIZ_CO, hay_f):
                return True
            return _phrase_hit(_seeds.BANK_PRODUCT_TOKENS, hay_t)

        checks = (("biz", _bank_biz), ("tech", _bank_tech)) if biz_first \
            else (("tech", _bank_tech), ("biz", _bank_biz))
        for kind, fn in checks:
            if fn():
                if kind == "tech":
                    return "keep", "division", "bank_tech_division"
                return "bucket", "", "bank_business_product"
        return "pending", "", "bank_review"

    # 2. N6B payer rule — S40 v2 A1 (desc-informed, specific-tier desc
    #    tokens, pair-ruled primaries); default order stays business-
    #    first (the S39 Humana G1B-upstream case never reaches here —
    #    review F10; the humana PAYER_OVERRIDE is DROPPED as inert).
    if _seeds.payer_seed_hit(ck, company_str or ""):
        if _phrase_hit(_seeds.PAYER_DIVISION_KWS, hay_f):
            return "keep", "division", "payer_division"
        if pair_primary_payer and _phrase_hit(
                _seeds.PAYER_PAIR_BIZ_CO, hay_f):
            return "bucket", "", "payer_business_product"
        if _phrase_hit(_seeds.PAYER_BUSINESS_TOKENS, hay_t):
            return "bucket", "", "payer_business_product"
        if pair_primary_payer and _phrase_hit(
                _seeds.PAYER_PAIR_TECH_CO, hay_f):
            return "keep", "division", "payer_tech_lean"
        if _phrase_hit(_seeds.PAYER_TECH_TOKENS, hay):
            return "keep", "division", "payer_tech_lean"
        if _phrase_hit(_seeds.PAYER_TECH_DESC_TOKENS, jd_c.lower()):
            return "keep", "division", "payer_tech_lean"
        return "pending", "", "payer_review"

    # 3. G3 deny match? (grocery seed substring OR the S38 physical-goods
    #    exact-key seeds OR the S39 NONTECH seeds OR the division keys)
    deny_key = None
    for seed in G3_GROCERY_SEED:
        if seed in org:
            deny_key = seed
            break
    if deny_key is None and (
            physical_goods_seed_hit(company_str or "")
            or _seeds.nontech_seed_hit(ck, company_str or "")):
        deny_key = company_seed_key(company_str or "") or \
            _seeds._slugify(company_str or "")
    if deny_key is None:
        for comp in DIVISION_ALLOWLIST:
            if comp in org:
                deny_key = comp
                break
    if deny_key is None:
        for slug in _seeds.DIVISION_ALLOWLIST_ADD:
            if slug and (slug == ck or slug == _seeds._slugify(org)):
                deny_key = slug
                break
    if deny_key is None:
        # not deniable: G2 evidence optional (roster/neutral policy —
        # company membership is NOT tech evidence, D-S36-1 — except the
        # curated TECH_SEED above)
        if jd_text and _JD_STRONG.search(jd_text) and not g1_fires(jd_text):
            return "keep", "jd", "jd_strong_marker"
        weak = len(_JD_WEAK.findall(jd_text or "")) if jd_text else 0
        if weak >= 2 and not g1_fires(jd_text or ""):
            return "keep", "jd", "jd_weak_x2"
        return "pending", "", "no_sector_evidence"
    # deniable company: division rescue (company+title haystack) >
    # JD override > bucket. The division entry is looked up under BOTH
    # the deny_key and its slug (NONTECH hits carry seed-key forms,
    # DIVISION_ADD carries slug forms).
    div_entries = list(DIVISION_ALLOWLIST.get(deny_key, []))
    div_entries += _seeds.DIVISION_ALLOWLIST_ADD.get(deny_key, []) + \
        _seeds.DIVISION_ALLOWLIST_ADD.get(_seeds._slugify(deny_key), []) + \
        _seeds.DIVISION_ALLOWLIST_ADD.get(ck, [])
    for div in div_entries:
        if div and div in hay:
            return "keep", "division", f"division:{div}"
    if jd_text and _JD_STRONG.search(jd_text) and not g1_fires(jd_text):
        return "keep", "jd", "g3_jd_override"
    # A7/F9: the jdblind marker — nontech-denied with no usable JD:
    # re-fetch priority for the next lane run (Fix A restores seed JDs)
    return "bucket", "", f"g3_deny:{deny_key}" + (
        "|jdblind" if not (jd_text or "").strip() else "")


def _phrase_hit(phrases, text: str) -> bool:
    r"""Token-phrase match for the S39 rule token sets: single tokens
    match as PREFIXES (\btoken\w* — 'treasur' catches treasury/
    treasuries, 'technolog' catches technologies), multi-word phrases
    match with \s+ separators and a prefix on the final token ('card
    acquisition' catches 'card acquisitions'). Case-insensitive."""
    t = text or ""
    if not t:
        return False
    for p in phrases:
        p = p.strip().lower()
        if not p:
            continue
        words = p.split()
        pat = r"\b" + r"\s+".join(
            re.escape(w) + (r"\w*" if i == len(words) - 1 else "")
            for i, w in enumerate(words)) + r"\b"
        if re.search(pat, t, re.I):
            return True
    return False


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
