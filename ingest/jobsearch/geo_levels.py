"""geo_levels.py — the shared region + level classification (S41).

Extracted from scripts/adhoc_company_test.py per DESIGN-S41 v2 §3.2:
the region gate (CA-context-gated Bay Area ∨ remote) is shared by the
adhoc company test AND the industry×region sweep — one module, no
copies, pins at extraction (S41-R1 F13: none existed — the reviewer
verified).

The S41-R1 F13 amendments baked in:
  * the VDC deny — "Virtual Design and Construction (VDC) Manager"
    is a core ON-SITE construction-tech role; the bare \bvirtual\b
    remote-pattern head-lined it as remote. VDC/virtual design is a
    location antonym, never a remote marker.
  * +10 Bay Area cities (San Leandro, San Carlos, Belmont, San Bruno,
    Brisbane, East Palo Alto, Oakley, American Canyon, Windsor,
    Healdsburg) — the lane-1 list under-counted the commuter belt.
"""
from __future__ import annotations

import re

# ── the Bay Area city list (9 counties, commuter-belt broad) ───────────────
BAY_AREA = (
    "san francisco", "south san francisco", "daly city", "burlingame",
    "san mateo", "foster city", "redwood city", "menlo park", "palo alto",
    "mountain view", "sunnyvale", "santa clara", "san jose", "milpitas",
    "cupertino", "campbell", "los gatos", "saratoga", "los altos",
    "oakland", "berkeley", "emeryville", "alameda", "richmond",
    "san ramon", "danville", "dublin", "pleasanton", "livermore",
    "fremont", "newark", "union city", "hayward", "castro valley",
    "walnut creek", "concord", "pleasant hill", "lafayette", "orinda",
    "moraga", "martinez", "pittsburg", "antioch", "brentwood",
    "san leandro", "san carlos", "belmont", "san bruno", "brisbane",
    "east palo alto", "oakley", "american canyon",
    "san rafael", "novato", "petaluma", "santa rosa", "sonoma",
    "windsor", "healdsburg", "vallejo", "fairfield", "napa",
    "suisun", "vacaville",
    "bay area", "silicon valley", "south bay",
)

# City names shared with OTHER states (the lane-1 false-positive
# class: Antioch TN, Dublin OH, Richmond KY, Newark NJ, Brentwood TN,
# Lafayette MS, Martinez GA...). A city match alone is NOT Bay Area
# evidence — CA context (state abbr or 'California') in the same loc
# string, or the unambiguous whole-region markers.
UNAMBIG_BAY = (
    "san francisco", "south san francisco", "silicon valley",
    "bay area", "san jose", "palo alto", "mountain view", "sunnyvale",
    "cupertino", "menlo park", "redwood city", "san mateo",
    "foster city", "milpitas", "santa clara", "south bay",
)
CA_PAT = re.compile(r"\bca\b|\bcalif\b|california", re.I)

REMOTE_PAT = re.compile(
    r"\bremote\b|\bvirtual\b|work\s*-?\s*from\s*-?\s*home|wfh|"
    r"anywhere\s+in",
    re.I,
)
# F13: VDC — "Virtual Design and Construction" is an on-site role
VDC_DENY = re.compile(
    r"\bvdc\b|virtual\s+design\s+and\s+construction|"
    r"virtual\s+design\s*-?\s*&?\s*construction",
    re.I,
)


def region_tag(loc: str, title: str = "") -> str:
    """region ∈ {bay_area, remote, other} — the CA-context gate.
    Remote first (title can carry it), then unambiguous whole-region
    markers, then city-in-list ∧ CA-context. A city match WITHOUT CA
    context is 'other' — Brentwood TN and Brentwood CA never
    conflate. The VDC deny fires BEFORE the remote check (F13)."""
    blob = f"{loc} {title}"
    if not VDC_DENY.search(blob):
        if REMOTE_PAT.search(blob):
            return "remote"
    loc_l = (loc or "").lower()
    for marker in UNAMBIG_BAY:
        if marker in loc_l:
            return "bay_area"
    ca = bool(CA_PAT.search(loc or ""))
    if ca:
        for city in BAY_AREA:
            if city in loc_l:
                return "bay_area"
    return "other"


