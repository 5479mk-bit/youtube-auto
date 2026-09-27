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


def record_decision(db_path: Path, task_id: str, approved: bool, title_index: int) -> None:
    """텔레그램에서 받은 승인/거절 판정을 업로드 전에 먼저 디스크에 남긴다.

    사람이 버튼을 누른 순간과 업로드가 끝나는 순간 사이에 실패할 수 있는 일이
    많다(유튜브 API 가 꺼져 있거나, 네트워크가 끊기거나). 그 실패로 방금 받은
    판정 자체가 사라지면 사람은 버튼을 또 눌러야 하는데, 텔레그램 콜백은 한 번
    받으면 다시 오지 않는다("승인을 눌렀는데도 계속 안 올라간다"는 문의의 또
    다른 원인이었다). 판정을 'decided' 상태로 먼저 저장해두면, 업로드가
    실패해도 다음 실행이 이 판정 그대로 업로드만 재시도할 수 있다.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload FROM pending_reviews WHERE task_id = ?", (task_id,)
        ).fetchone()
        payload = json.loads(row["payload"]) if row else {}
        payload["decision"] = {"approved": approved, "title_index": title_index}
        conn.execute(
            "UPDATE pending_reviews SET payload = ?, status = 'decided', "
            "updated_at = datetime('now') WHERE task_id = ?",
            (json.dumps(payload, ensure_ascii=False), task_id),
        )


def list_actionable_reviews(db_path: Path) -> list[dict[str, Any]]:
    """아직 끝나지 않은 회차들 — 승인/거절 응답을 못 받은 것('pending')과, 응답은
    받았지만 업로드가 아직 안 끝난 것('decided')을 둘 다 돌려준다. PC가 꺼지거나
    업로드가 중간에 실패해 끊긴 회차를 다시 확인할 때 쓴다(`review-pending` 명령).
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, payload, status, created_at FROM pending_reviews"
            " WHERE status IN ('pending', 'decided') ORDER BY created_at"
        ).fetchall()
    return [
        {
            "task_id": row["task_id"],
            "payload": json.loads(row["payload"]),
            "status": row["status"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def list_pending_reviews(db_path: Path) -> list[dict[str, Any]]:
    """아직 승인/거절 응답을 못 받은 회차들만(응답을 이미 받았지만 업로드가 안 끝난
    'decided' 회차는 제외). `list_actionable_reviews` 가 그 둘을 합쳐서 준다 —
    이 함수는 기존 호출부·테스트 호환용으로 남겨둔다."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, payload, created_at FROM pending_reviews WHERE status = 'pending'"
            " ORDER BY created_at"
        ).fetchall()
    return [
        {
            "task_id": row["task_id"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
