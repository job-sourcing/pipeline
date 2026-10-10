"""S27 pins — D-S27-2 the non_cn collection scope (+ D-S27-1 config).

geo_scope="non_cn" (greenhouse/ashby/lever/workable only): row
membership = NOT mainland-China-sited OR remote (remote-even-CN rows
are collected + flagged; the expat-vs-local judgment is the UI's).
HK/TW/MO are deliberately NOT China — they get their own geo labels.
The exporter gates non_cn boards on China-local rows only and fills
the classified country label (the bundle's geo facet).
"""
import importlib.util
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.test_site_boards import (  # noqa: E402
    _patch_fetch, _patch_fetch_list, _ashby_job, _gh_job)
from jobsearch.sources import site_boards  # noqa: E402
from jobsearch.config import Config  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "s26_export_refresh", REPO_ROOT / "scripts" / "s26_export_refresh.py")
exporter = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("s26_export_refresh", exporter)
_spec.loader.exec_module(exporter)

CFG_PATH = (REPO_ROOT / "ingest/data/board_watch/config.json")
_NON_CN_KINDS = {"greenhouse", "ashby", "lever", "workable"}


class TestTextIsCnSited:
    def test_positives(self):
        f = site_boards._text_is_cn_sited
        assert f("Shanghai, China")
        assert f("Shanghai")                       # city-only dialect
        assert f("Beijing")
        assert f("Remote, China")                  # remote+CN (still CN)
        assert f("Shenzhen | Shanghai")
        assert f("上海")                            # CJK
        assert f("Hangzhou, Zhejiang")             # province token
        assert f("CN")                             # ISO code segment
        assert f("Shenzhen, CN")

    def test_negatives(self):
        f = site_boards._text_is_cn_sited
        assert not f("Hong Kong")
        assert not f("Taipei, Taiwan")
        assert not f("Singapore")
        assert not f("Berlin, Germany")
        assert not f("Sunnyvale, CA")
        assert not f("Remote - Global")
        assert not f("APAC - Remote")
        assert not f("")
        assert not f("Tokyo, Japan")               # kanji-free Japan
        assert not f("US, WA, Seattle")


class TestLeverGeoScope:
    def _run(self, monkeypatch, jobs):
        _patch_fetch_list(monkeypatch, jobs)
        return site_boards.list_board(
            "ats:lever:binance", country="United States", cfg=Config(),
            geo_scope="non_cn")

    def _job(self, jid, loc, wp="onsite", country=None):
        j = {"id": jid, "text": f"Job {jid}", "createdAt": 1760000000000,
             "workplaceType": wp,
             "categories": {"commitment": "Full-time", "location": loc,
                            "team": "Infra"}}
        if country:
            j["country"] = country
        return j

    def test_membership(self, monkeypatch):
        rows, meta = self._run(monkeypatch, [
            self._job("us-1", "US, WA, Seattle"),
            self._job("sg-1", "Singapore"),
            self._job("de-1", "Berlin, Germany"),
            self._job("cn-1", "Shanghai, China"),
            self._job("cn-2", "Beijing"),
            self._job("cnr-1", "Remote, China", wp="remote"),
            self._job("hk-1", "Hong Kong"),
            self._job("rem-1", "Remote - Global", wp="remote"),
        ])
        assert set(rows) == {"us-1", "sg-1", "de-1", "cnr-1", "hk-1",
                             "rem-1"}
        assert meta["geo_scope"] == "non_cn"
        assert meta["client_filtered"] == 2     # cn-1, cn-2

    def test_country_code_evidence(self, monkeypatch):
        rows, _ = self._run(monkeypatch, [
            self._job("cnx", "Shanghai", country="CN"),
            self._job("sgx", "Singapore", country="Singapore"),
        ])
        assert set(rows) == {"sgx"}              # structured CN drops


