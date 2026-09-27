"""Facet-01 export contract (Wave-R R2) — hermetic mapping + job_id pins.

The consumer side is the MAIN repo's facet-01 loader
(facets/01_profile_foundation/build/profile_foundation/corpus.py,
load_postings_jsonl). These tests pin the PRODUCER side:
jobsearch/export.py + Store.export_jsonl(contract="facet01").
"""
from __future__ import annotations

import json

import pytest

from jobsearch.export import facet01_row, native_job_id, stable_job_id
from jobsearch.models import Job

from conftest import make_job

# ── job_id: board-native extraction ────────────────────────────────────────

NATIVE_IDS = [
    # (url, expected job_id) — one pin per pattern family
    ("https://www.linkedin.com/jobs/view/3904912345/?utm=whatever",
     "linkedin-3904912345"),
    ("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/3904912345",
     "linkedin-3904912345"),
    ("https://boards.greenhouse.io/acme/jobs/1234567",
     "greenhouse-1234567"),
    ("https://job-boards.greenhouse.io/acme/jobs/1234567",
     "greenhouse-1234567"),
    # Greenhouse postings behind company careers domains (gh_jid param)
    ("https://careers.datadoghq.com/detail/8136510/?gh_jid=8136510",
     "greenhouse-8136510"),
    ("https://stripe.com/jobs/search?gh_jid=6692166",
     "greenhouse-6692166"),
    ("https://jobs.lever.co/acme/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
     "lever-1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"),
    ("https://jobs.ashbyhq.com/acme/0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
     "ashby-0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d"),
    ("https://acme.jobs.personio.de/job/481234?language=en",
     "personio-481234"),
    ("https://careers.smartrecruiters.com/Acme/7123456789",
     "smartrecruiters-7123456789"),
    ("https://jobs.workable.com/view/x1vVGF5kf3mr9heXovhQww/remote-engineer",
     "workable-x1vVGF5kf3mr9heXovhQww"),
    ("https://wellfound.com/jobs/2847592-senior-backend-engineer",
     "wellfound-2847592"),
    ("https://remotive.com/remote-jobs/9988776/senior-platform-engineer",
     "remotive-9988776"),
    ("https://remotive.com/remote-jobs/marketing/remote-office-assistant-1680495",
     "remotive-1680495"),
    ("https://remoteok.com/remote-jobs/112233-senior-devops-engineer",
     "remoteok-112233"),
    ("https://remoteOK.com/remote-jobs/remote-junior-payroll-assistant-sleek-1137391",
     "remoteok-1137391"),
    ("https://www.arbeitnow.com/jobs/companies/workidentity/"
     "senior-cloud-engineer-gn-hamburg-48062",
     "arbeitnow-48062"),
    ("https://jobicy.com/jobs/150817-senior-software-developer-in-test",
     "jobicy-150817"),
    ("https://www.careerjet.com/jobad/abc1234567",
     None),  # careerjet ids are numeric — a non-numeric tail must NOT match
    ("https://www.careerjet.com/jobad/123456789",
     "careerjet-123456789"),
    ("https://www.adzuna.com/details/4856613987",
     "adzuna-4856613987"),
    ("https://www.usajobs.gov/job/812345600",
     "usajobs-812345600"),
    ("https://www.usajobs.gov:443/GetJob/ViewDetails/812345600",
     "usajobs-812345600"),
    ("https://news.ycombinator.com/item?id=44123456",
     "hn-44123456"),
    ("https://acme.wd5.myworkdayjobs.com/en-US/candidate/job/8123456789012",
     "workday-8123456789012"),
    # site-path format: /job/[location/]title-slug_reqId
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/"
     "US-CA-Santa-Clara/Senior-Software-Engineer--Cloud-Native-Stack---"
     "CSP-Engagements_JR2001098",
     "workday-JR2001098"),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/"
     "US-CA-Remote/Senior-Software-Engineer--AI-Agent-Compute_JR2021516-1",
     "workday-JR2021516-1"),
    ("https://www.welcometothejungle.com/fr/companies/acme/jobs/"
     "senior-platform-engineer_paris",
     "wttj-senior-platform-engineer_paris"),
    # no known board → no native id (caller falls back to the URL hash)
    ("https://example.com/job/1", None),
    ("", None),
    ("https://www.jooble.org/away/abc-def", None),
]


