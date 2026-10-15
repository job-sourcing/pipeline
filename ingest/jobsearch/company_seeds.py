"""company_seeds.py — S39 sector-triage fold (DESIGN-S39 v2 §B2).

Generated from the S39 triage wave (5 sub-agents on the 1,999-row pending
queue), then hand-curated:
  - T1  audit/s39/triage-banks.md            (banks 253 rows)
  - T3  audit/s39/triage-bigtech-allowlist.md (TECH_SEED 196 keys/624 rows)
  - T2a audit/s39/triage-payers-industrial.md (payers 30 / industrial 71)
  - T2b audit/s39/triage-midtail.md           (midtail 91 cos/422 rows)

KEY SPACE (F6): every set is matched against BOTH forms of a row's company
identity — the bundle companyKey slug AND company_seed_key(company_str) —
via `seed_hit()` below. Entries may be written in either form; the matcher
normalizes both sides to both spaces.

PRECEDENCE (F6): TECH_SEED membership overrides ALL deny seeds (checked
first in sector_verdict). TECH_SEED ∩ NONTECH_SEED = ∅ is pinned in the
test battery (the CACI/HII T2a-over-T3 resolution folded here).

Per DESIGN-S39 v2 F14: mercury is deliberately in NEITHER FINTECH_GUARD
nor BANK_SEED (the mercury.com-vs-Mercury-Financial identity conflict is
unresolved; its rows ride pending).
"""
import re

# ── TECH_SEED: company membership IS tech evidence (the D-S36-1 curated
# exception, T3 §5 contract: size pin + spot-check pins + ≤35%-of-pending
# coverage pin). 204 keys.
TECH_SEED = {
    # T3 §1a main block (54 keys / 451 rows)
    "nvidia", "indeed", "netflix", "google", "airwallex", "anthropic",
    "openai", "microsoft", "fiserv", "lenovo", "oracle", "coinbase",
    "visa", "trinet", "bloomberg", "athenahealth",
    "amazon-web-services-aws", "servicenow", "affirm", "okx", "mastercard",
    "bytedance", "anduril-industries", "webull", "transunion",
    "trans-union-llc", "webmd", "red-ventures", "finario", "volta",
    "t-mobile", "lexisnexis", "wolters-kluwer", "ebay", "instacart",
    "palo-alto-networks", "netapp", "globalfoundries", "procore-technologies",
    "autodesk", "spotify", "riot-games", "toast", "tp-link-usa-corp",
    "early-warning-services-llc", "adp", "onto", "dualentry", "altruist",
    "microsoft-corporation",  # T3 §2: bare 'amazon' = MIXED via division split (NOT tech-seeded)
    # T3 §1b anchor tail (142 keys / 173 rows)
    "1password", "acumatica", "advancedmd", "akamai-technologies",
    "amazon-data-services-inc", "amazon-development-center-u-s-inc",
    "amd-advanced-micro-devices", "analog-devices-inc", "apple-inc",
    "applied-systems", "appzen-inc", "asana", "avidxchange", "bazaarvoice",
    "betmgm", "bigbear-ai", "bitdeer", "biwin-technology", "blend",
    "brighthire", "broadcom-corporation", "boulevard", "cadence-design-systems-inc",
    "calm", "clickhouse", "clickup-project-management", "coreweave",
    "coupang", "celerdata", "cisco", "databricks", "datadog", "digicert",
    "echostar", "echostar-corporation", "equinix", "fleetworks", "grubhub",
    "help-scout", "housecall-pro", "ibm", "ibs-software", "luxer-one",
    "penn-interactive-ventures", "q2-software-inc", "rocketrez-inc", "sage",
    "salesforce", "stripe", "stubhub", "toshiba-global-commerce-solutions",
    "true-anomaly", "ukg", "veeva-systems", "verisk", "waymo",
    "xiaohongshu", "nokia-global", "adobe", "atlassian", "snowflake",
    "cloudflare", "squarespace", "vercel", "6sense", "expel", "lattice",
    "smartsheet", "wrike", "typeform", "calm-com", "plaid",
    "prime-video-amazon-mgm-studios", "datadog-inc", "docusign", "figma",
    "github", "gitlab", "intel", "intuit", "jump-trading", "meta",
    "meta-platforms", "micron", "micron-technology", "qualcomm",
    "samsung-electronics-america", "workday", "zscaler", "zoom",
    "chime", "block", "square", "paypal", "robinhood", "sofi", "betterment",
    "dave", "current", "upgrade", "affirm-com", "coinbase-com",
    "expedia", "expedia-group", "expedia-com",
    # T2a additions (7 keys / 18 rows; JD-verified)
    "cedar", "caci", "caci-international", "caci-international-inc",
    "hii-mission-technologies", "leidos", "warby-parker",
    # T2b confirmation set (all already covered above; kept for the pin)
    # T1 fintech/processor table (never hit by the bank rule; TECH membership
    # also rescues them from any title rule)
    "adyen", "payoneer", "remitly", "nubank", "crypto-com", "corpay",
    "coins-ph", "insurify", "alinea-invest", "citizen", "pave-finance",
    "boost-payment-solutions", "coalition-inc",
    "jack-henry-amp-associates", "ai-fund", "specialty-capital", "stash",
    # bank-software vendors (T1: sector-guard, never title-match)
    "jack-henry", "global-payments", "fis", "fis-global",
    # healthtech guards that are unambiguously software (T2a §4)
    "zocdoc", "nextgen", "nextgen-healthcare", "oscar-health-insurance",
    "optum", "majesco", "epic", "epic-games", "waystar", "availity",
}

