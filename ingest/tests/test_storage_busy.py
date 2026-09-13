"""SQLite busy/conflict + read-only behavior — D4 as BEHAVIOR, not pragmas.

D4 mandates WAL + busy_timeout=5000 + single writer. The previous suite only
asserted the PRAGMA *values* (CR-1-TESTS finding: "SQLite busy = pragma-assert
only"). These tests exercise the actual locking behavior:

  - one connection holds the WAL write lock (BEGIN IMMEDIATE)
  - a second writer must BLOCK for up to busy_timeout, then either succeed
    (lock released in time) or raise OperationalError("database is locked")
    once the window expires
  - a read-only connection can SELECT but never INSERT
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from jobsearch.storage import Store, connect

from conftest import make_job


def _seeded_db(tmp_path):
    """A WAL-mode DB with schema + one job row (created via Store, then closed)."""
    db = tmp_path / "busy.db"
    store = Store(db)                       # connect(): WAL + busy_timeout
    store.upsert_jobs([make_job(link="https://ex.com/1")])
    store.close()
    return db


def test_wal_mode_is_active(tmp_path):
    # WAL is persistent on the file: even a plain raw connection sees it.
    db = _seeded_db(tmp_path)
    conn = sqlite3.connect(db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_write_blocks_then_succeeds_after_lock_release(tmp_path):
    db = _seeded_db(tmp_path)
    other = connect(db)                     # second writer, busy_timeout=5000

    locked = threading.Event()
    released = threading.Event()

    def hold_lock_for_200ms():
        # sqlite3 connections are thread-affine: create, lock, and release
        # the locker connection entirely inside this thread.
        conn = sqlite3.connect(db)
        conn.execute("BEGIN IMMEDIATE")     # holds the WAL write lock
        conn.execute("UPDATE jobs SET title = 'locked'")
        locked.set()
        time.sleep(0.2)
        conn.commit()                       # lock freed at ~0.2s
        conn.close()
        released.set()

    thread = threading.Thread(target=hold_lock_for_200ms)
    thread.start()
    assert locked.wait(timeout=2.0)         # the write lock is held from here
    try:
        t0 = time.monotonic()
        other.execute("UPDATE jobs SET title = 'winner'")   # must block
        other.commit()
        elapsed = time.monotonic() - t0
    finally:
        thread.join()
        assert released.wait(timeout=2.0)

    # the write could only proceed AFTER the ~0.2s release: it genuinely
    # waited on the lock instead of failing fast or sneaking through
    assert elapsed >= 0.15
    assert elapsed < 2.0
    assert other.execute("SELECT title FROM jobs").fetchone()[0] == "winner"
    other.close()


def test_busy_timeout_exceeded_raises_locked(tmp_path):
    db = _seeded_db(tmp_path)
    locker = sqlite3.connect(db)
    locker.execute("BEGIN IMMEDIATE")       # holds the write lock
    locker.execute("UPDATE jobs SET title = 'locked'")
    try:
        impatient = sqlite3.connect(db, timeout=0.15)
        impatient.execute("PRAGMA busy_timeout=150")   # short window: fast test
        t0 = time.monotonic()
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            impatient.execute("UPDATE jobs SET title = 'x'")
        elapsed = time.monotonic() - t0
        assert "locked" in str(excinfo.value).lower()
        # it retried for the ~150ms busy window before giving up — not an
        # instant failure (which is what busy_timeout=0 would produce)
        assert elapsed >= 0.1
        assert elapsed < 2.0
        impatient.close()
    finally:
        locker.rollback()
        locker.close()


def test_readonly_connection_reads_but_cannot_write(tmp_path):
    db = _seeded_db(tmp_path)
    ro = connect(db, readonly=True)
    assert ro.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        ro.execute("INSERT INTO jobs (title, company) VALUES ('T', 'C')")
    assert "readonly" in str(excinfo.value).lower()
    # nothing leaked through — the read-only view is unchanged
    assert ro.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    ro.close()
