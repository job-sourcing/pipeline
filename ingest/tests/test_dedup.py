"""Fuzzy dedup: normalization, similarity, cross-board merge semantics."""
from __future__ import annotations

import pytest

from jobsearch.dedup import (
    source_ladder_trust,
    calculate_similarity,
    dedup_jobs,
    normalize_company_name,
    normalize_job_title,
)

from conftest import make_job


class TestNormalizeCompanyName:
    def test_suffix_stripped(self):
        assert normalize_company_name("Acme Inc.") == "acme"
        assert normalize_company_name("Acme") == "acme"

    @pytest.mark.parametrize("name,expected", [
        ("Acme Ltd", "acme"),
        ("Acme Limited", "acme"),
        ("Acme Corp", "acme"),
        ("Acme Corporation", "acme"),
        ("Acme LLC", "acme"),
        ("Acme Co", "acme"),
        ("Acme Company", "acme"),
        ("Acme Group", "acme"),
        ("Acme Holdings", "acme"),
        ("Acme International", "acme"),
        ("Acme Intl", "acme"),
        ("Acme PLC", "acme"),
        ("Acme LLP", "acme"),
        ("Acme UK", "acme"),
    ])
    def test_common_suffixes(self, name, expected):
        assert normalize_company_name(name) == expected

    def test_the_and_ampersand_stripped(self):
        assert normalize_company_name("The Boring Company") == \
            normalize_company_name("Boring") == "boring"
        assert normalize_company_name("Beta & Co") == "beta"

    def test_case_and_punctuation_normalized(self):
        assert normalize_company_name("  ACME-CLOUD! ") == "acme cloud"

    def test_normalize_job_title_lowercase_punctuation(self):
        assert normalize_job_title("Senior Python Developer (Remote)") == \
            "senior python developer remote"
        assert normalize_job_title("DevOps/Platform Engineer") == \
            "devops platform engineer"


class TestCalculateSimilarity:
    def test_equal_strings_score_100(self):
        assert calculate_similarity("python developer", "Python Developer") == 100

    def test_empty_string_scores_0(self):
        assert calculate_similarity("", "abc") == 0
        assert calculate_similarity("abc", "") == 0

    def test_containment_shortcut(self):
        # shorter/longer * 100 when one is contained in the other
        assert calculate_similarity("abc", "abcd") == 75
        assert calculate_similarity("python dev", "senior python dev") == 59
        assert calculate_similarity("senior python dev", "python dev") == 59
        assert calculate_similarity("", "anything") == 0

    def test_levenshtein_ratio(self):
        assert calculate_similarity("kitten", "sitting") == 57

    def test_disjoint_strings_score_low(self):
        assert calculate_similarity("python", "pottery") < 50

    def test_returns_int_in_range(self):
        for a, b in (("abc", "abd"), ("aaaa", "aa"), ("x", "y")):
            sim = calculate_similarity(a, b)
            assert isinstance(sim, int)
            assert 0 <= sim <= 100


class TestDedupJobs:
    def test_cross_board_duplicates_merge_into_one(self):
        a = make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a.example/1",
                     location="", description="", salary_text=None)
        b = make_job(title="Python Developer", company="Acme Cloud Inc.",
                     source="Jobicy", link="https://b.example/9",
                     location="Remote", salary_text="100k-120k USD")
        out = dedup_jobs([a, b])
        assert len(out) == 1
        merged = out[0]
        # identity stays with the base (first-seen) job
        assert merged.title == "Python Developer"
        assert merged.company == "Acme Cloud"
        assert merged.link == "https://a.example/1"
        # both boards are recorded
        assert set(merged.source.split(" + ")) == {"Remotive", "Jobicy"}
        # missing fields were filled from the duplicate
        assert merged.location == "Remote"
        assert merged.salary_text == "100k-120k USD"

    def test_source_join_is_deterministic_base_first(self):
        a = make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a.example/1")
        b = make_job(title="Python Developer", company="Acme Cloud",
                     source="Jobicy", link="https://b.example/2")
        merged = dedup_jobs([a, b])[0]
        assert merged.source == "Remotive + Jobicy"

    def test_existing_fields_never_overwritten_in_merge(self):
        a = make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a.example/1",
                     description="original rich description", remote=True)
        b = make_job(title="Python Developer", company="Acme Cloud",
                     source="Jobicy", link="https://b.example/2",
                     description="DIFFERENT", remote=False)
        merged = dedup_jobs([a, b])[0]
        assert merged.description == "original rich description"  # base wins
        assert merged.remote is True   # base value kept (False is non-null too)

    def test_merge_is_first_non_null_not_first_truthy(self):
        # contract: False is a VALUE, not a gap — base False is kept even when
        # the duplicate says True (contrast with storage.upsert_jobs, which
        # does fill 0 -> 1 for boolean flags).
        a = make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a.example/1",
                     remote=False)
        b = make_job(title="Python Developer", company="Acme Cloud",
                     source="Jobicy", link="https://b.example/2",
                     remote=True)
        assert dedup_jobs([a, b])[0].remote is False
        assert dedup_jobs([b, a])[0].remote is True

    def test_different_companies_survive(self):
        a = make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a.example/1")
        b = make_job(title="Python Developer", company="BetaData",
                     source="Jobicy", link="https://b.example/2")
        out = dedup_jobs([a, b])
        assert len(out) == 2

    def test_title_below_threshold_not_merged(self):
        a = make_job(title="Senior Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a.example/1")
        b = make_job(title="Python Developer", company="Acme Cloud",
                     source="Jobicy", link="https://b.example/2")
        # title similarity ~70 (containment) is below the 90 threshold
        assert len(dedup_jobs([a, b])) == 2

    def test_thresholds_are_inclusive_gates(self):
        a = make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a.example/1")
        b = make_job(title="Python Developer", company="Acme Cloudx",
                     source="Jobicy", link="https://b.example/2")
        # employer sim = 10/11 ≈ 90.9 > 85, title sim = 100 > 90 -> merge
        assert len(dedup_jobs([a, b])) == 1
        # ...but with a raised employer threshold it must NOT merge
        assert len(dedup_jobs([a, b], employer_threshold=95)) == 2

    def test_jobs_missing_company_pass_through_unmerged(self):
        a = make_job(title="T", company="", source="A", link="https://a/1")
        b = make_job(title="T", company="", source="B", link="https://b/2")
        assert len(dedup_jobs([a, b])) == 2

    def test_three_way_merge_accumulates_sources(self):
        jobs = [
            make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://a/1"),
            make_job(title="Python Developer", company="Acme Cloud",
                     source="Jobicy", link="https://b/2"),
            make_job(title="Python Developer", company="Acme Cloud",
                     source="Arbeitnow", link="https://c/3"),
        ]
        out = dedup_jobs(jobs)
        assert len(out) == 1
        assert set(out[0].source.split(" + ")) == {"Remotive", "Jobicy", "Arbeitnow"}

    def test_empty_input(self):
        assert dedup_jobs([]) == []