# ── NONTECH_SEED: deny keys routed to the G3 bucket path (recoverable,
# never silent). company_seed_key-normalized names + slug forms.
NONTECH_SEED = {
    # T2a industrial nontech (7)
    "stanley black & decker", "stanley black and decker",
    "stanley-black-decker", "kohler", "uline", "hni", "hni corporation",
    "terumo", "terumo medical", "shd composite materials",
    "procter & gamble", "p&g", "procter-gamble", "procter-amp-gamble",
    # T2a/T3 deny + rescue (mcdonald's digital org exists)
    "mcdonald s", "mcdonald's", "mcdonald-s",
    # T2b additions (4)
    "gartner", "blue origin", "blue-origin", "danaher", "danaher-corporation",
    "coca-cola", "the-coca-cola-company", "the coca cola company",
    # T3 §3 remainder (slug forms; CACI/HII removed — T2a tech-lean won)
    "circle-k", "circle-k-stores", "fanatics",
}

# ── BANK_SEED: the N3B rule's company gate (T1 bank + mixed tables +
# the S38 wave-2 N3 core list). Slug + name forms.
BANK_SEED = {
    # T1 bank (business-product heavy)
    "sterling-capital-management-llc", "sterling capital management",
    "michigan-farm-bureau", "michigan farm bureau",
    "golden-1-credit-union", "golden 1 credit union",
    "corebridge-financial", "corebridge financial",
    "guardian-life", "guardian life", "guardian life insurance",
    "blue-owl-capital-holdings", "blue owl capital holdings",
    "hyundai-capital-america", "hyundai capital america",
    "american-integrity-insurance-company",
    "american integrity insurance company", "aaa",
    # T1 mixed (44 keys)
    "j-p-morgan", "jpmorganchase", "jpmorgan-chase",
    "jpmorgan-chase-amp-co", "jpmorgan-chase-bank-n-a",
    "j.p. morgan", "jpmorgan chase", "jpmorgan chase & co",
    "bny", "bny-mellon", "bny mellon", "bank of new york mellon",
    "citi", "citigroup", "citibank",
    "capital-one", "capital one", "capital one financial",
    "usaa", "new-york-life-insurance-company",
    "new-york-life-insurance-co", "new york life",
    "american-express", "american express", "amex",
    "barclays", "morgan-stanley", "morgan stanley", "blackrock",
    "vanguard", "the vanguard group", "allstate-insurance",
    "allstate insurance company", "allstate", "charles-schwab",
    "charles schwab", "state-street-corporation", "state street corporation",
    "state street", "lpl-financial", "lpl financial", "capital-group",
    "capital group", "sumitomo-mitsui-banking-corporation",
    "sumitomo mitsui banking corporation",
    "sumitomo-mitsui-financial-group-inc",
    "sumitomo mitsui financial group inc",
    "crc-insurance-services-inc", "crc insurance services inc",
    "100-crc-insurance-group-llc", "truist", "truist bank",
    "western-alliance-bank", "western alliance bank",
    "u-s-bank", "u.s. bank", "us bank", "us bancorp",
    "goldman-sachs-co", "goldman sachs & co", "goldman sachs",
    "nationwide-mutual-insurance-company",
    "nationwide mutual insurance company", "nationwide",
    "london-stock-exchange-group", "london stock exchange group",
    "lseg", "peak6-investments-llc", "peak6 investments llc", "peak6",
    "empower-annuity-insurance-company-of-america",
    "empower annuity insurance company of america", "empower",
    "great-american-insurance-company",
    "great american insurance company",
    "embrace-pet-insurance", "embrace pet insurance",
    "utah-first-credit-union", "utah first credit union",
    "credit-acceptance-corporation", "credit acceptance corporation",
    "northern-trust", "northern trust",
    "burke-herbert-bank-trust", "burke & herbert bank & trust",
    "us-tiger-securities-inc", "us tiger securities inc",
    "fidelity-guaranty-life-insurance-company",
    "fidelity & guaranty life insurance company",
    "h-r-block", "h&r block", "hr block",
    "berkshire-hathaway-guard-insurance-company",
    "berkshire hathaway guard insurance company",
    # S38 wave-2 N3 core (careerjet-recovery coverage; 0 pending rows today)
    "city-national-bank", "city national bank",
    "pnc", "pnc financial services", "pnc-financial-services",
    "synchrony", "synchrony bank", "synchrony financial",
    "m&t bank", "m&t-bank", "mt bank", "banco santander", "santander",
    "santander-bank", "santander-us-capital-markets",
    "wells fargo", "wells-fargo", "regions", "regions bank",
    "regions-financial", "keybank", "keybank-com", "key bank",
    "ally", "ally bank", "ally financial", "navy federal",
    "navy-federal-credit-union", "navy federal credit union",
    "discover", "discover-financial-services", "discover card",
    "discover financial services", "citizens bank", "citizens-bank",
    "citizens financial group", "comerica", "comerica-bank",
    "huntington", "huntington-national-bank", "huntington national bank",
    "zions", "zions-bancorporation", "zions bancorporation",
    "fifth-third", "fifth third", "fifth-third-bank",
    "hsbc", "hsbc-bank-usa", "td bank", "td-bank", "td-bank-n-a",
}

