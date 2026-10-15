"""JobStore — SQLite persistence for the job-hunt agent and the jobs_ui PWA.

One dedicated database (data/jobs/jobs.db, independent of agents.db) shared by a
writer on the host (JobHuntAgent: CV snapshots, jobs, scores, Gmail-derived
applications) and the web app (job status, manual applications, notes,
manual search queries). WAL mode lets the two touch it concurrently.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Own directory so the jobs_ui container can bind-mount it (SQLite WAL needs a
# writable dir) without seeing agents.db or anything else under data/.
DEFAULT_JOBS_DB = Path(__file__).resolve().parent.parent / "data" / "jobs" / "jobs.db"

JOB_STATUSES = ("new", "seen", "dismissed", "applied")
APP_STATUSES = ("applied", "interview", "rejected", "offer", "withdrawn")
# An email event may only move an application sideways or forward — a late
# "application received" must never undo a rejection or interview.
_STATUS_RANK = {"applied": 0, "interview": 1, "offer": 2, "rejected": 2, "withdrawn": 2}

# Jobs joined with the status of the application they're linked to (if any).
_JOB_SELECT = ("SELECT jobs.*, a.status AS application_status FROM jobs "
               "LEFT JOIN applications a ON a.id = jobs.application_id")

_QUERY_RE = re.compile(r"^[\w .+/#-]{1,40}$")
_LEGAL_SUFFIXES = {"nv", "bv", "ltd", "limited", "inc", "gmbh", "plc", "llc", "ag", "sa", "se"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT, name TEXT, modified_time TEXT,
    text_hash TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('cv','manual')),
    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL, company TEXT NOT NULL, location TEXT, url TEXT NOT NULL, posted TEXT,
    description TEXT,
    triage TEXT CHECK (triage IN ('keep','drop')),
    score INTEGER, matched_skills TEXT, gaps TEXT, rationale TEXT, cv_snapshot_id INTEGER,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','seen','dismissed','applied')),
    notified INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL, scored_at TEXT,
    application_id INTEGER REFERENCES applications(id)
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
    job_id TEXT REFERENCES jobs(job_id),
    status TEXT NOT NULL CHECK (status IN ('applied','interview','rejected','offer','withdrawn')),
    source TEXT NOT NULL CHECK (source IN ('gmail','manual')),
    notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    status TEXT NOT NULL, message_id TEXT UNIQUE, gmail_thread_id TEXT,
    subject TEXT, occurred_at TEXT
);
CREATE TABLE IF NOT EXISTS gmail_seen (message_id TEXT PRIMARY KEY);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_events_app ON application_events(application_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_job ON applications(job_id) WHERE job_id IS NOT NULL;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_query(q: str) -> str | None:
    """Whitespace-collapsed query if it's a safe LinkedIn search term, else None."""
    q = re.sub(r"\s+", " ", q or "").strip()
    return q if _QUERY_RE.fullmatch(q) else None


def normalise_company(name: str) -> str:
    tokens = re.sub(r"[^a-z0-9& ]", "", (name or "").lower()).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


_ROLE_NOISE = {"senior", "sr", "junior", "jr", "medior", "mid", "lead", "staff", "principal",
               "i", "ii", "iii", "iv", "of", "and", "the", "a", "m", "f", "x", "d"}
_ROLE_ABBREV = {"sre": ("site", "reliability", "engineer")}


def _role_tokens(role: str) -> set:
    tokens = set()
    for w in re.findall(r"[a-z0-9]+", (role or "").lower()):
        tokens.update(_ROLE_ABBREV.get(w, (w,)))
    return tokens - _ROLE_NOISE


def _roles_match(a: str, b: str) -> bool:
    """Seniority-insensitive role match: one title's words contained in the
    other's, or Jaccard >= 0.6. "Data Engineer" != "Platform Engineer"."""
    ta, tb = _role_tokens(a), _role_tokens(b)
    if not ta or not tb:
        return True
    if ta <= tb or tb <= ta:
        return True
    return len(ta & tb) / len(ta | tb) >= 0.6


