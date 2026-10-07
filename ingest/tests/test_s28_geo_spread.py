"""S28 pins — D-S28-2: geo_scope="non_cn" beyond the S27 four classes.

feishuhire (the CN-portal class — ANY-city non-CN rule + 远程/remote
text evidence + remoteType stamp), adp (trailing-ISO country gate),
paylocity (STRUCTURED JobLocation.Country + IsRemote — the cleanest
class), and the workday tenant path (no country facet — full board +
client-side _text_is_cn_sited gate). Dispatcher: _ADAPTER_GEO_SCOPE
routes the flag; non-ats specs reach workday.list_board with it.
"""
import importlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from jobsearch.sources import site_boards, workday  # noqa: E402
from jobsearch.config import Config  # noqa: E402

from tests.test_site_boards import (  # noqa: E402
    _adp_row, _paylocity_page)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _feishu_post(rid, title, cities, tt="Full-time",
                 publish=1789097209208):
    return {"id": rid, "title": title,
            "city_list": [{"en_name": c, "code": "CT_%d" % i}
                          for i, c in enumerate(cities)],
            "recruit_type": {"en_name": tt},
            "publish_time": publish,
            "job_category": {"en_name": "R&D"},
            "job_function": {"en_name": "Engineering"},
            "description": None, "requirement": None}


def _feishu_board(monkeypatch, posts):
    from jobsearch.sources.site_boards import FeishuHireAdapter
    monkeypatch.setattr(site_boards, "_CACHE", {})

    def fake_rows(self):
        return posts
    monkeypatch.setattr(FeishuHireAdapter, "_fetch_rows", fake_rows)
    return "ats:feishuhire:vrfi1sk8a0"


class TestFeishuNonCn:
    def test_cn_rows_dropped_foreign_and_remote_kept(self, monkeypatch):
        cn = _feishu_post("222", "云原生架构师", ["Beijing", "Shanghai"])
        sg = _feishu_post("333", "APAC BD", ["Singapore"])
        hy = _feishu_post("111", "全球产品负责人",
                          ["Beijing", "San Francisco"])
        rem = _feishu_post("444", "远程研发", ["Remote"])
        spec = _feishu_board(monkeypatch, [cn, sg, hy, rem])
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"333", "111", "444"}     # CN-only dropped
        assert rows["333"]["countries"] == ["Singapore"]
        assert rows["111"]["countries"] == ["United States"]
        assert rows["444"]["remoteType"] == "Remote"  # remote evidence
        assert meta["geo_scope"] == "non_cn"
        assert meta["client_filtered_country"] == 1
        assert meta["complete"] is True

    def test_hk_tw_are_not_mainland(self, monkeypatch):
        hk = _feishu_post("555", "BD", ["Hong Kong"])
        tw = _feishu_post("666", "FE", ["Taipei"])
        spec = _feishu_board(monkeypatch, [hk, tw])
        rows, _ = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"555", "666"}
        assert rows["555"]["countries"] == ["Hong Kong"]
        assert rows["666"]["countries"] == ["Taiwan"]

    def test_cn_remote_city_kept_flagged(self, monkeypatch):
        # a CN-city role whose text says 远程 — kept + remoteType stamp
        r = _feishu_post("777", "算法工程师（远程）", ["Hangzhou"])
        spec = _feishu_board(monkeypatch, [r])
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"777"}
        assert rows["777"]["remoteType"] == "Remote"
        assert rows["777"]["countries"] == ["China"]
        assert meta["kept_remote"] == 1

    def test_unmapped_city_unresolved(self, monkeypatch):
        u = _feishu_post("888", "Mystery", ["Pleasantville"])
        spec = _feishu_board(monkeypatch, [u])
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == set()
        assert meta["unresolved_dropped"] == 1
        assert meta["complete"] is False

    def test_us_scope_unchanged_without_flag(self, monkeypatch):
        cn = _feishu_post("222", "云原生架构师", ["Beijing"])
        us = _feishu_post("111", "PM", ["San Francisco"])
        spec = _feishu_board(monkeypatch, [cn, us])
        rows, meta = site_boards.list_board(
            spec, country="United States")
        assert set(rows) == {"111"}
        assert meta.get("geo_scope") == ""


