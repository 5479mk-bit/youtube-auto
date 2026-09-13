"""이미 다룬 주제와 승인 대기 상태를 담는 SQLite 저장소."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS used_topics (
    topic_key TEXT PRIMARY KEY,
    headline   TEXT NOT NULL,
    used_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pending_reviews (
    task_id     TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def used_topic_keys(db_path: Path) -> set[str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT topic_key FROM used_topics").fetchall()
    return {row["topic_key"] for row in rows}


def mark_used(db_path: Path, topic_key: str, headline: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO used_topics (topic_key, headline) VALUES (?, ?)",
            (topic_key, headline),
        )


def save_pending(db_path: Path, task_id: str, payload: dict[str, Any]) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pending_reviews (task_id, payload, status)
            VALUES (?, ?, 'pending')
            ON CONFLICT(task_id) DO UPDATE
                SET payload = excluded.payload,
                    status = 'pending',
                    updated_at = datetime('now')
            """,
            (task_id, json.dumps(payload, ensure_ascii=False)),
        )


def set_review_status(db_path: Path, task_id: str, status: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE pending_reviews SET status = ?, updated_at = datetime('now') WHERE task_id = ?",
            (status, task_id),
        )
