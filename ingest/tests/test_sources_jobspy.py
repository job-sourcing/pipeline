"""JobSpy aggregator source — unit tests against a synthetic DataFrame
matching the schema captured in tests/fixtures/sources/jobspy.json (real
jobspy 1.1.82 scrape; descriptions trimmed). The mock replaces
`jobspy_source._scrape_jobs` (the module-level lazy seam around
`jobspy.scrape_jobs`) so the test never imports jobspy / pandas directly —
the seam stays mockable in any env, even one without the package installed.

One @pytest.mark.live test (deselected by default; `-m live` to run) hits
the real jobspy with results_wanted=2 to prove end-to-end integration.
"""
from __future__ import annotations

import json
from collections import namedtuple
from typing import Optional

import pytest

from jobsearch.config import Config
from jobsearch.sources import jobspy_source

from conftest import SOURCE_FIXTURES


# ── helpers ─────────────────────────────────────────────────────────────────

JobRow = namedtuple(
    "JobRow",
    [
        # Match the 33 columns returned by jobspy 1.1.82's scrape_jobs() —
        # only the ones fetch() reads are populated; the rest default to None
        # so the namedtuple shape stays stable across pandas versions.
        "id", "site", "job_url", "job_url_direct", "title", "company",
        "location", "date_posted", "job_type", "salary_source", "interval",
        "min_amount", "max_amount", "currency", "is_remote", "job_level",
        "job_function", "listing_type", "emails", "description",
        "company_industry", "company_url", "company_logo",
        "company_url_direct", "company_addresses", "company_num_employees",
        "company_revenue", "company_description", "skills",
        "experience_range", "company_rating", "company_reviews_count",
        "vacancy_count", "work_from_home_type",
    ],
)
JobRow.__new__.__defaults__ = (None,) * len(JobRow._fields)


class _FakeDataFrame:
    """Minimal stub that quacks like the parts of a pandas DataFrame our
    fetch() touches: __len__ + itertuples(index=False). Keeps the test
    suite independent of pandas (so it runs in envs without jobspy)."""

    def __init__(self, rows: list[JobRow]):
        self._rows = rows

    def __len__(self) -> int:
        return len(self._rows)

    def itertuples(self, *, index: bool = True):  # noqa: ARG002 — match real signature
        for row in self._rows:
            yield row


def load_fixture(name: str) -> dict:
    return json.loads((SOURCE_FIXTURES / name).read_text(encoding="utf-8"))


def _row_from_dict(d: dict) -> JobRow:
    """Build a JobRow from a fixture row dict — only the fields our fetch()
    reads are pulled; the rest stay at the namedtuple default (None)."""
    read = ("id", "site", "job_url", "job_url_direct", "title", "company",
            "location", "date_posted", "job_type", "salary_source",
            "interval", "min_amount", "max_amount", "currency", "is_remote",
            "job_level", "job_function", "listing_type", "emails",
            "description", "company_industry", "company_url", "company_logo",
            "company_url_direct", "company_addresses",
            "company_num_employees", "company_revenue",
            "company_description", "skills", "experience_range",
            "company_rating", "company_reviews_count", "vacancy_count",
            "work_from_home_type")
    return JobRow(**{k: d.get(k) for k in read})


@pytest.fixture
def patched_source(monkeypatch):
    """Install a fake _scrape_jobs + always-on is_configured on the source."""
    captured: dict = {}

    def fake_scrape(*args, **kwargs):
        captured["args"], captured["kwargs"] = args, kwargs
        return fake_scrape._df  # type: ignore[attr-defined]

    fake_scrape._df = _FakeDataFrame([])  # type: ignore[attr-defined]
    monkeypatch.setattr(jobspy_source, "_scrape_jobs", fake_scrape)
    monkeypatch.setattr(jobspy_source, "is_configured", lambda cfg: True)
    # Stash the fake on the fixture record so each test can set its own rows.
    return fake_scrape, captured


# ── parse + mapping ─────────────────────────────────────────────────────────

