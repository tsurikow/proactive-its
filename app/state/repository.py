from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.state.db import get_connection


def _utc_now_str(dt: datetime | None = None) -> str:
    current = dt or datetime.now(timezone.utc)
    return current.strftime("%Y-%m-%d %H:%M:%S")


class StateRepository:
    def ensure_learner(self, learner_id: str, timezone_name: str = "UTC") -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO learners (id, timezone)
                VALUES (?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (learner_id, timezone_name),
            )

    def get_or_create_session(self, learner_id: str, window_hours: int = 2) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id FROM sessions
                WHERE learner_id = ?
                  AND (ended_at IS NULL OR ended_at >= datetime(?))
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (learner_id, _utc_now_str(cutoff)),
            ).fetchone()
            if row:
                return int(row["id"])

            cur = conn.execute(
                "INSERT INTO sessions (learner_id) VALUES (?)",
                (learner_id,),
            )
            return int(cur.lastrowid)

    def add_interaction(
        self,
        learner_id: str,
        session_id: int,
        message: str,
        answer: str,
        module_id: str | None,
        section_id: str | None,
    ) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO interactions (learner_id, session_id, module_id, section_id, message, answer)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (learner_id, session_id, module_id, section_id, message, answer),
            )
            return int(cur.lastrowid)

    def add_interaction_sources(self, interaction_id: int, sources: list[dict[str, Any]]) -> None:
        with get_connection() as conn:
            for source in sources:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO interaction_sources (interaction_id, chunk_id, score, rank)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        interaction_id,
                        source["chunk_id"],
                        source.get("score"),
                        source["rank"],
                    ),
                )

    def update_interaction_confidence(self, interaction_id: int, confidence: int) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE interactions SET confidence = ? WHERE id = ?",
                (confidence, interaction_id),
            )

    def get_interaction(self, interaction_id: int) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM interactions WHERE id = ?",
                (interaction_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_topic_progress(
        self,
        learner_id: str,
        section_id: str,
        module_id: str | None,
        status: str,
        mastery_score: float,
    ) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO topic_progress (learner_id, section_id, module_id, status, mastery_score)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(learner_id, section_id) DO UPDATE SET
                    module_id=excluded.module_id,
                    status=excluded.status,
                    mastery_score=excluded.mastery_score,
                    updated_at=datetime('now')
                """,
                (
                    learner_id,
                    section_id,
                    module_id,
                    status,
                    mastery_score,
                ),
            )

    def list_topic_progress(self, learner_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT learner_id, module_id, section_id, status, mastery_score, updated_at
                FROM topic_progress
                WHERE learner_id = ?
                ORDER BY updated_at DESC
                """,
                (learner_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_study_plan(self, learner_id: str, week_start: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM study_plans
                WHERE learner_id = ?
                  AND week_start = ?
                  AND status = 'active'
                LIMIT 1
                """,
                (learner_id, week_start),
            ).fetchone()
        if not row:
            return None
        plan = dict(row)
        plan["plan"] = json.loads(plan.pop("plan_json"))
        return plan

    def save_study_plan(self, learner_id: str, week_start: str, plan: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(plan)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO study_plans (learner_id, week_start, plan_json, status)
                VALUES (?, ?, ?, 'active')
                ON CONFLICT(learner_id, week_start) DO UPDATE SET
                    plan_json = excluded.plan_json,
                    status = 'active',
                    updated_at = datetime('now')
                """,
                (learner_id, week_start, payload),
            )
            row = conn.execute(
                """
                SELECT * FROM study_plans
                WHERE learner_id = ? AND week_start = ?
                """,
                (learner_id, week_start),
            ).fetchone()
        result = dict(row)
        result["plan"] = json.loads(result.pop("plan_json"))
        return result

    def clear_topic_progress(self, learner_id: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM topic_progress WHERE learner_id = ?",
                (learner_id,),
            )

    def clear_study_plans(self, learner_id: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM study_plans WHERE learner_id = ?",
                (learner_id,),
            )