@pytest.mark.parametrize("url,expected", NATIVE_IDS)
def test_native_job_id_extraction(url, expected):
    assert native_job_id(url) == expected


def test_native_ids_never_collide_across_boards():
    """Same numeric id on two boards → two distinct namespaced job_ids."""
    a = stable_job_id(make_job(
        link="https://boards.greenhouse.io/x/jobs/1234", source="Greenhouse"))
    b = stable_job_id(make_job(
        link="https://www.linkedin.com/jobs/view/1234",
        source="LinkedIn Guest"))
    assert a == "greenhouse-1234"
    assert b == "linkedin-1234"
    assert a != b


# ── job_id: fallbacks ──────────────────────────────────────────────────────

def test_url_hash_fallback_is_stable_and_distinct():
    j1 = make_job(link="https://example.com/job/1")
    j2 = make_job(link="https://example.com/job/1")
    j3 = make_job(link="https://example.com/job/2")
    assert stable_job_id(j1) == stable_job_id(j2)      # stable across runs
    assert stable_job_id(j1) != stable_job_id(j3)      # distinct per URL
    assert stable_job_id(j1).startswith("u-")
    assert len(stable_job_id(j1)) == 2 + 16             # 'u-' + sha1[:16]


def test_linkless_fingerprint_fallback():
    kw = dict(source="Glassdoor", company="Acme", title="Data Engineer")
    a = stable_job_id(make_job(link="", **kw))
    b = stable_job_id(make_job(link="", **kw))
    c = stable_job_id(make_job(link="", source="Glassdoor",
                               company="Other", title="Data Engineer"))
    assert a == b and a != c
    assert a.startswith("c-")
    # same fingerprint basis as the upsert linkless identity
    d = stable_job_id(make_job(link="", source="Glassdoor", company="Acme",
                               title="Data Engineer"))
    assert a == d


def test_job_id_never_empty():
    assert stable_job_id(make_job(link="", company="", title="",
                                  source="")).startswith("c-")


# ── contract row mapping ───────────────────────────────────────────────────

def test_facet01_row_contract_fields():
    # known-USD board link so the salary band survives the S15-R4 P1-1
    # unknown-currency drop rule (an unmarked band must never be exported)
    j = make_job(
        link="https://www.usajobs.gov/job/812345600",
        tier="senior", work_mode="remote", remote=True,
        date_posted="2026-09-16", skills=["Python", "Go"],
        salary_min=120000.0, salary_max=150000.0,
        ats_platform="greenhouse", search_query="software engineer")
    row = facet01_row(j)
    # the contract keys, with the consumer's exact names
    assert row["title"] == "Python Developer"
    assert row["job_id"] == stable_job_id(j)
    assert row["company"] == "Acme Cloud"
    assert row["location"] is None or isinstance(row["location"], str)
    assert row["experience_level"] == "senior"     # from tier
    assert row["work_type"] == "remote"            # from work_mode
    assert row["remote_allowed"] is True           # real JSON bool
    assert row["salary_min"] == 120000.0
    assert row["salary_max"] == 150000.0
    assert row["currency"] == "USD"
    assert row["skills"] == ["Python", "Go"]       # real array
    assert row["listed_time"] == "2026-09-16"      # from date_posted
    assert row["source"] == "Remotive"
    assert row["description"] == j.description
    # provenance extras survive for non-facet-01 readers
    assert row["link"] == "https://www.usajobs.gov/job/812345600"
    assert row["tier"] == "senior" and row["work_mode"] == "remote"
    assert row["ats_platform"] == "greenhouse"
    # scoring artifacts are corpus noise — never exported
    assert "llm_score" not in row and "tfidf_score" not in row
    assert "trust_score" not in row


def test_work_mode_unknown_exports_null_not_noise():
    row = facet01_row(make_job(work_mode="unknown"))
    assert row["work_type"] is None
    assert row["work_mode"] == "unknown"            # raw value stays as extra


def test_remote_false_is_bool_false():
    row = facet01_row(make_job(remote=False))
    assert row["remote_allowed"] is False


def test_skills_none_becomes_empty_list():
    row = facet01_row(make_job(skills=None))
    assert row["skills"] == []