class TestJobSpyParse:
    def test_fetch_maps_fixture_rows(self, patched_source, cfg):
        fake, captured = patched_source
        fx = load_fixture("jobspy.json")
        fake._df = _FakeDataFrame([_row_from_dict(r) for r in fx["rows"]])

        jobs = jobspy_source.fetch("python developer", location="Remote",
                                    num_results=10, cfg=cfg)
        assert len(jobs) == 3

        # Call-site args forward to scrape_jobs correctly.
        assert captured["kwargs"]["search_term"] == "python developer"
        assert captured["kwargs"]["location"] == "Remote"
        assert captured["kwargs"]["results_wanted"] == 10
        # Default cfg sites normalise to jobspy enum names.
        assert captured["kwargs"]["site_name"] == [
            "indeed", "glassdoor", "google", "zip_recruiter",
        ]

        j0 = jobs[0]
        assert j0.title == "Senior ERPNext Developer & Functional Expert"
        assert j0.company == "Ecom-Specialst"
        # Per-site source attribution — "JobSpy.<Site>" so dedup layer can
        # distinguish cross-aggregator dupes (e.g. the same Indeed posting
        # arriving via JobSpy vs. an Indeed-direct fetch).
        assert j0.source == "JobSpy.Indeed"
        assert j0.link == "https://www.indeed.com/viewjob?jk=5e62c7c526a8d885"
        assert j0.location == "Remote, US"
        assert j0.date_posted == "2026-08-24"      # datetime.date → YYYY-MM-DD
        assert j0.remote is True                   # is_remote=True from row
        # Salary built from min/max + currency via base.salary_text.
        assert j0.salary_text == "$130,000 – $240,000/yr"
        assert j0.salary_min == 130000.0
        assert j0.salary_max == 240000.0
        # H1B mention via detect_h1b — fixture row 0 description mentions
        # "H1B visa sponsorship available".
        assert j0.h1b_mention is True
        # Email extracted from description (careers@ecomspecialist.com).
        assert j0.contact_email == "careers@ecomspecialist.com"
        # search_query echoes the keyword arg.
        assert j0.search_query == "python developer"

    def test_per_site_attribution_for_multiple_sites(self, patched_source, cfg):
        fake, _ = patched_source
        rows = [
            JobRow(id="1", site="indeed", job_url="https://i/1",
                   title="Py Dev", company="A", location="Remote",
                   date_posted="2026-08-20", is_remote=True, description="d",
                   min_amount=100.0, max_amount=200.0, currency="USD"),
            JobRow(id="2", site="google", job_url="https://g/2",
                   title="Py Dev 2", company="B", location="Remote",
                   date_posted="2026-08-21", is_remote=True, description="d",
                   min_amount=None, max_amount=None, currency=None),
            JobRow(id="3", site="zip_recruiter", job_url="https://z/3",
                   title="Py Dev 3", company="C", location="Remote",
                   date_posted="2026-08-22", is_remote=True, description="d",
                   min_amount=90.0, max_amount=110.0, currency="USD"),
            JobRow(id="4", site="glassdoor", job_url="https://gd/4",
                   title="Py Dev 4", company="D", location="Remote",
                   date_posted="2026-08-23", is_remote=True, description="d",
                   min_amount=None, max_amount=None, currency=None),
        ]
        fake._df = _FakeDataFrame(rows)
        jobs = jobspy_source.fetch("python", num_results=10, cfg=cfg)
        # Each row keeps its originating aggregator in the source string so
        # the dedup layer can treat cross-aggregator dupes correctly.
        assert [j.source for j in jobs] == [
            "JobSpy.Indeed", "JobSpy.Google", "JobSpy.Zip Recruiter",
            "JobSpy.Glassdoor",
        ]

    def test_job_url_falls_back_to_direct(self, patched_source, cfg):
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url=None, job_url_direct="https://direct/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=False, description="",
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].link == "https://direct/1"

    def test_row_without_salary_yields_none(self, patched_source, cfg):
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=True, description="",
            min_amount=None, max_amount=None, currency=None,
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].salary_text is None
        assert jobs[0].salary_min is None and jobs[0].salary_max is None

    def test_nan_salary_treated_as_missing(self, patched_source, cfg):
        import math
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=True, description="",
            min_amount=float("nan"), max_amount=float("nan"), currency=None,
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].salary_text is None
        assert jobs[0].salary_min is None and jobs[0].salary_max is None

    def test_rows_missing_title_or_company_skipped(self, patched_source, cfg):
        fake, _ = patched_source
        fake._df = _FakeDataFrame([
            JobRow(id="a", site="indeed", job_url="https://i/a",
                   title="", company="C", location="Remote", is_remote=True),
            JobRow(id="b", site="indeed", job_url="https://i/b",
                   title="T", company="", location="Remote", is_remote=True),
            JobRow(id="c", site="indeed", job_url="https://i/c",
                   title="T", company="C", location="Remote", is_remote=True),
        ])
        jobs = jobspy_source.fetch("python", num_results=10, cfg=cfg)
        assert len(jobs) == 1
        assert jobs[0].link == "https://i/c"

    def test_remote_heuristic_from_location_text(self, patched_source, cfg):
        # is_remote=False but the location says "Remote" → remote flagged True.
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/x",
            title="T", company="C", location="Remote, US",
            date_posted=None, is_remote=False, description="",
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].remote is True

    def test_date_posted_normalisation_handles_iso_string(self, patched_source, cfg):
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/x",
            title="T", company="C", location="NYC",
            date_posted="2026-08-20T10:00:00Z",  # ISO 8601 → YYYY-MM-DD
            is_remote=False, description="",
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].date_posted == "2026-08-20"

    def test_num_results_caps_row_count(self, patched_source, cfg):
        fake, captured = patched_source
        rows = [
            JobRow(id=str(i), site="indeed", job_url=f"https://i/{i}",
                   title=f"T{i}", company="C", location="Remote",
                   date_posted=None, is_remote=True, description="")
            for i in range(5)
        ]
        fake._df = _FakeDataFrame(rows)
        jobs = jobspy_source.fetch("python", num_results=2, cfg=cfg)
        assert len(jobs) == 2
        # results_wanted forwarded to scrape_jobs (jobspy's own pagination).
        assert captured["kwargs"]["results_wanted"] == 2


