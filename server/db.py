import json
import os
import sqlite3
import threading
import time
import uuid

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database", "calls.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    duration REAL,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    role TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_call ON segments(call_id, idx);

CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    word TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    confidence REAL
);
CREATE INDEX IF NOT EXISTS idx_words_segment ON words(segment_id, start);

CREATE TABLE IF NOT EXISTS metrics (
    call_id TEXT PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa (
    call_id TEXT PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
    score INTEGER,
    data TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text,
    call_id UNINDEXED,
    segment_id UNINDEXED,
    tokenize = 'porter'
);
"""


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init():
    connect().executescript(SCHEMA)
    connect().commit()


def create_call(filename: str, audio_path: str) -> str:
    call_id = uuid.uuid4().hex[:12]
    conn = connect()
    conn.execute(
        "INSERT INTO calls (id, filename, audio_path, status, created_at) VALUES (?,?,?,?,?)",
        (call_id, filename, audio_path, "queued", time.time()),
    )
    conn.commit()
    return call_id


def set_progress(call_id: str, stage: str, progress: int):
    conn = connect()
    conn.execute(
        "UPDATE calls SET status='processing', stage=?, progress=? WHERE id=?",
        (stage, progress, call_id),
    )
    conn.commit()


def set_duration(call_id: str, duration: float):
    conn = connect()
    conn.execute("UPDATE calls SET duration=? WHERE id=?", (duration, call_id))
    conn.commit()


def set_failed(call_id: str, error: str):
    conn = connect()
    conn.execute(
        "UPDATE calls SET status='failed', error=?, completed_at=? WHERE id=?",
        (error[:2000], time.time(), call_id),
    )
    conn.commit()


def set_completed(call_id: str):
    conn = connect()
    conn.execute(
        "UPDATE calls SET status='completed', stage='done', progress=100, completed_at=? WHERE id=?",
        (time.time(), call_id),
    )
    conn.commit()


def save_transcript(call_id: str, segments: list[dict]):
    """segments: [{speaker, role, start, end, text, words:[{word,start,end,confidence}]}]"""
    conn = connect()
    conn.execute("DELETE FROM segments WHERE call_id=?", (call_id,))
    conn.execute("DELETE FROM words WHERE call_id=?", (call_id,))
    conn.execute("DELETE FROM segments_fts WHERE call_id=?", (call_id,))
    for idx, seg in enumerate(segments):
        cur = conn.execute(
            "INSERT INTO segments (call_id, idx, speaker, role, start, end, text) VALUES (?,?,?,?,?,?,?)",
            (call_id, idx, seg["speaker"], seg["role"], seg["start"], seg["end"], seg["text"]),
        )
        seg_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO words (call_id, segment_id, word, start, end, confidence) VALUES (?,?,?,?,?,?)",
            [
                (call_id, seg_id, w["word"], w["start"], w["end"], w.get("confidence"))
                for w in seg["words"]
            ],
        )
        conn.execute(
            "INSERT INTO segments_fts (text, call_id, segment_id) VALUES (?,?,?)",
            (seg["text"], call_id, seg_id),
        )
    conn.commit()


def save_metrics(call_id: str, metrics: dict):
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO metrics (call_id, data) VALUES (?,?)",
        (call_id, json.dumps(metrics)),
    )
    conn.commit()


def save_qa(call_id: str, qa: dict):
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO qa (call_id, score, data) VALUES (?,?,?)",
        (call_id, qa.get("score"), json.dumps(qa)),
    )
    conn.commit()


def get_call(call_id: str) -> dict | None:
    row = connect().execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    return dict(row) if row else None


def list_calls(limit: int = 100) -> list[dict]:
    rows = connect().execute(
        """SELECT c.*, q.score FROM calls c LEFT JOIN qa q ON q.call_id = c.id
           ORDER BY c.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_transcript(call_id: str) -> list[dict]:
    conn = connect()
    segs = conn.execute(
        "SELECT * FROM segments WHERE call_id=? ORDER BY idx", (call_id,)
    ).fetchall()
    words = conn.execute(
        """SELECT w.* FROM words w JOIN segments s ON s.id = w.segment_id
           WHERE w.call_id=? ORDER BY s.idx, w.start""",
        (call_id,),
    ).fetchall()
    by_seg: dict[int, list] = {}
    for w in words:
        by_seg.setdefault(w["segment_id"], []).append(
            {"word": w["word"], "start": w["start"], "end": w["end"], "confidence": w["confidence"]}
        )
    return [
        {
            "id": s["id"],
            "speaker": s["speaker"],
            "role": s["role"],
            "start": s["start"],
            "end": s["end"],
            "text": s["text"],
            "words": by_seg.get(s["id"], []),
        }
        for s in segs
    ]


def get_metrics(call_id: str) -> dict | None:
    row = connect().execute("SELECT data FROM metrics WHERE call_id=?", (call_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def get_qa(call_id: str) -> dict | None:
    row = connect().execute("SELECT data FROM qa WHERE call_id=?", (call_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def delete_call(call_id: str):
    conn = connect()
    conn.execute("DELETE FROM segments_fts WHERE call_id=?", (call_id,))
    conn.execute("DELETE FROM calls WHERE id=?", (call_id,))
    conn.commit()


def search(query: str, limit: int = 50) -> list[dict]:
    rows = connect().execute(
        """SELECT f.call_id, f.segment_id, c.filename, s.start, s.end, s.role,
                  snippet(segments_fts, 0, char(1), char(2), '…', 12) AS snippet
           FROM segments_fts f
           JOIN calls c ON c.id = f.call_id
           JOIN segments s ON s.id = f.segment_id
           WHERE segments_fts MATCH ? ORDER BY rank LIMIT ?""",
        (query, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def reset_stuck_jobs():
    """Any job left 'processing' by a crash is requeued at startup."""
    conn = connect()
    conn.execute("UPDATE calls SET status='queued', stage=NULL, progress=0 WHERE status='processing'")
    conn.commit()


def pending_call_ids() -> list[str]:
    rows = connect().execute(
        "SELECT id FROM calls WHERE status='queued' ORDER BY created_at"
    ).fetchall()
    return [r["id"] for r in rows]
