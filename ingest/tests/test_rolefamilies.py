"""test_rolefamilies.py — the S36 taxonomy contracts (D-S36-1).

Pins DESIGN-S36 §2 (v2.1): the R2 adversarial tables (family +
seniority + G1 + the merge case), R3's mechanical amendments (the
re.VERBOSE compile contract, the 2-of-4 noun-first FP closure), and
the corpus distribution post-closure (167/47/312 on 526 product-word
titles — R2's pre-closure 171/51/304 with their own P2-5/P1-4 fixes
applied, exactly the delta the closure was designed to produce).
"""
import re

import pytest

from jobsearch.rolefamilies import (
    G1,
    PM_CORE,
    PM_QUERY_CORE,
    PM_QUERY_TAIL,
    _ADJACENT_CLOSED,
    _BARE_PM,
    _HEAD_DENY,
    _NOUN_FIRST_CHAIN,
    classify_family,
    g1_fires,
    pm_seniority,
    sector_verdict,
    title_core,
)

# ── R2's 40-title adversarial table (+ R3 pins) ─────────────────────────────
# (title, query_is_pm, family, seniority)
FAMILY_SENIORITY = [
    ("Product Manager II", False, "pm", "mid"),
    ("Product Manager, Payments", False, "pm", "mid"),
    ("Product Manager (Remote)", False, "pm", "mid"),
    ("Product Manager III", False, "pm", "senior"),
    ("Product Manager I", False, "pm", "junior"),
    ("Product Owner - Scrum", False, "pm", "mid"),
    ("Technical Product Owner", False, "pm", "mid"),
    ("Senior Manager, Product", False, "pm", "senior"),
    ("Manager, Product Management", False, "pm", "mid"),
    ("Product Mgr", False, "pm", "mid"),
    ("Sr Manager, Product Line Cost", False, "pm", "senior"),
    ("AI/HPC Product Line Manager", False, "pm", "mid"),
    ("Storage Product Line Manager", False, "pm", "mid"),
    ("North America Product Manager: Localization & GTM Strategy", False, "pm", "mid"),
    ("Product GM, Spend", False, "pm", "director+"),
    ("Group Product Manager, Plans Innovation", False, "pm", "senior"),
    ("Group Product Manager, Title Launch Management", False, "pm", "senior"),
    ("Director of Product", False, "pm", "director+"),
    ("VP of Product", False, "pm", "director+"),
    ("Associate Director, Product", False, "pm", "director+"),
    ("Distinguished PM", True, "pm", "senior"),
    ("Staff PM", True, "pm", "senior"),
    ("Principal Group PM", True, "pm", "senior"),
    ("Sr PM, Platform", True, "pm", "senior"),
    ("Group PM", True, "pm", "senior"),
    ("PM II", True, "pm", "mid"),
    ("PM II", False, "other", "mid"),           # no provenance -> NOT pm
    # redirects (positional function-adjacency)
    ("Director, Product Legal (EmFi & Payments)", False, "other", "director+"),
    ("Associate Director, Talent Acquisition (Product & Design)", False, "other", "director+"),
    ("Head of Product Design", False, "other", "director+"),
    ("Senior Product Security Engineer", False, "other", "senior"),
    ("New Product Introduction (NPI) Sourcing Manager", False, "other", "mid"),
    # positional poison: specialization tails stay PM
    ("Senior Product Manager, Identity & User Security", False, "pm", "senior"),
    ("Staff Product Manager, Embedded Finance", False, "pm", "senior"),
    ("Product Manager - Content Finance", False, "pm", "mid"),
    ("Product Manager, Ads (Billing and Finance)", False, "pm", "mid"),
    ("Product Manager, Financial Engineering", False, "pm", "mid"),
    ("Senior Manager, Product - Residential HVAC", False, "pm", "senior"),
    # adjacent closed set
    ("Senior Product Marketing Manager", False, "adjacent", "senior"),
    ("Product Marketing Specialist", False, "other", "mid"),
    ("Product Marketing Engineer", False, "other", "mid"),
    ("Manager, Product Marketing - Member Experience", False, "adjacent", "mid"),
    ("Head of Product Marketing", False, "adjacent", "director+"),
    ("Product Operations Specialist", False, "other", "mid"),
    ("Director, Product Operations", False, "adjacent", "director+"),
    ("Product Analyst", False, "adjacent", "mid"),
    ("Technical Program Manager", False, "adjacent", "mid"),
    ("Product Marketer, T:0", False, "adjacent", "mid"),
    ("Product Program Manager", False, "adjacent", "mid"),
    # noun-first FP closure (R3: all 4)
    ("GRC Program Manager, Product Lifecycle Assurance", False, "other", "mid"),
    ("Manager, Product & Technology Editorial Communications", False, "other", "mid"),
    ("Administrative Assistant, Product Management", False, "other", "mid"),
    ("Manager, Product Development Engineering - HBM", False, "other", "mid"),
    ("Sr. Specialist, Mobile SW Product Mgmt.", False, "other", "senior"),
    # production-manager boundary (the design's own trap)
    ("Production Manager", False, "other", "mid"),
    ("Senior Production Manager, Assembly", False, "other", "senior"),
    # retail banking / app-store / adtech pass family (G1 tested below)
    ("Product Manager, Retail Banking", False, "pm", "mid"),
    ("Product Manager, Google Play Store", False, "pm", "mid"),
    ("Product Manager, Brand Safety", False, "pm", "mid"),
    # seniority guards
    ("Entry Level Product Manager", False, "pm", "junior"),
    ("Product Manager, Entry", False, "pm", "junior"),
    ("Junior Product Manager", False, "pm", "junior"),
    ("Associate Product Manager", False, "pm", "junior"),
    ("Product Management Intern", False, "pm", "junior"),
    ("Chief Product Officer", False, "pm", "director+"),
    ("President, Product", False, "pm", "director+"),
    ("Lead Product Manager", False, "pm", "senior"),
    ("Product Lead", False, "pm", "senior"),
]


