from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from openai import AsyncOpenAI

from app.content.section_source import resolve_stage_source
from app.learner.models import MasteryUpdate
from app.learner.service import LearnerService
from app.platform.config import Settings, get_settings
from app.platform.logging import log_event
from app.platform.vector_store import AsyncVectorStore
from app.tutor.lesson_generation import SectionLessonGenerator
from app.tutor.plan import annotate_plan_tree, build_hierarchical_plan, load_book_data
from app.tutor.repository import TutorRepository

logger = logging.getLogger(__name__)


class TutorService:
    profile_version = "start_message_v1"
    timeout_seconds_default = 8.0
    prompt_temperature = 0.3
    prewarm_concurrency = 1

    def __init__(
        self,
        repository: TutorRepository,
        learner_service: LearnerService,
        vector_store: AsyncVectorStore,
        lesson_generator: SectionLessonGenerator,
        llm_client: AsyncOpenAI | None,
        book_json_path: str,
        settings: Settings | None = None,
    ):
        self.repository = repository
        self.learner_service = learner_service
        self.vector_store = vector_store
        self.lesson_generator = lesson_generator
        self.llm_client = llm_client
        self.book_json_path = book_json_path
        self.settings = settings or get_settings()
        self.default_template_id = "default_calc1"
        self.default_template_version = 2
        self.timeout_seconds = self.timeout_seconds_default
        self._prewarm_keys: set[tuple[str, int]] = set()
        self._prewarm_tasks: set[asyncio.Task[None]] = set()
        self._prewarm_semaphore = asyncio.Semaphore(self.prewarm_concurrency)

    async def ensure_default_template(self) -> dict[str, Any]:
        existing = await self.repository.get_plan_template(self.default_template_id)
        plan_json = (existing or {}).get("plan_json") or {}
        if existing and plan_json.get("stage_targets") and plan_json.get("plan_tree"):
            return existing
        raise RuntimeError(
            "Default plan template is not initialized. Run the runtime bootstrap command before starting the app."
        )

    async def ensure_context(
        self,
        learner_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
        await self.ensure_learner(learner_id)
        template = await self.ensure_default_template()
        targets = self.template_targets(template)
        state = await self.repository.get_or_create_learner_plan_state(
            learner_id=learner_id,
            template_id=template["id"],
            total_stages=len(targets),
        )
        current_stage = self.current_stage_from_state(state, targets)
        return template, state, targets, current_stage

    async def start_payload(self, learner_id: str) -> dict[str, Any]:
        template, state, targets, current_stage = await self.ensure_context(learner_id)
        total_stages = len(targets)
        plan = await self.build_plan_payload(template=template, state=state, current_stage=current_stage)
        previous_stage, next_stage = self.adjacent_stages(targets, current_stage)
        return {
            "message": self.default_start_message(
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
        message = await self.get_start_message(
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

    async def current_lesson_payload(self, learner_id: str) -> dict[str, Any]:
        template, state, targets, current_stage = await self.ensure_context(learner_id)
        plan = await self.build_plan_payload(template=template, state=state, current_stage=current_stage)
        if not current_stage:
            return {
                "current_stage": None,
                "lesson": None,
                "plan": plan,
                "plan_completed": True,
            }
        lesson, stage_with_parent = await self.get_or_generate_lesson(
            template_id=str(template["id"]),
            stage=current_stage,
        )
        self.schedule_prewarm(template_id=str(template["id"]), stage=self.next_stage(targets, current_stage))
        return {
            "current_stage": stage_with_parent,
            "lesson": lesson,
            "plan": plan,
            "plan_completed": bool(state["plan_completed"]),
        }

    async def next_payload(self, learner_id: str, force: bool = False) -> dict[str, Any]:
        _ = force
        template, state, targets, _current_stage = await self.ensure_context(learner_id)
        total_stages = len(targets)
        if total_stages == 0:
            plan = await self.build_plan_payload(template=template, state=state, current_stage=None)
            return {
                "message": "Plan completed.",
                "current_stage": None,
                "plan": plan,
                "plan_completed": True,
            }

        completed_count = int(state["completed_count"])
        current_stage_index = int(state["current_stage_index"])
        if completed_count < total_stages:
            completed_count += 1

        plan_completed = completed_count >= total_stages
        next_stage_index = total_stages - 1 if plan_completed else min(current_stage_index + 1, total_stages - 1)
        state = await self.repository.update_learner_plan_state(
            learner_id=learner_id,
            template_id=template["id"],
            current_stage_index=next_stage_index,
            completed_count=completed_count,
            plan_completed=plan_completed,
        )
        current_stage = self.current_stage_from_state(state, targets)
        plan = await self.build_plan_payload(template=template, state=state, current_stage=current_stage)
        self.schedule_prewarm(template_id=str(template["id"]), stage=self.next_stage(targets, current_stage))
        return {
            "message": "Plan completed." if plan_completed else "Moved to next stage.",
            "current_stage": current_stage,
            "plan": plan,
            "plan_completed": plan_completed,
        }

    async def apply_feedback(
        self,
        learner_id: str,
        interaction_id: int,
        section_id: str | None,
        module_id: str | None,
        confidence: int,
        assessment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template, _state, _targets, stage = await self.ensure_context(learner_id)
        current_section_id = str(section_id or (stage or {}).get("section_id") or "")
        current_module_id = module_id or (stage or {}).get("module_id")
        if current_section_id:
            mastery_map = await self.mastery_map(learner_id)
            current_mastery = mastery_map.get(current_section_id, 0.0)
            mastery_delta, update_source, assessment_decision, assessment_ignored_due_to_fallback = (
                self._feedback_delta(confidence=confidence, assessment=assessment)
            )
            new_mastery = self._clamp(current_mastery + mastery_delta)
            status = "completed" if new_mastery >= 0.8 else "in_progress"
            await self.learner_service.record_feedback_update(
                MasteryUpdate(
                    learner_id=learner_id,
                    section_id=current_section_id,
                    module_id=current_module_id,
                    interaction_id=interaction_id,
                    source_kind=self._source_kind_for_update_source(update_source),
                    assessment_decision=str((assessment or {}).get("decision") or "") or None,
                    recommended_next_action=str((assessment or {}).get("recommended_next_action") or "") or None,
                    confidence_submitted=confidence,
                    mastery_delta=mastery_delta,
                    mastery_before=current_mastery,
                    mastery_after=new_mastery,
                    status_after=status,
                    active_template_id=str(template["id"]),
                )
            )
            log_event(
                logger,
                "feedback.applied",
                learner_id=learner_id,
                interaction_id=interaction_id,
                section_id=current_section_id,
                module_id=current_module_id,
                update_source=update_source,
                confidence=confidence,
                assessment_decision=assessment_decision,
                assessment_ignored_due_to_fallback=assessment_ignored_due_to_fallback,
                mastery_delta=mastery_delta,
                previous_mastery=round(current_mastery, 4),
                new_mastery=round(new_mastery, 4),
                status=status,
            )
        else:
            log_event(
                logger,
                "feedback.applied",
                learner_id=learner_id,
                interaction_id=interaction_id,
                section_id=None,
                module_id=current_module_id,
                update_source="skipped",
                confidence=confidence,
                assessment_decision=(assessment or {}).get("decision"),
                assessment_ignored_due_to_fallback=bool((assessment or {}).get("fallback_used")),
                mastery_delta=0.0,
                previous_mastery=None,
                new_mastery=None,
                status=None,
            )
        return {
            "auto_advanced": False,
            "message": "Feedback saved. Continue when ready.",
            "current_stage": stage,
        }

    async def bootstrap_default_template(self) -> dict[str, Any]:
        existing = await self.repository.get_plan_template(self.default_template_id)
        plan_json = (existing or {}).get("plan_json") or {}
        if existing and plan_json.get("stage_targets") and plan_json.get("plan_tree"):
            return existing
        return await self._create_default_template()

    async def ensure_learner(self, learner_id: str) -> None:
        await self.repository.ensure_learner(learner_id)

    async def close(self) -> None:
        tasks = list(self._prewarm_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._prewarm_tasks.clear()
        self._prewarm_keys.clear()

    async def mastery_map(self, learner_id: str) -> dict[str, float]:
        progress = await self.repository.list_topic_progress(learner_id)
        return {row["section_id"]: float(row.get("mastery_score", 0.0)) for row in progress}

    async def build_plan_payload(
        self,
        *,
        template: dict[str, Any],
        state: dict[str, Any],
        current_stage: dict[str, Any] | None,
    ) -> dict[str, Any]:
        targets = self.template_targets(template)
        mastery_map = await self.mastery_map(str(state["learner_id"]))
        tree = annotate_plan_tree(
            self.template_tree(template),
            completed_count=int(state["completed_count"]),
            current_stage_index=None if current_stage is None else int(current_stage["stage_index"]),
            plan_completed=bool(state["plan_completed"]),
            mastery_map=mastery_map,
        )
        return {
            "template_id": str(template["id"]),
            "total_stages": len(targets),
            "completed_stages": int(state["completed_count"]),
            "mastery_score": float((tree or {}).get("mastery_score", 0.0)),
            "tree": tree,
        }

    async def get_or_generate_lesson(
        self,
        *,
        template_id: str,
        stage: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        source = await resolve_stage_source(self.vector_store, stage)
        cache = await self.repository.get_lesson_cache(
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
        )
        if self._is_valid_cache(cache, source.source_hash):
            lesson = dict(cache["lesson_json"])
            lesson["cached"] = True
            log_event(
                logger,
                "lesson.cache_hit",
                template_id=template_id,
                stage_index=int(stage["stage_index"]),
                section_id=str(stage.get("section_id") or ""),
                parent_doc_id=source.parent_doc_id,
                cache_hit=True,
                generation_mode=lesson.get("generation_mode"),
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
            return lesson, self._bind_parent_doc_id(stage, source.parent_doc_id)

        lesson = await self.lesson_generator.generate_lesson(
            section_id=str(stage["section_id"]),
            title=str(stage.get("title") or ""),
            breadcrumb=list(stage.get("breadcrumb") or []),
            parent_doc_id=source.parent_doc_id,
            source_markdown=source.source_markdown,
        )
        await self.repository.upsert_lesson_cache(
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
            lesson_json=lesson,
        )
        lesson["cached"] = False
        log_event(
            logger,
            "lesson.generated",
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
            section_id=str(stage.get("section_id") or ""),
            parent_doc_id=source.parent_doc_id,
            cache_hit=False,
            generation_mode=lesson.get("generation_mode"),
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
        return lesson, self._bind_parent_doc_id(stage, source.parent_doc_id)

    async def get_start_message(
        self,
        *,
        learner_id: str,
        template_id: str,
        current_stage: dict[str, object] | None,
        previous_stage: dict[str, object] | None,
        next_stage: dict[str, object] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        started = time.perf_counter()
        default = self.default_start_message(
            current_stage=current_stage,
            previous_stage=previous_stage,
            next_stage=next_stage,
            completed_count=completed_count,
            total_stages=total_stages,
            plan_completed=plan_completed,
        )
        stage_index = int((current_stage or {}).get("stage_index", -1))
        if self.llm_client is None:
            return default

        cached = await self.repository.get_start_message_cache(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=stage_index,
            completed_count=completed_count,
            plan_completed=plan_completed,
            profile_version=self.profile_version,
        )
        if cached and cached.get("message"):
            log_event(
                logger,
                "start_message.cache_hit",
                learner_id=learner_id,
                template_id=template_id,
                stage_index=stage_index,
                cache_hit=True,
                fallback_used=False,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
            return str(cached["message"])

        prompt = self._build_start_message_prompt(
            current_stage=current_stage,
            previous_stage=previous_stage,
            next_stage=next_stage,
            completed_count=completed_count,
            total_stages=total_stages,
            plan_completed=plan_completed,
        )
        try:
            raw = await self._chat_completion(prompt)
            message = str(raw or "").strip()
            if not message:
                log_event(
                    logger,
                    "start_message.empty_completion",
                    learner_id=learner_id,
                    template_id=template_id,
                    stage_index=stage_index,
                    cache_hit=False,
                    fallback_used=True,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
                )
                return default
            await self.repository.upsert_start_message_cache(
                learner_id=learner_id,
                template_id=template_id,
                stage_index=stage_index,
                completed_count=completed_count,
                plan_completed=plan_completed,
                profile_version=self.profile_version,
                message=message,
            )
            log_event(
                logger,
                "start_message.generated",
                learner_id=learner_id,
                template_id=template_id,
                stage_index=stage_index,
                cache_hit=False,
                fallback_used=False,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
            return message
        except Exception as exc:
            logger.warning("Start message fallback for learner '%s': %s", learner_id, exc)
            log_event(
                logger,
                "start_message.fallback",
                learner_id=learner_id,
                template_id=template_id,
                stage_index=stage_index,
                cache_hit=False,
                fallback_used=True,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
            return default

    def schedule_prewarm(self, *, template_id: str, stage: dict[str, Any] | None) -> None:
        if not stage:
            log_event(logger, "lesson.prewarm_skipped", template_id=template_id, reason="no_stage")
            return
        key = (template_id, int(stage["stage_index"]))
        if key in self._prewarm_keys:
            log_event(
                logger,
                "lesson.prewarm_skipped",
                template_id=template_id,
                stage_index=int(stage["stage_index"]),
                section_id=str(stage.get("section_id") or ""),
                reason="already_scheduled",
            )
            return
        self._prewarm_keys.add(key)
        log_event(
            logger,
            "lesson.prewarm_scheduled",
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
            section_id=str(stage.get("section_id") or ""),
            prewarm_scheduled=True,
        )
        task = asyncio.create_task(self._prewarm(template_id=template_id, stage=dict(stage), key=key))
        self._prewarm_tasks.add(task)
        task.add_done_callback(self._prewarm_tasks.discard)

    async def _prewarm(self, *, template_id: str, stage: dict[str, Any], key: tuple[str, int]) -> None:
        try:
            async with self._prewarm_semaphore:
                await self.get_or_generate_lesson(template_id=template_id, stage=stage)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Lesson prewarm skipped for stage '%s': %s", stage.get("section_id"), exc)
        finally:
            self._prewarm_keys.discard(key)

    async def _create_default_template(self) -> dict[str, Any]:
        book_id, toc = load_book_data(self.book_json_path)
        plan = build_hierarchical_plan(toc)
        template = await self.repository.upsert_plan_template(
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

    def _is_valid_cache(self, cache: dict[str, Any] | None, source_hash: str) -> bool:
        if not cache:
            return False
        lesson_json = dict(cache.get("lesson_json") or {})
        format_version = int(lesson_json.get("format_version", 0))
        if format_version < int(self.lesson_generator.settings.lesson_gen_format_version):
            return False
        if lesson_json.get("generator_version") != self.lesson_generator.generator_version:
            return False
        if lesson_json.get("prompt_profile_version") != self.lesson_generator.prompt_profile_version:
            return False
        return str(lesson_json.get("source_hash", "")) == str(source_hash)

    @staticmethod
    def _bind_parent_doc_id(stage: dict[str, Any], parent_doc_id: str) -> dict[str, Any]:
        enriched = dict(stage)
        enriched["parent_doc_id"] = parent_doc_id
        return enriched

    @staticmethod
    def template_targets(template: dict[str, Any]) -> list[dict[str, Any]]:
        plan_json = template.get("plan_json") or {}
        targets = plan_json.get("stage_targets") or plan_json.get("targets") or []
        return [dict(item) for item in targets if isinstance(item, dict)]

    @staticmethod
    def template_tree(template: dict[str, Any]) -> dict[str, Any] | None:
        tree = (template.get("plan_json") or {}).get("plan_tree")
        return dict(tree) if isinstance(tree, dict) else None

    @staticmethod
    def current_stage_from_state(state: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not targets or state.get("plan_completed"):
            return None
        index = int(state.get("current_stage_index", 0))
        index = max(0, min(index, len(targets) - 1))
        stage = dict(targets[index])
        stage["stage_index"] = index
        return stage

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
    def default_start_message(
        *,
        current_stage: dict[str, object] | None,
        previous_stage: dict[str, object] | None,
        next_stage: dict[str, object] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        if plan_completed:
            return (
                "Welcome back. You have completed the full study plan. "
                "We can review any section you want, revisit weak spots, or start a second pass from the beginning."
            )
        if not current_stage:
            return (
                "Welcome. I am your tutor. We will move through the book step by step and keep the pace clear and steady."
            )
        current_title = str(current_stage.get("title") or current_stage.get("section_id") or "current section")
        stage_number = int(current_stage.get("stage_index", 0)) + 1
        previous_title = str(previous_stage.get("title") or "").strip() if previous_stage else ""
        next_title = str(next_stage.get("title") or "").strip() if next_stage else ""
        if completed_count == 0:
            return (
                f"Welcome. I am your tutor, and we are starting with **{current_title}**. "
                "I will explain the section clearly, then we will build forward one stage at a time."
            )
        message = (
            f"Welcome back. Last time you finished **{previous_title or 'the previous stage'}**. "
            f"Now we continue with **{current_title}** (stage {stage_number} of {total_stages}, completed {completed_count}/{total_stages})."
        )
        if next_title:
            message += f" After this, we will move on to **{next_title}**."
        return message

    @staticmethod
    def _build_start_message_prompt(
        *,
        current_stage: dict[str, object] | None,
        previous_stage: dict[str, object] | None,
        next_stage: dict[str, object] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        return (
            "Write a short tutor greeting in Markdown.\n"
            "Sound like a real teacher, not a robotic status message.\n"
            "Keep it to 3-4 sentences.\n"
            "Mention where the learner stopped previously and what you will cover next.\n"
            "Do not mention internal ids. Do not list raw breadcrumbs.\n\n"
            f"Plan completed: {plan_completed}\n"
            f"Completed stages: {completed_count}/{total_stages}\n"
            f"Previous stage title: {str((previous_stage or {}).get('title') or '')}\n"
            f"Current stage title: {str((current_stage or {}).get('title') or '')}\n"
            f"Current stage number: {int((current_stage or {}).get('stage_index', -1)) + 1 if current_stage else 0}\n"
            f"Next stage title: {str((next_stage or {}).get('title') or '')}\n"
        )

    async def _chat_completion(self, user_prompt: str) -> str:
        if self.llm_client is None:
            return ""
        completion = await self.llm_client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a concise, supportive tutor writing short session greetings.",
                },
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.prompt_temperature,
            timeout=self.timeout_seconds,
        )
        return str(completion.choices[0].message.content or "").strip()

    @staticmethod
    def _confidence_delta(confidence: int) -> float:
        if confidence >= 4:
            return 0.20
        if confidence == 3:
            return 0.05
        return -0.10

    @staticmethod
    def _assessment_delta(decision: str) -> float:
        if decision == "correct":
            return 0.20
        if decision == "partially_correct":
            return 0.08
        if decision == "misconception":
            return -0.15
        if decision == "procedural_error":
            return -0.10
        if decision in {"off_topic", "insufficient_evidence"}:
            return -0.05
        return -0.05

    @classmethod
    def _feedback_delta(
        cls,
        *,
        confidence: int,
        assessment: dict[str, Any] | None,
    ) -> tuple[float, str, str | None, bool]:
        if assessment and not bool(assessment.get("fallback_used")):
            decision = str(assessment.get("decision") or "").strip()
            if decision:
                return cls._assessment_delta(decision), "assessment", decision, False
        return (
            cls._confidence_delta(confidence),
            "confidence",
            str((assessment or {}).get("decision") or "") or None,
            bool((assessment or {}).get("fallback_used")),
        )

    @staticmethod
    def _source_kind_for_update_source(update_source: str) -> str:
        if update_source == "assessment":
            return "feedback_assessment"
        return "feedback_confidence"

    @staticmethod
    def _clamp(value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value