class TestTrustPrecedence:
    """Sprint-2: trust-scored dedup precedence (methodology §objective-I
    ladder). Higher-ladder sources become the identity base on merge."""

    def test_source_ladder_trust_values(self):
        assert source_ladder_trust("Greenhouse") == 10
        assert source_ladder_trust("Ashby.openai") == 10
        assert source_ladder_trust("WTTJ") == 9
        assert source_ladder_trust("LinkedIn Guest") == 7
        assert source_ladder_trust("Adzuna") == 5
        assert source_ladder_trust("Careerjet") == 3
        assert source_ladder_trust("Findwork") == 1
        assert source_ladder_trust("Unknown Board") == 5      # default
        # joined duplicates take the max
        assert source_ladder_trust("Remotive + Adzuna") == 5

    def test_source_ladder_trust_alias_matching(self):
        """Alias predicate (CodeRabbit round-3): 'muse' must reach
        'the muse' (rank 3) via final-token matching — bare startswith
        never matched it (silently ranked 5, wrongly outranking Muse over
        Careerjet in _merge_two). Prefix aliases still work; junk doesn't."""
        assert source_ladder_trust("muse") == 3          # suffix alias
        assert source_ladder_trust("the muse") == 3      # exact
        assert source_ladder_trust("themuse") == 3       # exact
        assert source_ladder_trust("lever") == 10        # exact
        assert source_ladder_trust("leveraged") == 5     # NOT an alias
        assert source_ladder_trust("a") == 5             # len guard
        assert source_ladder_trust("") == 5              # empty → default

    def test_higher_ladder_source_becomes_identity(self):
        # Careerjet (ladder 3) seen first; Greenhouse (ladder 10) second →
        # the merged row keeps Greenhouse's link/title as identity.
        low = make_job(title="Python Developer", company="Acme Cloud",
                       source="Careerjet", link="https://careerjet/1")
        high = make_job(title="Python Developer", company="Acme Cloud",
                        source="Greenhouse", link="https://gh/1")
        out = dedup_jobs([low, high])
        assert len(out) == 1
        assert out[0].link == "https://gh/1"
        assert out[0].source.startswith("Greenhouse")

    def test_lower_ladder_keeps_first_seen_identity(self):
        high = make_job(title="Python Developer", company="Acme Cloud",
                        source="Greenhouse", link="https://gh/1")
        low = make_job(title="Python Developer", company="Acme Cloud",
                       source="Careerjet", link="https://careerjet/1")
        out = dedup_jobs([high, low])
        assert len(out) == 1
        assert out[0].link == "https://gh/1"

    def test_per_job_trust_score_breaks_ties(self):
        # Same ladder level (both 3): per-job trust_score decides.
        weak = make_job(title="Python Developer", company="Acme Cloud",
                        source="Remotive", link="https://rem/1")
        weak.trust_score = 60
        strong = make_job(title="Python Developer", company="Acme Cloud",
                          source="Jobicy", link="https://job/1")
        strong.trust_score = 95
        out = dedup_jobs([weak, strong])
        assert len(out) == 1
        assert out[0].link == "https://job/1"

    def test_missing_trust_scores_keep_first_seen(self):
        a = make_job(title="Python Developer", company="Acme Cloud",
                     source="Remotive", link="https://rem/1")
        b = make_job(title="Python Developer", company="Acme Cloud",
                     source="Jobicy", link="https://job/1")
        out = dedup_jobs([a, b])
        assert out[0].link == "https://rem/1"

    def test_merge_fills_fields_from_demoted_base(self):
        # The higher-ladder job misses salary; the lower-ladder sighting
        # carries it — merged row must keep high identity AND low's salary.
        low = make_job(title="Python Developer", company="Acme Cloud",
                       source="Adzuna", link="https://adz/1")
        low.salary_text = "$100k - $130k"
        high = make_job(title="Python Developer", company="Acme Cloud",
                        source="Lever", link="https://lev/1")
        out = dedup_jobs([low, high])
        assert out[0].link == "https://lev/1"
        assert out[0].salary_text == "$100k - $130k"
