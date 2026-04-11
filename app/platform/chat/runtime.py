from __future__ import annotations

import logging
from typing import Any

from app.platform.config import Settings
from app.platform.rag.grounded_answer_runtime import GroundedAnswerRuntime
from app.platform.chat.interaction_repository import InteractionRepository
from app.platform.logging import log_event
from app.state.repositories.learner_repository import LearnerStateRepository
from app.teacher.models import TeacherChatPlan, TeacherTurnContext

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        *,
        interaction_repository: InteractionRepository,
        learner_state_repository: LearnerStateRepository,
        teacher_chat_planner: Any | None = None,
        grounded_answer_runtime: GroundedAnswerRuntime,
        settings: Settings,
    ):
        self.interaction_repository = interaction_repository
        self.learner_state_repository = learner_state_repository
        self.teacher_chat_planner = teacher_chat_planner
        self.grounded_answer_runtime = grounded_answer_runtime
        self.settings = settings

    async def execute_chat_request(
        self,
        request: Any,
        *,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        await self.learner_state_repository.ensure_learner(request.learner_id)
        resolved_session_id = session_id
        if resolved_session_id is None:
            resolved_session_id = await self.interaction_repository.get_or_create_session(request.learner_id)

        module_id = request.context.current_module_id
        section_id = request.context.current_section_id
        filters = {
            "module_id": None,
            "section_id": None,
            "doc_type": "section",
        }
        teacher_surface_instruction = None
        teacher_policy_brief = None
        grounding_analysis = None
        chat_plan: TeacherChatPlan | None = None
        context_pack: TeacherTurnContext | None = None
        request_context_json = getattr(request, "teacher_context_json", None)
        if isinstance(request_context_json, dict):
            try:
                context_pack = TeacherTurnContext.model_validate(request_context_json)
            except Exception:
                context_pack = None
        if context_pack is None and self.teacher_chat_planner is not None:
            context_pack = await self.teacher_chat_planner.context_builder.build_chat_turn_context(
                learner_id=request.learner_id,
                module_id=module_id,
                section_id=section_id,
                learner_message=request.message,
            )

        try:
            retrieval_result = await self.grounded_answer_runtime.evaluate_retrieval(
                message=request.message,
                filters=filters,
                context={
                    "module_id": module_id,
                    "section_id": section_id,
                },
            )
            if context_pack is not None and self.teacher_chat_planner is not None:
                try:
                    chat_plan = await self.teacher_chat_planner.build_teacher_chat_plan(
                        context_pack,
                        retrieval_result=retrieval_result,
                    )
                    teacher_surface_instruction = chat_plan.surface_instruction
                    teacher_policy_brief = chat_plan.policy_brief
                    grounding_analysis = chat_plan.grounding_analysis
                    grounded_stage = context_pack.current_stage or {}
                    derived_memory = None if context_pack.working_turn_context is None else context_pack.working_turn_context.derived_memory
                    log_event(
                        logger,
                        "chat.teacher_plan_selected",
                        learner_id=request.learner_id,
                        stage_index=int(grounded_stage.get("stage_index", -1)),
                        section_id=str(grounded_stage.get("section_id") or ""),
                        teacher_action=chat_plan.teacher_action.action_type.value,
                        stage_signal=None
                        if derived_memory is None or derived_memory.adaptation_context is None
                        else derived_memory.adaptation_context.stage_signal,
                        has_grounding_analysis=grounding_analysis is not None,
                    )
                except Exception as exc:
                    grounded_stage = context_pack.current_stage or {}
                    log_event(
                        logger,
                        "chat.teacher_plan_fallback",
                        learner_id=request.learner_id,
                        stage_index=int(grounded_stage.get("stage_index", -1)),
                        section_id=str(grounded_stage.get("section_id") or ""),
                        error=str(exc),
                    )
            grounded_result = await self.grounded_answer_runtime.answer_from_retrieval(
                message=request.message,
                retrieval_result=retrieval_result,
                teacher_surface_instruction=teacher_surface_instruction,
                teacher_policy_brief=teacher_policy_brief,
                grounding_analysis=grounding_analysis,
            )
        except RuntimeError as exc:
            raise RuntimeError(str(exc)) from exc

        interaction_id = await self.interaction_repository.create_interaction_with_sources(
            learner_id=request.learner_id,
            session_id=resolved_session_id,
            message=request.message,
            answer=grounded_result["answer_md"],
            module_id=module_id,
            section_id=section_id,
            sources=[
                {
                    "chunk_id": chunk["chunk_id"],
                    "score": chunk.get("score"),
                    "rank": idx,
                }
                for idx, chunk in enumerate(grounded_result["chunks"])
            ],
        )

        retrieval_debug = grounded_result["debug"] if self.settings.enable_retrieval_debug else None
        return {
            "interaction_id": interaction_id,
            "answer_md": grounded_result["answer_md"],
            "citations": grounded_result["citations"],
            "retrieval_debug": retrieval_debug,
        }

    async def record_teacher_reply(
        self,
        *,
        learner_id: str,
        message: str,
        answer_md: str,
        module_id: str | None,
        section_id: str | None,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        await self.learner_state_repository.ensure_learner(learner_id)
        resolved_session_id = session_id
        if resolved_session_id is None:
            resolved_session_id = await self.interaction_repository.get_or_create_session(learner_id)
        interaction_id = await self.interaction_repository.create_interaction_with_sources(
            learner_id=learner_id,
            session_id=resolved_session_id,
            message=message,
            answer=answer_md,
            module_id=module_id,
            section_id=section_id,
            sources=[],
        )
        return {
            "interaction_id": interaction_id,
            "answer_md": answer_md,
            "citations": [],
            "retrieval_debug": None,
        }

    @staticmethod
    def _request_payload_json(request: Any) -> dict[str, Any]:
        return {
            "learner_id": request.learner_id,
            "message": request.message,
            "context": {
                "current_module_id": request.context.current_module_id,
                "current_section_id": request.context.current_section_id,
            },
        }


__all__ = ["ChatService"]