# ── the retail/hourly level gate (the adhoc cut, shared) ──────────────────
HOURLY_RETAIL_PAT = re.compile(
    r"\bbarista\b|shift\s*supervisor|team\s*supervisor|"
    r"retail\s*associat|hourly\b|part\s*-?\s*time\b|"
    r"\bsupervisor\b|team\s*leader|team\s*member|food\s*service|"
    r"cashier|crew\s*member|store\s*partner",
    re.I,
)
RETAIL_MGMT_PAT = re.compile(
    r"\bstore\s*manager\b|assistant\s*store\s*manager|district\s*manager|"
    r"\bdistrict\s*manager\b|retail\s*manager|store\s*leader",
    re.I,
)


def retail_level_tag(title: str, microsite: str = "") -> str:
    """level ∈ {corp, retail_hourly, retail_mgmt} — the adhoc cut."""
    blob = f"{title} {microsite}"
    if HOURLY_RETAIL_PAT.search(blob):
        return "retail_hourly"
    if RETAIL_MGMT_PAT.search(blob):
        return "retail_mgmt"
    if microsite and "retail" in microsite.lower():
        return "retail_hourly"
    return "corp"


# ── the industrial level taxonomy (S41 Strategy 2, DESIGN-S41 v2 §3.2) ──────
# The user cut: home services / construction roles that DON'T require a
# license (not a licensed contractor / professional), corp / office jobs
# okay, anything not pure labor but that helps ACCESS the business.
#
# Taxonomy (F11-amended — every drop is labeled; unclassified is the
# counted default, never a silent fallthrough):
#   corp_office          the head-lined cut (estimator, dispatcher,
#                       sales, office manager, admin, bookkeeper...)
#   field_mgmt          business-access but field-side (superintendent,
#                       foreman, site manager) — kept, secondarily shown
#   licensed_professional  EXCLUDED (electrician, plumber, licensed
#                       contractor, PE, architect...) — QUALIFIED forms
#                       only: "HVAC Project Manager" is corp, "HVAC
#                       Technician" is licensed-trade (F11)
#   pure_labor          EXCLUDED — with token guards: "Crew Scheduler"
#                       is corp (the crew token alone must not fire);
#                       "Driver Recruiter" is corp; "Installation
#                       Coordinator" is corp (F11 token over-reach)
#   retail_service      EXCLUDED (store-floor roles at building-supply
#                       retail: cashier, lot associate, store #)
#   unclassified        counted, reviewed at wave time

LICENSED_PROFESSIONAL_PAT = re.compile(
    r"""\blicensed\s+(?:contractor|electrician|plumber|hvac|
          technician|engineer|surveyor|locksmith|pest\s+control)\b
      | \b(?:journeyman|master)\s+(?:electrician|plumber|mechanic)\b
      | \b(?:electrician|plumber|lineman|pipefitter|steamfitter)\b
      | \bhvac\s+(?:technician|tech|mechanic)\b
      | \bprofessional\s+engineer\b
      | \bregistered\s+(?:architect|land\s+surveyor|sanitarian)\b
      | \b(?:architect|land\s+surveyor)\b
      | \bpest\s+control\s+(?:applicator|technician)\b
      | \belectrician['’]?s?\s+(?:helper|materiel)\b""",
    re.I | re.VERBOSE)
PURE_LABOR_PAT = re.compile(
    r"""\b(?:construction\s+)?labor(?:er|our)\b
      | \b(?:helper|apprentice)\b
      | \b(?:installer|installers)\b
      | \b(?:crew\s+(?:member|person|worker)|crew\s+member)\b
      | \b(?:framer|drywall(?:er)?|glazier|welder|roofer|
             concrete\s+finisher|tile\s+setter|mason|painter['’]?s?\s+helper)\b
      | \bdemo\s+(?:worker|laborer|tech)\b
      | \b(?:heavy\s+)?equipment\s+operator\b
      | \bwarehouse\s+(?:associate|worker|operative)\b
      | \bmover\b
      | \bhousekeeper\b|\bcleaner\b
      | \bshop\s+hand\b|\byard\s+worker\b|\byard\s+associate\b""",
    re.I | re.VERBOSE)