def test_currency_contract_usd_eur_and_unknown_dropped():
    """S15-R4 P1-1: USD marked; WTTJ marked EUR (the facet-01 loader
    EXCLUDES non-USD bands); unknown-currency boards DROP the band so no
    number rides the consumer's default-USD assumption."""
    usajobs = facet01_row(make_job(
        link="https://www.usajobs.gov/job/812345600",
        salary_min=100000.0, salary_max=120000.0))
    wttj = facet01_row(make_job(
        link="https://www.welcometothejungle.com/fr/companies/x/jobs/y",
        salary_min=50000.0, salary_max=60000.0))
    ashby = facet01_row(make_job(
        link="https://jobs.ashbyhq.com/acme/abc123",
        salary_min=90000.0, salary_max=110000.0))
    nosalary = facet01_row(make_job(
        link="https://www.usajobs.gov/job/812345600"))
    assert usajobs["currency"] == "USD"
    assert usajobs["salary_min"] == 100000.0
    assert wttj["currency"] == "EUR"        # marked non-USD → loader excludes
    assert wttj["salary_min"] == 50000.0    # band kept, consumer filters it
    assert ashby["currency"] is None        # unknown → band DROPPED
    assert ashby["salary_min"] is None and ashby["salary_max"] is None
    assert nosalary["currency"] is None


def test_jooble_native_id_beats_unstable_url_hash():
    """S15-R4 P1-2: Jooble URLs carry volatile rank/page params (pos/p/ckey)
    — the path id is the stable board-native identity."""
    j = make_job(link="https://jooble.org/desc/-5503626742189972386"
                      "?ckey=software+engineer&rgn=%28null%29&pos=35&p=2")
    row = facet01_row(j)
    assert row["job_id"] == "jooble--5503626742189972386"


def test_description_truncation_is_export_only():
    long_desc = "x" * 5000
    j = make_job(description=long_desc)
    row = facet01_row(j, max_description_chars=4000)
    assert len(row["description"]) == 4000
    assert row["description_truncated"] is True
    assert j.description == long_desc                  # DB/model untouched
    intact = facet01_row(j)
    assert intact["description"] == long_desc
    assert intact["description_truncated"] is False


def test_description_truncation_never_extends():
    row = facet01_row(make_job(description="short"),
                      max_description_chars=4000)
    assert row["description"] == "short"
    assert row["description_truncated"] is False


# ── Store.export_jsonl(contract="facet01") ─────────────────────────────────

def test_store_export_jsonl_facet01_default(store, tmp_path):
    jobs = [
        make_job(title=f"Engineer {i}", link=f"https://example.com/j/{i}",
                 search_query="python dev", tier="mid",
                 work_mode="hybrid", remote=True,
                 date_posted="2026-09-15", skills=["Python"])
        for i in range(3)
    ]
    store.upsert_jobs(jobs)
    out = tmp_path / "export.jsonl"
    n = store.export_jsonl(out, query="python dev")
    assert n == 3
    rows = [json.loads(x) for x in
            out.read_text(encoding="utf-8").strip().splitlines()]
    assert len(rows) == 3
    for r in rows:
        assert r["title"]
        assert r["job_id"]                       # never empty → no line-N ids
        assert r["experience_level"] == "mid"
        assert r["work_type"] == "hybrid"
        assert r["remote_allowed"] is True
        assert r["listed_time"] == "2026-09-15"
        assert r["skills"] == ["Python"]
    # job_ids unique across rows
    assert len({r["job_id"] for r in rows}) == 3


def test_store_export_jsonl_truncation_passthrough(store, tmp_path):
    store.upsert_jobs([make_job(description="y" * 9000,
                                search_query="q")])
    out = tmp_path / "t.jsonl"
    store.export_jsonl(out, query="q", max_description_chars=4000)
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert len(row["description"]) == 4000
    assert row["description_truncated"] is True


def test_store_export_jsonl_legacy_contract_keeps_model_dump(store, tmp_path):
    """contract=None = the Sprint-2 debug shape (raw model field names)."""
    store.upsert_jobs([make_job(search_query="q", tier="mid",
                                skills=["Go"])])
    out = tmp_path / "legacy.jsonl"
    store.export_jsonl(out, query="q", contract=None)
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert "llm_score" in row             # legacy dumps every model field
    assert "experience_level" not in row  # and none of the contract aliases
    assert row["skills"] == ["Go"]
