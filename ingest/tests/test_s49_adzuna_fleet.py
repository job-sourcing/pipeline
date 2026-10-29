"""test_s49_adzuna_fleet.py — [S49-D4] the adzuna key-FLEET pins.

Offline: rotation parsing, legacy fallback, dead-for-run quarantine,
and the salary round-trip seams (fetch-row construction covered via a
mocked _http_json so no network, no key spend).
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

rs = importlib.import_module("role_sweep")


@pytest.fixture(autouse=True)
def _reset_fleet(monkeypatch):
    """every test starts from a cold fleet + clean env — including the
    ingest/.env FILE (the real one carries the production key pair and
    _env falls back to it; the tests must be file-inert)."""
    monkeypatch.setattr(rs, "_ADZUNA_FLEET", {"keys": None, "idx": 0,
                                              "dead": set()})
    monkeypatch.setattr(
        rs, "_env",
        lambda name, _f=os.environ: _f.get(name, ""))
    yield


def test_fleet_empty_without_env():
    assert rs._adzuna_keys() == []
    assert rs._adzuna_next_key() == (None, "no-key")
    rows, v = rs.fetch_adzuna(["product manager"])
    assert rows == [] and v == "no-key"


def test_fleet_legacy_single_pair(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "legacy-id")
    monkeypatch.setenv("ADZUNA_API_KEY", "legacy-key")
    keys = rs._adzuna_keys()
    assert keys == [("legacy-id", "legacy-key")]
    # round-robin over one key is stable
    assert rs._adzuna_next_key() == (("legacy-id", "legacy-key"), "ok")


def test_fleet_parses_adzuna_keys_env(monkeypatch):
    monkeypatch.setenv("ADZUNA_KEYS", "id1:k1, id2:k2,bad-entry, :k4")
    monkeypatch.setenv("ADZUNA_APP_ID", "legacy-id")
    monkeypatch.setenv("ADZUNA_API_KEY", "legacy-key")
    keys = rs._adzuna_keys()
    assert keys == [("id1", "k1"), ("id2", "k2"),
                    ("legacy-id", "legacy-key")]


def test_fleet_rotation_round_robins(monkeypatch):
    monkeypatch.setenv("ADZUNA_KEYS", "id1:k1,id2:k2")
    picks = [rs._adzuna_next_key()[0] for _ in range(4)]
    assert picks == [("id1", "k1"), ("id2", "k2"),
                     ("id1", "k1"), ("id2", "k2")]


def test_fleet_quarantine_dead_for_run(monkeypatch):
    monkeypatch.setenv("ADZUNA_KEYS", "id1:k1,id2:k2")
    rs._adzuna_keys()
    rs._ADZUNA_FLEET["dead"].add(("id1", "k1"))
    picks = [rs._adzuna_next_key()[0] for _ in range(3)]
    assert picks == [("id2", "k2")] * 3
    # all dead -> the honest quota verdict
    rs._ADZUNA_FLEET["dead"].add(("id2", "k2"))
    assert rs._adzuna_next_key() == (None, "quota:all-keys")


def test_fetch_adzuna_preserves_salary(monkeypatch):
    """the [S49-D4] seam: salary_min/salary_max must survive fetch-row
    construction (they were DISCARDED pre-S49)."""
    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_API_KEY", "y")

    def fake_http_json(url, timeout=30):
        return {"results": [
            {"id": "A1", "title": "Office Manager", "created":
             "2026-10-20", "description": "d", "redirect_url": "u",
             "company": {"display_name": "Acme"},
             "location": {"display_name": "Fresno, CA"},
             "category": {"tag": "admin-jobs"},
             "salary_min": 52000, "salary_max": 71000},
            {"id": "A2", "title": "No salary row", "created":
             "2026-10-20", "description": "d", "redirect_url": "u2",
             "company": {"display_name": "Beta"},
             "location": {"display_name": "Fresno, CA"},
             "category": {"tag": "admin-jobs"}},
        ]}

    monkeypatch.setattr(rs, "_http_json", fake_http_json)
    rows, v = rs.fetch_adzuna(["office manager"])
    assert v == "ok" and len(rows) == 2
    assert rows[0]["salary_min"] == 52000
    assert rows[0]["salary_max"] == 71000
    assert "salary_min" not in rows[1]


def test_csv_salary_columns_family_gated():
    """gen/dmm CSVs carry the salary columns; pm's header stays
    byte-identical (the identity doctrine)."""
    row = {"title": "t", "company": "c", "company_key": "ck",
           "salary_min": 1, "salary_max": 2}
    gen_csv = rs._csv_bytes([row], "gen").decode()
    assert "salary_min,salary_max" in gen_csv.splitlines()[0]
    pm_csv = rs._csv_bytes([row], "pm").decode()
    assert "salary" not in pm_csv.splitlines()[0]