# ── site-list config + normalisation ─────────────────────────────────────────

class TestJobSpySiteConfig:
    def test_default_sites_use_spec_defaults(self, patched_source, cfg):
        fake, captured = patched_source
        fake._df = _FakeDataFrame([])
        jobspy_source.fetch("python", cfg=cfg)
        assert captured["kwargs"]["site_name"] == [
            "indeed", "glassdoor", "google", "zip_recruiter",
        ]

    def test_site_aliases_normalise_spec_names_to_enum(self, patched_source, cfg):
        # cfg.jobspy_sites accepts the user-facing spec names
        # ("google_jobs", "ziprecruiter") and normalises them to jobspy's
        # Site enum member names ("google", "zip_recruiter").
        fake, captured = patched_source
        fake._df = _FakeDataFrame([])
        cfg.jobspy_sites = ["indeed", "glassdoor", "google_jobs", "ziprecruiter"]
        jobspy_source.fetch("python", cfg=cfg)
        assert captured["kwargs"]["site_name"] == [
            "indeed", "glassdoor", "google", "zip_recruiter",
        ]

    def test_site_aliases_accept_canonical_names_too(self, patched_source, cfg):
        fake, captured = patched_source
        fake._df = _FakeDataFrame([])
        cfg.jobspy_sites = ["google", "zip_recruiter", "linkedin"]
        jobspy_source.fetch("python", cfg=cfg)
        assert captured["kwargs"]["site_name"] == [
            "google", "zip_recruiter", "linkedin",
        ]

    def test_unknown_site_dropped(self, patched_source, cfg):
        # An unknown site name is silently dropped (no KeyError from jobspy's
        # enum lookup) — fetch() returns whatever the surviving sites yield.
        fake, captured = patched_source
        fake._df = _FakeDataFrame([])
        cfg.jobspy_sites = ["indeed", "not_a_real_site", "glassdoor"]
        jobspy_source.fetch("python", cfg=cfg)
        assert captured["kwargs"]["site_name"] == ["indeed", "glassdoor"]

    def test_empty_site_list_short_circuits(self, patched_source, cfg):
        fake, captured = patched_source
        fake._df = _FakeDataFrame([])
        cfg.jobspy_sites = []
        jobs = jobspy_source.fetch("python", cfg=cfg)
        assert jobs == []
        # _scrape_jobs never called.
        assert "kwargs" not in captured

    def test_env_var_resolves_to_site_list(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JOBSY_SITES", "indeed, google_jobs , ziprecruiter")
        cfg = Config(db_path=tmp_path / "t.db")
        assert cfg.jobspy_sites == ["indeed", "google_jobs", "ziprecruiter"]


# ── is_configured + error paths ──────────────────────────────────────────────

class TestJobSpyConfigured:
    def test_is_configured_returns_bool(self, cfg):
        # Whatever the install state is, is_configured must return a bool —
        # we don't assert True/False so the test stays hermetic across envs.
        assert isinstance(jobspy_source.is_configured(cfg), bool)

    def test_fetch_raises_when_not_installed(self, monkeypatch, cfg):
        monkeypatch.setattr(jobspy_source, "is_configured", lambda c: False)
        with pytest.raises(RuntimeError, match="python-jobspy not installed"):
            jobspy_source.fetch("python", cfg=cfg)

    def test_scrape_failure_raises_runtime(self, patched_source, monkeypatch, cfg):
        fake, _ = patched_source

        def boom(*a, **k):
            raise ConnectionError("tls-client exploded")

        # is_configured still True (patched_source fixture); only the scrape
        # call fails — the source must surface a RuntimeError, never the raw
        # ConnectionError, so the aggregator's failure-isolation can catch it.
        monkeypatch.setattr(jobspy_source, "_scrape_jobs", boom)
        with pytest.raises(RuntimeError, match="JobPy scrape failed"):
            jobspy_source.fetch("python", cfg=cfg)

    def test_scrape_returning_none_yields_empty_list(self, patched_source, cfg):
        fake, _ = patched_source
        fake._df = None
        jobs = jobspy_source.fetch("python", cfg=cfg)
        assert jobs == []

    def test_empty_dataframe_yields_empty_list(self, patched_source, cfg):
        fake, _ = patched_source
        fake._df = _FakeDataFrame([])
        jobs = jobspy_source.fetch("python", cfg=cfg)
        assert jobs == []


# ── Live test (deselected by default; -m live to run) ────────────────────────

class TestJobSpyLive:
    @pytest.mark.live
    def test_live_indeed_only(self):
        """One real call, results_wanted=2, narrowed to Indeed only.

        Glassdoor/ZipRecruiter frequently return 403 (Cloudflare WAF) and
        Google Jobs rate-limits with 429s; narrowing to Indeed keeps the
        live-integration test deterministic. The default 4-site scrape still
        works in fresh IP contexts — see worklog SRC-JOBSPY-2 Step 1.
        """
        from jobsearch.config import load_config
        cfg = load_config()
        cfg.jobspy_sites = ["indeed"]
        if not jobspy_source.is_configured(cfg):
            pytest.skip(
                "python-jobspy not installed. "
                "Run: pip install python-jobspy  (see worklog SRC-JOBSPY-2)."
            )
        jobs = jobspy_source.fetch("python developer", location="Remote",
                                    num_results=2, cfg=cfg)
        assert isinstance(jobs, list)
        assert len(jobs) >= 1, "JobSpy returned 0 jobs — site may be down"
        for j in jobs:
            assert j.source.startswith("JobSpy.")
            assert j.title and j.company
            assert j.link


class TestJobSpyEmailsColumn:
    """P1-4 regression tests: verify the fetch() prefers the structured
    `emails` column from the DataFrame over regex-extracting from the
    description. JobSpy's scraper extracts recruiter emails server-side
    and exposes them as a list/dict; using it directly avoids losing
    recruiter emails on rows where the apply page has structured email
    data but the description text doesn't include them."""

    def test_structured_emails_list_takes_precedence(self, patched_source, cfg):
        """When the `emails` column is a list, it's used directly and the
        description-text extraction is skipped."""
        fake, _ = patched_source
        # Description has NO email; only the structured `emails` column has one.
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=True, description="Python dev role, no email here",
            emails=["recruiter@acme.com", "ops@acme.com"],
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].contact_email == "recruiter@acme.com"

    def test_structured_emails_string_takes_precedence(self, patched_source, cfg):
        """When the `emails` column is a comma-separated string, the first
        non-noise email is used."""
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=True, description="no email in desc",
            emails="hr@acme.com, careers@acme.com",
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].contact_email == "hr@acme.com"

    def test_noise_emails_filtered_from_structured_column(self, patched_source, cfg):
        """noreply/notifications/support/info emails are filtered out of
        the structured `emails` column too (matches the extract_email
        convention for description-text emails)."""
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=True, description="",
            emails=["noreply@acme.com", "support@acme.com",
                    "real.recruiter@acme.com"],
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].contact_email == "real.recruiter@acme.com"

    def test_structured_empty_falls_back_to_description(self, patched_source, cfg):
        """When the `emails` column is empty/None, fetch() falls back to
        regex extraction from the description (the prior behaviour)."""
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=True, description="Contact careers@acme.com to apply.",
            emails=None,
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        assert jobs[0].contact_email == "careers@acme.com"

    def test_structured_emails_takes_precedence_over_description(self, patched_source, cfg):
        """If both structured `emails` and a description-text email are
        present, the structured one wins (it's more authoritative —
        JobSpy extracted it server-side from the apply page's structured
        fields, not heuristically from free-text)."""
        fake, _ = patched_source
        fake._df = _FakeDataFrame([JobRow(
            id="x", site="indeed", job_url="https://i/1",
            title="T", company="C", location="Remote", date_posted=None,
            is_remote=True,
            description="Old recruiter: legacy@example.com (do not use).",
            emails=["modern.recruiter@acme.com"],
        )])
        jobs = jobspy_source.fetch("python", num_results=1, cfg=cfg)
        # Structured column wins.
        assert jobs[0].contact_email == "modern.recruiter@acme.com"
