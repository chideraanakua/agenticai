"""
storage.py — SQLite session store for Phantom Limb
Persists detect+plan sessions and user feedback ratings.
"""

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "sessions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    obj_count   INTEGER DEFAULT 0,
    zone_count  INTEGER DEFAULT 0,
    step_count  INTEGER DEFAULT 0,
    skipped     INTEGER DEFAULT 0,
    relocated   INTEGER DEFAULT 0,
    workspace   TEXT,
    plan        TEXT,
    rating      INTEGER,
    comment     TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(_SCHEMA)
        conn.commit()


# ── session lifecycle ────────────────────────────────────────────────────────

def new_session() -> str:
    sid = str(uuid.uuid4())[:8]
    ts = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, timestamp) VALUES (?, ?)", (sid, ts)
        )
        conn.commit()
    return sid


def save_workspace(session_id: str, workspace: dict):
    ws = workspace.get("workspace", workspace)
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET workspace=?, obj_count=?, zone_count=? WHERE id=?",
            (
                json.dumps(workspace),
                len(ws.get("objects", [])),
                len(ws.get("safety_zones", [])),
                session_id,
            ),
        )
        conn.commit()


def save_plan(session_id: str, plan: dict):
    seq = plan.get("sequence", [])
    skipped = sum(1 for s in seq if s.get("skip"))
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET plan=?, step_count=?, skipped=? WHERE id=?",
            (json.dumps(plan), len(seq), skipped, session_id),
        )
        conn.commit()


# ── feedback ─────────────────────────────────────────────────────────────────

def save_feedback(session_id: str, rating: int, comment: str = ""):
    if not (1 <= rating <= 5):
        raise ValueError("Rating must be 1–5")
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET rating=?, comment=? WHERE id=?",
            (rating, comment.strip(), session_id),
        )
        conn.commit()


# ── queries ──────────────────────────────────────────────────────────────────

def get_sessions(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, obj_count, zone_count, step_count, "
            "skipped, rating, comment "
            "FROM sessions ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("workspace"):
        d["workspace"] = json.loads(d["workspace"])
    if d.get("plan"):
        d["plan"] = json.loads(d["plan"])
    return d


def get_stats() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        rated = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE rating IS NOT NULL"
        ).fetchone()[0]
        avg = conn.execute(
            "SELECT AVG(rating) FROM sessions WHERE rating IS NOT NULL"
        ).fetchone()[0]
        trend = conn.execute(
            "SELECT timestamp, rating FROM sessions "
            "WHERE rating IS NOT NULL ORDER BY timestamp ASC"
        ).fetchall()
    return {
        "total": total,
        "rated": rated,
        "avg_rating": round(avg, 2) if avg else None,
        "trend": [{"ts": r["timestamp"], "rating": r["rating"]} for r in trend],
    }