# ── FINTECH_GUARD: never hit by the bank rule (T1 tech table; F14:
# mercury deliberately ABSENT).
FINTECH_GUARD = {
    "adyen", "payoneer", "remitly", "robinhood", "nubank", "blend",
    "crypto-com", "corpay", "coins-ph", "insurify", "alinea-invest",
    "citizen", "pave-finance", "boost-payment-solutions", "coalition-inc",
    "jack-henry-amp-associates", "jack-henry", "ai-fund",
    "airwallex", "altruist", "specialty-capital", "stash",
    "coinbase", "okx", "affirm", "visa", "mastercard", "bloomberg",
    "webull", "stripe", "q2-software-inc", "ripple",
    "fiserv", "paypal", "expedia", "chime", "block", "square", "sofi",
    "betterment", "dave", "current", "upgrade", "plaid", "mercury-com",
}

# ── PAYER_SEED: the N6B rule's company gate (T2a; deny-side deferred —
# the rule routes, not the deny path).
PAYER_SEED = {
    "humana", "unitedhealth", "unitedhealth group", "unitedhealth-group",
    "uhc", "united health", "cvs health", "cvs-health", "cvs",
    "cigna", "the cigna", "the cigna group", "cigna-group",
    "kaiser permanente", "kaiser-permanente", "aetna", "centene",
    "elevance", "elevance-health", "molina", "molina-healthcare",
}

