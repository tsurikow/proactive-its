from __future__ import annotations

from typing import Any

from app.learner.models import LearnerProfile, MasterySnapshot, MasteryUpdate, TopicEvidence
from app.learner.repository import LearnerRepository
from app.platform.db import get_session


class LearnerService:
    def __init__(
        self,
        repo: LearnerRepository,
        *,
        session_scope=get_session,
    ):
        self.repo = repo
        self._session_scope = session_scope

    async def record_feedback_update(self, update: MasteryUpdate) -> dict[str, Any]:
        async with self._session_scope() as session:
            profile = await self.repo.get_or_create_profile(
                update.learner_id,
                active_template_id=update.active_template_id,
                touch_evidence=True,
                session=session,
            )
            await self.repo.upsert_topic_progress_projection(
                learner_id=update.learner_id,
                section_id=update.section_id,
                module_id=update.module_id,
                status=update.status_after,
                mastery_score=update.mastery_after,
                session=session,
            )
            evidence = await self.repo.append_topic_evidence(update, session=session)
            snapshot = await self.repo.upsert_mastery_snapshot(update, session=session)
        return {
            "profile": LearnerProfile.model_validate(profile),
            "snapshot": MasterySnapshot.model_validate(snapshot),
            "evidence": TopicEvidence.model_validate(evidence),
        }

    async def get_topic_state(self, learner_id: str, section_id: str) -> dict[str, Any]:
        profile = await self.repo.get_profile(learner_id)
        snapshot = await self.repo.get_mastery_snapshot(learner_id, section_id)
        recent_evidence = await self.repo.list_recent_topic_evidence(learner_id, section_id, limit=5)
        return {
            "profile": None if profile is None else LearnerProfile.model_validate(profile),
            "snapshot": None if snapshot is None else MasterySnapshot.model_validate(snapshot),
            "recent_evidence": [TopicEvidence.model_validate(item) for item in recent_evidence],
        }
