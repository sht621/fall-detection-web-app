"""SQLite access kept deliberately small for the single-server deployment."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS detections (
                    event_id TEXT PRIMARY KEY,
                    camera_id TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    video_status TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    video_path TEXT,
                    video_size INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT
                );
                CREATE TABLE IF NOT EXISTS review_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    review_result TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    reviewed_by TEXT NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES detections(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_detections_detected_at
                    ON detections(detected_at DESC);
                """
            )

    def register_detection(self, event_id: str, camera_id: str, detected_at: str) -> tuple[dict[str, Any], bool]:
        timestamp = now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO detections (
                    event_id, camera_id, detected_at, received_at, video_status,
                    review_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'CAPTURING', 'UNREVIEWED', ?, ?)
                """,
                (event_id, camera_id, detected_at, timestamp, timestamp, timestamp),
            )
            row = self._fetch_detection(connection, event_id)
        if row is None:
            raise RuntimeError("detection was not persisted")
        return row, cursor.rowcount == 1

    def mark_video_uploading(self, event_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE detections SET video_status = 'UPLOADING', updated_at = ? WHERE event_id = ?",
                (now_iso(), event_id),
            )
        return cursor.rowcount == 1

    def mark_video_ready(self, event_id: str, video_path: str, video_size: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE detections
                SET video_status = 'READY', video_path = ?, video_size = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (video_path, video_size, now_iso(), event_id),
            )
            return self._fetch_detection(connection, event_id)

    def mark_video_failed(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE detections SET video_status = 'FAILED', updated_at = ? WHERE event_id = ?",
                (now_iso(), event_id),
            )

    def list_detections(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM detections ORDER BY detected_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_detection(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._fetch_detection(connection, event_id)

    def review_detection(
        self, event_id: str, review_result: str, reviewed_by: str
    ) -> dict[str, Any] | None:
        reviewed_at = now_iso()
        # Both current state and audit history belong to the same transaction.
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE detections
                SET review_status = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (review_result, reviewed_at, reviewed_by, reviewed_at, event_id),
            )
            if cursor.rowcount == 0:
                return None
            connection.execute(
                """
                INSERT INTO review_logs (event_id, review_result, reviewed_at, reviewed_by)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, review_result, reviewed_at, reviewed_by),
            )
            return self._fetch_detection(connection, event_id)

    def delete_detection(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = self._fetch_detection(connection, event_id)
            if row is None:
                return None
            connection.execute("DELETE FROM review_logs WHERE event_id = ?", (event_id,))
            connection.execute("DELETE FROM detections WHERE event_id = ?", (event_id,))
            return row

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _fetch_detection(connection: sqlite3.Connection, event_id: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT * FROM detections WHERE event_id = ?", (event_id,)).fetchone()
        return dict(row) if row is not None else None
