from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.platform.cache import RedisCache
from app.platform.chat.repository import DurableChatRepository
from app.platform.chat.transport_models import ChatTurnRequest
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
        chat_repository: DurableChatRepository,
        interaction_repository: InteractionRepository,
        learner_state_repository: LearnerStateRepository,
        teacher_chat_planner: Any | None = None,
        grounded_answer_runtime: GroundedAnswerRuntime,
        redis_cache: RedisCache | None = None,
        settings: Settings,
        task_publisher: Any | None = None,
    ):
        self.chat_repository = chat_repository
        self.interaction_repository = interaction_repository
        self.learner_state_repository = learner_state_repository
        self.teacher_chat_planner = teacher_chat_planner
        self.grounded_answer_runtime = grounded_answer_runtime
        self.redis_cache = redis_cache
        self.settings = settings
        self.task_publisher = task_publisher

    async def chat(
        self,
        request: Any,
        *,
        request_id: str | None = None,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.durable_chat_enabled:
            return await self._execute_chat_request(request)

        await self.learner_state_repository.ensure_learner(request.learner_id)
        session_id = await self.interaction_repository.get_or_create_session(request.learner_id)
        request_key = await self._derive_request_key(
            request,
            session_id=session_id,
            request_id=request_id,
            client_request_id=client_request_id,
        )

        bundle = await self.chat_repository.create_chat_turn_bundle(
            request_key=request_key,
            learner_id=request.learner_id,
            session_id=session_id,
            module_id=request.context.current_module_id,
            section_id=request.context.current_section_id,
            request_payload_json=self._request_payload_json(request),
        )
        turn = bundle["turn"]
        if turn.get("state") == "completed" and turn.get("final_result_json") is not None:
            return dict(turn["final_result_json"])

        if turn.get("state") == "failed":
            fallback_reason = str(turn.get("error_message") or "worker_failed")
            await self.chat_repository.mark_chat_turn_degraded(turn["id"], fallback_reason=fallback_reason)
            return await self.execute_durable_chat_turn(turn["id"], degrade_reason=fallback_reason)

        if bundle["created"] or turn.get("state") == "accepted":
            published, publish_reason = await self._publish_chat_turn(bundle)
            if not published:
                await self.chat_repository.mark_chat_turn_degraded(turn["id"], fallback_reason=publish_reason)
                return await self.execute_durable_chat_turn(turn["id"], degrade_reason=publish_reason)

        result = await self._wait_for_turn_completion(turn["id"])
        if result is not None:
            return result

        raise RuntimeError("Chat generation is still in progress. Retry the same request to resume.")

    async def execute_durable_chat_turn(
        self,
        turn_id: str,
        *,
        degrade_reason: str | None = None,
    ) -> dict[str, Any]:
        claim = await self.chat_repository.claim_chat_turn_execution(turn_id)
        status = str(claim.get("status") or "")
        if status == "completed":
            turn = claim.get("turn") or {}
            result = turn.get("final_result_json")
            if isinstance(result, dict):
                return dict(result)
            stored_result = await self.chat_repository.get_teacher_job_result(turn_id)
            if stored_result is not None:
                return dict(stored_result["result_payload_json"])
            raise RuntimeError("Completed chat turn has no persisted result.")
        if status == "busy":
            result = await self._wait_for_turn_completion(turn_id)
            if result is not None:
                return result
            raise RuntimeError("Chat generation is already in progress for this request.")
        if status == "missing":
            raise RuntimeError("Durable chat turn not found.")

        turn = claim.get("turn") or {}
        request_payload = turn.get("request_payload_json") or {}
        request_model = ChatTurnRequest.model_validate(request_payload)
        try:
            result = await self._execute_chat_request(request_model, session_id=turn.get("session_id"))
        except Exception as exc:
            await self.chat_repository.mark_chat_turn_failed(turn_id, error_message=str(exc))
            raise

        completed_turn = await self.chat_repository.complete_chat_turn(
            turn_id=turn_id,
            final_interaction_id=int(result["interaction_id"]),
            final_result_json=result,
            worker_metadata_json={
                "degraded_execution": degrade_reason is not None,
                "fallback_reason": degrade_reason,
            },
        )
        if completed_turn is None:
            raise RuntimeError("Failed to finalize durable chat turn.")
        return result

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

    async def _execute_chat_request(
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

    async def _publish_chat_turn(self, bundle: dict[str, Any]) -> tuple[bool, str]:
        turn = bundle["turn"]
        pending_events = await self.chat_repository.list_pending_outbox_events(
            aggregate_type="chat_turn",
            aggregate_id=str(turn["id"]),
            event_kind="chat_generation",
        )
        if not pending_events:
            return True, "already_published"

        job = bundle.get("job") or await self.chat_repository.get_teacher_job_for_turn(str(turn["id"]))
        if job is None:
            return False, "missing_job"

        for event in pending_events:
            try:
                broker_message_id = await self._enqueue_chat_generation_task(
                    turn_id=str(turn["id"]),
                    job_id=str(job["id"]),
                )
            except Exception as exc:
                reason = f"broker_publish_failed:{exc}"
                await self.chat_repository.mark_outbox_event_failed(int(event["id"]), reason)
                return False, reason
            await self.chat_repository.mark_outbox_event_published(int(event["id"]), broker_message_id)
            await self.chat_repository.mark_chat_turn_queued(str(turn["id"]), broker_message_id=broker_message_id)
        return True, "published"

    async def _enqueue_chat_generation_task(self, *, turn_id: str, job_id: str) -> str:
        if self.task_publisher is not None:
            maybe_result = self.task_publisher(turn_id, job_id)
            if asyncio.iscoroutine(maybe_result):
                maybe_result = await maybe_result
            return str(maybe_result)
        from app.platform.chat.tasks import enqueue_chat_generation_task

        return await asyncio.to_thread(enqueue_chat_generation_task, turn_id=turn_id, job_id=job_id)

    async def _wait_for_turn_completion(self, turn_id: str) -> dict[str, Any] | None:
        # Initial DB check — covers the race where the worker finishes before we subscribe
        turn = await self.chat_repository.get_chat_turn(turn_id)
        if turn is not None and turn.get("state") == "completed" and turn.get("final_result_json") is not None:
            return dict(turn["final_result_json"])
        if turn is not None and turn.get("state") == "failed":
            raise RuntimeError(str(turn.get("error_message") or "Chat generation failed."))

        deadline = time.monotonic() + max(0.0, float(self.settings.chat_turn_inline_wait_seconds))

        # Try Redis pub/sub notification first
        if self.redis_cache is not None and self.redis_cache.available:
            result = await self._wait_via_pubsub(turn_id, deadline)
            if result is not None:
                return result

        # Fallback: slow polling (2s default interval)
        return await self._wait_via_polling(turn_id, deadline)

    async def _wait_via_pubsub(self, turn_id: str, deadline: float) -> dict[str, Any] | None:
        channel = f"chat_turn:{turn_id}"
        pubsub = await self.redis_cache.subscribe(channel)
        if pubsub is None:
            return None
        try:
            while time.monotonic() <= deadline:
                remaining = max(0.1, deadline - time.monotonic())
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining),
                    timeout=remaining + 1.0,
                )
                if msg is not None and msg.get("type") == "message":
                    turn = await self.chat_repository.get_chat_turn(turn_id)
                    if turn is not None and turn.get("state") == "completed" and turn.get("final_result_json") is not None:
                        return dict(turn["final_result_json"])
                    if turn is not None and turn.get("state") == "failed":
                        raise RuntimeError(str(turn.get("error_message") or "Chat generation failed."))
        except asyncio.TimeoutError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        return None

    async def _wait_via_polling(self, turn_id: str, deadline: float) -> dict[str, Any] | None:
        poll_interval = max(0.5, float(self.settings.chat_turn_poll_interval_seconds))
        while time.monotonic() <= deadline:
            await asyncio.sleep(poll_interval)
            turn = await self.chat_repository.get_chat_turn(turn_id)
            if turn is not None and turn.get("state") == "completed" and turn.get("final_result_json") is not None:
                return dict(turn["final_result_json"])
            if turn is not None and turn.get("state") == "failed":
                raise RuntimeError(str(turn.get("error_message") or "Chat generation failed."))
        return None

    async def _derive_request_key(
        self,
        request: Any,
        *,
        session_id: int,
        request_id: str | None,
        client_request_id: str | None,
    ) -> str:
        if client_request_id:
            payload = {
                "learner_id": request.learner_id,
                "session_id": session_id,
                "request_id": client_request_id,
            }
            return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

        recent_turns = await self.chat_repository.list_recent_turns_for_request(
            learner_id=request.learner_id,
            module_id=request.context.current_module_id,
            section_id=request.context.current_section_id,
            message=request.message,
            created_after=datetime.now(timezone.utc)
            - timedelta(seconds=float(self.settings.chat_turn_retry_window_seconds)),
        )
        if recent_turns:
            return str(recent_turns[0]["request_key"])

        payload = {
            "learner_id": request.learner_id,
            "session_id": session_id,
            "message": request.message,
            "module_id": request.context.current_module_id,
            "section_id": request.context.current_section_id,
            "request_id": request_id,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

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
