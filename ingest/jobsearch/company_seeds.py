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
    # single tokens (S40 A2: platform/platforms DEMOTED to pair
    # primaries — solo 'Global Clearing Platform' measured BUSINESS)
    "technology", "technical", "technologies", "digital", "data",
    "analytics", "api", "apis",
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
    # S40 B-S40-3 additions (bank wave notes 6 — measured-decisive)
    "go to market", "market positioning", "industry strategy",
    "policy positions", "installments", "co branded", "co-branded",
    "virtual cards", "embedded payments", "checking", "wires",
    "trade working capital", "trade & working capital", "fx",
    "corporate trust", "escrow", "global family office",
    "deposits and cash management", "retail lending", "retail lending organization",
    "claims and disputes", "commerce offerings", "cardmembers",
    "product line", "acquisition and retention", "value propositions",
    "product marketing", "trust and agency",
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
    "technic", "technolog", "digital", "data", "api",
    "ai", "ml", "interoperab", "analytics", "software", "cloud",
    "cyber", "servicenow", "automation",
    # S40: platform DEMOTED to pair primaries (A2)
]
PAYER_TECH_DESC_TOKENS = [   # S40 A1 desc tier: specific phrases only
    "informatics", "claims platform", "care platform", "epic",
    "applied ai", "clinical applications", "clinical analytics",
    "servicenow", "interoperab", "user stories", "agile squad",
    "backlog", "fhir",
]
PAYER_DIVISION_KWS = [
    "optum", "uhc digital", "centerwell", "digital pharmacy",
    "evernorth", "applied ai", "itg", "penndna", "selecthealth",
    "clinical applications", "clinical analytics",
]

