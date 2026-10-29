"""test_s49_fleet_tier.py — [S49-D5] the Netlify fleet tier pins.

The free US-egress transport tier in the census probe ladder
(board_probe_census.py): class-gating (which direct failures earn the
fleet retry), the worker response mapping (same status vocabulary as
the direct leg), fail-soft registry handling, and the ladder ORDER
(direct → fleet → firecrawl; the fleet try can only displace paid
successes, never add spend).
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "audit/s48/census"))
import board_probe_census as bp  # noqa: E402


# ── the class gates ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("status,expect", [
    # the measured 18% blocked class: fleet first, always
    ("blocked_http401", True), ("blocked_http403", True),
    ("blocked_http406", True), ("blocked_http429", True),
    # transient + conn-fail: a US egress may simply reach it
    ("error_http500", True), ("error_http503", True),
    ("direct_fail:Timeout", True), ("direct_fail:ConnectionError", True),
    # NO JS render in the worker — a js_wall re-fetch re-walls
    ("js_wall", False),
    # definitive classes never reach ANY paid/free retry
    ("error_http404", False), ("error_http400", False),
    ("error_http410", False), ("error_http429", False),
    ("error_http405", False),
])
def test_fleet_escalatable_classes(status, expect):
    assert bp._fleet_escalatable(status) is expect


def test_fc_gate_unchanged_by_fleet():
    """The fc gate keeps its S49-D4 semantics — the fleet tier must not
    have loosened the PAID gate."""
    assert bp._fc_escalatable("js_wall") is True          # stealth renders
    assert bp._fc_escalatable("blocked_http403") is True
    assert bp._fc_escalatable("error_http404") is False   # never pay 404s
    assert bp._fc_escalatable("blocked_http429") is False  # never pay 429s


# ── fetch_via_fleet: mapping + fail-soft ────────────────────────────────────
class _FakeResp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fleet_no_registry_failsoft(monkeypatch):
    monkeypatch.setattr(bp, "FLEET_REG",
                        REPO / "no/such/registry.json")
    p = bp.fetch_via_fleet("https://example.com/", "/")
    assert p.transport == "fleet"
    assert p.status == "fleet_fail:no-registry"


def test_fleet_ok_mapping(monkeypatch):
    monkeypatch.setattr(bp, "_fleet_workers",
                        lambda: ["https://worker.test/fleet"])
    monkeypatch.setattr(
        bp.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(
            {"ok": True, "status": 200, "body": "<html>careers</html>",
             "bytes": 22}))
    p = bp.fetch_via_fleet("https://example.com/", "/")
    assert (p.transport, p.status) == ("fleet", "ok")
    assert p.body == "<html>careers</html>"
    assert p.http_code == 200


def test_fleet_blocked_mapping(monkeypatch):
    monkeypatch.setattr(bp, "_fleet_workers",
                        lambda: ["https://worker.test/fleet"])
    monkeypatch.setattr(
        bp.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(
            {"ok": True, "status": 403, "body": "", "bytes": 0}))
    p = bp.fetch_via_fleet("https://example.com/", "/")
    assert (p.transport, p.status) == ("fleet", "blocked_http403")


def test_fleet_js_wall_detection(monkeypatch):
    monkeypatch.setattr(bp, "_fleet_workers",
                        lambda: ["https://worker.test/fleet"])
    monkeypatch.setattr(
        bp.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(
            {"ok": True, "status": 200,
             "body": "Enable JavaScript and cookies to continue", "bytes": 40}))
    p = bp.fetch_via_fleet("https://example.com/", "/")
    assert (p.transport, p.status) == ("fleet", "js_wall")


def test_fleet_worker_error_failsoft(monkeypatch):
    monkeypatch.setattr(bp, "_fleet_workers",
                        lambda: ["https://worker.test/fleet"])

    def _raise(req, timeout):
        raise urllib.error.URLError("worker down")

    monkeypatch.setattr(bp.urllib.request, "urlopen", _raise)
    p = bp.fetch_via_fleet("https://example.com/", "/")
    assert p.transport == "fleet"
    assert p.status.startswith("fleet_fail:")


def test_fleet_rotation_round_robin(monkeypatch):
    """the 6 workers rotate — the load spreads and one dead worker
    never wedges the tier."""
    calls = []
    monkeypatch.setattr(bp, "_fleet_workers",
                        lambda: [f"https://w{i}.test/fleet" for i in range(3)])

    def _fake(req, timeout):
        calls.append(req.full_url)
        return _FakeResp({"ok": True, "status": 200,
                          "body": "<html>x</html>", "bytes": 1})

    monkeypatch.setattr(bp.urllib.request, "urlopen", _fake)
    for _ in range(3):
        bp.fetch_via_fleet("https://example.com/", "/")
    assert calls == ["https://w1.test/fleet", "https://w2.test/fleet",
                     "https://w0.test/fleet"]


# ── the ladder ORDER: direct → fleet → firecrawl ────────────────────────────
def test_ladder_fleet_lifts_no_fc_spend(monkeypatch):
    """direct blocked → fleet ok: the PAID leg must NEVER fire."""
    fc_called = []

    def _no_fc(url, path, budget):
        fc_called.append(url)
        return bp.Page(url, path, "firecrawl", "ok", "fc-body")

    monkeypatch.setattr(bp, "fetch_direct",
                        lambda u, p: bp.Page(u, p, "direct",
                                             "blocked_http403", "", 403))
    monkeypatch.setattr(bp, "fetch_via_fleet",
                        lambda u, p: bp.Page(u, p, "fleet", "ok",
                                             "fleet-body", 200))
    monkeypatch.setattr(bp, "fetch_via_firecrawl", _no_fc)
    p = bp.fetch_page("https://x.test/", "/", bp.ProbeBudget(fc_budget=5))
    assert p.transport == "fleet" and p.status == "ok"
    assert "fleet-lifted" in p.note
    assert fc_called == []


def test_ladder_fleet_miss_falls_to_fc_real(monkeypatch):
    """direct blocked → fleet also blocked → firecrawl carries it."""
    monkeypatch.setattr(bp, "fetch_direct",
                        lambda u, p: bp.Page(u, p, "direct",
                                             "blocked_http403", "", 403))
    monkeypatch.setattr(bp, "fetch_via_fleet",
                        lambda u, p: bp.Page(u, p, "fleet",
                                             "blocked_http403", "", 403))
    monkeypatch.setattr(bp, "fetch_via_firecrawl",
                        lambda u, p, b: bp.Page(u, p, "firecrawl", "ok",
                                                "fc-body", 200))
    p = bp.fetch_page("https://x.test/", "/", bp.ProbeBudget(fc_budget=5))
    assert p.transport == "firecrawl" and p.status == "ok"
    assert p.note.startswith("direct=blocked_http403")


def test_ladder_404_never_escalates_at_all(monkeypatch):
    """a definitive 404 touches NEITHER the fleet NOR firecrawl."""
    fleet_called, fc_called = [], []

    def _fleet(u, p):
        fleet_called.append(u)
        return bp.Page(u, p, "fleet", "ok", "x", 200)

    def _fc(u, p, b):
        fc_called.append(u)
        return bp.Page(u, p, "firecrawl", "ok", "x", 200)

    monkeypatch.setattr(bp, "fetch_direct",
                        lambda u, p: bp.Page(u, p, "direct",
                                             "error_http404", "", 404))
    monkeypatch.setattr(bp, "fetch_via_fleet", _fleet)
    monkeypatch.setattr(bp, "fetch_via_firecrawl", _fc)
    p = bp.fetch_page("https://x.test/", "/", bp.ProbeBudget(fc_budget=5))
    assert p.status == "error_http404" and p.transport == "direct"
    assert "no-fc-escalate" in p.note
    assert fleet_called == [] and fc_called == []


def test_ladder_js_wall_skips_fleet_not_fc(monkeypatch):
    """js_wall: the worker has no JS render — skip it, the stealth
    PAID browser still gets its chance."""
    fleet_called, fc_called = [], []

    def _fleet(u, p):
        fleet_called.append(u)
        return bp.Page(u, p, "fleet", "ok", "x", 200)

    def _fc(u, p, b):
        fc_called.append(u)
        return bp.Page(u, p, "firecrawl", "ok", "fc-body", 200)

    monkeypatch.setattr(bp, "fetch_direct",
                        lambda u, p: bp.Page(u, p, "direct", "js_wall",
                                             "wall", 200))
    monkeypatch.setattr(bp, "fetch_via_fleet", _fleet)
    monkeypatch.setattr(bp, "fetch_via_firecrawl", _fc)
    p = bp.fetch_page("https://x.test/", "/", bp.ProbeBudget(fc_budget=5))
    assert p.transport == "firecrawl" and p.status == "ok"
    assert fleet_called == [] and len(fc_called) == 1
