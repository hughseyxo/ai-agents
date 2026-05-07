"""SQLite state management for agents."""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "agents.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent       TEXT NOT NULL,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    output_summary TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES runs(id),
    step     TEXT NOT NULL,
    status   TEXT NOT NULL,
    error    TEXT,
    ts       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS state (
    agent   TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT,
    updated TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (agent, key)
);

CREATE TABLE IF NOT EXISTS seen (
    agent      TEXT NOT NULL,
    category   TEXT NOT NULL,
    identifier TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (agent, category, identifier)
);

CREATE INDEX IF NOT EXISTS idx_runs_agent_date ON runs(agent, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_seen_lookup ON seen(agent, category, identifier);
"""


class AgentDB:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self):
        self._conn.close()

    # --- Run tracking ---

    def start_run(self, agent: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (agent) VALUES (?)", (agent,)
        )
        self._conn.commit()
        return cur.lastrowid

    def complete_run(self, run_id: int, status: str, output_summary: str = None, error: str = None):
        self._conn.execute(
            "UPDATE runs SET finished_at = datetime('now'), status = ?, output_summary = ?, error = ? WHERE id = ?",
            (status, output_summary, error, run_id),
        )
        self._conn.commit()

    def record_step(self, run_id: int, step: str, status: str, error: str = None):
        self._conn.execute(
            "INSERT INTO steps (run_id, step, status, error) VALUES (?, ?, ?, ?)",
            (run_id, step, status, error),
        )
        self._conn.commit()

    def get_last_run(self, agent: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE agent = ? ORDER BY started_at DESC LIMIT 1",
            (agent,),
        ).fetchone()
        return dict(row) if row else None

    def get_run_history(self, agent: str, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE agent = ? ORDER BY started_at DESC LIMIT ?",
            (agent, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_step_results(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY ts", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Key-value state ---

    def get_state(self, agent: str, key: str, default=None):
        row = self._conn.execute(
            "SELECT value FROM state WHERE agent = ? AND key = ?",
            (agent, key),
        ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def set_state(self, agent: str, key: str, value):
        self._conn.execute(
            "INSERT INTO state (agent, key, value, updated) VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(agent, key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
            (agent, key, json.dumps(value)),
        )
        self._conn.commit()

    # --- Dedup ---

    def check_dedup(self, agent: str, category: str, identifier: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen WHERE agent = ? AND category = ? AND identifier = ?",
            (agent, category, identifier),
        ).fetchone()
        return row is not None

    def mark_seen(self, agent: str, category: str, identifier: str):
        self._conn.execute(
            "INSERT OR IGNORE INTO seen (agent, category, identifier) VALUES (?, ?, ?)",
            (agent, category, identifier),
        )
        self._conn.commit()
