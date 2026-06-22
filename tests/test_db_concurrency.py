"""Concurrency tests for agents.db.AgentDB (WAL + locking)."""

import threading

import pytest

from agents.db import AgentDB


@pytest.fixture
def db(tmp_path):
    d = AgentDB(db_path=tmp_path / "agents.db")
    yield d
    d.close()


def test_wal_mode_enabled(db):
    mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_busy_timeout_set(db):
    # busy_timeout is reported in milliseconds; must be non-zero so writers wait.
    timeout = db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout >= 1000


def test_concurrent_set_state_no_lock_error(db):
    errors = []

    def worker(n):
        try:
            for i in range(25):
                db.set_state("agent", f"key-{n}", {"i": i})
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent writes raised: {errors}"
    # All eight keys persisted their final value.
    for n in range(8):
        assert db.get_state("agent", f"key-{n}") == {"i": 24}


def test_concurrent_mark_seen_and_run_tracking(db):
    errors = []

    def worker(n):
        try:
            rid = db.start_run("agent")
            db.record_step(rid, "step", "success")
            db.mark_seen("agent", "cat", f"id-{n}")
            db.complete_run(rid, "success")
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for n in range(8):
        assert db.check_dedup("agent", "cat", f"id-{n}") is True