# ── N3B bank rule token sets (T1 §2 verbatim + F7 fold) ────────────────
# tech-division tokens (rescue-first for banks; word-boundary phrase match)
BANK_TECH_TOKENS = [
    # phrases first (longer match wins)
    "ai hub", "ai measurement", "ai enablement", "ai services",
    "employee platforms", "digital assets", "digital asset",
    "risk data", "intelligent data", "core engineering",
    "enterprise platform", "enterprise ai", "software engineering",
    "data platform", "product technology", "personalized indexing",
    "advisor platform", "fraud idv", "p&c digital", "digital commerce",
    "gen ai", "55ip", "aladdin", "verint", "edmp", "ciam",
    # single tokens
    "technology", "technical", "technologies", "digital", "data",
    "platform", "platforms", "analytics", "api", "apis",
    "software", "cloud", "infrastructure", "cyber", "security",
    "automation", "developer", "engineering", "crypto", "ai", "ml",
    "genai", "decisioning", "fintech",
]
# bank business-product tokens (FP class; NEVER-gate list in design v2 F7)
BANK_PRODUCT_TOKENS = [
    "deposits", "deposit", "treasury", "treasur", "wealth management",
    "commercial banking", "business banking", "consumer community banking",
    "small business banking", "lending", "mortgage",
    "card acquisitions", "card acquisition", "consumer cards",
    "credit cards", "annuit", "annuity", "group benefits",
    "group retirement", "investment solutions", "capital management",
    "due diligence", "quantitative", "margin",
]
# NEVER tokens (T1-measured TP hazards — absent from BANK_PRODUCT on purpose):
#   utilities, payments, payroll, cards (bare), wealth advisors group

# ── N6B payer rule token sets (T2a §1 verbatim) ─────────────────────────
PAYER_BUSINESS_TOKENS = [
    "medicare", "medicaid", "insurance products", "insurance product",
    "products & benefits", "products and benefits",
    "patient support", "hub services", "hub service",
    "benefits administration",
]
PAYER_TECH_TOKENS = [
    "technic", "technolog", "digital", "data", "platform", "api",
    "ai", "ml", "interoperab", "analytics", "software", "cloud",
    "cyber", "servicenow", "automation",
]
PAYER_DIVISION_KWS = [
    "optum", "uhc digital", "centerwell", "digital pharmacy",
    "evernorth", "applied ai", "itg", "penndna", "selecthealth",
    "clinical applications", "clinical analytics",
]

# ── DIVISION_ALLOWLIST_ADD: deny+rescue entries (companyKey slug keys;
# T2a industrial mixed + T2b non-bank mixed). The legacy 4 keys
# (walmart/target/kroger/nike) stay in rolefamilies with substring match;
# these new ones match exact companyKey / seed-key + haystack
# company+title (T3's measured requirement).
DIVISION_ALLOWLIST_ADD = {
    # payers (mixed — reachable via PAYER_SEED rule, kept for the deny-side
    # future fold T2a spec'd; the rule consults PAYER_DIVISION_KWS)
    "unitedhealth-group": ["optum", "optum tech", "uhc digital", "technology"],
    "humana": ["centerwell", "digital pharmacy", "pharmacy", "technology"],
    "cigna": ["evernorth", "applied ai", "technology", "digital"],
    "cvs-health": ["cvs digital", "interoperability", "technology", "digital"],
    "kaiser-permanente": ["technology", "clinical applications", "digital"],
    # industrial (T2a mixed — deny + rescue)
    "mcdonald-s": ["technical product", "mcdonald's digital", "technology",
                   "digital"],
    "ford-motor-company": ["enterprise technology", "integrated services",
                           "model e", "digital", "software", "data",
                           "technology"],
    "general-motors": ["software", "energy software", "digital", "data",
                       "ai", "technology"],
    "ge-aerospace": ["data product marketplace", "digital technology", "data",
                     "technology"],
    "ge-vernova": ["ai", "software", "data", "technology"],
    "boeing": ["mission system software", "software", "digital", "cyber"],
    "raytheon": ["cyber", "cybersecurity", "software", "digital"],
    "siemens": ["grid software", "software", "digital industries",
                "technical"],
    "caterpillar": ["technical", "technology", "digital", "data"],
    "honeywell-technologies": ["forge", "software", "connected", "digital"],
    "honeywell": ["forge", "software", "connected", "digital"],
    "pepsico": ["data & analytics", "d&ai", "data", "digital"],
    "emerson": ["software", "technical", "test software"],
    "toyota-automated-logistics": ["software", "technology"],
    "procter-gamble": ["information technology", "data & analytics"],
    "procter-amp-gamble": ["information technology", "data & analytics"],
    # T2b non-bank mixed (pharma/medtech; 55/110 rows fire)
    "gilead-sciences-inc": ["data", "ai", "it", "technology", "workday"],
    "gilead-sciences": ["data", "ai", "it", "technology", "workday"],
    "merck": ["technical", "technology", "data"],
    "j-j-family-of-companies": ["it", "technical", "plm", "chemistry"],
    "johnson-johnson": ["it", "technical", "plm", "chemistry"],
    "edwards-lifesciences": ["software", "data", "digital"],
    # T3's Amazon split (division matcher haystack = company+title)
    "amazon": ["aws", "kiro", "agentcore", "quicksight", "velocity labs",
               "amazon leo", "kuiper", "stores fintech", "creator products",
               "prime video", "alexa", "lab126", "amazon ads",
               "-tech", "technical"],
}

