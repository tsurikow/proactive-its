from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import get_settings


def _db_path() -> Path:
    settings = get_settings()
    path = settings.sqlite_abs_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path(), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS learners (
                id TEXT PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learner_id TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at TEXT,
                FOREIGN KEY (learner_id) REFERENCES learners(id)
            );

            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learner_id TEXT NOT NULL,
                session_id INTEGER,
                module_id TEXT,
                section_id TEXT,
                message TEXT NOT NULL,
                answer TEXT NOT NULL,
                confidence INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (learner_id) REFERENCES learners(id),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS interaction_sources (
                interaction_id INTEGER NOT NULL,
                chunk_id TEXT NOT NULL,
                score REAL,
                rank INTEGER NOT NULL,
                PRIMARY KEY (interaction_id, chunk_id),
                FOREIGN KEY (interaction_id) REFERENCES interactions(id)
            );

            CREATE TABLE IF NOT EXISTS topic_progress (
                learner_id TEXT NOT NULL,
                module_id TEXT,
                section_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                mastery_score REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (learner_id, section_id),
                FOREIGN KEY (learner_id) REFERENCES learners(id)
            );

            CREATE TABLE IF NOT EXISTS study_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learner_id TEXT NOT NULL,
                week_start TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (learner_id, week_start),
                FOREIGN KEY (learner_id) REFERENCES learners(id)
            );

            CREATE INDEX IF NOT EXISTS idx_interactions_learner_created
                ON interactions (learner_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_topic_progress_learner_updated
                ON topic_progress (learner_id, updated_at DESC);
            """
        )