@pytest.mark.parametrize("title,q,fam,sen", FAMILY_SENIORITY)
def test_family_and_seniority(title, q, fam, sen):
    got_fam, ev = classify_family(title, query_is_pm=q)
    assert got_fam == fam, f"{title!r}: family {got_fam} != {fam}"
    assert pm_seniority(title) == sen, f"{title!r}: seniority mismatch"
    if q and got_fam == "pm" and "PM" in title and "product" not in title.lower():
        assert ev == "qp"          # bare-PM rows carry query provenance


def test_verbose_compile_contract():
    """R3 mechanical finding: the multi-line patterns REQUIRE
    re.VERBOSE — a naive re.I compile matches almost nothing."""
    title = "Product Manager, Payments"
    naive = re.compile(PM_CORE.pattern, re.I)
    assert not naive.search(title)   # the naive compile is broken
    assert PM_CORE.search(title)     # the shipped compile works
    g1_naive = re.compile(G1.pattern, re.I)
    assert not g1_naive.search("Product Manager, Grocery Category")
    assert G1.search("Product Manager, Grocery Category")


# ── G1 (R2's amended table — 11/11) ────────────────────────────────────────
G1_TABLE = [
    ("Product Manager, Retail Banking", False),
    ("Product Manager, Google Play Store", False),
    ("Product Manager, App Store", False),
    ("Product Manager, Amazon Store", False),
    ("Senior Product Manager, Retail Media", False),
    ("Product Manager, Brand Safety", False),
    ("Retail Product Manager, Store Operations", True),
    ("Product Manager, Grocery Category", True),
    ("Category Manager – Grocery", True),
    ("Merchandising Product Analyst", True),
    ("Product Manager - SKU Rationalization", True),
]


@pytest.mark.parametrize("title,want", G1_TABLE)
def test_g1(title, want):
    assert g1_fires(title) is want