# ── STAFF_BRANDS_ADD: T2b's 17 unflagged consultancies + T2a referrals
# (F5). Name forms (hyphens → spaces; the is_staffing_company prefix
# regex matches on company display names).
STAFF_BRANDS_ADD = [
    "aditi consulting", "agile resources", "bc forward", "blue signal",
    "chg healthcare", "clearbridge technology", "compri consulting",
    "elite technical", "govcio", "incedo", "inspyr solutions",
    "j vers", "jsr tech", "lumen solutions group", "ltimindtree", "ltm",
    "manpowergroup", "next step systems", "photon interactive", "photon",
    "pinnacle technical resources", "producto", "raft", "sincera",
    "prominent technology", "fstone technologies",
    "informatic technologies", "netserv", "iris software",
    "reuben cooley", "cosmic-i",
]


# ── matching helpers (both key spaces; F6) ──────────────────────────────
def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s


def _nameify(s: str) -> str:
    s = re.sub(r"[-]+", " ", (s or "").lower()).strip()
    return s


def _both_forms(entries):
    out = set()
    for e in entries:
        e = (e or "").strip().lower()
        if not e:
            continue
        out.add(e)
        out.add(_slugify(e))
        out.add(_nameify(_slugify(e)))
    return out


TECH_KEYS = _both_forms(TECH_SEED)
NONTECH_KEYS = _both_forms(NONTECH_SEED)
BANK_KEYS = _both_forms(BANK_SEED)
FINTECH_KEYS = _both_forms(FINTECH_GUARD)
PAYER_KEYS = _both_forms(PAYER_SEED)


def seed_hit(company_key: str, company_str: str, key_set: set) -> bool:
    """True when either identity form of the row matches the seed set
    (the set carries both slug and name forms)."""
    ck = (company_key or "").lower().strip()
    if ck and ck in key_set:
        return True
    # company_str: check the raw normalized name + its slug
    from .rolefamilies import company_seed_key
    cs = (company_str or "").lower().strip()
    if not cs:
        return False
    if cs in key_set or _slugify(cs) in key_set:
        return True
    sk = company_seed_key(cs)
    return bool(sk and (sk in key_set or _slugify(sk) in key_set))


def tech_seed_hit(company_key: str, company_str: str) -> bool:
    return seed_hit(company_key, company_str, TECH_KEYS)


def nontech_seed_hit(company_key: str, company_str: str) -> bool:
    return seed_hit(company_key, company_str, NONTECH_KEYS)


def bank_seed_hit(company_key: str, company_str: str) -> bool:
    return seed_hit(company_key, company_str, BANK_KEYS)


def fintech_guard_hit(company_key: str, company_str: str) -> bool:
    return seed_hit(company_key, company_str, FINTECH_KEYS)


def payer_seed_hit(company_key: str, company_str: str) -> bool:
    return seed_hit(company_key, company_str, PAYER_KEYS)
