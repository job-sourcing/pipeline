"""Unit tests for base.detect_h1b — H1B sponsorship detection.

Pins the negation-guard contract added in the PR_REVIEW_CODE_v2 P1-1 fix:
descriptions that explicitly say they don't sponsor must return False,
even when positive phrases like "sponsorship available" appear in the
same text.
"""
from __future__ import annotations

import pytest

from jobsearch.sources.base import detect_h1b


# ── Positive cases ──────────────────────────────────────────────────────────

class TestDetectH1B_Positive:
    @pytest.mark.parametrize("text", [
        "We sponsor H1B visas.",
        "H-1B sponsorship available for qualified candidates.",
        "Open to sponsorship — we will sponsor work authorization.",
        "Visa sponsorship provided.",
        "Immigration sponsorship is part of our benefits package.",
        "We will sponsor H1B transfers for senior engineers.",
        "h1 visa transfer supported",
        "We are an H1B-sponsoring employer.",
    ])
    def test_positive_phrases(self, text):
        assert detect_h1b(text) is True

    def test_empty_string_returns_false(self):
        assert detect_h1b("") is False

    def test_none_returns_false(self):
        assert detect_h1b(None) is False


# ── Negative cases (the P1-1 fix) ──────────────────────────────────────────

class TestDetectH1B_Negation:
    @pytest.mark.parametrize("text", [
        "No sponsorship available.",
        "Not open to sponsorship.",
        "No visa sponsor — must be authorized to work in the US.",
        "We do not provide sponsorship.",
        "Cannot sponsor visas at this time.",
        "Will not sponsor H1B.",
        "Without sponsorship — must have existing work authorization.",
        "Doesn't offer sponsorship.",
        "Does not offer sponsorship for this role.",
        "Not eligible for sponsorship.",
        "Unable to sponsor — applicants must be US citizens.",
        "We are not able to sponsor work visas.",
    ])
    def test_negation_phrases(self, text):
        assert detect_h1b(text) is False, (
            f"Expected False for negation: {text!r}")

    def test_negation_overrides_positive_keyword(self):
        """The negation guard runs BEFORE the positive scan, so descriptions
        that contain BOTH "no sponsorship" AND "sponsorship available"
        must return False (the negation wins)."""
        text = (
            "Note: no sponsorship available for this position. "
            "We are not open to sponsorship."
        )
        assert detect_h1b(text) is False

    def test_negation_with_positive_in_same_sentence(self):
        """Even when the negation and positive keyword are in the same
        sentence ('We do not provide visa sponsorship'), the negation
        guard still flips the verdict to False."""
        assert detect_h1b(
            "We do not provide visa sponsorship for this role."
        ) is False


# ── Neutral cases ───────────────────────────────────────────────────────────

class TestDetectH1B_Neutral:
    def test_no_keywords_returns_false(self):
        assert detect_h1b("We are hiring a senior software engineer.") is False

    def test_unrelated_text_returns_false(self):
        assert detect_h1b(
            "Looking for 5 years of Python experience, AWS, Kubernetes."
        ) is False

    def test_case_insensitive(self):
        """H1B detection is case-insensitive (lowercases input)."""
        assert detect_h1b("WE SPONSOR H1B VISAS") is True
        assert detect_h1b("We Do Not Sponsor H1B Visas") is False
