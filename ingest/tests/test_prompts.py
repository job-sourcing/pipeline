"""Prompt asset loading (D11: prompts are versioned files, never inline)."""
from __future__ import annotations

import pytest

from jobsearch.prompts import PromptAsset, load_prompt


def test_load_score_jobs_prompt():
    p = load_prompt("score_jobs")
    assert isinstance(p, PromptAsset)
    assert p.name == "score_jobs"
    assert p.version == "3"
    assert p.source  # provenance recorded in frontmatter


def test_v3_prompt_has_sector_contract():
    """Sprint 3: the sector axis (methodology §9 gap) — the prompt must
    carry the fixed taxonomy and the sector field in the JSON contract."""
    p = load_prompt("score_jobs")
    assert '"sector"' in p.template
    for tax in ("fintech", "healthtech", "ai-ml", "devtools", "other"):
        assert tax in p.template, f"taxonomy missing {tax}"


def test_template_has_placeholders():
    template = load_prompt("score_jobs").template
    for placeholder in ("{resume}", "{title}", "{company}", "{description}"):
        assert placeholder in template, f"prompt missing {placeholder}"


def test_template_is_not_the_frontmatter():
    p = load_prompt("score_jobs")
    assert not p.template.startswith("---")


def test_missing_prompt_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("no_such_prompt")


def test_version_is_a_string():
    # frontmatter values are strings — cache key uses it verbatim
    assert isinstance(load_prompt("score_jobs").version, str)
