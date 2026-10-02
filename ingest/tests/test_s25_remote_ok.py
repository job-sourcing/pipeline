"""S25 pins — the remote-OK policy (D-S25-2).

"Any 'remote ok' roles are basically US-based" — rows whose OWN data
says remote (lever workplaceType / ashby workplaceType / workable
telecommuting / greenhouse location text) survive the country filter
when the watch opts in via include_remote. Default behavior unchanged
(off): a non-US remote row still drops.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests.test_site_boards import (  # noqa: E402
    _patch_fetch, _patch_fetch_list, _ashby_job, _gh_job)
from jobsearch.sources import site_boards  # noqa: E402
from jobsearch.config import Config  # noqa: E402


class TestLeverRemoteOk:
    def _board(self, monkeypatch, jobs):
        _patch_fetch_list(monkeypatch, jobs)
        return "ats:lever:binance"

    def _remote_cn(self):
        return {"id": "rm-1", "text": "Global Remote SRE",
                "country": "CN", "createdAt": 1760000000000,
                "workplaceType": "remote",
                "categories": {"commitment": "Full-time",
                               "location": "Remote - Global",
                               "team": "Infra"}}

    def test_remote_kept_when_opted_in(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [self._remote_cn()]),
            country="United States", cfg=Config())
        # NOTE: the dispatch seam only threads the flag for adapters
        # that accept it — lever accepts; but list_board's dispatch
        # path is exercised via site_boards.list_board below
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [self._remote_cn()]),
            country="United States", cfg=Config(), include_remote=True)
        assert set(rows) == {"rm-1"}
        assert meta["kept_remote"] == 1

    def test_remote_dropped_without_flag(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [self._remote_cn()]),
            country="United States", cfg=Config())
        assert rows == {}
        assert meta.get("kept_remote", 0) == 0

    def test_onsite_non_us_still_drops_with_flag(self, monkeypatch):
        onsite = dict(self._remote_cn(), id="on-2",
                      workplaceType="onsite")
        rows, _ = site_boards.list_board(
            self._board(monkeypatch, [onsite]),
            country="United States", cfg=Config(), include_remote=True)
        assert rows == {}


class TestAshbyRemoteOk:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:ashby:airwallex"

    def _remote_cn(self):
        j = _ashby_job("ar-1", "Remote Platform Eng",
                       country="China", loc="Remote")
        j["workplaceType"] = "remote"
        return j

    def test_remote_kept_when_opted_in(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [self._remote_cn()]),
            country="United States", cfg=Config(), include_remote=True)
        assert set(rows) == {"ar-1"}
        assert meta["kept_remote"] == 1

    def test_remote_dropped_without_flag(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [self._remote_cn()]),
            country="United States", cfg=Config())
        assert rows == {}
        assert meta.get("kept_remote", 0) == 0


class TestGreenhouseRemoteOk:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:greenhouse:okx"

    def _remote_global(self):
        return _gh_job(999, "gh-1", "Remote Compliance", [],
                       published="2026-09-20")

    def test_remote_location_text_kept_when_opted_in(self, monkeypatch):
        job = self._remote_global()
        job["location"] = {"name": "Remote - Global"}
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [job]),
            country="United States", cfg=Config(), include_remote=True)
        assert set(rows) == {"gh-1"}
        assert meta["kept_remote"] == 1

    def test_remote_dropped_without_flag(self, monkeypatch):
        job = self._remote_global()
        job["location"] = {"name": "Remote - Global"}
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [job]),
            country="United States", cfg=Config())
        assert rows == {}
        assert meta.get("kept_remote", 0) == 0


class TestWorkableRemoteOk:
    def _board(self, monkeypatch, jobs):
        _patch_fetch(monkeypatch, jobs)
        return "ats:workable:amber"

    def _remote_sg(self):
        return {"shortcode": "wk-1", "title": "Remote Quant",
                "country": "Singapore", "city": "", "state": "",
                "telecommuting": True,
                "employment_type": "Full-time",
                "published_on": "2026-09-20",
                "url": "https://apply.workable.com/j/wk-1",
                "locations": []}

    def test_telecommuting_kept_when_opted_in(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [self._remote_sg()]),
            country="United States", cfg=Config(), include_remote=True)
        assert set(rows) == {"wk-1"}
        assert meta["kept_remote"] == 1

    def test_telecommuting_dropped_without_flag(self, monkeypatch):
        rows, meta = site_boards.list_board(
            self._board(monkeypatch, [self._remote_sg()]),
            country="United States", cfg=Config())
        assert rows == {}
        assert meta.get("kept_remote", 0) == 0


class TestRemoteOkDispatch:
    def test_flag_threads_through_dispatch(self):
        # the dispatch seam passes include_remote only to adapters
        # that accept it (greenhouse/ashby/lever/workable)
        assert site_boards._ADAPTER_ACCEPTS_REMOTE == {
            "greenhouse": True, "ashby": True, "lever": True,
            "workable": True}
