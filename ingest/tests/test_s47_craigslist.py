"""test_s47_craigslist.py — the T0 craigslist lane pins (S47 §3).

DESIGN-S47 §3 + the F15 amendments: the canonical URL form (the
city-subdomain 301s), dateless rows (no datetime attr in the static
HTML — no per-post fetches), the city-pause captcha doctrine (no
bypass, ever), the accumulate-across-windows artifact semantics
(fresh-sweep-wins per city), and the derived-data doctrine (title/loc/
url/rid/counts — never post bodies). The lane is BOX-SIDE only: no
yml exists on purpose (IP reputation — the S44/R1 doctrine); this pin
holds THAT too.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import role_sweep as rs  # noqa: E402
import craigslist_sweep as cs  # noqa: E402

# the live probe shape (2026-10-26, sfbay/hvac: 49 rows)
_PROBE_HTML = """
<li class="cl-static-search-result" title="HVAC LEAD INSTALLER 5+ YEARS">
  <a href="https://www.craigslist.org/view/d/san-francisco-hvac/e6YNaYGx">
    <div class="title">HVAC LEAD INSTALLER 5+ YEARS</div>
    <div class="details">
      <div class="price">$0</div>
      <div class="location">
        city of san francisco
      </div>
    </div>
  </a>
</li>
<li class="cl-static-search-result" title="Plumber - Journeyman">
  <a href="https://www.craigslist.org/view/d/sanjose-plumber/aB3x">
    <div class="title">Plumber - Journeyman</div>
    <div class="details">
      <div class="price">$0</div>
      <div class="location">san jose</div>
    </div>
  </a>
</li>
"""


def _fetch_stub(monkeypatch, html_by_url, verdict="ok"):
    calls = []

    class _R:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake(req, timeout=30):
        url = getattr(req, "full_url", str(req))
        calls.append(url)
        for k, v in html_by_url.items():
            if k in url:
                return _R(v)
        return _R("<html></html>")

    monkeypatch.setattr(rs.urllib.request, "urlopen", _fake)
    return calls


class TestRegistries:
    def test_city_and_query_rosters(self):
        assert len(rs.CRAIGSLIST_CITIES) == 30
        assert "sfbay" in rs.CRAIGSLIST_CITIES
        assert "losangeles" in rs.CRAIGSLIST_CITIES
        # the F9 zero-aggregator-yield verticals ride the query set
        for q in ("hvac", "plumbing", "pool cleaning", "appliance repair",
                  "junk removal", "moving", "office manager", "dispatcher"):
            assert q in rs.CRAIGSLIST_QUERIES

    def test_no_github_lane_exists(self):
        """the T0 lane is BOX-SIDE only — a yml would be the S44/R1
        IP-reputation violation. The driver script exists; no workflow."""
        wf = Path(__file__).resolve().parents[2] / ".github/workflows"
        for f in wf.glob("*.yml"):
            assert "craigslist" not in f.name.lower(), \
                "craigslist must NEVER be a scheduled GHA lane"


class TestParser:
    def test_rows_parse_with_dateless_stamp(self, monkeypatch):
        _fetch_stub(monkeypatch,
                    {"query=plumbing": _PROBE_HTML, "query=hvac":
                     _PROBE_HTML})
        rows, v = rs.fetch_craigslist("sfbay", ["plumbing"])
        assert v.startswith("ok")
        assert len(rows) == 2
        r = rows[0]
        assert r["title"] == "HVAC LEAD INSTALLER 5+ YEARS"
        assert r["source"] == "craigslist"
        assert r["dateless"] is True          # F15: no datetime attr exists
        assert r["posted"] == ""
        assert r["rid"].startswith("cl:")
        assert r["url"].startswith("https://www.craigslist.org/view/")
        assert "sfbay" in r["loc"]           # the metro rides the loc

    def test_canonical_url_form(self, monkeypatch):
        calls = _fetch_stub(monkeypatch, {})
        rs.fetch_craigslist("sfbay", ["hvac"])
        assert calls, "one fetch per query"
        assert calls[0].startswith(
            "https://www.craigslist.org/search/area/sfbay?cat=jjj&query=")

    def test_captcha_pauses_the_city(self, monkeypatch):
        _fetch_stub(monkeypatch, {"query=hvac":
                                  "<html>recaptcha required</html>"})
        rows, v = rs.fetch_craigslist("sfbay", ["hvac", "plumbing"])
        assert v.startswith("blocked:captcha")
        assert rows == []                     # the city pauses — no bypass

    def test_http_error_is_fail_soft_per_query(self, monkeypatch):
        def _err(url, timeout=30):
            import urllib.error
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        monkeypatch.setattr(rs.urllib.request, "urlopen", _err)
        rows, v = rs.fetch_craigslist("sfbay", ["hvac"])
        assert rows == []
        assert "http403" in v                 # honest verdict, recorded


class TestDriver:
    def test_window_accumulates_and_replaces(self, tmp_path, monkeypatch):
        out = tmp_path / "craigslist_home_services.json"
        monkeypatch.setattr(cs, "OUT", out)
        monkeypatch.setattr(cs, "REPORT",
                            tmp_path / "craigslist.report.md")
        _fetch_stub(monkeypatch, {"query=plumbing": _PROBE_HTML})

        def _fake_fetch(city, queries):
            tag = f"[{city}]"
            return ([{"title": f"t {tag}", "company": "",
                      "loc": f"x ({city})", "url": f"u/{city}",
                      "posted": "", "dateless": True, "desc": "",
                      "source": "craigslist", "layer": "l2",
                      "query": f"cl:{city}:hvac",
                      "rid": f"cl:{city}1"}], "ok|hvac:1")
        monkeypatch.setattr(cs.rs, "fetch_craigslist", _fake_fetch)

        cs.run_window(["sfbay"])
        d = json.load(open(out))
        assert len(d["rows"]) == 1 and "sfbay" in d["cities_run"]
        cs.run_window(["losangeles"])
        d = json.load(open(out))
        assert len(d["rows"]) == 2            # accumulated across windows
        assert {r["query"].split(":")[1] for r in d["rows"]} == \
            {"sfbay", "losangeles"}
        cs.run_window(["sfbay"])               # re-run replaces the city
        d = json.load(open(out))
        assert len(d["rows"]) == 2            # LA kept + sfbay replaced
        assert d["cities_run"]["sfbay"]["rows"] == 1
        # the report twin exists with the per-city table
        rep = (tmp_path / "craigslist.report.md").read_text()
        assert "| sfbay |" in rep and "| losangeles |" in rep

    def test_status_reads_only(self, tmp_path, monkeypatch, capsys):
        out = tmp_path / "craigslist_home_services.json"
        monkeypatch.setattr(cs, "OUT", out)
        monkeypatch.setattr(cs, "REPORT",
                            tmp_path / "craigslist.report.md")
        out.write_text(json.dumps({"ran_at": "2026-10-26T00:00:00Z",
                                   "cities_run": {"sfbay": {}},
                                   "rows": [{"query": "cl:sfbay:hvac"}]}))
        assert cs.main_with(["--status"]) == 0
        outp = capsys.readouterr().out
        assert "sfbay" in outp and "1" in outp
