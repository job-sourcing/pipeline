"""PipelineLock: mkdir-based cross-process advisory lock."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from jobsearch.pipeline_lock import LockTimeoutError, PipelineLock


def _write_owner(lock_dir: Path, pid: int, token: str = "tok-xyz") -> None:
    (lock_dir / "owner.json").write_text(
        json.dumps({"pid": pid, "token": token, "started_at": time.time()}),
        encoding="utf-8")


def _backdate(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


class TestAcquireRelease:
    def test_acquire_creates_lock_dir_with_owner(self, tmp_path):
        target = tmp_path / "run.db"
        lock = PipelineLock(target)
        with lock:
            assert lock.lock_dir.is_dir()
            owner = json.loads((lock.lock_dir / "owner.json").read_text())
            assert owner["pid"] == os.getpid()
            assert owner["token"] == lock._token
        assert not lock.lock_dir.exists()  # released

    def test_reentrant_after_release(self, tmp_path):
        target = tmp_path / "run.db"
        with PipelineLock(target):
            pass
        with PipelineLock(target):  # immediate re-acquire must succeed
            pass


class TestContention:
    def test_double_acquire_raises_lock_timeout(self, tmp_path):
        target = tmp_path / "run.db"
        lock1 = PipelineLock(target)
        lock1.acquire()
        try:
            lock2 = PipelineLock(target, timeout_s=0.1)
            with pytest.raises(LockTimeoutError):
                lock2.acquire()
        finally:
            lock1.release()
        assert not lock1.lock_dir.exists()

    def test_live_pid_lock_is_never_reclaimed(self, tmp_path):
        target = tmp_path / "run.db"
        lock_dir = Path(str(target) + ".lock")
        lock_dir.mkdir()
        _write_owner(lock_dir, pid=os.getpid())   # live process holds it
        _backdate(lock_dir, seconds=3600)          # even a very old one
        with pytest.raises(LockTimeoutError):
            PipelineLock(target, timeout_s=0.1).acquire()
        # the live-owner lock is still intact
        assert lock_dir.is_dir()
        assert json.loads((lock_dir / "owner.json").read_text())["pid"] == os.getpid()
        import shutil
        shutil.rmtree(lock_dir)

    def test_stale_dead_pid_lock_is_reclaimed(self, tmp_path):
        target = tmp_path / "run.db"
        lock_dir = Path(str(target) + ".lock")
        lock_dir.mkdir()
        _write_owner(lock_dir, pid=999_999_999)   # no such process
        _backdate(lock_dir, seconds=60)            # older than stale_ms
        lock = PipelineLock(target, stale_ms=30_000, timeout_s=1.0)
        lock.acquire()  # reclaims and takes over
        try:
            owner = json.loads((lock.lock_dir / "owner.json").read_text())
            assert owner["pid"] == os.getpid()
            assert owner["token"] == lock._token
        finally:
            lock.release()

    def test_ownerless_dir_older_than_grace_is_reclaimed(self, tmp_path):
        target = tmp_path / "run.db"
        lock_dir = Path(str(target) + ".lock")
        lock_dir.mkdir()               # no owner.json at all
        _backdate(lock_dir, seconds=2)  # beyond the 1000ms grace period
        lock = PipelineLock(target, timeout_s=1.0)
        lock.acquire()
        lock.release()

    def test_fresh_ownerless_dir_gets_grace_period(self, tmp_path):
        target = tmp_path / "run.db"
        lock_dir = Path(str(target) + ".lock")
        lock_dir.mkdir()  # created microseconds ago, no owner.json yet
        with pytest.raises(LockTimeoutError):
            PipelineLock(target, timeout_s=0.15).acquire()
        import shutil
        shutil.rmtree(lock_dir)


class TestRelease:
    def test_token_mismatch_on_release_leaves_lock(self, tmp_path):
        target = tmp_path / "run.db"
        holder = PipelineLock(target)
        holder.acquire()
        try:
            other = PipelineLock(target)   # different token
            other.release()                # must NOT steal the lock
            assert holder.lock_dir.is_dir()
            owner = json.loads((holder.lock_dir / "owner.json").read_text())
            assert owner["token"] == holder._token
        finally:
            holder.release()
        assert not holder.lock_dir.exists()

    def test_release_of_absent_lock_is_a_noop(self, tmp_path):
        lock = PipelineLock(tmp_path / "run.db")
        lock.release()  # nothing to release — must not raise
