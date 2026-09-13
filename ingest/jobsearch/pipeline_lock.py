"""Cross-process advisory lock — Python port of career-ops pipeline-lock.mjs
(provenance: santifer/career-ops, ~642 LOC TS; core protocol ported).

Protocol (identical shape to the original):
  - the lock is a DIRECTORY; mkdir is atomic
  - holder writes owner.json {pid, token, started_at}
  - staleness: owner-PID liveness first, directory-age fallback only when
    metadata is missing/unreadable; ownerless dirs get a grace period so a
    lock created microseconds ago is never reclaimable
  - stale reclamation is serialized behind a recover-guard directory to
    avoid the TOCTOU race of two reclaimers

Used to serialize pipeline runs (search+score) against each other and
against future APScheduler cron writers. SQLite itself is additionally
protected by WAL + busy_timeout (storage.py, D4).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

DEFAULT_STALE_MS = 30_000
OWNERLESS_GRACE_MS = 1_000
DEFAULT_RETRY_S = 0.08
DEFAULT_TIMEOUT_S = 8.0


class LockTimeoutError(RuntimeError):
    def __init__(self, lock_dir: str, timeout_s: float):
        super().__init__(f"pipeline lock timeout: {lock_dir} held > {timeout_s}s")
        self.lock_dir = lock_dir


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user


def _read_owner(lock_dir: Path) -> tuple[dict | None, bool]:
    """Returns (owner, absence_is_fact). Only ENOENT counts as factual absence."""
    try:
        raw = (lock_dir / "owner.json").read_text(encoding="utf-8")
        return json.loads(raw), True
    except FileNotFoundError:
        return None, True
    except (OSError, json.JSONDecodeError):
        return None, False


def _dir_age_ms(path: Path) -> float:
    try:
        return max(0.0, (time.time() - path.stat().st_mtime) * 1000)
    except OSError:
        return float("inf")


class PipelineLock:
    def __init__(self, target: Path | str, stale_ms: int = DEFAULT_STALE_MS,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        self.target = Path(target)
        self.lock_dir = Path(str(self.target) + ".lock")
        self.recover_dir = Path(str(self.target) + ".lock.recover")
        self.stale_ms = stale_ms
        self.timeout_s = timeout_s
        self._token = uuid.uuid4().hex

    def __enter__(self) -> "PipelineLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self.lock_dir.mkdir(parents=True)
                (self.lock_dir / "owner.json").write_text(
                    json.dumps({"pid": os.getpid(), "token": self._token,
                                "started_at": time.time()}),
                    encoding="utf-8",
                )
                return
            except FileExistsError:
                pass
            if self._try_reclaim_stale():
                continue
            if time.monotonic() >= deadline:
                raise LockTimeoutError(str(self.lock_dir), self.timeout_s)
            time.sleep(DEFAULT_RETRY_S)

    def _try_reclaim_stale(self) -> bool:
        """Reclaim if stale. Serialized behind the recover guard (TOCTOU-safe)."""
        owner, absence_fact = _read_owner(self.lock_dir)
        if owner is not None:
            pid = owner.get("pid")
            if isinstance(pid, int) and _pid_alive(pid):
                return False  # live owner — never stale
            stale = _dir_age_ms(self.lock_dir) > self.stale_ms
        elif absence_fact:
            # Genuine ENOENT between mkdir and owner.json write: age rule only.
            stale = _dir_age_ms(self.lock_dir) > OWNERLESS_GRACE_MS
        else:
            # Unreadable metadata — age rule only, same as original.
            stale = _dir_age_ms(self.lock_dir) > OWNERLESS_GRACE_MS
        if not stale:
            return False
        # Serialize reclamation.
        try:
            self.recover_dir.mkdir(parents=True)
        except FileExistsError:
            return False
        try:
            owner2, _ = _read_owner(self.lock_dir)
            if owner2 is not None:
                pid = owner2.get("pid")
                if isinstance(pid, int) and _pid_alive(pid):
                    return False
            import shutil
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            return True
        finally:
            try:
                self.recover_dir.rmdir()
            except OSError:
                pass

    def release(self) -> None:
        owner, _ = _read_owner(self.lock_dir)
        if owner is None or owner.get("token") == self._token:
            import shutil
            shutil.rmtree(self.lock_dir, ignore_errors=True)