class TestAdpNonCn:
    def _board(self, monkeypatch, pages):
        from jobsearch.sources.site_boards import ADPWorkforceNowAdapter
        monkeypatch.setattr(site_boards, "_CACHE", {})

        def fake(url, cfg=None, **kw):
            for skip, payload in pages:
                if f"$skip={skip}&" in url + "&":
                    return payload
            return {"jobRequisitions": [],
                    "meta": {"startSequence": 999, "totalNumber": 0,
                             "links": []}}
        monkeypatch.setattr(site_boards, "fetch_json", fake)
        return "ats:adp:25319558-d6a9-48ab-93d2-99e4878a8ffc"

    def test_cn_dropped_us_kept(self, monkeypatch):
        us = _adp_row("A1", loc="Moraine, OH, US")
        cn = _adp_row("A2", loc="Weifang, Shandong, CN")
        blank = _adp_row("A3", loc="Global Flex")
        pages = [(0, {"jobRequisitions": [us, cn, blank],
                      "meta": {"startSequence": 0, "totalNumber": 3,
                               "links": []}})]
        spec = self._board(monkeypatch, pages)
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"A1", "A3"}       # CN dropped, blank kept
        assert rows["A1"]["countries"] == ["United States"]
        assert meta["geo_scope"] == "non_cn"
        assert meta["client_filtered_country"] == 1


class TestPaylocityNonCn:
    def _board(self, monkeypatch, jobs):
        monkeypatch.setattr(site_boards, "_CACHE", {})
        html = _paylocity_page(jobs)

        def fake_text(url, cfg=None, **kw):
            return html
        monkeypatch.setattr(site_boards, "fetch_text", fake_text)
        return "ats:paylocity:d527ad39-680d-45fa-9178-38a81898aec2"

    @staticmethod
    def _job(jid, country="USA", remote=False, title="Specialist",
             loc="Chicago, IL"):
        return {"JobId": jid, "JobTitle": title, "LocationName": loc,
                "ShouldDisplayLocation": True,
                "PublishedDate": "2026-09-22T15:54:27-05:00",
                "Description": "Role", "IsRemote": remote,
                "HiringDepartment": None,
                "JobLocation": {"LocationId": 1, "ModuleId": 3,
                                "Name": loc, "Country": country,
                                "City": None, "State": None}}

    def test_cn_dropped_cn_remote_kept(self, monkeypatch):
        us = self._job("P1", country="USA")
        cn = self._job("P2", country="China")
        cnr = self._job("P3", country="China", remote=True)
        sg = self._job("P4", country="Singapore")
        spec = self._board(monkeypatch, [us, cn, cnr, sg])
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"P1", "P3", "P4"}
        assert rows["P3"]["remoteType"] == "Remote"
        assert rows["P4"]["countries"] == ["Singapore"]
        assert meta["client_filtered_country"] == 1
        assert meta["kept_remote"] == 1