# token guards (F11): the labor tokens above must not eat real corp
# roles — these titles re-route to corp_office BEFORE the labor check.
_LABOR_TOKEN_GUARDS = re.compile(
    r"""\bcrew\s+(?:scheduler|dispatcher|coordinator|lead)\b
      | \bdriver\s+(?:recruiter|manager|coordinator|dispatch)\b
      | \binstallation\s+(?:coordinator|manager|scheduler)\b
      | \b(?:paint|flooring|door|window)s?\s+sales\b
      | \b(?:dispatch|scheduler|dispatcher)\b""",
    re.I | re.VERBOSE)
FIELD_MGMT_PAT = re.compile(
    r"""\b(?:superintendent|foreman|forewoman|site\s+manager|
            site\s+supervisor|field\s+(?:manager|supervisor|engineer)|
            construction\s+manager)\b""",
    re.I | re.VERBOSE)
CORP_OFFICE_PAT = re.compile(
    r"""\b(?:project\s+(?:coordinator|manager|administrator|assistant)|
            estimator|senior\s+estimator|bid\s+(?:coordinator|manager)|
            contracts\s+(?:admin|administrator|coordinator|specialist)|
            dispatcher|dispatch\s+(?:coordinator|manager|supervisor)|
            scheduler|scheduling\s+(?:coordinator|manager)|
            office\s+manager|office\s+(?:assistant|coordinator|admin)|
            administrative\s+(?:assistant|coordinator|specialist)|
            receptionist|front\s+desk\b|
            bookkeeper|account(?:ant|ing)\s+(?:manager|specialist|clerk)?|
            payroll\s+(?:specialist|administrator|coordinator|clerk)|
            accounts\s+(?:payable|receivable)\s+(?:specialist|clerk)|
            customer\s+(?:service|support|care)\s+(?:representative|
                specialist|manager|coordinator|rep)|
            inside\s+sales|sales\s+(?:representative|rep|associate|
                consultant|manager|director|executive|specialist)|
            account\s+(?:manager|executive|coordinator)|
            appointment\s+(?:setter|coordinator|scheduler)|
            marketing\s+(?:coordinator|manager|specialist|director)|
            (?:digital\s+)?marketing\s+(?:manager|specialist|coordinator)|
            operations\s+(?:manager|coordinator|analyst|specialist)|
            (?:purchasing|procurement|buyer|expeditor)\b|
            hr\s+(?:manager|specialist|coordinator)|human\s+resources\b|
            recruiting\s+(?:coordinator|manager|specialist)|
            it\s+(?:specialist|manager|support|coordinator)|
            (?:assistant|junior|senior)?\s*(?:general|branch|region|
                area)?\s*(?:manager|director)\b|
            (?:construction|remodeling|home\s+services|plumbing|hvac|
             electrical|landscaping|roofing)\s+(?:manager|coordinator|
             assistant|admin(?:istrator)?)\b)""",
    re.I | re.VERBOSE)
RETAIL_SERVICE_PAT = re.compile(
    r"""\bcashier\b
      | \bretail\s+(?:associate|sales\s+associate|cashier)\b
      | \blot\s+associate\b
      | \bstore\s+#\d+
      | \bsales\s+floor\b""",
    re.I | re.VERBOSE)


