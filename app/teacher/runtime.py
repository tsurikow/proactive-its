"""
Teacher runtime — SGR engine orchestration.

Clean flow:
1. Load state context
2. Classify intent (if learner reply) — 1 SGR call
3. Handle the classified intent:
   a. Task answer → evaluate_answer → (weak_answer_plan if not correct)
   b. Content question → grounded reply via ChatService
   c. Navigation → advance/revisit/continue
   d. Everything else → plan_teacher_turn
4. Return result

All LLM decisions go through TeacherEngine (3-5 SGR calls per turn).
State management reuses existing infrastructure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from app.api.schemas import LessonPayload
from app.platform.config import Settings, get_settings
from app.platform.chat.transport_models import ChatTurnContext, ChatTurnRequest
from app.state.models import AdaptationContext
from app.state.repositories.session_repository import SessionStateRepository
from app.state.services.learner_service import LearnerService
from app.state.services.service import TeacherStateService
from app.state.stage_state import public_stage, stage_by_section_id
from app.teacher.engine import TeacherEngine
from app.teacher.models import (
    CheckpointEvaluation,
    CheckpointEvaluationStatus,
    InteractionRouteType,
    LearnerNavigationAction,
    LearnerTurnIntentType,
    LearningDebtItem,
    LearningDebtKind,
    PendingTeacherTask,
    RepairHistorySummary,
    SectionUnderstandingArtifact,
    TeacherAction,
    TeacherActionType,
    TeacherProposal,
    TeacherProposalType,
    TeacherSessionEventType,
    TeacherSessionRequest,
    TeacherSessionResult,
)
from app.teacher.planning.pending_task_runtime import PendingTaskRuntime
from app.teacher.planning.section_understanding_service import SectionUnderstandingService
from app.teacher.schemas import AnswerEvaluation, IntentAndRoute, TeacherTurn, WeakAnswerPlan

logger = logging.getLogger(__name__)


class TeacherRuntime:
    """
    Teacher runtime using SGR engine.

    Orchestrates the teacher-student conversation with 3-5 LLM calls per turn
    instead of the old 13+ agent calls.
    """

    def __init__(
        self,
        *,
        engine: TeacherEngine,
        state_service: TeacherStateService,
        session_repository: SessionStateRepository,
        learner_service: LearnerService,
        section_understanding_service: SectionUnderstandingService,
        pending_task_runtime: PendingTaskRuntime,
        artifact_runtime: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.engine = engine
        self.state_service = state_service
        self.session_repository = session_repository
        self.learner_service = learner_service
        self.section_understanding_service = section_understanding_service
        self.pending_task_runtime = pending_task_runtime
        self.artifact_runtime = artifact_runtime
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------

    async def _get_conversation_history(
        self,
        learner_id: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, str]]:
        """Build a sliding window of recent conversation turns."""
        rows = await self.session_repository.list_recent_teacher_session_events(
            learner_id=learner_id,
            limit=limit,
        )
        history: list[dict[str, str]] = []
        for row in reversed(rows):  # oldest first
            msg = row.get("message")
            if msg:
                history.append({"role": "learner", "text": str(msg)})
            payload = row.get("event_payload_json") or {}
            teacher_action = payload.get("teacher_action") or {}
            teacher_msg = teacher_action.get("prompt_instruction")
            if teacher_msg:
                history.append({"role": "teacher", "text": str(teacher_msg)})
        return history[-limit * 2:]  # cap to ~20 messages

    # ------------------------------------------------------------------
    # Learner memory (persistent)
    # ------------------------------------------------------------------

    async def _get_learner_memory(
        self,
        learner_id: str,
        template_id: str,
    ) -> dict[str, Any] | None:
        """Load persistent learner memory from DB."""
        row = await self.session_repository.get_learner_memory(
            learner_id,
            template_id=template_id,
        )
        if row is None:
            return None
        return row.get("memory_json")

    # ------------------------------------------------------------------
    # Memory synthesis (fire-and-forget after response)
    # ------------------------------------------------------------------

    _MEMORY_SYNC_INTERVAL: int = 5  # synthesize every N turns

    async def _maybe_synthesize_memory(
        self,
        *,
        learner_id: str,
        template_id: str,
        event_type: TeacherSessionEventType,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None,
    ) -> None:
        """Fire-and-forget: synthesize learner memory periodically.

        Triggers on session-ending events or every N turns.
        Errors are logged but never propagate to the caller.
        """
        session_end_events = {
            TeacherSessionEventType.OPEN_SESSION,
            TeacherSessionEventType.CONTINUE_SESSION,
        }
        is_session_start = event_type in session_end_events

        # Only synthesize if enough conversation happened
        turn_count = len([m for m in conversation_history if m["role"] == "learner"])
        should_run = (
            turn_count > 0
            and (turn_count % self._MEMORY_SYNC_INTERVAL == 0 or is_session_start)
        )
        if not should_run:
            return

        try:
            # Gather evaluation evidence from recent events
            rows = await self.session_repository.list_recent_teacher_session_events(
                learner_id=learner_id, limit=20,
            )
            evaluation_results: list[dict[str, Any]] = []
            sections_seen: list[str] = []
            for row in rows:
                payload = row.get("event_payload_json") or {}
                cp_eval = payload.get("checkpoint_evaluation")
                if isinstance(cp_eval, dict) and cp_eval.get("status"):
                    evaluation_results.append(cp_eval)
                sid = (row.get("section_id") or payload.get("section_id") or "")
                if sid and sid not in sections_seen:
                    sections_seen.append(str(sid))

            debt_rows = await self.session_repository.list_open_learning_debt(
                learner_id,
            )
            learning_debt = [dict(r) for r in debt_rows] if debt_rows else []

            sgr = await self.engine.synthesize_memory(
                current_memory=learner_memory,
                session_interactions=conversation_history,
                sections_covered=sections_seen,
                evaluation_results=evaluation_results,
                learning_debt=learning_debt,
            )

            memory_json = sgr.model_dump(exclude={"session_evidence_read", "pattern_analysis"})
            await self.session_repository.upsert_learner_memory(
                learner_id=learner_id,
                template_id=template_id,
                memory_json=memory_json,
            )
            logger.info("Learner memory synthesized for %s (turn %d)", learner_id, turn_count)

        except Exception:
            logger.warning("Memory synthesis failed for %s — non-critical, skipping", learner_id, exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _teacher_action_from_turn(
        turn: TeacherTurn,
        *,
        current_stage: dict[str, Any] | None,
    ) -> TeacherAction:
        """Convert SGR TeacherTurn output to domain TeacherAction."""
        stage = current_stage or {}
        return TeacherAction(
            action_type=turn.action_type,
            rationale=turn.pedagogical_reasoning[:1200],
            section_id=str(stage.get("section_id") or ""),
            module_id=stage.get("module_id"),
            question_prompt=turn.question_prompt,
            exercise_ref=turn.exercise_ref,
            prompt_instruction=turn.teacher_message,
        )

    @staticmethod
    def _proposal_from_turn(turn: TeacherTurn) -> TeacherProposal | None:
        """Convert SGR proposal output to domain TeacherProposal."""
        if turn.proposal is None:
            return None
        return TeacherProposal(
            proposal_type=turn.proposal.proposal_type,
            rationale=turn.proposal.rationale[:1200],
            target_section_id=turn.proposal.target_section_id,
            target_title=turn.proposal.target_title,
            can_defer=turn.proposal.can_defer,
        )

    @staticmethod
    def _evaluation_from_sgr(
        sgr: AnswerEvaluation,
        *,
        pending_task: PendingTeacherTask,
    ) -> CheckpointEvaluation:
        """Convert SGR AnswerEvaluation to domain CheckpointEvaluation."""
        return CheckpointEvaluation(
            status=sgr.status,
            section_id=pending_task.section_id,
            exercise_ref=pending_task.item_ref if pending_task.item_ref else None,
            evaluator_source="teacher_graph",
            hidden_answer_used=pending_task.answer_check_context is not None,
            learner_claim_brief=sgr.learner_claim_brief[:300] if sgr.learner_claim_brief else None,
            source_alignment=sgr.source_alignment[:300] if sgr.source_alignment else None,
            missing_or_wrong_piece=sgr.missing_or_wrong_piece[:300] if sgr.missing_or_wrong_piece else None,
            rationale=sgr.verdict_basis[:300],
            teacher_feedback_brief=sgr.teacher_feedback_brief[:300],
            confidence=sgr.confidence,
        )

    # ------------------------------------------------------------------
    # Main entry point (API-side durable wrapper)
    # ------------------------------------------------------------------

    def validate_request(self, request: TeacherSessionRequest) -> None:
        """Validate request preconditions, raise RuntimeError on failure."""
        if (
            request.event_type == TeacherSessionEventType.LEARNER_REPLY
            and not request.message
            and request.learner_signal is None
        ):
            raise RuntimeError("message_required")
        if not self.engine.is_available():
            raise RuntimeError("llm_unavailable")

    async def dispatch_or_inline(
        self,
        request: TeacherSessionRequest,
        *,
        chat_service: Any | None = None,
        client_request_id: str | None = None,
    ) -> tuple[str | None, TeacherSessionResult | None]:
        """Create bundle, publish to Celery. Returns (turn_id, None) on success,
        or (turn_id, result) if completed/degraded inline."""
        if not self.settings.durable_chat_enabled:
            result = await self.execute_session_inner(request, chat_service=chat_service)
            return None, result

        from app.api.dependencies import get_durable_chat_repository

        repo = get_durable_chat_repository()
        request_key = self._derive_session_request_key(request, client_request_id=client_request_id)

        bundle = await repo.create_chat_turn_bundle(
            request_key=request_key,
            learner_id=request.learner_id,
            session_id=None,
            module_id=request.context.current_module_id,
            section_id=request.context.current_section_id,
            request_payload_json=request.model_dump(mode="json"),
            job_kind="teacher_session",
            event_kind="teacher_session",
        )

        turn = bundle["turn"]

        # Idempotency: already completed
        if turn.get("state") == "completed" and turn.get("final_result_json"):
            return turn["id"], TeacherSessionResult.model_validate(turn["final_result_json"])

        # Failed previously: degrade to inline
        if turn.get("state") == "failed":
            await repo.mark_chat_turn_degraded(turn["id"], fallback_reason="retry_after_failure")
            result = await self.execute_session_inner(request, chat_service=chat_service)
            await repo.complete_chat_turn(
                turn_id=turn["id"],
                final_interaction_id=None,
                final_result_json=result.model_dump(mode="json"),
                worker_metadata_json={"degraded_execution": True},
            )
            return turn["id"], result

        # Publish to Celery
        if bundle["created"] or turn.get("state") == "accepted":
            published, reason = await self._publish_session_turn(bundle, repo)
            if not published:
                await repo.mark_chat_turn_degraded(turn["id"], fallback_reason=reason)
                result = await self.execute_session_inner(request, chat_service=chat_service)
                await repo.complete_chat_turn(
                    turn_id=turn["id"],
                    final_interaction_id=None,
                    final_result_json=result.model_dump(mode="json"),
                    worker_metadata_json={"degraded_execution": True, "fallback_reason": reason},
                )
                return turn["id"], result

        return turn["id"], None

    async def execute_session(
        self,
        request: TeacherSessionRequest,
        *,
        chat_service: Any | None = None,
        request_id: str | None = None,
        client_request_id: str | None = None,
    ) -> TeacherSessionResult:
        """API-side entry: validate, dispatch to Celery worker, wait for result."""
        self.validate_request(request)

        turn_id, result = await self.dispatch_or_inline(
            request, chat_service=chat_service, client_request_id=client_request_id,
        )
        if result is not None:
            return result

        # Wait for worker completion
        from app.api.dependencies import get_durable_chat_repository, get_redis_cache

        result_json = await self._wait_for_session_completion(turn_id, get_durable_chat_repository(), get_redis_cache())
        if result_json is not None:
            return TeacherSessionResult.model_validate(result_json)

        raise RuntimeError("Teacher session is still in progress. Retry the same request.")

    # ------------------------------------------------------------------
    # Inner execution (runs in Celery worker or inline as fallback)
    # ------------------------------------------------------------------

    async def execute_session_inner(
        self,
        request: TeacherSessionRequest,
        *,
        chat_service: Any | None = None,
    ) -> TeacherSessionResult:
        """Execute one teacher session turn (all LLM work happens here)."""

        # 1. Load state context
        template, state, targets, current_stage_raw, adaptation_context = (
            await self.state_service.ensure_context(request.learner_id)
        )
        template_id = str(template["id"])
        current_stage = public_stage(current_stage_raw)

        # 2. Load section understanding (cached)
        section_understanding, _ = (
            await self.section_understanding_service.get_or_create_section_understanding(
                learner_id=request.learner_id,
                template_id=template_id,
                current_stage=current_stage_raw,
                adaptation_context=adaptation_context,
            )
        )

        # 3. Load pending task
        current_pending_task = await self.pending_task_runtime.resolve_pending_task(
            learner_id=request.learner_id,
            current_stage=current_stage_raw,
            section_understanding=section_understanding,
        )

        # 4. Load conversation history and learner memory
        conversation_history = await self._get_conversation_history(request.learner_id)
        learner_memory = await self._get_learner_memory(request.learner_id, template_id)

        # 5. Resolve recent proposal
        recent_proposal = await self._resolve_recent_proposal(
            learner_id=request.learner_id,
            proposal_type=request.proposal_type,
        )

        # 6. Dispatch based on event type
        if request.event_type == TeacherSessionEventType.LEARNER_REPLY:
            result = await self._handle_learner_reply(
                request=request,
                chat_service=chat_service,
                template=template,
                state=state,
                targets=targets,
                current_stage_raw=current_stage_raw,
                adaptation_context=adaptation_context,
                section_understanding=section_understanding,
                current_pending_task=current_pending_task,
                conversation_history=conversation_history,
                learner_memory=learner_memory,
                recent_proposal=recent_proposal,
            )
        else:
            result = await self._handle_non_reply_event(
                request=request,
                template=template,
                state=state,
                targets=targets,
                current_stage_raw=current_stage_raw,
                adaptation_context=adaptation_context,
                section_understanding=section_understanding,
                current_pending_task=current_pending_task,
                conversation_history=conversation_history,
                learner_memory=learner_memory,
                recent_proposal=recent_proposal,
            )

        # 7. Memory synthesis — Celery (durable) or asyncio (fallback)
        if self.settings.durable_chat_enabled:
            try:
                from app.platform.chat.tasks import enqueue_memory_synthesis_task

                await asyncio.to_thread(
                    enqueue_memory_synthesis_task,
                    learner_id=request.learner_id,
                    template_id=template_id,
                    event_type=request.event_type.value,
                    conversation_history=conversation_history,
                    learner_memory=learner_memory,
                )
            except Exception:
                logger.warning("Celery memory synthesis enqueue failed, falling back to asyncio", exc_info=True)
                asyncio.create_task(
                    self._maybe_synthesize_memory(
                        learner_id=request.learner_id,
                        template_id=template_id,
                        event_type=request.event_type,
                        conversation_history=conversation_history,
                        learner_memory=learner_memory,
                    ),
                    name=f"memory-sync-{request.learner_id}",
                )
        else:
            asyncio.create_task(
                self._maybe_synthesize_memory(
                    learner_id=request.learner_id,
                    template_id=template_id,
                    event_type=request.event_type,
                    conversation_history=conversation_history,
                    learner_memory=learner_memory,
                ),
                name=f"memory-sync-{request.learner_id}",
            )

        return result

    # ------------------------------------------------------------------
    # Durable dispatch helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_session_request_key(
        request: TeacherSessionRequest,
        *,
        client_request_id: str | None,
    ) -> str:
        payload = {
            "learner_id": request.learner_id,
            "event_type": request.event_type.value,
            "message": request.message,
            "module_id": request.context.current_module_id,
            "section_id": request.context.current_section_id,
            "client_request_id": client_request_id,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    async def _publish_session_turn(
        bundle: dict[str, Any],
        repo: Any,
    ) -> tuple[bool, str]:
        turn = bundle["turn"]
        pending_events = await repo.list_pending_outbox_events(
            aggregate_type="chat_turn",
            aggregate_id=str(turn["id"]),
            event_kind="teacher_session",
        )
        if not pending_events:
            return True, "already_published"

        job = bundle.get("job") or await repo.get_teacher_job_for_turn(str(turn["id"]))
        if job is None:
            return False, "missing_job"

        for event in pending_events:
            try:
                from app.platform.chat.tasks import enqueue_teacher_session_task

                broker_message_id = await asyncio.to_thread(
                    enqueue_teacher_session_task,
                    turn_id=str(turn["id"]),
                    job_id=str(job["id"]),
                )
            except Exception as exc:
                reason = f"broker_publish_failed:{exc}"
                await repo.mark_outbox_event_failed(int(event["id"]), reason)
                return False, reason
            await repo.mark_outbox_event_published(int(event["id"]), broker_message_id)
            await repo.mark_chat_turn_queued(str(turn["id"]), broker_message_id=broker_message_id)
        return True, "published"

    @staticmethod
    async def _wait_for_session_completion(
        turn_id: str,
        repo: Any,
        redis_cache: Any,
    ) -> dict[str, Any] | None:
        from app.platform.config import get_settings

        settings = get_settings()

        # Initial DB check — covers race where worker finishes before we subscribe
        turn = await repo.get_chat_turn(turn_id)
        if turn is not None and turn.get("state") == "completed" and turn.get("final_result_json") is not None:
            return dict(turn["final_result_json"])
        if turn is not None and turn.get("state") == "failed":
            raise RuntimeError(str(turn.get("error_message") or "Teacher session failed."))

        deadline = time.monotonic() + max(0.0, float(settings.chat_turn_inline_wait_seconds))

        # Try Redis pub/sub first
        if redis_cache is not None and redis_cache.available:
            channel = f"chat_turn:{turn_id}"
            pubsub = await redis_cache.subscribe(channel)
            if pubsub is not None:
                try:
                    while time.monotonic() <= deadline:
                        remaining = max(0.1, deadline - time.monotonic())
                        msg = await asyncio.wait_for(
                            pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining),
                            timeout=remaining + 1.0,
                        )
                        if msg is not None and msg.get("type") == "message":
                            turn = await repo.get_chat_turn(turn_id)
                            if turn is not None and turn.get("state") == "completed" and turn.get("final_result_json") is not None:
                                return dict(turn["final_result_json"])
                            if turn is not None and turn.get("state") == "failed":
                                raise RuntimeError(str(turn.get("error_message") or "Teacher session failed."))
                except asyncio.TimeoutError:
                    pass
                finally:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()

        # Fallback: DB polling
        poll_interval = max(0.5, float(settings.chat_turn_poll_interval_seconds))
        while time.monotonic() <= deadline:
            await asyncio.sleep(poll_interval)
            turn = await repo.get_chat_turn(turn_id)
            if turn is not None and turn.get("state") == "completed" and turn.get("final_result_json") is not None:
                return dict(turn["final_result_json"])
            if turn is not None and turn.get("state") == "failed":
                raise RuntimeError(str(turn.get("error_message") or "Teacher session failed."))
        return None

    # ------------------------------------------------------------------
    # Handle learner reply
    # ------------------------------------------------------------------

    async def _handle_learner_reply(
        self,
        *,
        request: TeacherSessionRequest,
        chat_service: Any | None,
        template: dict[str, Any],
        state: dict[str, Any],
        targets: list[dict[str, Any]],
        current_stage_raw: dict[str, Any] | None,
        adaptation_context: AdaptationContext | None,
        section_understanding: SectionUnderstandingArtifact | None,
        current_pending_task: PendingTeacherTask | None,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None,
        recent_proposal: TeacherProposal | None,
    ) -> TeacherSessionResult:
        learner_message = request.message or ""
        template_id = str(template["id"])
        current_stage = public_stage(current_stage_raw)

        # Step 1: Classify intent via LLM
        intent_result = await self.engine.classify_intent(
            learner_message=learner_message,
            current_stage=current_stage,
            pending_task=current_pending_task,
            recent_proposal=recent_proposal,
            section_understanding=section_understanding,
            conversation_history=conversation_history,
            learner_memory=learner_memory,
        )

        # Step 2: Dispatch based on classified intent
        intent = intent_result.intent_type

        # --- Task answer ---
        if intent == LearnerTurnIntentType.TASK_ANSWER and current_pending_task is not None:
            return await self._handle_task_answer(
                request=request,
                template=template,
                state=state,
                targets=targets,
                current_stage_raw=current_stage_raw,
                section_understanding=section_understanding,
                pending_task=current_pending_task,
                conversation_history=conversation_history,
                learner_memory=learner_memory,
            )

        # --- Content question (needs RAG) ---
        if intent == LearnerTurnIntentType.CONTENT_QUESTION:
            if intent_result.route_type == InteractionRouteType.GROUNDED_REPLY and chat_service is not None:
                return await self._handle_grounded_reply(
                    request=request,
                    chat_service=chat_service,
                    template=template,
                    state=state,
                    targets=targets,
                    current_stage_raw=current_stage_raw,
                    section_understanding=section_understanding,
                    conversation_history=conversation_history,
                    learner_memory=learner_memory,
                )

        # --- Navigation ---
        if intent == LearnerTurnIntentType.NAVIGATION:
            return await self._handle_navigation(
                request=request,
                intent_result=intent_result,
                template=template,
                state=state,
                targets=targets,
                current_stage_raw=current_stage_raw,
                adaptation_context=adaptation_context,
                section_understanding=section_understanding,
                current_pending_task=current_pending_task,
                conversation_history=conversation_history,
                learner_memory=learner_memory,
                recent_proposal=recent_proposal,
            )

        # --- Default: teacher turn (understanding signal, acknowledgement, pedagogical reply) ---
        if intent == LearnerTurnIntentType.TASK_ANSWER and current_pending_task is None:
            trigger = (
                f"Learner answered the teacher's question (intent: {intent.value}). "
                "Evaluate their answer based on the conversation context and respond."
            )
        elif intent in {LearnerTurnIntentType.ACKNOWLEDGEMENT, LearnerTurnIntentType.UNDERSTANDING_SIGNAL}:
            trigger = (
                f"Learner replied (intent: {intent.value}). "
                "The student has confirmed understanding. Move forward to the next "
                "topic, exercise, or section. Do NOT re-explain what was just covered."
            )
        else:
            trigger = f"Learner replied (intent: {intent.value})"
        turn = await self.engine.plan_teacher_turn(
            trigger=trigger,
            event_type=request.event_type,
            learner_message=learner_message,
            current_stage=current_stage,
            section_understanding=section_understanding,
            pending_task=current_pending_task,
            conversation_history=conversation_history,
            learner_memory=learner_memory,
        )

        teacher_action = self._teacher_action_from_turn(turn, current_stage=current_stage)
        proposal = self._proposal_from_turn(turn)

        # Generate lesson on-demand when teacher decides to teach and
        # artifact_runtime is available (covers deferred OPEN_SESSION flow)
        lesson: LessonPayload | None = None
        if (
            turn.action_type == TeacherActionType.TEACH_SECTION
            and self.artifact_runtime is not None
            and current_stage_raw is not None
            and not state["plan_completed"]
        ):
            try:
                lesson_payload, stage_with_parent = await self.artifact_runtime.get_or_generate_lesson(
                    learner_id=request.learner_id,
                    template_id=template_id,
                    stage=current_stage_raw,
                    adaptation_context=adaptation_context,
                    persist_decision=False,
                    use_agentic=False,
                    use_learner_model=False,
                )
                current_stage_raw = stage_with_parent
                current_stage = public_stage(stage_with_parent)
                lesson = LessonPayload.model_validate(lesson_payload)
            except Exception:
                logger.warning("Lesson generation failed in learner reply path, continuing without lesson")

        await self._record_session_event(
            request=request,
            template_id=template_id,
            current_stage=current_stage_raw,
            teacher_action=teacher_action,
            proposal=proposal,
        )

        plan = await self.state_service.build_plan_payload(
            template=template, state=state, current_stage=current_stage_raw,
        )

        return TeacherSessionResult(
            teacher_message=turn.teacher_message,
            teacher_action=teacher_action,
            proposal=proposal,
            lesson=lesson,
            section_understanding=section_understanding,
            current_stage=current_stage,
            plan=plan,
            plan_completed=bool(state["plan_completed"]),
        )

    # ------------------------------------------------------------------
    # Handle task answer
    # ------------------------------------------------------------------

    async def _handle_task_answer(
        self,
        *,
        request: TeacherSessionRequest,
        template: dict[str, Any],
        state: dict[str, Any],
        targets: list[dict[str, Any]],
        current_stage_raw: dict[str, Any] | None,
        section_understanding: SectionUnderstandingArtifact | None,
        pending_task: PendingTeacherTask,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None,
    ) -> TeacherSessionResult:
        learner_message = request.message or ""
        template_id = str(template["id"])
        current_stage = public_stage(current_stage_raw)

        # Evaluate the answer
        evaluation_sgr = await self.engine.evaluate_answer(
            learner_message=learner_message,
            pending_task=pending_task,
            conversation_history=conversation_history,
        )
        evaluation = self._evaluation_from_sgr(evaluation_sgr, pending_task=pending_task)

        # Record mastery update from the evaluation
        await self.learner_service.record_evaluation_update(
            learner_id=request.learner_id,
            section_id=pending_task.section_id,
            module_id=(current_stage_raw or {}).get("module_id"),
            interaction_id=None,
            status=evaluation.status.value,
            model_confidence=evaluation.confidence,
            attempt_count=pending_task.attempt_count,
            active_template_id=template_id,
        )

        # If correct — celebrate and potentially advance
        if evaluation.status == CheckpointEvaluationStatus.CORRECT:
            turn = await self.engine.plan_teacher_turn(
                trigger="Student answered correctly!",
                learner_message=learner_message,
                current_stage=current_stage,
                section_understanding=section_understanding,
                pending_task=None,  # resolved
                conversation_history=conversation_history,
                learner_memory=learner_memory,
                checkpoint_evaluation=evaluation,
            )
            teacher_action = self._teacher_action_from_turn(turn, current_stage=current_stage)
            proposal = self._proposal_from_turn(turn)

            await self._record_session_event(
                request=request, template_id=template_id,
                current_stage=current_stage_raw,
                teacher_action=teacher_action,
                checkpoint_evaluation=evaluation,
                proposal=proposal,
            )

            plan = await self.state_service.build_plan_payload(
                template=template, state=state, current_stage=current_stage_raw,
            )

            return TeacherSessionResult(
                teacher_message=turn.teacher_message,
                teacher_action=teacher_action,
                proposal=proposal,
                checkpoint_evaluation=evaluation,
                section_understanding=section_understanding,
                current_stage=current_stage,
                plan=plan,
                plan_completed=bool(state["plan_completed"]),
            )

        # Not correct — plan weak answer response
        repair_history = await self._get_repair_history(
            request.learner_id, pending_task, current_stage_raw,
        )
        weak_plan = await self.engine.plan_weak_answer(
            learner_message=learner_message,
            evaluation=evaluation,
            pending_task=pending_task,
            repair_history=repair_history,
            conversation_history=conversation_history,
        )

        teacher_action = TeacherAction(
            action_type=TeacherActionType.CHECK_STUDENT_ANSWER,
            rationale=weak_plan.evaluation_read[:1200],
            section_id=str((current_stage or {}).get("section_id") or ""),
            prompt_instruction=weak_plan.teacher_message,
        )
        proposal = None
        if weak_plan.proposal:
            proposal = TeacherProposal(
                proposal_type=weak_plan.proposal.proposal_type,
                rationale=weak_plan.proposal.rationale[:1200],
                target_section_id=weak_plan.proposal.target_section_id,
                target_title=weak_plan.proposal.target_title,
                can_defer=weak_plan.proposal.can_defer,
            )

        await self._record_session_event(
            request=request, template_id=template_id,
            current_stage=current_stage_raw,
            teacher_action=teacher_action,
            checkpoint_evaluation=evaluation,
            proposal=proposal,
        )

        plan = await self.state_service.build_plan_payload(
            template=template, state=state, current_stage=current_stage_raw,
        )

        return TeacherSessionResult(
            teacher_message=weak_plan.teacher_message,
            teacher_action=teacher_action,
            proposal=proposal,
            checkpoint_evaluation=evaluation,
            section_understanding=section_understanding,
            current_stage=current_stage,
            plan=plan,
            plan_completed=bool(state["plan_completed"]),
        )

    # ------------------------------------------------------------------
    # Handle grounded reply (RAG)
    # ------------------------------------------------------------------

    async def _handle_grounded_reply(
        self,
        *,
        request: TeacherSessionRequest,
        chat_service: Any,
        template: dict[str, Any],
        state: dict[str, Any],
        targets: list[dict[str, Any]],
        current_stage_raw: dict[str, Any] | None,
        section_understanding: SectionUnderstandingArtifact | None,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None,
    ) -> TeacherSessionResult:
        template_id = str(template["id"])
        current_stage = public_stage(current_stage_raw)

        payload = await chat_service.execute_chat_request(
            ChatTurnRequest(
                learner_id=request.learner_id,
                message=request.message or "",
                context=ChatTurnContext(
                    current_module_id=request.context.current_module_id,
                    current_section_id=request.context.current_section_id,
                ),
            ),
        )

        teacher_message = str(payload.get("answer_md") or "")
        interaction_id = payload.get("interaction_id")
        citations = list(payload.get("citations") or [])
        retrieval_debug = payload.get("retrieval_debug")

        teacher_action = TeacherAction(
            action_type=TeacherActionType.TEACH_SECTION,
            rationale="Grounded retrieval to answer student's content question.",
            section_id=str((current_stage or {}).get("section_id") or ""),
            prompt_instruction=teacher_message,
        )

        await self._record_session_event(
            request=request, template_id=template_id,
            current_stage=current_stage_raw,
            teacher_action=teacher_action,
        )

        plan = await self.state_service.build_plan_payload(
            template=template, state=state, current_stage=current_stage_raw,
        )

        return TeacherSessionResult(
            teacher_message=teacher_message,
            teacher_action=teacher_action,
            section_understanding=section_understanding,
            current_stage=current_stage,
            plan=plan,
            plan_completed=bool(state["plan_completed"]),
            interaction_id=int(interaction_id) if interaction_id is not None else None,
            citations=citations,
            retrieval_debug=retrieval_debug,
        )

    # ------------------------------------------------------------------
    # Handle navigation
    # ------------------------------------------------------------------

    async def _handle_navigation(
        self,
        *,
        request: TeacherSessionRequest,
        intent_result: IntentAndRoute,
        template: dict[str, Any],
        state: dict[str, Any],
        targets: list[dict[str, Any]],
        current_stage_raw: dict[str, Any] | None,
        adaptation_context: AdaptationContext | None,
        section_understanding: SectionUnderstandingArtifact | None,
        current_pending_task: PendingTeacherTask | None,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None,
        recent_proposal: TeacherProposal | None,
    ) -> TeacherSessionResult:
        template_id = str(template["id"])
        nav = intent_result.navigation_action
        debt_updates: list[LearningDebtItem] = []

        # Accept proposal
        if nav == LearnerNavigationAction.ACCEPT_PROPOSAL:
            proposal = recent_proposal
            if proposal and proposal.proposal_type == TeacherProposalType.REVISIT_PREVIOUS_SECTION:
                target = proposal.target_section_id
                if target:
                    template, state, targets, current_stage_raw, adaptation_context = (
                        await self.state_service.move_to_stage(request.learner_id, target_section_id=target)
                    )
            elif proposal and proposal.proposal_type == TeacherProposalType.ADVANCE_TO_NEXT_SECTION:
                template, state, targets, current_stage_raw, adaptation_context = (
                    await self.state_service.advance_stage(request.learner_id)
                )
            # Reload section understanding for new stage
            section_understanding, _ = (
                await self.section_understanding_service.get_or_create_section_understanding(
                    learner_id=request.learner_id,
                    template_id=template_id,
                    current_stage=current_stage_raw,
                    adaptation_context=adaptation_context,
                )
            )

        # Advance to next section
        elif nav == LearnerNavigationAction.ADVANCE_TO_NEXT_SECTION:
            if current_pending_task:
                debt_item = await self._append_learning_debt(
                    learner_id=request.learner_id, template_id=template_id,
                    current_stage=current_stage_raw,
                    debt_kind=LearningDebtKind.SKIPPED_SECTION,
                )
                if debt_item:
                    debt_updates.append(debt_item)
            template, state, targets, current_stage_raw, adaptation_context = (
                await self.state_service.advance_stage(request.learner_id)
            )
            section_understanding, _ = (
                await self.section_understanding_service.get_or_create_section_understanding(
                    learner_id=request.learner_id,
                    template_id=template_id,
                    current_stage=current_stage_raw,
                    adaptation_context=adaptation_context,
                )
            )

        # Revisit section
        elif nav == LearnerNavigationAction.REVISIT_SECTION:
            target = intent_result.target_section_id
            if not target and intent_result.target_title:
                # LLM already identified the target or gave a title — find it
                target = self._find_section_by_title(targets, intent_result.target_title)
            if target:
                template, state, targets, current_stage_raw, adaptation_context = (
                    await self.state_service.move_to_stage(request.learner_id, target_section_id=target)
                )
                section_understanding, _ = (
                    await self.section_understanding_service.get_or_create_section_understanding(
                        learner_id=request.learner_id,
                        template_id=template_id,
                        current_stage=current_stage_raw,
                        adaptation_context=adaptation_context,
                    )
                )

        # Refuse proposal
        elif nav == LearnerNavigationAction.REFUSE_PROPOSAL:
            if recent_proposal and recent_proposal.proposal_type == TeacherProposalType.REVISIT_PREVIOUS_SECTION:
                debt_item = await self._append_learning_debt(
                    learner_id=request.learner_id, template_id=template_id,
                    current_stage=current_stage_raw,
                    debt_kind=LearningDebtKind.REFUSED_REVISIT,
                )
                if debt_item:
                    debt_updates.append(debt_item)

        # Plan teacher turn for the new context
        current_stage = public_stage(current_stage_raw)
        turn = await self.engine.plan_teacher_turn(
            trigger=f"Navigation: {nav.value if nav else 'unknown'}",
            event_type=request.event_type,
            learner_message=request.message,
            current_stage=current_stage,
            section_understanding=section_understanding,
            pending_task=None,  # navigation clears pending task context
            conversation_history=conversation_history,
            learner_memory=learner_memory,
        )

        teacher_action = self._teacher_action_from_turn(turn, current_stage=current_stage)
        proposal = self._proposal_from_turn(turn)

        await self._record_session_event(
            request=request, template_id=template_id,
            current_stage=current_stage_raw,
            teacher_action=teacher_action,
            proposal=proposal,
        )

        plan = await self.state_service.build_plan_payload(
            template=template, state=state, current_stage=current_stage_raw,
        )

        return TeacherSessionResult(
            teacher_message=turn.teacher_message,
            teacher_action=teacher_action,
            proposal=proposal,
            debt_updates=debt_updates,
            section_understanding=section_understanding,
            current_stage=current_stage,
            plan=plan,
            plan_completed=bool(state["plan_completed"]),
        )

    # ------------------------------------------------------------------
    # Handle non-reply events (open_session, continue, accept/refuse)
    # ------------------------------------------------------------------

    async def _handle_non_reply_event(
        self,
        *,
        request: TeacherSessionRequest,
        template: dict[str, Any],
        state: dict[str, Any],
        targets: list[dict[str, Any]],
        current_stage_raw: dict[str, Any] | None,
        adaptation_context: AdaptationContext | None,
        section_understanding: SectionUnderstandingArtifact | None,
        current_pending_task: PendingTeacherTask | None,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None,
        recent_proposal: TeacherProposal | None,
    ) -> TeacherSessionResult:
        template_id = str(template["id"])
        debt_updates: list[LearningDebtItem] = []

        # Handle accept/refuse proposal
        if request.event_type == TeacherSessionEventType.ACCEPT_PROPOSAL:
            if request.proposal_type == TeacherProposalType.REVISIT_PREVIOUS_SECTION:
                target = recent_proposal.target_section_id if recent_proposal else None
                if target:
                    template, state, targets, current_stage_raw, adaptation_context = (
                        await self.state_service.move_to_stage(request.learner_id, target_section_id=target)
                    )
            elif request.proposal_type in {
                TeacherProposalType.ADVANCE_TO_NEXT_SECTION,
                TeacherProposalType.SKIP_CURRENT_SECTION,
            }:
                template, state, targets, current_stage_raw, adaptation_context = (
                    await self.state_service.advance_stage(request.learner_id)
                )
            # Reload section understanding
            section_understanding, _ = (
                await self.section_understanding_service.get_or_create_section_understanding(
                    learner_id=request.learner_id,
                    template_id=template_id,
                    current_stage=current_stage_raw,
                    adaptation_context=adaptation_context,
                )
            )

        elif request.event_type == TeacherSessionEventType.REFUSE_PROPOSAL:
            if request.proposal_type == TeacherProposalType.REVISIT_PREVIOUS_SECTION:
                debt_item = await self._append_learning_debt(
                    learner_id=request.learner_id, template_id=template_id,
                    current_stage=current_stage_raw,
                    debt_kind=LearningDebtKind.REFUSED_REVISIT,
                )
                if debt_item:
                    debt_updates.append(debt_item)

        elif request.event_type == TeacherSessionEventType.REQUEST_MOVE_ON:
            if current_pending_task:
                kind = (
                    LearningDebtKind.UNANSWERED_CHECKPOINT
                    if current_pending_task.task_kind.name == "CHECKPOINT_QUESTION"
                    else LearningDebtKind.UNATTEMPTED_EXERCISE
                )
                debt_item = await self._append_learning_debt(
                    learner_id=request.learner_id, template_id=template_id,
                    current_stage=current_stage_raw, debt_kind=kind,
                )
                if debt_item:
                    debt_updates.append(debt_item)
            template, state, targets, current_stage_raw, adaptation_context = (
                await self.state_service.advance_stage(request.learner_id)
            )
            section_understanding, _ = (
                await self.section_understanding_service.get_or_create_section_understanding(
                    learner_id=request.learner_id,
                    template_id=template_id,
                    current_stage=current_stage_raw,
                    adaptation_context=adaptation_context,
                )
            )

        # Resolve pending task for new context
        final_pending_task = await self.pending_task_runtime.resolve_pending_task(
            learner_id=request.learner_id,
            current_stage=current_stage_raw,
            section_understanding=section_understanding,
        )

        # Get learning debt for context
        debt_rows = await self.session_repository.list_open_learning_debt(
            request.learner_id,
        )
        learning_debt = [dict(r) for r in debt_rows] if debt_rows else []

        # Determine trigger description
        trigger_map = {
            TeacherSessionEventType.OPEN_SESSION: "Session started. Welcome the student and begin teaching.",
            TeacherSessionEventType.CONTINUE_SESSION: "Student wants to continue. Keep teaching.",
            TeacherSessionEventType.ACCEPT_PROPOSAL: f"Student accepted proposal: {request.proposal_type.value if request.proposal_type else '?'}",
            TeacherSessionEventType.REFUSE_PROPOSAL: f"Student refused proposal: {request.proposal_type.value if request.proposal_type else '?'}",
            TeacherSessionEventType.REQUEST_MOVE_ON: "Student asked to move on to the next section.",
        }
        trigger = trigger_map.get(request.event_type, request.event_type.value)

        # Next stage for context
        from app.state.stage_state import next_stage as next_stage_fn
        next_stage = next_stage_fn(targets, current_stage_raw)

        # Plan teacher turn
        current_stage = public_stage(current_stage_raw)
        turn = await self.engine.plan_teacher_turn(
            trigger=trigger,
            event_type=request.event_type,
            current_stage=current_stage,
            section_understanding=section_understanding,
            pending_task=final_pending_task,
            conversation_history=conversation_history,
            learner_memory=learner_memory,
            next_stage=public_stage(next_stage) if next_stage else None,
            learning_debt=learning_debt,
        )

        teacher_action = self._teacher_action_from_turn(turn, current_stage=current_stage)
        proposal = self._proposal_from_turn(turn)

        # Generate lesson if applicable (OPEN_SESSION excluded — lesson is
        # deferred until the student replies to the greeting)
        lesson: LessonPayload | None = None
        should_gen_lesson = request.event_type in {
            TeacherSessionEventType.REQUEST_MOVE_ON,
            TeacherSessionEventType.CONTINUE_SESSION,
        }
        if (
            should_gen_lesson
            and current_stage_raw is not None
            and not state["plan_completed"]
            and self.artifact_runtime is not None
        ):
            try:
                lesson_payload, stage_with_parent = await self.artifact_runtime.get_or_generate_lesson(
                    learner_id=request.learner_id,
                    template_id=template_id,
                    stage=current_stage_raw,
                    adaptation_context=adaptation_context,
                    persist_decision=False,
                    use_agentic=False,
                    use_learner_model=False,
                )
                current_stage_raw = stage_with_parent
                current_stage = public_stage(stage_with_parent)
                lesson = LessonPayload.model_validate(lesson_payload)
            except Exception:
                logger.warning("Lesson generation failed, continuing without lesson")

        await self._record_session_event(
            request=request, template_id=template_id,
            current_stage=current_stage_raw,
            teacher_action=teacher_action,
            proposal=proposal,
        )

        plan = await self.state_service.build_plan_payload(
            template=template, state=state, current_stage=current_stage_raw,
        )

        return TeacherSessionResult(
            teacher_message=turn.teacher_message,
            teacher_action=teacher_action,
            proposal=proposal,
            debt_updates=debt_updates,
            section_understanding=section_understanding,
            current_stage=current_stage,
            plan=plan,
            plan_completed=bool(state["plan_completed"]),
            lesson=lesson,
        )

    # ------------------------------------------------------------------
    # Utility methods (reused from old runtime, simplified)
    # ------------------------------------------------------------------

    async def _resolve_recent_proposal(
        self,
        *,
        learner_id: str,
        proposal_type: TeacherProposalType | None,
    ) -> TeacherProposal | None:
        payload = await self.session_repository.get_latest_teacher_proposal(
            learner_id,
            proposal_type=None if proposal_type is None else proposal_type.value,
        )
        if payload is None:
            return None
        try:
            return TeacherProposal.model_validate(payload)
        except Exception:
            return None

    async def _record_session_event(
        self,
        *,
        request: TeacherSessionRequest,
        template_id: str,
        current_stage: dict[str, Any] | None,
        teacher_action: TeacherAction | None = None,
        checkpoint_evaluation: CheckpointEvaluation | None = None,
        proposal: TeacherProposal | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if teacher_action is not None:
            payload["teacher_action"] = teacher_action.model_dump(mode="json")
        if checkpoint_evaluation is not None:
            payload["checkpoint_evaluation"] = checkpoint_evaluation.model_dump(mode="json")
        if proposal is not None:
            payload["proposal"] = proposal.model_dump(mode="json")
        await self.session_repository.append_teacher_session_event(
            learner_id=request.learner_id,
            template_id=template_id,
            interaction_id=None,
            event_type=request.event_type.value,
            proposal_type=None if request.proposal_type is None else request.proposal_type.value,
            stage_index=None if current_stage is None else int(current_stage.get("stage_index", -1)),
            section_id=None if current_stage is None else str(current_stage.get("section_id") or ""),
            module_id=None if current_stage is None or current_stage.get("module_id") is None else str(current_stage["module_id"]),
            message=request.message,
            event_payload_json=payload,
        )

    async def _append_learning_debt(
        self,
        *,
        learner_id: str,
        template_id: str,
        current_stage: dict[str, Any] | None,
        debt_kind: LearningDebtKind,
    ) -> LearningDebtItem | None:
        if current_stage is None:
            return None
        row = await self.session_repository.append_learning_debt(
            learner_id=learner_id,
            template_id=template_id,
            section_id=str(current_stage.get("section_id") or ""),
            module_id=None if current_stage.get("module_id") is None else str(current_stage["module_id"]),
            debt_kind=debt_kind.value,
            rationale=f"Learning debt: {debt_kind.value}",
            source_event_id=None,
        )
        return LearningDebtItem.model_validate(row)

    async def _get_repair_history(
        self,
        learner_id: str,
        pending_task: PendingTeacherTask,
        current_stage: dict[str, Any] | None = None,
    ) -> RepairHistorySummary | None:
        try:
            row = await self.session_repository.summarize_repair_history_for_task(
                learner_id,
                current_stage=current_stage,
                item_ref=pending_task.item_ref,
            )
            if row:
                return RepairHistorySummary.model_validate(row)
        except Exception:
            pass
        return None

    @staticmethod
    def _find_section_by_title(
        targets: list[dict[str, Any]],
        title_hint: str,
    ) -> str | None:
        """Simple section lookup by title — LLM already did the hard work."""
        hint = title_hint.lower().strip()
        if not hint:
            return None
        for target in targets:
            section_id = str(target.get("section_id") or "")
            title = str(target.get("title") or "").lower()
            if hint in title or title in hint:
                return section_id
        return None


__all__ = ["TeacherRuntime"]
