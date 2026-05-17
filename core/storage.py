"""
storage.py — SQLite session store for Safearc
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
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    obj_count       INTEGER DEFAULT 0,
    zone_count      INTEGER DEFAULT 0,
    step_count      INTEGER DEFAULT 0,
    skipped         INTEGER DEFAULT 0,
    relocated       INTEGER DEFAULT 0,
    workspace       TEXT,
    plan            TEXT,
    rating          INTEGER,
    comment         TEXT,
    image_original  TEXT,
    image_annotated TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(_SCHEMA)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        for col, typedef in [
            ("image_original",    "TEXT"),
            ("image_annotated",   "TEXT"),
            ("eval_score",        "REAL"),
            ("eval_critique",     "TEXT"),
            ("eval_suggestions",  "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typedef}")
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


def save_images(session_id: str, original_url: str, annotated_url: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET image_original=?, image_annotated=? WHERE id=?",
            (original_url, annotated_url, session_id),
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


# ── evaluation ───────────────────────────────────────────────────────────────

def save_evaluation(session_id: str, predicted_score, critique: str, suggestions: list):
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET eval_score=?, eval_critique=?, eval_suggestions=? WHERE id=?",
            (predicted_score, critique.strip(), json.dumps(suggestions), session_id),
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


def get_rated_sessions(min_rating: int = 1, max_rating: int = 5, limit: int = 20) -> list[dict]:
    """
    Return sessions that have a saved plan and either a user rating or an evaluator
    score, newest first. User rating takes priority; eval_score is used as a fallback
    so every evaluated plan contributes to few-shot learning even without manual rating.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, rating, comment, obj_count, skipped, relocated, "
            "workspace, plan, eval_score, eval_critique, eval_suggestions, "
            "COALESCE(rating, ROUND(eval_score)) AS effective_rating "
            "FROM sessions "
            "WHERE plan IS NOT NULL "
            "  AND (rating IS NOT NULL OR eval_score IS NOT NULL) "
            "  AND COALESCE(rating, ROUND(eval_score)) BETWEEN ? AND ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (min_rating, max_rating, limit),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("workspace"):
            d["workspace"] = json.loads(d["workspace"])
        if d.get("plan"):
            d["plan"] = json.loads(d["plan"])
        if d.get("eval_suggestions"):
            d["eval_suggestions"] = json.loads(d["eval_suggestions"])
        result.append(d)
    return result


def get_calibration_stats() -> dict:
    """
    Returns per-session predicted vs actual deltas and aggregate MAE/bias
    for sessions where both eval_score and human rating exist.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, rating, eval_score "
            "FROM sessions "
            "WHERE rating IS NOT NULL AND eval_score IS NOT NULL "
            "ORDER BY timestamp DESC"
        ).fetchall()
    points = []
    for r in rows:
        delta = round(r["eval_score"] - r["rating"], 2)
        points.append({
            "id": r["id"],
            "timestamp": r["timestamp"],
            "actual": r["rating"],
            "predicted": round(r["eval_score"], 2),
            "delta": delta,
        })
    if points:
        deltas = [abs(p["delta"]) for p in points]
        biases = [p["delta"] for p in points]
        mae = round(sum(deltas) / len(deltas), 3)
        bias = round(sum(biases) / len(biases), 3)
    else:
        mae, bias = None, None
    return {"points": points, "mae": mae, "bias": bias, "n": len(points)}


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