# ── G2/G3 sector verdicts ─────────────────────────────────────────────────
def test_sector_verdicts():
    # division rescue (strongest evidence)
    act, ev, reason = sector_verdict("Product Manager", "Walmart Global Tech")
    assert (act, ev) == ("keep", "division")
    act, ev, _ = sector_verdict("Product Manager", "Kroger 84.51")
    assert (act, ev) == ("keep", "division")
    # grocery deny, no rescue -> recoverable bucket
    act, ev, reason = sector_verdict("Product Manager", "Albertsons")
    assert act == "bucket" and "g3_deny" in reason
    # plain walmart (no division) -> bucket (recoverable)
    act, _, reason = sector_verdict("Digital Product Manager", "Walmart")
    assert act == "bucket" and "g3_deny" in reason
    # JD strong-marker rescue
    act, ev, _ = sector_verdict(
        "Product Manager", "Stripe",
        "Own the roadmap, write user stories, ship our APIs each sprint")
    assert (act, ev) == ("keep", "jd")
    # JD weak x2 rescue
    act, ev, _ = sector_verdict(
        "Product Manager", "Unknown Co",
        "Work with stakeholders on the platform; drive OKRs")
    assert (act, ev) == ("keep", "jd")
    # no evidence -> pending, never dropped
    act, ev, reason = sector_verdict("Product Manager", "Unknown Co")
    assert (act, ev, reason) == ("pending", "", "no_sector_evidence")
    # JD retail markers block the weak rescue
    act, _, _ = sector_verdict(
        "Product Manager", "Mystery Retail Co",
        "Manage planogram and shelf space for our stores")
    assert act == "pending"


# ── §2.4 the merge contract (R2 P0-4's canonical case) ─────────────────────
def test_title_core_merge_case():
    a = title_core("Senior Product Manager")
    b = title_core("Sr Product Manager — Payments")
    assert a == b == "senior product manager"


def test_title_core_keeps_seniority_tokens():
    # senior vs non-senior never merge (the #2009 doctrine)
    assert title_core("Senior Product Manager") != title_core("Product Manager")
    assert title_core("Senior PM") == title_core("Senior Product Manager")


def test_title_core_specialization_tail_stripped():
    assert title_core("Product Manager, Payments") == "product manager"
    assert title_core("Technical Product Manager - Platform") == \
        "technical product manager"


# ── corpus distribution (post-closure; see module docstring) ───────────────
def test_corpus_distribution_post_closure():
    """pm 172 / adjacent 50 / other 294 on the 516 product-word corpus
    titles = R2's 171/51/304 with their P2-5 + P1-4 closures applied
    (the delta IS the closure: 4 noun-first FPs + 4 non-role-noun
    forms moved to other). Pins walked with the bundle: 526/47/312
    (S36) → 520/46/307 (niu retirement + #48) → 520/166/47 (the S38
    grammar fix: program-mgmt noun-first redirect) → 522/168/47 (the
    #49 export + the W6-a companyKey re-key: +2 pm titles) →
    524/174/50 (#51) → 516/172/50/294 (the #52 export, 2026-10-24,
    8,110 jobs: corpus churn, not grammar). S47 stale-snapshot law:
    the pin now reads the JSR-COMMITTED bundle (ingest/data/ui/
    jobs.json, back-synced from the org at every mirror run) — the
    platform's /home/z/my-project snapshot can lag many exports and
    MUST NOT gate the suite (the 2026-10-26 resume found it at #51-
    era 522 while jsr carried #52)."""
    import json
    import os
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    bundle = os.path.join(repo_root, "data", "ui", "jobs.json")
    if not os.path.exists(bundle):
        pytest.skip("bundle absent in this environment")
    from collections import Counter
    b = json.load(open(bundle))
    titles = {j.get("title", "") for j in b.get("jobs", [])
              if "product" in (j.get("title") or "").lower()}
    fams = Counter(classify_family(t)[0] for t in titles)
    assert len(titles) == 516
    assert fams == {"other": 294, "pm": 172, "adjacent": 50}


# ── module invariants ──────────────────────────────────────────────────────
def test_query_census_tiering():
    assert len(PM_QUERY_CORE) == 7
    assert len(PM_QUERY_TAIL) == 7
    assert set(PM_QUERY_CORE).isdisjoint(PM_QUERY_TAIL)


def test_head_deny_and_chain_patterns():
    assert _HEAD_DENY.search("GRC Program Manager, Product Lifecycle Assurance")
    assert _NOUN_FIRST_CHAIN.search(
        "Manager, Product & Technology Editorial Communications")
    assert not _NOUN_FIRST_CHAIN.search("Manager, Product")


def test_bare_pm_needs_provenance():
    assert _BARE_PM.search("Sr PM, Platform")
    assert _BARE_PM.search("Group PM")
    assert not _BARE_PM.search("Platform Engineer")   # PM as a word, not a token
