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


class TestIngestHygiene:
    """[W2B-3 + W2B-8] the title transforms at T0 ingest: personal
    phone numbers redacted (poster-published contact digits are the
    PII class riding the wholesale industry mirror — 22 rows in the
    10-26 census) and HTML entities decoded (945 titles carried raw
    &amp;/&#39;/&nbsp; — the title token IS the only T0 signal)."""

    def test_phone_redaction_regex(self):
        # [W2B-3] the pinned shapes: the example transform verbatim +
        # the contiguous/paren/+1/dotted phone forms; the two-group
        # ranges (miles/pay/truck-years) are title signal, NOT PII
        sub = lambda t: rs._CL_PHONE.sub("[redacted]", t)
        assert sub("Tina 713-493-2288") == "Tina [redacted]"
        assert sub("Luna roofing hiring 4254265760") == \
            "Luna roofing hiring [redacted]"
        assert sub("Call (561) 718-7316 today") == "Call [redacted] today"
        assert sub("Tina +1 713-493-2288") == "Tina [redacted]"
        assert sub("713.493.2288") == "[redacted]"
        # the exemptions — 4-4 / 3-4 two-group ranges stay
        assert sub("CDL 2500-3000 MILES/WEEK") == "CDL 2500-3000 MILES/WEEK"
        assert sub("Delivery Driver 900-1000") == "Delivery Driver 900-1000"
        assert sub("2021-2026 Trucks") == "2021-2026 Trucks"
        assert sub("75- 80 CPM") == "75- 80 CPM"
        assert sub("HVAC LEAD INSTALLER 5+ YEARS") == \
            "HVAC LEAD INSTALLER 5+ YEARS"

    def test_phone_redaction_and_entities_at_ingest(self, monkeypatch):
        page = """
<li class="cl-static-search-result" title="Houston O/O | Home Weekends | Tina 713-493-2288">
  <a href="https://www.craigslist.org/view/d/houston-oo/e6YNaYGx">
    <div class="title">Houston O/O | Home Weekends | Tina 713-493-2288</div>
    <div class="details">
      <div class="price">$0</div>
      <div class="location">houston</div>
    </div>
  </a>
</li>
<li class="cl-static-search-result" title="Plumbing &amp; HVAC Installer">
  <a href="https://www.craigslist.org/view/d/plumb/aB3x">
    <div class="title">Plumbing &amp; HVAC Installer</div>
    <div class="details">
      <div class="price">$0</div>
      <div class="location">kansas city</div>
    </div>
  </a>
</li>
"""
        _fetch_stub(monkeypatch, {"query=hvac": page})
        rows, v = rs.fetch_craigslist("houston", ["hvac"])
        assert v.startswith("ok")
        by_rid = {r["rid"]: r for r in rows}
        assert by_rid["cl:e6YNaYGx"]["title"] == \
            "Houston O/O | Home Weekends | Tina [redacted]"   # W2B-3
        assert by_rid["cl:aB3x"]["title"] == "Plumbing & HVAC Installer"  # W2B-8

    def test_retro_pass_is_idempotent_on_the_artifact(self):
        # [W2B-3/W2B-8] the one-off (scripts/cl_retro_redact.py) ran
        # 2026-10-26: 22 phones redacted, 945 entities decoded. The
        # shipped artifact must now be free of BOTH classes (the pass
        # is idempotent — [redacted] carries no digits).
        import re
        art = (Path(__file__).resolve().parents[2]
               / "ingest/data/industry/craigslist_home_services.json")
        if not art.exists():
            pytest.skip("artifact absent in this environment")
        d = json.loads(art.read_text())
        ent = re.compile(r"&[a-zA-Z]+;|&#\d+;")
        n_ph = sum(1 for r in d["rows"]
                   if rs._CL_PHONE.search(r["title"]))
        n_ent = sum(1 for r in d["rows"] if ent.search(r["title"]))
        assert n_ph == 0, "phone-shaped digits survived the retro pass"
        assert n_ent == 0, "HTML entities survived the retro pass"
        # gates/ledger preserved by the in-place pass
        assert d["gates"]["rows_total"] == len(d["rows"])


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

    def test_blocked_rerun_preserves_prior_rows(self, tmp_path, monkeypatch):
        """[W2B-1] the fresh-sweep-wins replace fires ONLY on an ok
        verdict — a captcha'd/http-error city keeps its prior window's
        rows (the city pauses; the artifact is the only store and rows
        are dateless). The old unconditional replace silently regressed
        cross-window data; the verdict is still recorded honestly."""
        out = tmp_path / "craigslist_home_services.json"
        monkeypatch.setattr(cs, "OUT", out)
        monkeypatch.setattr(cs, "REPORT",
                            tmp_path / "craigslist.report.md")
        good = ([{"title": "HVAC Lead Installer", "company": "",
                  "loc": "x (sfbay)", "url": "u/1", "posted": "",
                  "dateless": True, "desc": "", "source": "craigslist",
                  "layer": "l2", "query": "cl:sfbay:hvac",
                  "rid": "cl:s1"}], "ok|hvac:1")
        monkeypatch.setattr(cs.rs, "fetch_craigslist", lambda *a: good)
        cs.run_window(["sfbay"])
        assert len(json.load(open(out))["rows"]) == 1

        # blocked re-run (captcha mid-city): prior rows KEPT, the
        # partial fetch discarded, the verdict recorded
        blocked = ([{"title": "partial junk", "company": "",
                     "loc": "x (sfbay)", "url": "u/2", "posted": "",
                     "dateless": True, "desc": "", "source": "craigslist",
                     "layer": "l2", "query": "cl:sfbay:plumbing",
                     "rid": "cl:s2"}], "blocked:captcha|plumbing:1")
        monkeypatch.setattr(cs.rs, "fetch_craigslist",
                            lambda *a: blocked)
        cs.run_window(["sfbay"])
        d = json.load(open(out))
        titles = [r["title"] for r in d["rows"]]
        assert titles == ["HVAC Lead Installer"]   # prior row survived
        assert "partial junk" not in titles        # partial discarded
        assert d["cities_run"]["sfbay"]["verdict"].startswith(
            "blocked:captcha")                      # honest verdict
        assert d["cities_run"]["sfbay"]["rows"] == 1   # ledger exact

        # a full-http-error city ('blocked', 0 rows) also preserves
        dead = ([], "blocked|hvac:http403|plumbing:http403")
        monkeypatch.setattr(cs.rs, "fetch_craigslist", lambda *a: dead)
        cs.run_window(["sfbay"])
        d = json.load(open(out))
        assert [r["title"] for r in d["rows"]] == ["HVAC Lead Installer"]
        assert d["cities_run"]["sfbay"]["rows"] == 1

        # and the ok path still replaces (the fresh sweep wins)
        fresh = ([{"title": "new sweep", "company": "",
                   "loc": "x (sfbay)", "url": "u/3", "posted": "",
                   "dateless": True, "desc": "", "source": "craigslist",
                   "layer": "l2", "query": "cl:sfbay:hvac",
                   "rid": "cl:s3"}], "ok|hvac:1")
        monkeypatch.setattr(cs.rs, "fetch_craigslist", lambda *a: fresh)
        cs.run_window(["sfbay"])
        d = json.load(open(out))
        assert [r["title"] for r in d["rows"]] == ["new sweep"]

    def test_ok_replace_keeps_queryless_rows(self, tmp_path, monkeypatch):
        """[W2B-1 sub-note] the defensive fallback KEEPS query-less
        rows (the old `or f"cl:{city}:"` default dropped them from
        the OTHER cities' keep-set)."""
        out = tmp_path / "craigslist_home_services.json"
        monkeypatch.setattr(cs, "OUT", out)
        monkeypatch.setattr(cs, "REPORT",
                            tmp_path / "craigslist.report.md")
        out.write_text(json.dumps({
            "ran_at": "t", "cities_run": {},
            "rows": [{"title": "orphan", "query": ""}],
            "gates": {}}))
        good = ([{"title": "fresh", "company": "", "loc": "x",
                  "url": "u/1", "posted": "", "dateless": True,
                  "desc": "", "source": "craigslist", "layer": "l2",
                  "query": "cl:sfbay:hvac", "rid": "cl:s1"}],
                "ok|hvac:1")
        monkeypatch.setattr(cs.rs, "fetch_craigslist", lambda *a: good)
        cs.run_window(["sfbay"])
        titles = [r["title"] for r in json.load(open(out))["rows"]]
        assert "orphan" in titles             # kept, not dropped

    def test_corrupt_artifact_backed_up_and_fail_loud(self, tmp_path,
                                                      monkeypatch):
        """[W2B-5] a corrupt read must NEVER silently reset to the
        empty skeleton — run_window would then _save over it, losing
        every other city's rows. Back up + raise."""
        out = tmp_path / "craigslist_home_services.json"
        monkeypatch.setattr(cs, "OUT", out)
        monkeypatch.setattr(cs, "REPORT",
                            tmp_path / "craigslist.report.md")
        out.write_text('{"rows": [truncated')   # corrupt JSON
        with pytest.raises(json.JSONDecodeError):
            cs._load()
        bak = tmp_path / "craigslist_home_services.json.corrupt"
        assert bak.exists()
        assert bak.read_text() == '{"rows": [truncated'  # data rescued
        # the corrupt original is left for the operator — refuse to
        # clobber, fail loud on EVERY access
        with pytest.raises(json.JSONDecodeError):
            cs.run_window(["sfbay"])          # never reaches _save

