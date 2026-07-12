"""SQLite state management for agents."""

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "agents.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS plant_weather_cache (
    plant_name      TEXT PRIMARY KEY,
    adjusted_date   TEXT NOT NULL,
    adjustment_reason TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

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

CREATE TABLE IF NOT EXISTS plants (
    name    TEXT PRIMARY KEY,
    data    TEXT NOT NULL,
    updated TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plant_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_name  TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    taken_at    TEXT NOT NULL DEFAULT (datetime('now')),
    assessment_summary TEXT,
    assessment_status  TEXT
);

CREATE TABLE IF NOT EXISTS photo_batch_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL DEFAULT 'running',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    items_json    TEXT NOT NULL,
    current_index INTEGER NOT NULL DEFAULT 0,
    pause_reason  TEXT,
    next_ping_at  TEXT,
    paused_seconds INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_runs_agent_date ON runs(agent, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_seen_lookup ON seen(agent, category, identifier);
CREATE INDEX IF NOT EXISTS idx_plant_photos_lookup ON plant_photos(plant_name, taken_at DESC);
"""


class AgentDB:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # The connection is shared across threads (agents + bot + PWA + servers),
        # so serialise all access with a reentrant lock and let SQLite use WAL +
        # a busy timeout so concurrent writers wait rather than raising
        # "database is locked" / corrupting via torn writes.
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self._conn.close()

    # --- Run tracking ---

    def start_run(self, agent: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (agent) VALUES (?)", (agent,)
            )
            self._conn.commit()
            return cur.lastrowid

    def complete_run(self, run_id: int, status: str, output_summary: str = None, error: str = None):
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished_at = datetime('now'), status = ?, output_summary = ?, error = ? WHERE id = ?",
                (status, output_summary, error, run_id),
            )
            self._conn.commit()

    def record_step(self, run_id: int, step: str, status: str, error: str = None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO steps (run_id, step, status, error) VALUES (?, ?, ?, ?)",
                (run_id, step, status, error),
            )
            self._conn.commit()

    def get_last_run(self, agent: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE agent = ? ORDER BY started_at DESC LIMIT 1",
                (agent,),
            ).fetchone()
        return dict(row) if row else None

    def get_run_history(self, agent: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE agent = ? ORDER BY started_at DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_step_results(self, run_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY ts", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Key-value state ---

    def get_state(self, agent: str, key: str, default=None):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM state WHERE agent = ? AND key = ?",
                (agent, key),
            ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def set_state(self, agent: str, key: str, value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO state (agent, key, value, updated) VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(agent, key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                (agent, key, json.dumps(value)),
            )
            self._conn.commit()

    # --- Dedup ---

    def check_dedup(self, agent: str, category: str, identifier: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM seen WHERE agent = ? AND category = ? AND identifier = ?",
                (agent, category, identifier),
            ).fetchone()
        return row is not None

    def mark_seen(self, agent: str, category: str, identifier: str):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO seen (agent, category, identifier) VALUES (?, ?, ?)",
                (agent, category, identifier),
            )
            self._conn.commit()

    def delete_seen(self, category: str, identifier: str, agent: str = ""):
        with self._lock:
            if agent:
                self._conn.execute(
                    "DELETE FROM seen WHERE agent=? AND category=? AND identifier=?",
                    (agent, category, identifier),
                )
            else:
                self._conn.execute(
                    "DELETE FROM seen WHERE category=? AND identifier=?",
                    (category, identifier),
                )
            self._conn.commit()

    # --- Plant weather cache ---

    def upsert_plant_weather_cache(self, plant_name: str, adjusted_date: str, adjustment_reason: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO plant_weather_cache (plant_name, adjusted_date, adjustment_reason, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(plant_name) DO UPDATE SET "
                "adjusted_date = excluded.adjusted_date, "
                "adjustment_reason = excluded.adjustment_reason, "
                "updated_at = excluded.updated_at",
                (plant_name, adjusted_date, adjustment_reason),
            )
            self._conn.commit()

    def get_plant_weather_cache(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT plant_name, adjusted_date, adjustment_reason, updated_at FROM plant_weather_cache"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Plants (row-per-plant) ---

    def get_plant_rows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM plants ORDER BY rowid").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def upsert_plant_row(self, name: str, data: dict):
        with self._lock:
            self._conn.execute(
                "INSERT INTO plants (name, data, updated) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(name) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                (name.lower(), json.dumps(data)))
            self._conn.commit()

    def delete_plant_row(self, name: str):
        with self._lock:
            self._conn.execute("DELETE FROM plants WHERE name = ?", (name.lower(),))
            self._conn.commit()

    def replace_plant_rows(self, rows: list[dict]):
        with self._lock:
            self._conn.execute("DELETE FROM plants")
            for data in rows:
                self._conn.execute("INSERT INTO plants (name, data) VALUES (?, ?)",
                                   (data["name"].lower(), json.dumps(data)))
            self._conn.commit()

    # --- Plant photo history ---

    def add_plant_photo(self, plant_name: str, file_path: str, assessment_summary: str = None,
                         assessment_status: str = None, taken_at: str = None) -> int:
        with self._lock:
            if taken_at is not None:
                cur = self._conn.execute(
                    "INSERT INTO plant_photos (plant_name, file_path, taken_at, assessment_summary, assessment_status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (plant_name, file_path, taken_at, assessment_summary, assessment_status),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO plant_photos (plant_name, file_path, assessment_summary, assessment_status) "
                    "VALUES (?, ?, ?, ?)",
                    (plant_name, file_path, assessment_summary, assessment_status),
                )
            self._conn.commit()
            return cur.lastrowid

    def get_recent_plant_photos(self, plant_name: str, limit: int = 3) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM plant_photos WHERE plant_name = ? ORDER BY taken_at DESC, id DESC LIMIT ?",
                (plant_name, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_plant_photo_history(self, plant_name: str, limit: int = 10) -> list[dict]:
        return self.get_recent_plant_photos(plant_name, limit=limit)

    def prune_plant_photos(self, plant_name: str, keep: int = 10) -> list[str]:
        """Delete plant_photos rows beyond the `keep` most recent for this plant.

        Returns the file_path of every pruned row so the caller can unlink the
        backing image file — this DB layer doesn't touch the filesystem itself.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, file_path FROM plant_photos WHERE plant_name = ? "
                "ORDER BY taken_at DESC, id DESC LIMIT -1 OFFSET ?",
                (plant_name, keep),
            ).fetchall()
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            self._conn.executemany("DELETE FROM plant_photos WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()
        return [r["file_path"] for r in rows]

    # --- Photo batch jobs ---

    def create_batch_job(self, items: list[dict]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO photo_batch_jobs (items_json) VALUES (?)",
                (json.dumps(items),),
            )
            self._conn.commit()
            return cur.lastrowid

    def _row_to_batch_job(self, row: sqlite3.Row) -> dict:
        job = dict(row)
        job["items"] = json.loads(job.pop("items_json"))
        return job

    def get_batch_job(self, job_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM photo_batch_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row_to_batch_job(row) if row else None

    _BATCH_JOB_UPDATABLE_FIELDS = {
        "status", "current_index", "pause_reason", "next_ping_at", "paused_seconds", "items_json",
    }

    def update_batch_job(self, job_id: int, **fields):
        if not fields:
            return
        items = fields.pop("items", None)
        if items is not None:
            fields["items_json"] = json.dumps(items)
        unknown = set(fields) - self._BATCH_JOB_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"update_batch_job: unknown field(s) {sorted(unknown)}")
        set_clauses = [f"{key} = ?" for key in fields]
        values = list(fields.values())
        set_clauses.append("updated_at = datetime('now')")
        values.append(job_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE photo_batch_jobs SET {', '.join(set_clauses)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def list_active_batch_jobs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM photo_batch_jobs WHERE status IN ('running', 'paused') ORDER BY created_at"
            ).fetchall()
        return [self._row_to_batch_job(r) for r in rows]