class JobStore:
    def __init__(self, path: str | Path = DEFAULT_JOBS_DB):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        if "application_id" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN application_id INTEGER REFERENCES applications(id)")
        # Backfill links made before jobs.application_id existed (applications.job_id only).
        self.conn.execute(
            "UPDATE jobs SET application_id = (SELECT a.id FROM applications a WHERE a.job_id = jobs.job_id) "
            "WHERE application_id IS NULL AND EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.job_id)"
        )

    def close(self):
        self.conn.close()

    def _all(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params=()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    # --- CV snapshots ---

    @staticmethod
    def text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def latest_cv(self) -> dict | None:
        return self._one("SELECT * FROM cv_snapshots ORDER BY id DESC LIMIT 1")

    def add_cv_snapshot(self, file_id: str, name: str, modified_time: str, text: str) -> dict:
        cur = self.conn.execute(
            "INSERT INTO cv_snapshots (file_id, name, modified_time, text_hash, text, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (file_id, name, modified_time, self.text_hash(text), text, _now()),
        )
        self.conn.commit()
        return self._one("SELECT * FROM cv_snapshots WHERE id=?", (cur.lastrowid,))

    # --- search queries ---

    def list_queries(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM search_queries"
        if enabled_only:
            sql += " WHERE enabled=1"
        return self._all(sql + " ORDER BY source, id")

    def replace_cv_queries(self, queries: list[str]) -> int:
        """Replace all CV-derived queries; invalid and duplicate ones are dropped.
        Manual queries are untouched. Returns the number stored."""
        valid: list[str] = []
        for q in queries:
            v = validate_query(q)
            if v and v.lower() not in {x.lower() for x in valid}:
                valid.append(v)
        with self.conn:
            self.conn.execute("DELETE FROM search_queries WHERE source='cv'")
            self.conn.executemany(
                "INSERT INTO search_queries (query, source, created_at) VALUES (?, 'cv', ?)",
                [(q, _now()) for q in valid],
            )
        return len(valid)

    def add_manual_query(self, query: str) -> dict:
        q = validate_query(query)
        if not q:
            raise ValueError("Query must be 1-40 chars: letters, digits, space . + / # -")
        if self._one("SELECT id FROM search_queries WHERE lower(query)=lower(?)", (q,)):
            raise ValueError(f"Query '{q}' already exists")
        cur = self.conn.execute(
            "INSERT INTO search_queries (query, source, created_at) VALUES (?, 'manual', ?)",
            (q, _now()),
        )
        self.conn.commit()
        return self._one("SELECT * FROM search_queries WHERE id=?", (cur.lastrowid,))

    def delete_query(self, query_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM search_queries WHERE id=?", (query_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- jobs ---

    @staticmethod
    def _decode_job(job: dict | None) -> dict | None:
        if job is not None:
            for key in ("matched_skills", "gaps"):
                job[key] = json.loads(job[key]) if job.get(key) else []
        return job

    def has_job(self, job_id: str) -> bool:
        return self._one("SELECT 1 AS x FROM jobs WHERE job_id=?", (job_id,)) is not None

    def insert_job(self, job: dict) -> bool:
        """Insert a normalised job as status 'new'. False if already known."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO jobs (job_id, title, company, location, url, posted, first_seen) "
            "VALUES (?,?,?,?,?,?,?)",
            (job["job_id"], job["title"], job["company"], job.get("location", ""),
             job["url"], job.get("posted"), _now()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_job(self, job_id: str) -> dict | None:
        return self._decode_job(self._one(f"{_JOB_SELECT} WHERE jobs.job_id=?", (job_id,)))

    def jobs_needing_triage(self, limit: int) -> list[dict]:
        return self._all(
            "SELECT job_id, title, company, location FROM jobs "
            "WHERE triage IS NULL AND status IN ('new','seen') ORDER BY first_seen, job_id LIMIT ?",
            (limit,),
        )

    def set_triage(self, job_id: str, keep: bool):
        self.conn.execute("UPDATE jobs SET triage=? WHERE job_id=?",
                          ("keep" if keep else "drop", job_id))
        self.conn.commit()

    def jobs_to_score(self, limit: int) -> list[dict]:
        return [self._decode_job(j) for j in self._all(
            "SELECT * FROM jobs WHERE triage='keep' AND score IS NULL AND status IN ('new','seen') "
            "ORDER BY first_seen DESC, job_id LIMIT ?",
            (limit,),
        )]

    def set_description(self, job_id: str, text: str):
        self.conn.execute("UPDATE jobs SET description=? WHERE job_id=?", (text, job_id))
        self.conn.commit()

    def save_score(self, job_id: str, score: int, matched: list[str], gaps: list[str],
                   rationale: str, cv_snapshot_id: int | None):
        self.conn.execute(
            "UPDATE jobs SET score=?, matched_skills=?, gaps=?, rationale=?, cv_snapshot_id=?, "
            "scored_at=? WHERE job_id=?",
            (score, json.dumps(matched), json.dumps(gaps), rationale, cv_snapshot_id, _now(), job_id),
        )
        self.conn.commit()

    def reset_scores_since(self, days: int) -> int:
        """Clear scores of still-open jobs first seen in the last `days` days,
        so they're re-scored against a new CV."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self.conn.execute(
            "UPDATE jobs SET score=NULL, scored_at=NULL "
            "WHERE score IS NOT NULL AND status IN ('new','seen') AND first_seen >= ?",
            (cutoff,),
        )
        self.conn.commit()
        return cur.rowcount

    def list_jobs(self, status: str | None = None, min_score: int | None = None) -> list[dict]:
        sql, params = f"{_JOB_SELECT} WHERE 1=1", []
        if status:
            sql += " AND jobs.status=?"
            params.append(status)
        if min_score is not None:
            sql += " AND jobs.score >= ?"
            params.append(min_score)
        sql += " ORDER BY jobs.score IS NULL, jobs.score DESC, jobs.first_seen DESC"
        return [self._decode_job(j) for j in self._all(sql, params)]

    def set_job_status(self, job_id: str, status: str) -> bool:
        if status not in JOB_STATUSES:
            raise ValueError(f"Invalid job status '{status}'")
        cur = self.conn.execute("UPDATE jobs SET status=? WHERE job_id=?", (status, job_id))
        self.conn.commit()
        return cur.rowcount > 0

    def jobs_to_notify(self, min_score: int) -> list[dict]:
        return self._all(
            "SELECT * FROM jobs WHERE score >= ? AND notified=0 AND status IN ('new','seen') "
            "ORDER BY score DESC",
            (min_score,),
        )

    def mark_notified(self, job_ids: list[str]):
        self.conn.executemany("UPDATE jobs SET notified=1 WHERE job_id=?", [(j,) for j in job_ids])
        self.conn.commit()

    def mark_applied(self, job_id: str) -> dict | None:
        """Flag a job applied and return its application, creating a manual one
        if none is linked yet. Idempotent. None for an unknown job."""
        job = self.get_job(job_id)
        if not job:
            return None
        self.set_job_status(job_id, "applied")
        if job.get("application_id"):
            return self.get_application(job["application_id"])
        existing = self._one("SELECT id FROM applications WHERE job_id=?", (job_id,))
        if existing:
            app = self.get_application(existing["id"])
        else:
            try:
                app = self.create_application(job["company"], job["title"], "applied", "manual", job_id=job_id)
            except sqlite3.IntegrityError:  # concurrent double-tap won the insert
                app = self.get_application(
                    self._one("SELECT id FROM applications WHERE job_id=?", (job_id,))["id"])
        self.conn.execute("UPDATE jobs SET application_id=? WHERE job_id=?", (app["id"], job_id))
        self.conn.commit()
        return app

    # --- applications ---

    def get_application(self, app_id: int) -> dict | None:
        app = self._one("SELECT * FROM applications WHERE id=?", (app_id,))
        if app:
            app["events"] = self._all(
                "SELECT * FROM application_events WHERE application_id=? ORDER BY occurred_at, id",
                (app_id,),
            )
        return app

    def list_applications(self) -> list[dict]:
        ids = [r["id"] for r in self._all("SELECT id FROM applications ORDER BY updated_at DESC, id DESC")]
        return [self.get_application(i) for i in ids]

    def create_application(self, company: str, role: str, status: str, source: str,
                           job_id: str | None = None, notes: str = "") -> dict:
        if status not in APP_STATUSES:
            raise ValueError(f"Invalid application status '{status}'")
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO applications (company, role, job_id, status, source, notes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (company, role or "", job_id, status, source, notes or "", now, now),
        )
        self.conn.commit()
        return self.get_application(cur.lastrowid)

    def update_application(self, app_id: int, status: str | None = None,
                           notes: str | None = None) -> dict | None:
        if not self._one("SELECT id FROM applications WHERE id=?", (app_id,)):
            return None
        if status is not None and status not in APP_STATUSES:
            raise ValueError(f"Invalid application status '{status}'")
        sets, params = ["updated_at=?"], [_now()]
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if notes is not None:
            sets.append("notes=?")
            params.append(notes)
        self.conn.execute(f"UPDATE applications SET {', '.join(sets)} WHERE id=?", (*params, app_id))
        self.conn.commit()
        return self.get_application(app_id)

    def find_application(self, company: str, role: str) -> dict | None:
        """Most recently updated application at the same (normalised) company
        whose role matches. An email with no role only matches when the company
        has exactly one application — otherwise it could land on the wrong one."""
        target = normalise_company(company)
        if not target:
            return None
        same_company = [a for a in self._all("SELECT * FROM applications ORDER BY updated_at DESC, id DESC")
                        if normalise_company(a["company"]) == target]
        if not (role or "").strip():
            return same_company[0] if len(same_company) == 1 else None
        for app in same_company:
            if _roles_match(app["role"], role):
                return app
        return None

    def _link_jobs(self, app: dict) -> int:
        """Link still-open jobs at the same company with a matching role to this
        application and mark them applied. Needs a role: matching on company
        alone would swallow every posting from a recruiter or big employer."""
        if not (app.get("role") or "").strip():
            return 0
        target = normalise_company(app["company"])
        linked = 0
        for job in self._all("SELECT job_id, company, title FROM jobs "
                             "WHERE application_id IS NULL AND status != 'dismissed' ORDER BY first_seen"):
            if normalise_company(job["company"]) == target and _roles_match(job["title"], app["role"]):
                self.conn.execute("UPDATE jobs SET application_id=?, status='applied' WHERE job_id=?",
                                  (app["id"], job["job_id"]))
                self.conn.execute("UPDATE applications SET job_id=COALESCE(job_id, ?) WHERE id=?",
                                  (job["job_id"], app["id"]))
                linked += 1
        return linked

    def reconcile_applications(self) -> int:
        """Link every unlinked job that matches a tracked application (so jobs
        already applied for / rejected from drop out of scoring and pings).
        Returns the number of jobs newly linked. Idempotent."""
        with self.conn:
            return sum(self._link_jobs(app) for app in self._all("SELECT * FROM applications"))

    def record_email_event(self, company: str, role: str, status: str, message_id: str,
                           thread_id: str, subject: str, occurred_at: str) -> dict:
        """Attach a classified email to its application (find-or-create), log
        an event, and advance status without ever downgrading it. Replaying
        the same message_id is a no-op."""
        if status not in APP_STATUSES:
            raise ValueError(f"Invalid application status '{status}'")
        app = self.find_application(company, role)
        if app is None:
            app = self.create_application(company, role, status, "gmail")
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO application_events "
                "(application_id, status, message_id, gmail_thread_id, subject, occurred_at) "
                "VALUES (?,?,?,?,?,?)",
                (app["id"], status, message_id, thread_id, subject, occurred_at),
            )
            if cur.rowcount and _STATUS_RANK[status] >= _STATUS_RANK[app["status"]]:
                self.conn.execute("UPDATE applications SET status=?, updated_at=? WHERE id=?",
                                  (status, _now(), app["id"]))
            self._link_jobs(self.get_application(app["id"]))
        return self.get_application(app["id"])

    # --- gmail dedup ---

    def gmail_seen(self, message_id: str) -> bool:
        return self._one("SELECT 1 AS x FROM gmail_seen WHERE message_id=?", (message_id,)) is not None

    def mark_gmail_seen(self, message_ids: list[str]):
        self.conn.executemany("INSERT OR IGNORE INTO gmail_seen (message_id) VALUES (?)",
                              [(m,) for m in message_ids])
        self.conn.commit()

    def has_gmail_history(self) -> bool:
        return self._one("SELECT 1 AS x FROM gmail_seen LIMIT 1") is not None