class TestAshbyGeoScope:
    def test_membership(self, monkeypatch):
        jobs = [
            _ashby_job("us-1", "US Eng", country="United States",
                       loc="San Francisco"),
            _ashby_job("cn-1", "CN Eng", country="China",
                       loc="Shanghai"),
            _ashby_job("cn-remote", "CN Remote", country="China",
                       loc="Remote"),
        ]
        # the fixture defaults workplaceType="remote" — make the two
        # onsite rows explicit
        jobs[0]["workplaceType"] = "onsite"
        jobs[1]["workplaceType"] = "onsite"
        jobs[2]["workplaceType"] = "remote"
        _patch_fetch(monkeypatch, jobs)
        rows, meta = site_boards.list_board(
            "ats:ashby:airwallex", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert set(rows) == {"us-1", "cn-remote"}
        assert meta["geo_scope"] == "non_cn"
        assert meta["client_filtered"] == 1

    def test_country_name_mismatch_drops(self, monkeypatch):
        jobs = [_ashby_job("cnx", "CN Eng", country="China",
                           loc="Shanghai")]
        jobs[0]["workplaceType"] = "onsite"
        _patch_fetch(monkeypatch, jobs)
        rows, _ = site_boards.list_board(
            "ats:ashby:airwallex", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert rows == {}


class TestGreenhouseGeoScope:
    def test_membership(self, monkeypatch):
        cn = _gh_job("G1", "r-cn", "CN Role",
                     [{"name": "Shanghai, China"}])
        cn_remote = _gh_job("G2", "r-cnr", "CN Remote Role",
                            [{"name": "Remote, China"}])
        sg = _gh_job("G3", "r-sg", "SG Role",
                     [{"name": "Singapore"}])
        us = _gh_job("G4", "r-us", "US Role",
                     [{"name": "San Francisco, CA"}])
        us_remote = _gh_job("G5", "r-usr", "US Remote Role",
                            [{"name": "Remote - US"}])
        _patch_fetch(monkeypatch, [cn, cn_remote, sg, us, us_remote])
        rows, meta = site_boards.list_board(
            "ats:greenhouse:shein", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert set(rows) == {"r-cnr", "r-sg", "r-us", "r-usr"}
        assert meta["geo_scope"] == "non_cn"

    def test_offices_evidence_counts(self, monkeypatch):
        # E1 office text is CN even when E2 is empty — the shared test
        # consults both channels
        job = _gh_job("G9", "r-off", "Multi Role", [])
        job["location"] = {"name": ""}
        job["offices"] = [{"name": "Beijing, China"}]
        _patch_fetch(monkeypatch, [job])
        rows, _ = site_boards.list_board(
            "ats:greenhouse:shein", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert rows == {}


class TestWorkableGeoScope:
    def _job(self, code, city, country, remote=False):
        return {"shortcode": code, "title": f"T {code}",
                "country": country,
                "city": city, "state": "", "employment_type": "Full-time",
                "telecommuting": remote, "published_on": "2026-05-12",
                "url": f"https://apply.workable.com/j/{code}",
                "locations": []}

    def test_membership(self, monkeypatch):
        _patch_fetch(monkeypatch, [
            self._job("US1", "Irvine", "United States"),
            self._job("CN1", "Shenzhen", "CN"),
            self._job("SG1", "Singapore", "Singapore"),
            self._job("HK1", "Hong Kong", "Hong Kong"),
            self._job("CNR1", "Shanghai", "CN", remote=True),
        ])
        rows, meta = site_boards.list_board(
            "ats:workable:tp-link-usa-corp", country="United States",
            cfg=Config(), geo_scope="non_cn")
        assert set(rows) == {"US1", "SG1", "HK1", "CNR1"}
        assert meta["geo_scope"] == "non_cn"

    def test_city_only_cn_when_code_absent(self, monkeypatch):
        j = self._job("CC1", "Chengdu", "")
        _patch_fetch(monkeypatch, [j])
        rows, _ = site_boards.list_board(
            "ats:workable:tp-link-usa-corp", country="United States",
            cfg=Config(), geo_scope="non_cn")
        assert rows == {}


class TestShippedConfig:
    def test_geo_scope_only_on_accepting_kinds(self):
        # S28: the acceptance set grew — feishuhire/adp/paylocity + the
        # 7 CN-company workday tenants (D-S28-2); the US-COMPANY anchors
        # (anthropic/openai — reference boards, foreign rows = noise)
        # were unflagged; riotgames STAYS flagged (Tencent-owned);
        # nvidia/netflix are workday (unflagged — kind not accepting).
        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        _S28_CN_WORKDAY = {
            "gea_us_fulltime", "jd_us_fulltime", "tencent_us_fulltime",
            "popmart_us_fulltime", "beone_us_fulltime",
            "chagee_us_fulltime", "canadiansolar_us_fulltime"}
        _US_ANCHORS = {"nvidia_us_fulltime", "openai_us_fulltime",
                       "netflix_us_fulltime", "anthropic_us_fulltime"}
        # S30 RE wave: smartrecruiters/trakstar/icims/paycom/teamtailor
        # joined the acceptance set (all carry country evidence); the
        # 6 new wires (zailab/midea/minisous/psi/polestar + custom:a123
        # — a123 is a CUSTOM kind: geo_scope NOT set on it).
        _S30_KINDS = {"smartrecruiters", "trakstar", "icims", "paycom",
                      "teamtailor"}
        _S30_CUSTOM = {"a123_us_fulltime"}
        flagged = 0
        for w in cfg["watches"]:
            kind = (w["board"].split(":")[1]
                    if w["board"].startswith("ats:") else "")
            if w.get("geo_scope"):
                assert w["geo_scope"] == "non_cn"
                assert (kind in _NON_CN_KINDS
                        or kind in {"feishuhire", "adp", "paylocity"}
                        or kind in _S30_KINDS
                        or w["label"] in _S28_CN_WORKDAY), w["label"]
                assert w["label"] not in _US_ANCHORS
                assert w["label"] not in _S30_CUSTOM   # customs: honest
                flagged += 1
        assert flagged >= 75   # S28: 69 → S30: 75-board rollout

    def test_all_four_kind_boards_flagged(self):
        # S28: every board of an accepting SITE kind is flagged, EXCEPT
        # the two US-company reference anchors (anthropic/openai —
        # foreign rows are mission noise; riotgames stays: Tencent-
        # owned). nvidia/netflix are workday (kind not accepting).
        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        _S28_KINDS = _NON_CN_KINDS | {"feishuhire", "adp", "paylocity"}
        _S28_EXCEPT = {"anthropic_us_fulltime", "openai_us_fulltime"}
        for w in cfg["watches"]:
            kind = (w["board"].split(":")[1]
                    if w["board"].startswith("ats:") else "")
            if kind in _S28_KINDS and w["label"] not in _S28_EXCEPT:
                assert w.get("geo_scope") == "non_cn", w["label"]
            elif w["label"] in _S28_EXCEPT:
                assert not w.get("geo_scope"), w["label"]


class TestClassifyGeo:
    def test_labels(self):
        c = exporter._classify_geo
        assert c("Sunnyvale, CA") == "United States"
        assert c("US, WA, Seattle") == "United States"
        assert c("Singapore") == "Singapore"
        assert c("Berlin, Germany") == "Germany"
        assert c("Shanghai, China") == "China"
        assert c("Hong Kong") == "Hong Kong SAR"
        assert c("Taipei, Taiwan") == "Taiwan"
        assert c("Remote - Global") == "Remote"
        assert c("London, UK") == "United Kingdom"
        assert c("") == ""

    def test_kyushu_trap(self):
        # 'us' must match only as a whole token — Kyushu is Japan
        assert exporter._classify_geo("Kyushu, Japan") == "Japan"


class TestRowIsCnLocal:
    def test_gates(self):
        g = exporter._row_is_cn_local
        assert g({"locationsText": "Shanghai, China",
                  "remoteFlag": "false"})
        assert not g({"locationsText": "Shanghai, China",
                      "remoteFlag": "true"})          # CN + remote = keep
        assert not g({"locationsText": "Singapore"})
        assert not g({"country": "Singapore",
                      "locationsText": ""})
        assert g({"country": "CN", "locationsText": "Beijing"})
        assert not g({"locationsText": ""})           # no evidence = keep

    def test_csv_row_evidence(self):
        g = exporter._row_is_cn_local
        assert g({}, {"primaryLocation": "Shenzhen",
                      "locations": "Shenzhen",
                      "remoteFlag": "false"})
        assert not g({}, {"primaryLocation": "Remote",
                          "remoteFlag": "true"})


class TestExporterNonCnGate:
    """refresh_one with geo_scope=non_cn: China-local rows never ship;
    CN-remote + non-CN rows ship; the country column is classified."""

    def _setup(self, tmp_path, w_over, state_rows, feed_rows):
        watch = tmp_path / "board_watch"
        workday = tmp_path / "workday"
        watch.mkdir(parents=True)
        workday.mkdir(parents=True)
        (watch / "config.json").write_text(json.dumps(
            {"watches": []}), encoding="utf-8")
        with open(watch / "testco_us_fulltime.state.jsonl", "w") as f:
            for r in state_rows:
                f.write(json.dumps(r) + "\n")
        with open(watch / "testco_us_fulltime.newposts.jsonl", "w") as f:
            for r in feed_rows:
                f.write(json.dumps(r) + "\n")
        exporter.WATCH_DIR = watch
        exporter.WORKDAY = workday
        w = {"label": "testco_us_fulltime", "company": "TestCo",
             "country": "United States"}
        w.update(w_over)
        return w

    def test_membership_and_country_column(self, tmp_path):
        w = self._setup(
            tmp_path, {"geo_scope": "non_cn"},
            [{"reqId": r} for r in ("us-1", "sg-1", "cn-1", "cnr-1")],
            [
                {"reqId": "us-1", "title": "US", "company": "TestCo",
                 "locationsText": "US, CA, Santa Clara",
                 "locations": ["US, CA, Santa Clara"],
                 "postedOn": "Posted 3 Days Ago",
                 "url": "https://x/us-1", "startDate": "2026-09-28",
                 "description": "Do things. " * 20, "timeType": "Full time",
                 "first_seen": "2026-09-20"},
                {"reqId": "sg-1", "title": "SG", "company": "TestCo",
                 "locationsText": "Singapore",
                 "locations": ["Singapore"],
                 "postedOn": "Posted 3 Days Ago",
                 "url": "https://x/sg-1", "startDate": "2026-09-28",
                 "description": "Do things. " * 20, "timeType": "Full time",
                 "first_seen": "2026-09-20"},
                {"reqId": "cn-1", "title": "CN", "company": "TestCo",
                 "locationsText": "Shanghai, China",
                 "locations": ["Shanghai, China"],
                 "postedOn": "Posted 3 Days Ago",
                 "url": "https://x/cn-1", "startDate": "2026-09-28",
                 "description": "Do things. " * 20, "timeType": "Full time",
                 "first_seen": "2026-09-20"},
                {"reqId": "cnr-1", "title": "CNR", "company": "TestCo",
                 "locationsText": "Remote, China", "remoteFlag": "true",
                 "locations": ["Remote, China"],
                 "postedOn": "Posted 3 Days Ago",
                 "url": "https://x/cnr-1", "startDate": "2026-09-28",
                 "description": "Do things. " * 20, "timeType": "Full time",
                 "first_seen": "2026-09-20"},
            ])
        stats = exporter.refresh_one(
            "testco_us_fulltime", w, exporter.date(2026, 10, 7))
        assert stats["rows"] == 3            # cn-1 excluded
        import csv as _csv
        with open(tmp_path / "workday/testco_us_fulltime.csv",
                  newline="", encoding="utf-8-sig") as f:
            out = {r["reqId"]: r for r in _csv.DictReader(f)}
        assert set(out) == {"us-1", "sg-1", "cnr-1"}
        assert out["us-1"]["country"] == "United States"
        assert out["sg-1"]["country"] == "Singapore"
        assert out["cnr-1"]["remoteFlag"] == "true"

    def test_us_scope_untouched_when_no_flag(self, tmp_path):
        # regression guard: without geo_scope the D-S26-5 feed gate runs
        w = self._setup(
            tmp_path, {},
            [{"reqId": "fx-1"}],
            [{"reqId": "fx-1", "country": "Singapore",
              "locationsText": "Singapore", "title": "FX",
              "company": "TestCo", "postedOn": "Posted 3 Days Ago",
              "url": "https://x/fx-1", "startDate": "2026-09-28",
              "description": "Do things. " * 20, "timeType": "Full time",
              "first_seen": "2026-09-20"}])
        stats = exporter.refresh_one(
            "testco_us_fulltime", w, exporter.date(2026, 10, 7))
        assert stats["rows"] == 0            # feed-foreign gate (US scope)


class TestReviewRoundPins:
    """S27-REV2 review fixes: the seam remote propagation (P0-1), the
    ashby/lever text-remote dialect (P1-5), greenhouse office fallback
    (P1-4), _row_is_cn_local evidence precedence (P1-2/3), classify
    label order (P2-9)."""

    def test_ashby_text_remote_cn_kept(self, monkeypatch):
        # "Remote, China" with workplaceType hybrid (not 'remote') —
        # the location TEXT is the row's own remote evidence (P1-5)
        jobs = [_ashby_job("txr", "CN Text Remote", country="China",
                           loc="Remote, China")]
        jobs[0]["workplaceType"] = "hybrid"
        _patch_fetch(monkeypatch, jobs)
        rows, _ = site_boards.list_board(
            "ats:ashby:airwallex", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert set(rows) == {"txr"}

    def test_lever_text_remote_cn_kept(self, monkeypatch):
        _patch_fetch_list(monkeypatch, [{
            "id": "ltxr", "text": "CN Remote", "createdAt": 1760000000000,
            "workplaceType": "hybrid",
            "categories": {"commitment": "Full-time",
                           "location": "Remote, China"}}])
        rows, _ = site_boards.list_board(
            "ats:lever:binance", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert set(rows) == {"ltxr"}

    def test_greenhouse_us_primary_with_cn_office_kept(self, monkeypatch):
        # P1-4: offices are company-global evidence, NOT per-row — a
        # US-primary posting must survive a Beijing office sibling.
        # (The fixture joins offices into location.name — override it
        # so E2 carries ONLY the primary.)
        job = _gh_job("GO1", "r-us2", "US Primary",
                      [{"name": "San Francisco, CA"}])
        job["offices"] = [{"name": "San Francisco, CA"},
                          {"name": "Beijing, China"}]
        _patch_fetch(monkeypatch, [job])
        rows, _ = site_boards.list_board(
            "ats:greenhouse:shein", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert set(rows) == {"r-us2"}

    def test_row_is_cn_local_remote_type_evidence(self):
        # P0-1: the seam now propagates remoteType — a China-located
        # remote row (workplaceType remote, city text no 'remote' word)
        # must NOT be CN-local
        g = exporter._row_is_cn_local
        assert not g({"locationsText": "Shanghai",
                      "remoteType": "Remote"})
        assert g({"locationsText": "Shanghai",
                  "remoteType": ""})
        assert not g({"locationsText": "Shanghai",
                      "telecommuting": True})

    def test_row_is_cn_local_rec_evidence_wins(self):
        # P1-3: rec's own countries beats a stale CSV 'United States'
        g = exporter._row_is_cn_local
        assert g({"countries": ["China"], "locationsText": ""},
                 {"country": "United States"})

    def test_row_is_cn_local_locations_list_joined(self):
        # P1-2: list-typed locations join (never the Python repr)
        g = exporter._row_is_cn_local
        assert g({"locations": ["Shanghai"], "locationsText": ""})

    def test_classify_order_fixes(self):
        c = exporter._classify_geo
        assert c("Toronto, CA") == "Canada"      # not the US 'CA' state
        assert c("Remote - Global") == "Remote"
        assert c("Remote, U.S.") == "United States"

    def test_greenhouse_no_fabricated_country_under_geo_scope(
            self, monkeypatch):
        job = _gh_job("GG1", "r-gg", "Role",
                      [{"name": "Singapore"}])
        _patch_fetch(monkeypatch, [job])
        rows, _ = site_boards.list_board(
            "ats:greenhouse:shein", country="United States", cfg=Config(),
            geo_scope="non_cn")
        assert rows["r-gg"]["countries"] == []   # P2-11: never stamped
