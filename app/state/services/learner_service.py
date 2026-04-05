from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.state.models import (
    ADAPTATION_CONTEXT_VERSION,
    AdaptationContext,
    LearnerProfile,
    MasterySnapshot,
    MasteryUpdate,
    RecentEvidencePattern,
    StageMasteryView,
    TopicEvidence,
)
from app.platform.config import Settings, get_settings
from app.platform.db import get_session
from app.state.repositories.learner_repository import LearnerStateRepository


class LearnerService:
    def __init__(
        self,
        repo: LearnerStateRepository,
        *,
        session_scope=get_session,
        settings: Settings | None = None,
        now_provider=None,
    ):
        self.repo = repo
        self._session_scope = session_scope
        self.settings = settings or get_settings()
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    async def record_feedback_update(self, update: MasteryUpdate) -> dict[str, Any]:
        async with self._session_scope() as session:
            profile = await self.repo.get_or_create_profile(
                update.learner_id,
                active_template_id=update.active_template_id,
                touch_evidence=True,
                session=session,
            )
            evidence = await self.repo.append_topic_evidence(update, session=session)
            snapshot = await self.repo.upsert_mastery_snapshot(update, session=session)
            projection = self._build_effective_projection(snapshot)
            await self.repo.upsert_topic_progress_projection(
                learner_id=update.learner_id,
                section_id=update.section_id,
                module_id=update.module_id,
                status=projection["effective_status"],
                mastery_score=projection["effective_mastery_score"],
                session=session,
            )
        return {
            "profile": LearnerProfile.model_validate(profile),
            "snapshot": MasterySnapshot.model_validate(snapshot),
            "evidence": TopicEvidence.model_validate(evidence),
        }

    async def refresh_projection(self, learner_id: str, *, section_id: str | None = None) -> list[dict[str, Any]]:
        async with self._session_scope() as session:
            if section_id:
                snapshot = await self.repo.get_mastery_snapshot(learner_id, section_id, session=session)
                snapshots = [] if snapshot is None else [snapshot]
            else:
                snapshots = await self.repo.list_mastery_snapshots(learner_id, session=session)

            projections: list[dict[str, Any]] = []
            for snapshot in snapshots:
                projection = self._build_effective_projection(snapshot)
                await self.repo.upsert_topic_progress_projection(
                    learner_id=learner_id,
                    section_id=str(snapshot["section_id"]),
                    module_id=snapshot.get("module_id"),
                    status=projection["effective_status"],
                    mastery_score=projection["effective_mastery_score"],
                    session=session,
                )
                projections.append(projection)
        return projections

    async def get_topic_state(self, learner_id: str, section_id: str) -> dict[str, Any]:
        profile = await self.repo.get_profile(learner_id)
        snapshot = await self.repo.get_mastery_snapshot(learner_id, section_id)
        recent_evidence = await self.repo.list_recent_topic_evidence(learner_id, section_id, limit=5)
        projection = None if snapshot is None else self._build_effective_projection(snapshot)
        return {
            "profile": None if profile is None else LearnerProfile.model_validate(profile),
            "snapshot": None if snapshot is None else MasterySnapshot.model_validate(snapshot),
            "recent_evidence": [TopicEvidence.model_validate(item) for item in recent_evidence],
            "raw_mastery_score": None if projection is None else projection["raw_mastery_score"],
            "effective_mastery_score": None if projection is None else projection["effective_mastery_score"],
            "decay_multiplier": None if projection is None else projection["decay_multiplier"],
            "hours_since_last_evidence": None if projection is None else projection["hours_since_last_evidence"],
            "effective_status": None if projection is None else projection["effective_status"],
        }

    async def build_adaptation_context(
        self,
        learner_id: str,
        current_stage: dict[str, Any] | None,
        stage_targets: list[dict[str, Any]],
    ) -> AdaptationContext:
        snapshots = await self.repo.list_mastery_snapshots(learner_id)
        projections = [self._build_effective_projection(snapshot) for snapshot in snapshots]
        projection_by_section = {str(item["section_id"]): item for item in projections}

        current_stage_payload = None if current_stage is None else dict(current_stage)
        current_section_id = None if current_stage is None else str(current_stage.get("section_id") or "")
        current_module_id = None if current_stage is None else current_stage.get("module_id")
        current_topic = self._stage_mastery_view_from_projection(
            projection_by_section.get(current_section_id or "")
        )

        module_targets = self._module_targets(stage_targets, current_stage)
        module_section_ids = {
            str(item.get("section_id") or "")
            for item in module_targets
            if str(item.get("section_id") or "")
        }
        module_views = [
            self._stage_mastery_view_from_projection(item)
            for section_id, item in projection_by_section.items()
            if section_id in module_section_ids
        ]
        module_views = [item for item in module_views if item is not None]
        weak_related_topics = self._weak_related_topics(module_views, current_section_id)
        strong_related_topics = self._strong_related_topics(module_views, current_section_id)

        module_summary = self._module_summary(
            total_module_topics=len(module_targets),
            module_views=module_views,
        )
        recent_evidence = await self.repo.list_recent_evidence(
            learner_id,
            module_id=str(current_module_id) if current_module_id is not None else None,
            limit=5,
        )
        if not recent_evidence:
            recent_evidence = await self.repo.list_recent_evidence(learner_id, limit=5)

        return AdaptationContext(
            learner_id=learner_id,
            current_stage=current_stage_payload,
            stage_signal=self._stage_signal(current_topic),
            current_topic=current_topic,
            module_summary=module_summary,
            weak_related_topics=weak_related_topics,
            strong_related_topics=strong_related_topics,
            recent_pattern=self._recent_evidence_pattern(recent_evidence),
            context_version=ADAPTATION_CONTEXT_VERSION,
        )

    async def build_learner_model_source(
        self,
        learner_id: str,
        *,
        current_stage: dict[str, Any] | None,
        adaptation_context: AdaptationContext,
        limit: int = 6,
    ) -> dict[str, Any]:
        profile = await self.repo.get_profile(learner_id)
        module_id = None if current_stage is None else current_stage.get("module_id")
        recent_evidence = await self.repo.list_recent_evidence(
            learner_id,
            module_id=None if module_id is None else str(module_id),
            limit=limit,
        )
        if not recent_evidence:
            recent_evidence = await self.repo.list_recent_evidence(learner_id, limit=limit)

        assessment_counts: dict[str, int] = {}
        recommended_actions: list[str] = []
        recent_sections: list[str] = []
        source_kinds: list[str] = []
        mastery_deltas: list[float] = []
        for item in recent_evidence:
            decision = str(item.get("assessment_decision") or "").strip()
            if decision:
                assessment_counts[decision] = assessment_counts.get(decision, 0) + 1
            action = str(item.get("recommended_next_action") or "").strip()
            if action and action not in recommended_actions:
                recommended_actions.append(action)
            section_id = str(item.get("section_id") or "").strip()
            if section_id and section_id not in recent_sections:
                recent_sections.append(section_id)
            source_kind = str(item.get("source_kind") or "").strip()
            if source_kind and source_kind not in source_kinds:
                source_kinds.append(source_kind)
            mastery_deltas.append(float(item.get("mastery_delta", 0.0)))

        current_topic = adaptation_context.current_topic
        return {
            "profile": {
                "active_template_id": None if profile is None else profile.get("active_template_id"),
                "last_activity_at": None if profile is None else profile.get("last_activity_at"),
                "last_evidence_at": None if profile is None else profile.get("last_evidence_at"),
            },
            "recent_evidence_summary": {
                "count": len(recent_evidence),
                "latest_assessment_decision": None if not recent_evidence else recent_evidence[0].get("assessment_decision"),
                "assessment_counts": assessment_counts,
                "recommended_actions": recommended_actions[:4],
                "recent_sections": recent_sections[:4],
                "source_kinds": source_kinds[:4],
                "average_mastery_delta": None
                if not mastery_deltas
                else round(sum(mastery_deltas) / len(mastery_deltas), 4),
            },
            "mastery_signals": {
                "stage_signal": adaptation_context.stage_signal,
                "current_effective_mastery": None
                if current_topic is None
                else round(current_topic.effective_mastery_score, 4),
                "current_last_assessment_decision": None
                if current_topic is None
                else current_topic.last_assessment_decision,
                "weak_related_sections": [item.section_id for item in adaptation_context.weak_related_topics[:4]],
                "strong_related_sections": [item.section_id for item in adaptation_context.strong_related_topics[:4]],
                "module_evidence_coverage": adaptation_context.module_summary.get("evidence_coverage_ratio"),
            },
        }

    def _build_effective_projection(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        raw_mastery = float(snapshot.get("mastery_score", 0.0))
        last_evidence_at = snapshot["last_evidence_at"]
        now = self._now_provider()
        hours_since_last_evidence = max((now - last_evidence_at).total_seconds() / 3600.0, 0.0)
        decay_multiplier = self._decay_multiplier(last_evidence_at, now)
        effective_mastery = max(0.0, min(raw_mastery, raw_mastery * decay_multiplier))
        effective_status = "completed" if effective_mastery >= 0.8 else "in_progress"
        return {
            "learner_id": snapshot["learner_id"],
            "section_id": snapshot["section_id"],
            "module_id": snapshot.get("module_id"),
            "raw_mastery_score": raw_mastery,
            "effective_mastery_score": effective_mastery,
            "decay_multiplier": decay_multiplier,
            "hours_since_last_evidence": hours_since_last_evidence,
            "effective_status": effective_status,
            "evidence_count": int(snapshot.get("evidence_count", 0)),
            "last_assessment_decision": snapshot.get("last_assessment_decision"),
            "last_update_source": snapshot.get("last_update_source"),
            "last_evidence_at": last_evidence_at,
        }

    def _decay_multiplier(self, last_evidence_at: datetime, now: datetime) -> float:
        if not self.settings.mastery_decay_enabled:
            return 1.0
        grace_period = timedelta(hours=self.settings.mastery_decay_grace_period_hours)
        half_life = timedelta(days=self.settings.mastery_decay_half_life_days)
        if now <= last_evidence_at + grace_period:
            return 1.0
        if half_life <= timedelta(0):
            return 0.0
        age_since_grace = now - last_evidence_at - grace_period
        return 0.5 ** (age_since_grace / half_life)

    @staticmethod
    def _module_targets(
        stage_targets: list[dict[str, Any]],
        current_stage: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if current_stage is None:
            return []
        current_module_id = current_stage.get("module_id")
        if current_module_id is not None:
            module_targets = [dict(item) for item in stage_targets if item.get("module_id") == current_module_id]
            if module_targets:
                return module_targets
        return [dict(current_stage)]

    @staticmethod
    def _stage_mastery_view_from_projection(projection: dict[str, Any] | None) -> StageMasteryView | None:
        if projection is None:
            return None
        return StageMasteryView(
            section_id=str(projection["section_id"]),
            module_id=str(projection["module_id"]) if projection.get("module_id") is not None else None,
            raw_mastery_score=float(projection["raw_mastery_score"]),
            effective_mastery_score=float(projection["effective_mastery_score"]),
            effective_status=str(projection["effective_status"]),
            decay_multiplier=float(projection["decay_multiplier"]),
            hours_since_last_evidence=float(projection["hours_since_last_evidence"]),
            evidence_count=int(projection.get("evidence_count", 0)),
            last_assessment_decision=projection.get("last_assessment_decision"),
            last_update_source=projection.get("last_update_source"),
        )

    @staticmethod
    def _stage_signal(current_topic: StageMasteryView | None) -> str:
        if current_topic is None:
            return "new"
        if current_topic.last_assessment_decision in {"misconception", "procedural_error"}:
            return "needs_support"
        if current_topic.effective_mastery_score < 0.5:
            return "needs_support"
        if current_topic.effective_mastery_score < 0.85:
            return "progressing"
        return "ready"

    @staticmethod
    def _module_summary(
        *,
        total_module_topics: int,
        module_views: list[StageMasteryView],
    ) -> dict[str, float | int | None]:
        topics_with_evidence = len(module_views)
        if topics_with_evidence:
            average_effective_mastery = sum(item.effective_mastery_score for item in module_views) / topics_with_evidence
        else:
            average_effective_mastery = None
        return {
            "average_effective_mastery": average_effective_mastery,
            "topics_with_evidence": topics_with_evidence,
            "total_module_topics": total_module_topics,
            "evidence_coverage_ratio": 0.0 if total_module_topics <= 0 else topics_with_evidence / total_module_topics,
        }

    @staticmethod
    def _weak_related_topics(
        module_views: list[StageMasteryView],
        current_section_id: str | None,
    ) -> list[StageMasteryView]:
        rows = [
            item
            for item in module_views
            if item.section_id != current_section_id and item.effective_mastery_score < 0.6
        ]
        rows.sort(key=lambda item: (item.effective_mastery_score, item.hours_since_last_evidence))
        return rows[:3]

    @staticmethod
    def _strong_related_topics(
        module_views: list[StageMasteryView],
        current_section_id: str | None,
    ) -> list[StageMasteryView]:
        rows = [
            item
            for item in module_views
            if item.section_id != current_section_id and item.effective_mastery_score >= 0.85
        ]
        rows.sort(key=lambda item: (-item.effective_mastery_score, item.hours_since_last_evidence))
        return rows[:3]

    @staticmethod
    def _recent_evidence_pattern(recent_evidence: list[dict[str, Any]]) -> RecentEvidencePattern:
        latest_assessment_decision = next(
            (str(item.get("assessment_decision")) for item in recent_evidence if item.get("assessment_decision")),
            None,
        )
        correct_like_count = sum(
            1 for item in recent_evidence if item.get("assessment_decision") in {"correct", "partially_correct"}
        )
        support_needed_count = sum(
            1
            for item in recent_evidence
            if item.get("assessment_decision") in {"misconception", "procedural_error", "off_topic", "insufficient_evidence"}
        )
        fallback_confidence_count = sum(1 for item in recent_evidence if item.get("source_kind") == "feedback_confidence")
        return RecentEvidencePattern(
            correct_like_count=correct_like_count,
            support_needed_count=support_needed_count,
            fallback_confidence_count=fallback_confidence_count,
            latest_assessment_decision=latest_assessment_decision,
        )