# ── S40 v2 rule data (DESIGN-S40 §2 A1/A2/B-S40-2/B-S40-3) ────────────
BANK_TECH_DESC_TOKENS = [   # A1 desc tier: SPECIFIC tool/craft phrases
    # only — boilerplate-proof (BNY 'cutting-edge AI' trap, F1 note-3)
    "appian", "salesforce", "delinea", "sdk", "developer platform",
    "api-first", "api first", "data product", "applied ai",
    "digital utilities", "informatic", "ad server", "ivr",
    "mobile product manager", "it product owner", "martech",
    "agile squad", "user stories", "backlog groom", "kubernetes",
    "containers", "platform modernization", "epic", "servicenow",
    "blockworks", "digital product vision", "internal tools",
    "fhir", "interoperab", "machine learning model",
]
BANK_OVERRIDES = {         # B-S40-2: ≥80%-decided per-company orderings
    "citi": "business_first",
    "citigroup": "business_first",
    "michigan-farm-bureau": "business_first",
}
# A2 pair rules: primaries only count with a co-token (bank wave 4/7/8)
BANK_PAIR_PRIMARIES = [
    "platform", "platforms", "agentic", "ai agent", "agents",
    "aml", "fraud", "financial crimes", "monitoring",
]
BANK_PAIR_TECH_CO = [
    "api", "developer", "engineering", "sdk", "data",
    "solutions", "technology", "build", "automation", "model",
]
BANK_PAIR_BIZ_CO = [
    "payments", "payment", "cash", "card", "lending", "market",
    "strategy", "policy", "standards", "product line",
    "positioning", "offerings", "members",
]
PAYER_PAIR_PRIMARIES = [
    "platform", "platforms", "agentic", "ai agent", "claims",
]
PAYER_PAIR_TECH_CO = [
    "api", "developer", "engineering", "automation", "data",
    "solutions", "technology", "build",
]
PAYER_PAIR_BIZ_CO = [
    "payments", "payment", "members", "benefits", "medicare",
    "market", "strategy", "policy", "offerings",
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

TECH_SEED_S40 = {  # S40 TECH fold + flips (499 keys)
    '0x', '12twenty', '2k', '2k-games',
    '3e', 'abacus-business-computer', 'abacus-insights', 'activeprospect',
    'activeprospect-inc', 'adapty', 'adobe-systems', 'adp-automatic-data-processing',
    'affinity', 'agilemd', 'airbnb', 'akamai',
    'aledade', 'aledade-pbc', 'alert-logic', 'alkira',
    'allvue', 'allvue-systems', 'alpaca', 'alpaca-health',
    'alphasense', 'alternative-payments', 'alteryx', 'amazonwebservices',
    'amboras', 'ande', 'appetize', 'appian',
    'appmysite', 'arcadia', 'archive-resale', 'arctic-wolf-networks',
    'ascend-learning', 'ascend-learning-llc', 'assembled', 'assetmark',
    'athena-health', 'atrium', 'axion-ray', 'axle-health',
    'axon', 'babylist', 'backops', 'bamboo-health',
    'beacon-biosignals', 'beacon-holdings', 'beacon-software', 'bentley-systems',
    'bill-com', 'bilt', 'bilt-rewards', 'bitpay',
    'bitsight', 'black-knight', 'black-knight-financial-services', 'black-spectacles',
    'blizzard-entertainment', 'blox-digital', 'bobtail', 'bold',
    'bottomline', 'bottomline-technologies', 'brandt-information-services', 'braze',
    'brex', 'brigit', 'broadridge', 'broadridge-financial-solutions',
    'builders-at-gigs', 'built', 'built-technologies', 'cacheflow',
    'cambium-learning-group', 'canary-technologies', 'canopy', 'cengage-group',
    'centrl', 'cerbo', 'champ', 'check-technologies',
    'checkly', 'circle-internet-financial', 'clarivate', 'clearwater-analytics-cwan',
    'cleo', 'clera', 'clinical-ink', 'clinically-ai',
    'clio', 'cloudera', 'cockroach-labs', 'cogitativo',
    'cohesity', 'colibri-group', 'combocurve-inc', 'comcate',
    'commure', 'compugroup-medical', 'concourse', 'confluent',
    'connexity', 'connexity-skimlinks-a-taboola-company', 'convosight', 'costar-group',
    'cotiviti', 'courtyard', 'cpsi', 'creatoriq',
    'cresta', 'crowdstrike', 'cvent', 'datacore-france',
    'datavant', 'datavisor', 'dealeron-inc', 'deepmind',
    'delinea', 'dell', 'dell-technologies', 'deque-systems',
    'dioptra', 'direct-supply', 'dnsfilter', 'doceree',
    'domaintools', 'doordash', 'dorsia', 'doubleverify',
    'draft-kings', 'draftkings', 'drivecentric', 'dtn',
    'dun-bradstreet', 'dynatrace', 'earnin', 'ebsco',
    'egnyte', 'ema', 'emsi', 'endear',
    'enfos-inc', 'engine', 'enterprise-security', 'entrust',
    'epsilon', 'equifax', 'equiniti', 'etsy',
    'everseen', 'exciting', 'exiger', 'experian',
    'f-lix', 'f5', 'f5-networks', 'fabulous-inc',
    'facebook', 'faire', 'faro-health-inc', 'fastly',
    'felix-technologies', 'fetch-rewards', 'fictiv', 'fiddler-ai',
    'fieldguide', 'fieldpulse', 'finastra', 'finboa-inc',
    'finite-state', 'fireworks-ai', 'firstclose', 'fivetran',
    'flexi-software', 'flexport', 'florence', 'florence-healthcare',
    'foreflight', 'fortra', 'freshworks', 'gainsight',
    'gen-digital', 'genesys', 'ghx', 'google-deepmind',
    'gt-motive', 'gusto', 'halcyon', 'hansen-technologies',
    'harris-computer', 'healerz', 'healthleap', 'healthstream',
    'healthverity', 'healthy-roster', 'heidi', 'hercules',
    'hewlett-packard-enterprise', 'higharc', 'highlevel', 'hopper',
    'hungerrush', 'i3-verticals', 'iboss', 'ifs',
    'imagetrend', 'imply', 'imprivata', 'improvado',
    'impruvon', 'industrial-and-financial-systems', 'infor', 'infotech-current-openings',
    'inkitt', 'inovalon', 'inriver', 'instalily-ai',
    'intapp', 'integra-connect', 'integrichain', 'internet-brands',
    'intersystems', 'ionq-inc', 'irys', 'jack-henry-associates',
    'jd-com', 'jerry-ai', 'jerry-insurance-agency', 'joveo-inc',
    'justfund-us', 'justpark', 'justworks', 'kalshi-inc',
    'kaseya-careers', 'katalyze-ai-inc', 'kayak', 'kayak-europe',
    'keka-technologies-private-limited', 'keyfactor', 'keyfactor-inc', 'kicksite',
    'kikoff', 'knowtex', 'kong', 'kueski',
    'ladders', 'launchdarkly', 'legion', 'leland',
    'lightning-ai', 'lilt', 'litify', 'livekit',
    'liveramp', 'lodgify', 'loop', 'luma-financial-technologies',
    'lyft', 'macstadium', 'magnet-forensics-inc', 'maintainx',
    'marble-technology', 'marginedge', 'maven-agi', 'mcafee',
    'mcafee-inc', 'mcleod-software', 'medallia', 'mend',
    'mercor', 'mercury', 'metriport', 'metropolis',
    'mews', 'mineral', 'mitratech', 'mixpanel',
    'modesens', 'modmed', 'momentive-software', 'momentive-software-inc',
    'moneylion', 'motive', 'mudflap', 'nash',
    'nebius', 'neighbor', 'neogov', 'nexhealth',
    'nice-systems', 'nielsen', 'niural-inc', 'nocd',
    'noma-security', 'normal-computing-corporation', 'northbeam-llc', 'notion',
    'nova-credit', 'nucleus-security', 'nutanix', 'obin-ai',
    'oligo-security', 'omada-health', 'ooak-data', 'openart-ai',
    'openkrill', 'openlane', 'opentext', 'optionmetrics',
    'origami-risk', 'orkes', 'ornn-com', 'osmo',
    'outdoor-holding-company', 'outreach', 'paessler-gmbh', 'pagerduty',
    'pax8', 'paxafe', 'paxos', 'paysafe',
    'pdi-technologies', 'peerspace', 'pendo', 'permitflow',
    'perry-weather', 'pharos-systems-international-inc', 'pi-ata-rent', 'pilot',
    'pinterest', 'planet-labs', 'planetart', 'playstation',
    'polymarket', 'posthog', 'power-factors', 'powerschool',
    'powerschool-group-llc', 'prava-therapy', 'prefect', 'prescryptive-health-inc',
    'product-pulse', 'pros', 'provation', 'ptc',
    'pubmatic', 'q2-software', 'qad', 'quadient',
    'qualio', 'qualys', 'quantifind', 'quora',
    'quorum-software', 'radical-ai-inc', 'rain', 'rapid7',
    'rb-global', 'realpage', 'realpage-inc', 'realtor-com',
    'recidiviz', 'recoursehealth-com', 'recurly', 'reddit',
    'reddit-inc', 'redis', 'redo', 'reflex-media-inc',
    'regal-voice', 'regard', 'relativity', 'relx',
    'resurety', 'revel', 'rightway-healthcare', 'ripple',
    'rippling', 'riskmethods', 'rm-education', 'roblox',
    'rocket-money', 'roku', 'rosnet', 'ruby-labs',
    's-p-global', 'salsify', 'salv', 'samba-tv',
    'samsara', 'samsara-inc', 'samsung', 'sandboxaq',
    'sap', 'sap-americas-inc', 'sapience-ai-corporation', 'sas',
    'sas-institute', 'scale-ai', 'scrambly', 'screenpipe',
    'scribd', 'seismic', 'sequencing', 'shutterstock',
    'sierra', 'signant-health', 'similarweb', 'skillable',
    'skylo-technologies', 'smartcat', 'smartleaf', 'smartrent',
    'socure', 'solera', 'sony-interactive-entertainment-america', 'sony-interactive-entertainment-sie',
    'sophos', 'sourcegraph', 'speak', 'spoton',
    'sprout-social', 'ss-c', 'ss-c-technologies', 'starburst-data',
    'starrez', 'stream', 'striveworks', 'stuut',
    'substack', 'superhuman', 'surecost', 'swingvision',
    'sycle-llc', 'syniti', 'synopsys', 'teachable',
    'teamviewer', 'tekion-corp', 'tekmetric', 'temporal-technologies',
    'tempus-ai', 'tenable', 'tencent', 'tenna',
    'the-nielsen-company', 'thunes', 'tifin', 'titl',
    'topaz-labs', 'tp-link', 'traba', 'trellance-llc',
    'tripcom', 'triple-whale', 'tubi-tv', 'tucuvi',
    'tyler-technologies', 'uber', 'ultimate-kronos-group', 'ultimate-software',
    'ura', 'vanta', 'vast-ai', 'verisign',
    'verisk-analytics', 'verkada', 'via-transportation', 'videa-health',
    'voize', 'vultr', 'wayve', 'wayward',
    'wealthfront', 'weave', 'weaviate', 'whatnot',
    'whoop', 'wikimedia-foundation', 'willow-education', 'wolters-kluwer-elm-solutions-inc',
    'wolters-kluwer-n-v', 'workstream', 'writer', 'x-company',
    'xceptor', 'xsolla', 'yardstik', 'yext',
    'yipitdata', 'youtube', 'zapier', 'zeitview',
    'zillow', 'zinnia', 'zip',
}
NONTECH_SEED_S40 = {  # S40 NONTECH fold minus flips (569 keys)
    '1-800-got-junk-llc', '10x-health-system', '10x-national-security', '1x',
    '3core-systems-inc', '3m', '6046-janssen-supply-group-legal-entity', '6084-janssen-research-development-legal-entity',
    '7-eleven', 'aalberts-integrated-piping-systems', 'abbvie', 'accenture',
    'acosta', 'acuity-brands', 'advance-local', 'advanced-micro-devices',
    'aerovironment', 'agileengine', 'agilent-technologies', 'agilix-solutions',
    'agreeya-solutions', 'ags-american-gaming-systems', 'alice-olivia', 'allegion',
    'alliantgroup', 'altana-ag', 'altar-d-state', 'alto-shaam',
    'american-airlines', 'american-eagle', 'american-medical-association', 'amla-commerce',
    'amphenol-communications-solutions', 'analog-devices', 'aon-corporation', 'apogee-engineering-llc',
    'applied-materials', 'apptronik', 'aps', 'aptiv',
    'aramark', 'arc-teryx', 'arm', 'arthrex',
    'arup-laboratories', 'ashley-furniture-industries', 'asml', 'assistrx-llc',
    'astrodyne', 'astrodyne-tdi', 'autocar', 'autonation',
    'autonation-mobile-service', 'autozone', 'av-inc', 'avis-budget-group',
    'axcelis', 'axiologic-solutions', 'bain-company', 'baker-tilly',
    'baltimore-aircoil-company', 'banfield-pet-hospital', 'bapi', 'base-power-company',
    'bd', 'bdo', 'bealls-inc', 'becton-dickinson-and-co',
    'belkin', 'berkeley-research-group-llc', 'best-buy', 'best-version-media',
    'bigbelly-solar', 'biostem-technologies', 'bits-in-glass', 'bj-s-wholesale-club',
    'bleacher-report', 'blend360', 'blockworks', 'blue-bottle-coffee',
    'blue-river-technology', 'bluechew', 'book-of-the-month', 'bosch',
    'brady', 'brady-corporation', 'brewer-science', 'bridgenext',
    'bridgestone-americas', 'brighton', 'bristol-myers-squibb', 'broadcom',
    'brycetech', 'building-automation-products', 'burns-mcdonnell', 'buyers-products-company',
    'by-light-professional-it-services', 'cabi-llc', 'caesars-entertainment', 'capgemini',
    'caraway-home', 'carecentrix', 'cargill', 'carrier',
    'carrier-corporation', 'carter-s-oshkosh', 'cathexis', 'cbre',
    'cbts', 'cdw', 'celero-communications-inc', 'cencora',
    'cfo-trane-u-s-inc-cfo', 'cgi', 'cgs-federal-contact-government-services', 'chamberlain-group',
    'charles-river-laboratories', 'cheiron', 'city-of-new-york', 'clayco',
    'clean-harbors', 'clindcast', 'cochlear', 'code-and-theory',
    'cognizant', 'colonial-downs', 'coloplast', 'comcast',
    'confiz', 'connectiverx', 'consilio', 'constellation-brands',
    'construction-specialties', 'continental-industry', 'corewell-health', 'cornerstone-building-brands',
    'corning', 'corning-incorporated', 'cox-little-company', 'creative-artists-agency',
    'cricut', 'crown-equipment-corporation', 'ctc', 'cummins',
    'curana-health', 'cyderes', 'daikin', 'dallasnews',
    'damco', 'danfoss', 'davidson', 'delta-air-lines',
    'delta-light-group-north-america', 'department-of-homeland-security-headquarters', 'diebold-nixdorf', 'disney-interactive',
    'dollar-general', 'domino-print', 'dover-corporation', 'dr-reddy-s-laboratories',
    'dxc-technology', 'ecolab', 'ecom-brand', 'elanco',
    'electric-power-engineers', 'electroswitch-switches-relays', 'eli-health', 'enersys',
    'engie-impact', 'enlighten', 'ensemble-health-partners', 'entegris',
    'envista', 'epam-systems', 'epson', 'ericsson',
    'ernst-young-advisory-services-sdn-bhd', 'express', 'exxonmobil', 'ey',
    'eyeglass-world', 'fanatics-collectibles', 'faraday-future', 'fashionphile',
    'flexco', 'flinn', 'fluxon', 'for-people',
    'formlabs', 'fortive', 'forvis-mazars', 'four-seasons-new-york-worldwide-sales-office',
    'frank-darling', 'franklincovey', 'freshpoint', 'fujifilm',
    'future-plc', 'garmin', 'gator-co-gator-cases-cableworks-frameworks-rackworks-levy-s-music-accessories', 'ge-healthcare',
    'generac', 'generac-power-systems', 'general-dynamics', 'genesis',
    'gentex', 'genuine-parts-company', 'georgia-pacific', 'getinge',
    'global-partners', 'graco', 'grail', 'grainger',
    'grainger-businesses', 'green-thumb-industries', 'grt-rubber', 'guitar-center',
    'gvw-group', 'h-e-b', 'hadrian', 'haleon',
    'halliburton', 'hand2mind', 'hard-rock-international', 'hasbro-inc',
    'haworth', 'hca-healthcare', 'hearst', 'heb',
    'hid', 'hii-mission-technologies-division', 'hilti', 'hilton',
    'hinge-health', 'hisense-usa', 'holman', 'holman-automotive-group',
    'home-depot', 'honeywell-aerospace', 'hp', 'huntington-ingalls-industries',
    'husqvarna', 'husqvarna-group', 'hyatt-corporatation', 'hydac',
    'iberdrola', 'icf', 'icf-international', 'icp-group',
    'idex-corporation', 'idexx', 'ierus-technologies', 'ihg-hotels-resorts',
    'illinois-tool-works', 'illumina', 'indiana-packers-corporation', 'inficon',
    'infiltrator-water-technologies', 'infosys', 'infosys-technologies', 'integrated-dna-technologies',
    'intermountain-health', 'intersources', 'intuitive-surgical', 'iqvia',
    'iridium-communications', 'iridium-satellite-llc', 'itron', 'jabil',
    'jacobs', 'jj', 'johns-manville', 'johnson-controls',
    'justrite', 'kampgrounds-of-america', 'kemet', 'kemet-electronics-corporation',
    'keysight-technologies', 'kimberbell', 'klein-tools', 'koch-industries',
    'komatsu', 'kone', 'kontron-america', 'kp-aviation',
    'kpmg-us', 'kqed', 'kraft-heinz', 'kunai',
    'kunai-part-of-the-pwc-network', 'kyndryl', 'l3harris-technologies', 'labcorp',
    'land-o-lakes', 'leafhome', 'learning-care-group', 'legrand',
    'lennox', 'levi-ray-shoup-inc', 'leviton-manufacturing', 'lg-electronics',
    'lifestation', 'lockheed-martin', 'logitech', 'lonza',
    'lowe-s', 'ls-tractor-usa', 'lumafield', 'm9-solutions',
    'mack-trucks', 'maersk-a-k-a-a-p-moller', 'malvern-panalytical', 'marathon-petroleum',
    'mariana-minerals', 'marketsphere-unclaimed-property-specialists', 'marlabs-llc', 'marmon-holdings',
    'marsh', 'marsh-mclennan', 'mattel', 'mattelinc',
    'mattermore', 'mattson-technology', 'maxcess', 'maximus',
    'mayo-clinic', 'mccarthy-building-companies', 'mckesson', 'mckinstry-co',
    'medtronic', 'michaels-stores', 'milliman', 'milwaukee-electric-tool',
    'milwaukee-tool', 'miracle-software-systems-inc', 'miso', 'mitutoyo-america',
    'molex', 'mom-s-meals-a-purfoods-company', 'monks', 'motional',
    'motorola', 'msd', 'napa-home-garden', 'national-vision-inc',
    'navistar', 'nbcuniversal', 'neros-technologies', 'nexstar',
    'nextera-energy', 'nextera-energy-inc', 'nextera-energy-resources', 'nimble-robotics',
    'nisum', 'njstateofficeofinnovation', 'nordstrom', 'norfolk-southern',
    'northrop-grumman', 'northwestern-medicine', 'northwestern-memorial-hospital', 'novartis',
    'novelis', 'novo-nordisk', 'novo-nordisk-inc', 'novolex',
    'novonesis', 'novozymes', 'nox-metals', 'nrg',
    'ntt-data', 'ntt-data-americas-inc', 'ntt-data-inc', 'nucor',
    'nxp-semiconductors', 'nyp-holdings-inc', 'nyu-langone-health', 'o-reilly-auto-parts',
    'office-of-technology-and-innovation', 'oge-energy-corp', 'ollie-s-bargain-outlet', 'olympus',
    'omnicom', 'omnivision', 'omron', 'omron-automation',
    'on-location-inc', 'onto-innovation', 'outcomes', 'outfront-media',
    'owens-corning', 'ox-group-global', 'pacbio', 'panasonic',
    'paramount', 'parsons', 'patterson-uti', 'pds-health',
    'pearson', 'pentair', 'petco', 'pfizer',
    'pg-e', 'pga-tour', 'phil', 'philips',
    'pilot-thomas-logistics', 'pitney-bowes', 'plaud', 'pods-enterprises-llc',
    'ppg-industries', 'preferred-hotels-resorts', 'prenuvo', 'pricewaterhousecoopers-llp',
    'production-resource-group', 'propharma-group', 'prophet', 'prosum',
    'prysmian', 'pseg', 'psomagen-inc', 'publicis-groupe',
    'publicis-media', 'pure-storage', 'quanex', 'quera-computing',
    'r1-rcm', 'ralph-lauren', 'ramsey-solutions', 'raycon-inc',
    'regeneron-pharmaceutical', 'regeneron-pharmaceuticals', 'rei', 'relanto-inc',
    'relativity-space', 'renesas-electronics', 'resound', 'retail-solutions-unlimited',
    'rhythm-energy', 'robots-pencils', 'rockwell-automation', 'royal-caribbean-cruises',
    'rtx', 'ryan-llc', 'ryder', 'ryder-system',
    's-s-activewear', 'sage-publications', 'sam-s-club', 'sandisk',
    'sanmina-sci', 'sanofi', 'sanofi-u-s', 'sany-america-inc',
    'sarnova', 'saronic', 'schneider-electric', 'scout-motors',
    'sentrycs', 'sentrycs-counter-drone-solutions-adapting-at-the-speed-of', 'servier', 'shield-ai',
    'signature-aviation', 'sinclair-broadcast-group', 'sineng-electric-inc', 'skechers',
    'skylight', 'social-security-administration', 'solventum', 'sonifi',
    'sonova', 'soprema-usa', 'southern-california-edison', 'spa',
    'specialized', 'spencer-s', 'speridian-technologies', 'sprouts-farmers-market',
    'spx-cooling-technologies', 'spx-corporation', 'spx-technologies', 'starbucks',
    'stealth-ai-startup', 'steel-dynamics', 'steinhafels', 'stellantis',
    'stellus-rx-llc', 'stryker', 'subway', 'sumeru-solutions',
    'sun-king', 'sunrise-medical', 'sunsource', 'supplyhouse',
    'supplyhouse-com', 'synechron', 'sysco', 'tactile-medical',
    'tailored-brands', 'takeda-pharmaceutical', 'tdk-aisight', 'technipfmc',
    'technogen-inc', 'tegna', 'teladoc-health', 'tempus',
    'the-clorox-company', 'the-ritz-carlton-yacht-collection', 'thermofisher-scientific', 'thyme-care',
    'thyssenkrupp-materials-na-inc', 'tiny-health', 'tko', 'tovala',
    'trademark-global-llc', 'trane-technologies', 'transgrid-energy', 'travel-leisure-co',
    'travel-leisure-resort-development-inc', 'trc-companies', 'trex-company', 'trueanomalyinc',
    'tutor-intelligence', 'two-six-technologies', 'u-s-news-world-report', 'u-s-renal-care',
    'u-s-renal-care-inc', 'undocked', 'unfi', 'unigroup',
    'united-imaging-north-america', 'united-states-government', 'universal-plant-services', 'ups',
    'urban-outfitters', 'urbn', 'us-government-jobs-other-agencies', 'usg-corporation',
    'usic', 'ust', 'valmet', 'vast',
    'vast-space', 'veir', 'verifone', 'verily-life-sciences',
    'vertex-pharmaceuticals', 'vertiv', 'vesync', 'veterinary-emergency-group',
    'vf-corporation', 'viasat', 'virginia-jobs', 'vontier',
    'warner-bros-discovery', 'warner-music', 'washington-post', 'weatherford-international',
    'weis-markets', 'western-states-cat', 'winsupply', 'wiwynn-international-corporation',
    'world-wide-technology', 'x-energy', 'xoriant-corporation', 'yibi-group-global-manufacturing-partner-for-luxury-jewelry-leather-goods-hardware',
    'yum-brands', 'zebra-technologies-corporation', 'zeiss', 'zipline',
    'zs-associates',
}
BANK_SEED_S40 = {  # S40 BANK fold minus zip-co-limited (125 keys)
    'aaa-auto-club-enterprises', 'af-group', 'alliant-credit-union', 'allied-solutions',
    'allied-world', 'america-first-credit-union', 'american-financial-group', 'ameriprise',
    'ameriprise-financial', 'apex-group', 'ares-wealth-management-solutions', 'argo-group',
    'ascensus', 'atlantic-union-bank', 'axis-capital', 'axos-bank',
    'bank-of-america', 'bank-of-oklahoma', 'bank-ozk', 'bankunited',
    'berkley', 'berkshire-hathaway-guard', 'berkshire-hathaway-specialty-insurance', 'beyond-finance',
    'biberk-insurance-services', 'bmo-u-s', 'bok-financial', 'cathay-bank',
    'central-bank', 'citizens-bank-n-a', 'cme-group-inc', 'columbia-banking-system-inc',
    'communityamerica-credit-union', 'compeer-financial', 'cr-dit-agricole-cib', 'crc-group',
    'crum-forster', 'crum-forster-insurance', 'customers-bank', 'edward-jones',
    'empower-retirement', 'facet', 'fannie-mae', 'farmers-insurance-group',
    'federal-reserve-bank', 'fidelity-investments-careers', 'first-american', 'first-american-financial-corporation',
    'fitch-group', 'frontline-insurance-managers-inc', 'galaxy', 'geico',
    'general-motors-financial', 'genworth', 'global-x-etfs', 'gm-financial',
    'golden1-credit-union', 'great-american-insurance', 'great-gray', 'grow-financial-federal-credit-union',
    'guard-insurance-group', 'hamilton-lane', 'harbourvest-partners', 'hiscox',
    'hiscox-ag', 'hudson-river-trading', 'imc', 'imc-inc',
    'independence-pet-group', 'integrated-specialty-coverages-llc', 'jp-morgan-chase', 'keycorp',
    'liberty-mutual', 'liberty-mutual-insurance', 'lseg-london-stock-exchange-group', 'manulife',
    'massmutual', 'mem-insurance', 'mission-wealth-management-lp', 'mizuho-bank',
    'msci-inc', 'msi', 'mufg', 'mulligan-funding-llc',
    'munich-re', 'mutual-of-enumclaw', 'mutual-of-enumclaw-insurance', 'nelnet',
    'nicolet-national-bank', 'oportun', 'otr-solutions', 'pennymac',
    'plymouth-rock-assurance', 'premier-bankcard-llc', 'quicken-loans', 'royal-bank-of-canada',
    'rural-mutual-insurance', 'sagesure', 'seacoastbank', 'securian-financial',
    'security-benefit-life-insurance-company', 'selective-insurance', 'selective-insurance-company-of-america', 'shelter-insurance-companies',
    'silicon-valley-bank', 'simmons-bank', 'southstate-bank', 'southwest-business-corporation',
    'state-auto', 'stifel-financial', 'swbc', 'symetra-life-insurance-company',
    'synovus', 't-rowe-price', 'texas-dow-employees-credit-union', 'the-blackstone-group-l-p',
    'the-hartford', 'the-pnc-financial-services-group', 'thrivent', 'thrivent-financial-for-lutherans',
    'utica-national-insurance', 'wellington-management', 'western-southern-financial-group', 'wintermute',
    'zions-bank',
}
PAYER_SEED_S40 = {  # S40 PAYER fold (12 keys)
    'bright-health', 'cambia-health-solutions', 'caresource', 'centene-commercial-solutions',
    'centerwell', 'communitycare', 'communitycare-hmo-inc', 'delta-dental-of-california',
    'devoted-health', 'highmark-health', 'nationsbenefits', 'scan-health-plan',
}
STAFF_SEED_S40 = {  # S40 staffing/agency fold — companyKey slugs (A3) (179 keys)
    '4-corner-resources', 'ac-lion', 'accruepartners', 'acquisition-network',
    'adecco', 'adecco-us-inc', 'adnet', 'advanced-technology-search',
    'affinity-search-partners', 'alliance-search-group', 'apollo-professional-solutions', 'aquent',
    'axelon-services', 'aziro-technologies-llc', 'baer', 'barrington-james',
    'bcforward', 'bemana', 'bitsoft-international-inc', 'brainworks',
    'bravotech', 'career-development-partners', 'carman-solutions-group', 'cayuse-holdings-llc',
    'client-growth-resources', 'clifyx', 'collabera', 'conch-technologies',
    'confisa-international-group', 'consortia', 'consultnet', 'covetus',
    'creative-circle', 'curate-partners', 'cyber-sphere-llc', 'cybercoders',
    'cynet-systems', 'danta-technologies', 'david-joseph-company', 'delan-associates-inc',
    'dexian-disys', 'digital-technology-solutions', 'diverse-lynx', 'divihn-integration-inc',
    'e-solutions', 'eapps-tech-llc-dba-magicforce', 'epsilon-solutions-ltd', 'espire-technologies',
    'everest-search-partners', 'first-soft-solutions', 'flextg', 'fullstack',
    'g2i', 'gables-search-group', 'generis-tek-inc', 'geopaq-logic',
    'georgia-it-inc', 'globifye', 'glocomms', 'go2group-llc',
    'gsrinc', 'gurulink', 'hallmark-global-solutions-ltd', 'harvey-nash',
    'hays', 'hireeazy', 'hireeducation', 'honorvet-technologies',
    'hophr', 'hug', 'humac-inc', 'iconma',
    'idr-inc', 'info-dinamica-inc', 'infodyne-solutions', 'innovative-information-technologies-inc',
    'intraedge', 'it-minds-llc', 'jacobs-management-group', 'jconnect-infotech',
    'jobformance-llc', 'jobsrus', 'jrg-partners-llc', 'juno-search-partners',
    'kaltechsoft', 'kaygen', 'kbc-technologies-inc', 'kds-strategic-search-group',
    'kellymitchell', 'kmm-technologies-inc', 'kob-solutions', 'kutir-inc',
    'labur', 'lancesoft', 'lighthouse-technology-services', 'major-scale',
    'manifest-solutions', 'maxonic-inc', 'metasense', 'mondo',
    'mpower-plus-rezolve-ai-group-ltd', 'mural-group', 'nasscomm-inc', 'netline-technologies-llc',
    'next-level-business-services-inc', 'nextdeavor', 'nextpath-workforce-solutions', 'noblesoft-technologies',
    'omni-inclusive', 'otsi', 'palni-inc', 'peaklnk-technologies-inc',
    'pegasys-information-technologies', 'pinnacle-search', 'pra-usa', 'producto',
    'profiles', 'prophecy-technologies', 'protingent-inc', 'quantum-technologies-llc',
    'raydar', 'rcm-health-care-services', 'rootshell-enterprise-technologies-inc', 'russell-tobin-associates',
    'satcon-inc', 'scientific-search', 'scout-global', 'selby-jennings',
    'sgic-cloud-technologies-inc', 'sharpatoms', 'smart-ims-inc', 'smith-arnold-partners',
    'softratech-llc', 'software-resources', 'solomon-page', 'spectraforce',
    'sradha-technologies-llc', 'ssv-technologies-inc', 'sts-technical-services', 'sunrise-systems-inc',
    'swift-hr-solutions-inc', 'syntricate-technologies', 'system-one', 'systemsaccountants',
    'talentarchitect-com', 'tandym-tech', 'tasc-technical-services', 'tech-rakers',
    'tech-tammina', 'techdigital-group', 'technocraft-solutions', 'teksystems',
    'tekwissen-llc', 'testingxperts-inc-dba-damcosoft', 'the-caler-group', 'the-carrera-agency',
    'the-custom-group-of-companies', 'the-cypress-group', 'the-intersect-group', 'the-reserves-network-inc',
    'the-thomas-edwards-group', 'thorndale-partners', 'tier4-group', 'tms',
    'transparent-search-group', 'tranzeal', 'tranzeal-inc', 'v-centrix-us-llc',
    'vaco-by-highspring', 'veritas-search-group', 'vision-infotech-inc', 'whalen-search-group',
    'whitecap-search', 'wss-associates', 'yochana-it-solutions', 'yoh',
    'yoh-a-day-zimmerman-company', 'zachary-piper-solutions', 'zipstaff',
}
DIVISION_ALLOWLIST_S40 = {  # S40 division rescues — re-derived row text (A5) (22 companies)
    'samsung': ['mobile payment'],
    'hp': ['software product', 'hp solutions'],
    'epson': ['subscription program'],
    'logitech': ['l4b'],
    'pure-storage': ['data management'],
    'northrop-grumman': ['software product', 'sw product', 'agile product owner'],
    'rockwell-automation': ['plex', 'data integration', 'ai product owner'],
    'marathon-petroleum': ['cybersecurity', 'fusion center'],
    'mckesson': ['analytics'],
    'pga-tour': ['shotlink'],
    'pearson': ['technical product'],
    'pilot-thomas-logistics': ['onshape', 'cpq'],
    '3m': ['ecommerce', 'digital'],
    'applied-materials': ['enterprise ai'],
    'intuitive-surgical': ['downstream software', 'digital solutions'],
    'getinge': ['act digital', 'digital solutions'],
    'caesars-entertainment': ['poker'],
    'hard-rock-international': ['casino games', 'game producer'],
    '7-eleven': ['digital product management'],
    'autozone': ['b2c'],
    'hilton': ['digital'],
    'avis-budget-group': ['web and ai'],
}

# merges (precedence unchanged: TECH first in sector_verdict)
TECH_SEED = TECH_SEED | TECH_SEED_S40
NONTECH_SEED = NONTECH_SEED | NONTECH_SEED_S40
BANK_SEED = BANK_SEED | BANK_SEED_S40
PAYER_SEED = PAYER_SEED | PAYER_SEED_S40
FINTECH_GUARD = FINTECH_GUARD - {"ripple", "mercury"}  # A8 explicit
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
