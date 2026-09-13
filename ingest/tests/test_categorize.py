"""Categorization (Sprint 3): tier classification, skill extraction, work
mode — ports of T2 classify-tier.mjs + skill-extract.mjs plus the central
work-mode heuristic. Case lists mirror T2's own inline test suites."""
from __future__ import annotations

import pytest

from jobsearch.categorize import (
    categorize, canonicalize_skill, classify_tier, detect_work_mode,
    extract_skills,
)


# ── classify_tier ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    # T2's own inline test list (classify-tier.mjs --test)
    ("Software Engineer Intern", "intern"),
    ("Junior Software Engineer", "entry"),
    ("Software Engineer I", "entry"),
    ("Software Engineer II", "mid"),
    ("Senior Software Engineer", "senior"),
    ("Staff Engineer", "senior"),
    ("Principal Engineer", "senior"),
    ("VP of Engineering", "senior"),
    ("Engineering Intern Program", "intern"),
    ("Software Engineer", "mid"),
    ("Senior Intern Coordinator", "senior"),
    ("Graduate Engineer", "mid"),
    ("Graduate Engineer Program", "intern"),
    ("A.I. Researcher", "mid"),
    ("I.T. Specialist II", "mid"),
    # leftmost-marker rule: the role's own level leads the title
    ("Summer Intern, Director of Product", "intern"),
    ("Senior Intern Coordinator", "senior"),
    # guard (a): associate + senior noun → senior band
    ("Associate Director of Marketing", "senior"),
    ("Associate Vice President of Sales", "senior"),
    ("Associate Creative Director", "senior"),
    # associate alone → entry
    ("Associate Product Manager", "entry"),
    # guard (b): bridge noun + senior role → senior
    ("Intern Program Director", "senior"),
    ("Trainee Scheme Lead", "senior"),
    # roman numerals / L-levels
    ("Engineer III", "senior"),
    ("Marketing Analyst II", "mid"),
    ("Data Scientist I", "entry"),
    ("Software Engineer L4", "mid"),
    ("Software Engineer L5", "mid"),
    ("Software Engineer L2", "entry"),
    # weights as same-offset tie-break: entry-level beats entry at same index
    ("Entry-Level Analyst", "entry"),
    # defaults
    ("", "mid"),
    ("Product Manager", "mid"),
])
def test_classify_tier(title, expected):
    assert classify_tier(title) == expected


def test_classify_tier_non_string():
    assert classify_tier(None) == "mid"
    assert classify_tier(123) == "mid"


# ── extract_skills ─────────────────────────────────────────────────────────

def test_extract_skills_basic_and_canonical():
    text = ("We need k8s, GraphQL and Go; CI/CD with GitHub Actions. "
            "Experience with postgres and pytorch required.")
    skills = extract_skills(text)
    assert "Kubernetes" in skills        # k8s → canonical
    assert "GraphQL" in skills
    assert "Go" in skills                # case-sensitive standalone token
    assert "CI/CD" in skills
    assert "GitHub Actions" in skills
    assert "PostgreSQL" in skills        # postgres → canonical
    assert "PyTorch" in skills
    assert "Go" in skills


def test_extract_skills_case_sensitive_passes():
    # 'go' in prose must NOT register the Go language; 'Go' must.
    prose = "you may go the extra mile"
    assert "Go" not in extract_skills(prose)
    lang = "Experience with Go and Rust"
    assert "Go" in extract_skills(lang)
    # 'safe' in prose must NOT register SAFe; 'SAFe' must.
    assert "SAFe" not in extract_skills("a safe environment, safety first")
    assert "SAFe" in extract_skills("SAFe 6 certification preferred")


def test_extract_skills_symbol_edge_tokens():
    # \b fails at symbol edges; lookarounds make these match standalone
    skills = extract_skills("C++ and C# and .NET and Node.js and Vue.js")
    assert {"C++", "C#", ".NET", "Node.js", "Vue.js"} <= skills


def test_extract_skills_alternation_order():
    # longest-first within a family: React Native over React
    skills = extract_skills("React Native developer")
    assert "React Native" in skills
    assert "React" not in skills
    # Lean Six Sigma over Six Sigma
    skills = extract_skills("Lean Six Sigma Green Belt")
    assert "Lean Six Sigma" in skills
    assert "Six Sigma" not in skills


def test_extract_skills_no_umbrella_aliases():
    # "cloud" must never count as knowing AWS/GCP/Azure
    skills = extract_skills("cloud experience required")
    assert not ({ "AWS", "GCP", "Azure" } & skills)


def test_extract_skills_certification_spellings():
    # spaced and fused spellings canonicalize to the same display string
    assert canonicalize_skill("Certified Scrum Master") == "Certified ScrumMaster"
    assert canonicalize_skill("certified scrummaster") == "Certified ScrumMaster"
    assert canonicalize_skill("PMI ACP") == "PMI-ACP"
    assert canonicalize_skill("prince 2") == "PRINCE2"
    # CSM deliberately omitted (Customer Success Manager collision)
    skills = extract_skills("part Customer Success Manager (CSM)")
    assert "CSM" not in skills and "csm" not in skills


def test_extract_skills_unknown_passthrough():
    # KNOWN skills resolve to display casing from the token list; genuinely
    # unknown tokens pass through UNCHANGED (never title-cased into a
    # phantom known-skill key)
    assert canonicalize_skill("Graphql") == "GraphQL"     # known via display map
    assert canonicalize_skill("tensorflow") == "TensorFlow"
    assert canonicalize_skill("zzzznotaskill") == "zzzznotaskill"


def test_extract_skills_empty():
    assert extract_skills(None) == set()
    assert extract_skills("") == set()


# ── detect_work_mode ───────────────────────────────────────────────────────

@pytest.mark.parametrize("title,location,description,hint,expected", [
    ("Senior Backend Engineer", "Remote (US)", "", False, "remote"),
    ("Engineer", "Berlin", "This is a hybrid role, 2 days in office", False, "hybrid"),
    ("Engineer", "", "This is a hybrid role, 2 days in office", False, "hybrid"),
    ("Engineer", "London", "", True, "remote"),
    ("Hybrid Backend Engineer", "Amsterdam", "", False, "hybrid"),
    ("Onsite Security Engineer", "", "", False, "onsite"),
    ("Engineer", "", "Work in person at our HQ", False, "onsite"),
    ("Engineer", "", "no arrangement mentioned", False, "unknown"),
    # both markers in BODY text = ambiguous boilerplate ("fully remote team,
    # quarterly onsite" vs "3 days onsite") — flagged unknown, never guessed
    ("Engineer", "NYC", "Fully remote team, quarterly onsite", False, "unknown"),
    # title/location markers beat description markers
    ("Remote Engineer", "Remote", "onsite required sometimes", False, "remote"),
])
def test_detect_work_mode(title, location, description, hint, expected):
    assert detect_work_mode(title, location, description, hint) == expected


def test_categorize_composite():
    result = categorize(
        "Senior Python Engineer (Remote)", "Remote",
        "Python, Django, PostgreSQL stack. We work remotely.")
    assert result["tier"] == "senior"
    assert result["work_mode"] == "remote"
    assert {"Python", "Django", "PostgreSQL"} <= set(result["skills"])
    # skills come back sorted for deterministic storage
    assert result["skills"] == sorted(result["skills"])