class TestWorkdayNonCn:
    def _page(self, postings, total=None):
        return {"total": total if total is not None else len(postings),
                "jobPostings": postings,
                "facets": [],       # country facet present or not —
                "userAuthenticated": False}   # gate is client-side

    def _postings(self):
        def p(n, loc):
            return {"title": f"Role {n}", "externalPath": f"/job/{n}",
                    "locationsText": loc, "postedOn": "Posted Today",
                    "bulletFields": [f"JR{n}"]}
        return [p(1, "CHN-Beijing-Chaoyang"),
                p(2, "NLD-South Holland-Rotterdam"),
                p(3, "Remote - US, Multiple Locations"),
                p(4, "USA, CA, Santa Clara"),
                p(5, "City-Only Dialect")]

    def test_gate_keeps_non_cn_and_remote(self, monkeypatch, cfg):
        posts = self._postings()
        monkeypatch.setattr(
            workday, "_page",
            lambda b, f, o, c: self._page(posts))
        monkeypatch.setattr(workday, "_facet_id",
                            lambda p, param, lbl: None)
        rows, meta = workday.list_board(
            "jd|wd103|Careers_at_JD", cfg=cfg, geo_scope="non_cn")
        # CHN-Beijing dropped; NLD/Remote/USA/City-Only kept (fail-open)
        assert set(rows) == {"JR2", "JR3", "JR4", "JR5"}
        assert meta["geo_scope"] == "non_cn"
        assert meta["country_client"] is False   # membership=set(current)

    def test_no_country_facet_applied(self, monkeypatch, cfg):
        # the facets dict handed to _page must NOT carry the country
        # facet under non_cn (full board listing)
        seen = {}

        def fake_page(b, f, o, c):
            seen.setdefault("facets", []).append(f)
            return self._page(self._postings())
        monkeypatch.setattr(workday, "_page", fake_page)
        monkeypatch.setattr(workday, "_facet_id",
                            lambda p, param, lbl: None)
        workday.list_board("jd|wd103|Careers_at_JD", cfg=cfg,
                           geo_scope="non_cn", country="United States")
        for f in seen["facets"]:
            assert "locationHierarchy1" not in f


class TestDispatcherRouting:
    def test_geo_scope_accepting_set(self):
        assert site_boards._ADAPTER_GEO_SCOPE == {
            "greenhouse", "ashby", "lever", "workable",
            "feishuhire", "adp", "paylocity"}

    def test_unknown_kind_ignores_flag(self, monkeypatch):
        # a kind NOT in the set must never receive geo_scope (the
        # honesty convention — rippling/jazzhr classes ignore it)
        from jobsearch.sources.site_boards import RipplingAdapter
        calls = {}

        def spy(self, **kw):
            calls.update(kw)
            return {}, {}
        monkeypatch.setattr(RipplingAdapter, "list_board", spy)
        try:
            site_boards.list_board("ats:rippling:webull",
                                   geo_scope="non_cn", cfg=Config())
        finally:
            monkeypatch.undo()
        assert "geo_scope" not in calls


class TestFeishuMapGrowth:
    """S28: run #99's unmapped-cities fix — Xuzhou (petkit) made the
    board 0-rows + incomplete → the fail-safe LIST FAILED. Map growth
    → complete=True, 0 trusted rows, GREEN."""

    def test_petkit_xuzhou_completes_green(self, monkeypatch):
        xz = _feishu_post("991", "原料工程师", ["Xuzhou"])
        hz = _feishu_post("992", "市场经理", ["Hangzhou"])
        spec = _feishu_board(monkeypatch, [xz, hz])
        rows, meta = site_boards.list_board(spec, country="United States")
        assert set(rows) == set()
        assert meta["complete"] is True       # the petkit fix
        assert meta["unresolved_dropped"] == 0
        assert meta["client_filtered_country"] == 2  # both CN-mapped

    def test_intl_only_row_kept_under_non_cn(self, monkeypatch):
        st = _feishu_post("993", "EM Vertrieb", ["Stuttgart"])
        bk = _feishu_post("994", "BD ANZ", ["Brisbane"])
        spec = _feishu_board(monkeypatch, [st, bk])
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == {"993", "994"}
        assert rows["993"]["countries"] == ["Germany"]
        assert rows["994"]["countries"] == ["Australia"]
        assert meta["complete"] is True

    def test_ambiguous_names_stay_unmapped(self, monkeypatch):
        # Carterton/Balveren/Van Reenen: ambiguous → honest unresolved
        ct = _feishu_post("995", "Mystery", ["Carterton"])
        spec = _feishu_board(monkeypatch, [ct])
        rows, meta = site_boards.list_board(spec, geo_scope="non_cn")
        assert set(rows) == set()
        assert meta["complete"] is False
        assert meta["unmapped_cities"] == ["Carterton"]
