from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings, get_settings
from app.state.tutor_state_repository import TutorStateRepository
from app.tutor.plan import build_hierarchical_plan, load_book_data
from app.tutor.plan_projection import PlanProjectionService
from app.tutor.start_message import TutorMessageService

logger = logging.getLogger(__name__)


class TutorSessionService:
    def __init__(
        self,
        repo: TutorStateRepository,
        plan_projection: PlanProjectionService,
        message_service: TutorMessageService,
        book_json_path: str,
        settings: Settings | None = None,
    ):
        self.repo = repo
        self.plan_projection = plan_projection
        self.message_service = message_service
        self.book_json_path = book_json_path
        self.settings = settings or get_settings()
        self.default_template_id = "default_calc1"
        self.default_template_version = 2

    async def ensure_default_template(self) -> dict[str, Any]:
        existing = await self.repo.get_plan_template(self.default_template_id)
        plan_json = (existing or {}).get("plan_json") or {}
        if existing and plan_json.get("stage_targets") and plan_json.get("plan_tree"):
            return existing
        raise RuntimeError(
            "Default plan template is not initialized. Run the runtime bootstrap command before starting the app."
        )

    async def ensure_context(self, learner_id: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
        await self.repo.ensure_learner(learner_id)
        template = await self.ensure_default_template()
        targets = self.plan_projection.template_targets(template)
        state = await self.repo.get_or_create_learner_plan_state(
            learner_id=learner_id,
            template_id=template["id"],
            total_stages=len(targets),
        )
        current_stage = self.plan_projection.current_stage_from_state(state, targets)
        return template, state, targets, current_stage

    async def start_payload(self, learner_id: str) -> dict[str, Any]:
        template, state, targets, current_stage = await self.ensure_context(learner_id)
        total_stages = len(targets)
        plan = await self.plan_projection.build_plan_payload(
            template=template,
            state=state,
            current_stage=current_stage,
        )
        previous_stage, next_stage = self.adjacent_stages(targets, current_stage)
        return {
            "message": self.message_service.default_start_message(
                current_stage=current_stage,
                previous_stage=previous_stage,
                next_stage=next_stage,
                completed_count=int(state["completed_count"]),
                total_stages=total_stages,
                plan_completed=bool(state["plan_completed"]),
            ),
            "plan": plan,
            "current_stage": current_stage,
            "plan_completed": bool(state["plan_completed"]),
        }

    async def start_message_payload(self, learner_id: str) -> dict[str, Any]:
        template, state, targets, current_stage = await self.ensure_context(learner_id)
        previous_stage, next_stage = self.adjacent_stages(targets, current_stage)
        message = await self.message_service.get_start_message(
            learner_id=learner_id,
            template_id=str(template["id"]),
            current_stage=current_stage,
            previous_stage=previous_stage,
            next_stage=next_stage,
            completed_count=int(state["completed_count"]),
            total_stages=len(targets),
            plan_completed=bool(state["plan_completed"]),
        )
        return {
            "message": message,
            "current_stage": current_stage,
            "plan_completed": bool(state["plan_completed"]),
        }

    async def advance_payload(
        self,
        learner_id: str,
        force: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        _ = force
        template, state, targets, _current_stage = await self.ensure_context(learner_id)
        total_stages = len(targets)
        if total_stages == 0:
            plan = await self.plan_projection.build_plan_payload(template=template, state=state, current_stage=None)
            return (
                {
                    "message": "Plan completed.",
                    "current_stage": None,
                    "plan": plan,
                    "plan_completed": True,
                },
                None,
                str(template["id"]),
            )

        completed_count = int(state["completed_count"])
        current_stage_index = int(state["current_stage_index"])
        if completed_count < total_stages:
            completed_count += 1

        plan_completed = completed_count >= total_stages
        next_stage_index = total_stages - 1 if plan_completed else min(current_stage_index + 1, total_stages - 1)
        state = await self.repo.update_learner_plan_state(
            learner_id=learner_id,
            template_id=template["id"],
            current_stage_index=next_stage_index,
            completed_count=completed_count,
            plan_completed=plan_completed,
        )
        current_stage = self.plan_projection.current_stage_from_state(state, targets)
        plan = await self.plan_projection.build_plan_payload(
            template=template,
            state=state,
            current_stage=current_stage,
        )
        next_stage = self.next_stage(targets, current_stage)
        return (
            {
                "message": "Plan completed." if plan_completed else "Moved to next stage.",
                "current_stage": current_stage,
                "plan": plan,
                "plan_completed": plan_completed,
            },
            next_stage,
            str(template["id"]),
        )

    async def current_item(self, learner_id: str) -> dict[str, Any] | None:
        _template, _state, _targets, current_stage = await self.ensure_context(learner_id)
        return current_stage

    async def apply_feedback(
        self,
        learner_id: str,
        section_id: str | None,
        module_id: str | None,
        confidence: int,
    ) -> dict[str, Any]:
        stage = await self.current_item(learner_id)
        current_section_id = str(section_id or (stage or {}).get("section_id") or "")
        current_module_id = module_id or (stage or {}).get("module_id")
        if current_section_id:
            mastery_map = await self.plan_projection.mastery_map(learner_id)
            current_mastery = mastery_map.get(current_section_id, 0.0)
            new_mastery = self._clamp(current_mastery + self._confidence_delta(confidence))
            status = "completed" if new_mastery >= 0.8 else "in_progress"
            await self.repo.upsert_topic_progress(
                learner_id=learner_id,
                section_id=current_section_id,
                module_id=current_module_id,
                status=status,
                mastery_score=new_mastery,
            )
        return {
            "auto_advanced": False,
            "message": "Feedback saved. Continue when ready.",
            "current_stage": stage,
        }

    async def _create_default_template(self) -> dict[str, Any]:
        book_id, toc = load_book_data(self.book_json_path)
        plan = build_hierarchical_plan(toc)
        template = await self.repo.upsert_plan_template(
            template_id=self.default_template_id,
            book_id=book_id,
            version=self.default_template_version,
            plan_json={"book_id": book_id, **plan},
            is_active=True,
        )
        logger.info(
            "Created default plan template '%s' with %d stages.",
            template["id"],
            len(plan.get("stage_targets") or []),
        )
        return template

    async def bootstrap_default_template(self) -> dict[str, Any]:
        existing = await self.repo.get_plan_template(self.default_template_id)
        plan_json = (existing or {}).get("plan_json") or {}
        if existing and plan_json.get("stage_targets") and plan_json.get("plan_tree"):
            return existing
        return await self._create_default_template()

    @staticmethod
    def adjacent_stages(
        targets: list[dict[str, Any]],
        current_stage: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not current_stage:
            return None, None
        index = int(current_stage["stage_index"])
        previous_stage = dict(targets[index - 1]) if index > 0 else None
        next_stage = dict(targets[index + 1]) if index + 1 < len(targets) else None
        return previous_stage, next_stage

    @staticmethod
    def next_stage(targets: list[dict[str, Any]], current_stage: dict[str, Any] | None) -> dict[str, Any] | None:
        if not current_stage:
            return None
        index = int(current_stage["stage_index"])
        if index + 1 >= len(targets):
            return None
        return dict(targets[index + 1])

    @staticmethod
    def _confidence_delta(confidence: int) -> float:
        if confidence >= 4:
            return 0.20
        if confidence == 3:
            return 0.05
        return -0.10

    @staticmethod
    def _clamp(value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value
