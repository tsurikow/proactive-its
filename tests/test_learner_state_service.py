from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
import unittest

from app.learner.models import (
    LEARNER_STATE_SCHEMA_VERSION,
    MasteryUpdate,
)
from app.learner.service import LearnerService


class FakeLearnerStateRepository:
    def __init__(self) -> None:
        self._profiles: dict[str, dict] = {}
        self._snapshots: dict[tuple[str, str], dict] = {}
        self._evidence: list[dict] = []
        self._now = datetime(2026, 3, 19, 12, 0, tzinfo=UTC)
        self._next_id = 1

    def _tick(self) -> datetime:
        now = self._now
        self._now = self._now + timedelta(seconds=1)
        return now

    async def get_profile(self, learner_id: str, *, session=None):
        _ = session
        return self._profiles.get(learner_id)

    async def get_or_create_profile(
        self,
        learner_id: str,
        *,
        active_template_id: str | None = None,
        touch_evidence: bool = False,
        session=None,
    ):
        _ = session
        now = self._tick()
        profile = self._profiles.get(learner_id)
        if profile is None:
            profile = {
                "learner_id": learner_id,
                "active_template_id": active_template_id,
                "state_schema_version": LEARNER_STATE_SCHEMA_VERSION,
                "last_activity_at": now,
                "last_evidence_at": now if touch_evidence else None,
                "created_at": now,
                "updated_at": now,
            }
        else:
            profile = dict(profile)
            profile["last_activity_at"] = now
            profile["updated_at"] = now
            if active_template_id is not None:
                profile["active_template_id"] = active_template_id
            if touch_evidence:
                profile["last_evidence_at"] = now
        self._profiles[learner_id] = profile
        return dict(profile)

    async def upsert_topic_progress_projection(self, **kwargs):
        _ = kwargs
        return None

    async def append_topic_evidence(self, update: MasteryUpdate, *, session=None):
        _ = session
        created_at = self._tick()
        evidence = {
            "id": self._next_id,
            "learner_id": update.learner_id,
            "section_id": update.section_id,
            "module_id": update.module_id,
            "interaction_id": update.interaction_id,
            "source_kind": update.source_kind,
            "assessment_decision": update.assessment_decision,
            "recommended_next_action": update.recommended_next_action,
            "confidence_submitted": update.confidence_submitted,
            "mastery_delta": update.mastery_delta,
            "mastery_before": update.mastery_before,
            "mastery_after": update.mastery_after,
            "status_after": update.status_after,
            "created_at": created_at,
        }
        self._next_id += 1
        self._evidence.append(evidence)
        return dict(evidence)

    async def upsert_mastery_snapshot(self, update: MasteryUpdate, *, session=None):
        _ = session
        key = (update.learner_id, update.section_id)
        now = self._tick()
        current = self._snapshots.get(key)
        evidence_count = 1 if current is None else int(current["evidence_count"]) + 1
        created_at = now if current is None else current["created_at"]
        snapshot = {
            "learner_id": update.learner_id,
            "section_id": update.section_id,
            "module_id": update.module_id,
            "mastery_score": update.mastery_after,
            "status": update.status_after,
            "evidence_count": evidence_count,
            "last_evidence_at": now,
            "last_update_source": update.source_kind,
            "last_interaction_id": update.interaction_id,
            "last_assessment_decision": update.assessment_decision,
            "created_at": created_at,
            "updated_at": now,
        }
        self._snapshots[key] = snapshot
        return dict(snapshot)

    async def get_mastery_snapshot(self, learner_id: str, section_id: str, *, session=None):
        _ = session
        snapshot = self._snapshots.get((learner_id, section_id))
        return None if snapshot is None else dict(snapshot)

    async def list_recent_topic_evidence(self, learner_id: str, section_id: str, *, limit: int = 5, session=None):
        _ = session
        rows = [
            dict(item)
            for item in self._evidence
            if item["learner_id"] == learner_id and item["section_id"] == section_id
        ]
        rows.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return rows[:limit]


@asynccontextmanager
async def fake_session_scope():
    yield object()


class LearnerStateServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repo = FakeLearnerStateRepository()
        self.service = LearnerService(self.repo, session_scope=fake_session_scope)

    async def test_record_feedback_update_creates_profile_evidence_and_snapshot(self) -> None:
        result = await self.service.record_feedback_update(
            MasteryUpdate(
                learner_id="learner-1",
                section_id="limits",
                module_id="m1",
                interaction_id=10,
                source_kind="feedback_assessment",
                assessment_decision="correct",
                recommended_next_action="affirm_and_advance",
                confidence_submitted=5,
                mastery_delta=0.2,
                mastery_before=0.4,
                mastery_after=0.6,
                status_after="in_progress",
                active_template_id="default_calc1",
            )
        )
        self.assertEqual(result["profile"].active_template_id, "default_calc1")
        self.assertEqual(result["snapshot"].mastery_score, 0.6)
        self.assertEqual(result["snapshot"].evidence_count, 1)
        self.assertEqual(result["evidence"].source_kind, "feedback_assessment")
        self.assertEqual(result["evidence"].assessment_decision, "correct")

    async def test_record_feedback_update_confidence_path_and_repeated_updates_increment_snapshot(self) -> None:
        await self.service.record_feedback_update(
            MasteryUpdate(
                learner_id="learner-1",
                section_id="limits",
                module_id="m1",
                interaction_id=11,
                source_kind="feedback_confidence",
                assessment_decision=None,
                recommended_next_action=None,
                confidence_submitted=2,
                mastery_delta=-0.1,
                mastery_before=0.6,
                mastery_after=0.5,
                status_after="in_progress",
                active_template_id="default_calc1",
            )
        )
        result = await self.service.record_feedback_update(
            MasteryUpdate(
                learner_id="learner-1",
                section_id="limits",
                module_id="m1",
                interaction_id=12,
                source_kind="feedback_assessment",
                assessment_decision="partially_correct",
                recommended_next_action="reinforce_key_point",
                confidence_submitted=3,
                mastery_delta=0.08,
                mastery_before=0.5,
                mastery_after=0.58,
                status_after="in_progress",
                active_template_id="default_calc1",
            )
        )
        self.assertEqual(result["snapshot"].evidence_count, 2)
        self.assertEqual(result["snapshot"].last_update_source, "feedback_assessment")
        self.assertEqual(result["snapshot"].last_assessment_decision, "partially_correct")

    async def test_get_topic_state_returns_latest_five_evidence_rows_in_reverse_chronological_order(self) -> None:
        for idx in range(6):
            await self.service.record_feedback_update(
                MasteryUpdate(
                    learner_id="learner-1",
                    section_id="limits",
                    module_id="m1",
                    interaction_id=20 + idx,
                    source_kind="feedback_assessment",
                    assessment_decision="correct",
                    recommended_next_action="affirm_and_advance",
                    confidence_submitted=5,
                    mastery_delta=0.01,
                    mastery_before=0.1 + idx * 0.01,
                    mastery_after=0.11 + idx * 0.01,
                    status_after="in_progress",
                    active_template_id="default_calc1",
                )
            )
        topic_state = await self.service.get_topic_state("learner-1", "limits")
        self.assertEqual(topic_state["profile"].active_template_id, "default_calc1")
        self.assertEqual(topic_state["snapshot"].evidence_count, 6)
        self.assertEqual(len(topic_state["recent_evidence"]), 5)
        self.assertEqual(topic_state["recent_evidence"][0].interaction_id, 25)
        self.assertEqual(topic_state["recent_evidence"][-1].interaction_id, 21)


class LearnerStateMigrationVerificationTests(unittest.TestCase):
    def test_baseline_migration_contains_current_schema_without_backfill(self) -> None:
        versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        migration_files = sorted(path.name for path in versions_dir.glob("*.py"))
        self.assertEqual(migration_files, ["20260219_0001_initial_schema.py"])

        migration_path = versions_dir / "20260219_0001_initial_schema.py"
        text = migration_path.read_text(encoding="utf-8")
        self.assertIn("interaction_assessments", text)
        self.assertIn("learner_profiles", text)
        self.assertIn("mastery_snapshots", text)
        self.assertIn("topic_evidence", text)
        self.assertNotIn("legacy_seed", text)
        self.assertIn("mastery_before", text)
        self.assertIn("mastery_after", text)
        self.assertNotIn("WHERE NOT EXISTS", text)


if __name__ == "__main__":
    unittest.main()
