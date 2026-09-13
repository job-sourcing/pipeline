"""Shared fixtures for the jobsearch test suite.

Every database lives in tmp_path — the real data/tracker.db is never touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from jobsearch.config import Config
from jobsearch.models import Job
from jobsearch.storage import Store

LLM_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"
SOURCE_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sources"

RESUME = (
    "Senior Backend Engineer. Skills: Python, FastAPI, PostgreSQL, Redis, "
    "Docker, AWS. 10 years building backend services and data pipelines."
)


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "tracker.db"


@pytest.fixture
def store(db_path) -> Store:
    s = Store(db_path)
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path) -> Config:
    """Config with a temp DB but the REAL repo llm/ dir (needed by seam tests)."""
    return Config(db_path=tmp_path / "tracker.db")


def make_job(**kwargs) -> Job:
    """Job factory with sane defaults; kwargs override everything."""
    defaults = dict(
        title="Python Developer",
        company="Acme Cloud",
        description="Python backend role: FastAPI, PostgreSQL, Docker.",
        link="https://example.com/job/1",
        source="Remotive",
    )
    defaults.update(kwargs)
    return Job(**defaults)


@pytest.fixture
def sample_jobs() -> list[Job]:
    """Three jobs with clearly separable TF-IDF signals."""
    return [
        make_job(
            title="Senior Python Developer",
            company="Acme Cloud",
            description="Python, FastAPI, PostgreSQL, Docker, AWS backend services.",
            link="https://example.com/job/1",
            source="Remotive",
        ),
        make_job(
            title="Data Engineer",
            company="BetaData",
            description="Python, SQL, Airflow, Spark data pipelines and ETL.",
            link="https://example.com/job/2",
            source="Arbeitnow",
        ),
        make_job(
            title="Graphic Designer",
            company="Gamma Creative",
            description="Figma, Photoshop, marketing assets, branding, illustration.",
            link="https://example.com/job/3",
            source="The Muse",
        ),
    ]