def industrial_level_tag(title: str, desc: str = "") -> str:
    """level ∈ {corp_office, field_mgmt, licensed_professional,
    pure_labor, retail_service, unclassified} — the Strategy-2 cut.
    Title evidence first; desc is consulted for the licensed class
    only when the title is ambiguous about hands-on trade work."""
    t = title or ""
    if _LABOR_TOKEN_GUARDS.search(t):
        return "corp_office"           # F11: crew SCHEDULER ≠ crew member
    if LICENSED_PROFESSIONAL_PAT.search(t):
        return "licensed_professional"
    if PURE_LABOR_PAT.search(t):
        return "pure_labor"
    if RETAIL_SERVICE_PAT.search(t):
        return "retail_service"
    # F11 order fix: field vocabulary BEFORE the generic *Manager corp
    # pattern — "Site Manager" is field-side, the generic pattern would
    # otherwise eat it
    if FIELD_MGMT_PAT.search(t):
        return "field_mgmt"
    if CORP_OFFICE_PAT.search(t):
        return "corp_office"
    return "unclassified"             # F11: counted default, never silent


# ── the US-wide region gate (S42 industry v2, DESIGN-S42 v2 B3/B4) ─────────
# The user directive: "local + remote (all US wide) home services &
# construction" — the us geo mode keeps any US-anchored row and any
# US-remote row. The tagger is deny-first (the F13 name-collision
# standard applied at country scale): a two-letter suffix alone is
# NOT US evidence — "Munich, DE" would read as Delaware, "Chennai, IN"
# as Indiana. The deny list fires first, then the USPS state set.
US_STATES = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    "dc", "pr",            # DC + PR (US territory; usajobs covers it)
)
NON_US_DENY = re.compile(
    r",\s*(?:on|bc|ab|qb|qc|sk|mb|ns|nb|nl|pe|nt|yt)\b"   # CA provinces
    r"|\b(?:united\s+kingdom|england|\bscotland\b|\bwales\b)"
    r"|\bcanad(?:a|ian)\b"
    r"|\b(?:australia|new\s+zealand|germany|france|spain|italy|"
    r"netherlands|sweden|norway|denmark|finland|poland|portugal|"
    r"india|singapore|japan|china|brazil|mexico|argentina|"
    r"philippines|vietnam|thailand|indonesia|ireland|austria|"
    r"switzerland|belgium|czech|romania|israel|emirates|dubai)\b"
    r"|\b(?:london|manchester|birmingham|leeds|glasgow|edinburgh|"
    r"dublin|paris|berlin|munich|hamburg|amsterdam|rotterdam|"
    r"madrid|barcelona|lisbon|milan|rome|vienna|zurich|geneva|"
    r"stockholm|oslo|copenhagen|helsinki|warsaw|prague|budapest|"
    r"toronto|vancouver|montreal|ottawa|calgary|sydney|melbourne|"
    r"auckland|wellington|mumbai|bangalore|chennai|delhi|singapore|"
    r"tokyo|osaka|shanghai|beijing|hong\s*kong|taipei|seoul)\b",
    re.I,
)
_US_STATE_PAT = re.compile(r",\s*([a-z]{2})\s*$", re.I)
US_MARKER_PAT = re.compile(
    r"\bunited\s+states\b|\busa\b|\bu\.s\.?\b|(?<![a-z])us(?![a-z])",
    re.I)


def us_region_tag(loc: str, title: str = "",
                 origin: str = "") -> str:
    """region ∈ {us, remote, other} — the all-US gate (DESIGN-S42 v2
    B3/B4).

    origin: the fetcher class — "geo" fetchers (adzuna/jooble/careerjet/
    indeed) are US-locale APIs, so their remote rows are US-remote by
    construction; "wide" fetchers (global firehoses) must carry a US
    marker for a remote tag (F8: "Remote, Berlin" is not US-remote).
    """
    blob = f"{loc} {title}"
    if NON_US_DENY.search(blob):
        return "other"
    if not VDC_DENY.search(blob) and REMOTE_PAT.search(blob):
        if origin == "wide":
            return "remote" if US_MARKER_PAT.search(blob) else "other"
        return "remote"               # geo-fetcher: US by construction
    loc_s = (loc or "").strip()
    if loc_s:
        m = _US_STATE_PAT.search(loc_s)
        if m and m.group(1).lower() in US_STATES:
            return "us"
        if US_MARKER_PAT.search(loc_s):
            return "us"
    return "other"
