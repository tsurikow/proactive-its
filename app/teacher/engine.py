"""
Teacher engine — simplified orchestration of 3-5 SGR calls per turn.

Replaces the old TeacherPolicyEngine (13+ agents) with a single class
that uses purpose-built SGR schemas. No fallback logic, no god objects,
no transport layer.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput

from app.platform.ai.openrouter import build_model, llm_available
from app.platform.config import Settings, get_settings
from app.teacher.models import (
    CheckpointEvaluation,
    PendingTeacherTask,
    RepairHistorySummary,
    SectionUnderstandingArtifact,
    TeacherProposal,
    TeacherSessionEventType,
)
from app.teacher.prompt_builder import (
    build_answer_evaluation_prompt,
    build_intent_and_route_prompt,
    build_learner_memory_prompt,
    build_section_understanding_prompt,
    build_teacher_turn_prompt,
    build_weak_answer_plan_prompt,
)
from app.teacher.schemas import (
    AnswerEvaluation,
    IntentAndRoute,
    LearnerMemory,
    SectionUnderstanding,
    TeacherTurn,
    WeakAnswerPlan,
)
from app.teacher.system_prompt import teacher_system_prompt

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Agent factory — one function, no class overhead
# ---------------------------------------------------------------------------

def _build_agent(output_type: type[T]) -> Agent[None, T]:
    """Build a PydanticAI agent for a specific SGR schema."""
    return Agent(
        model=None,  # provided at run time
        output_type=NativeOutput(output_type, strict=True),
        system_prompt=teacher_system_prompt(),
        retries=1,
        output_retries=2,
        defer_model_check=True,
    )


async def _run_sgr(
    agent: Agent[None, T],
    prompt: str,
    *,
    settings: Settings,
    model_name: str | None = None,
    timeout_seconds: float = 10.0,
    temperature: float = 0.0,
) -> T:
    """Run an SGR call and return the typed output."""
    model = build_model(
        settings,
        model=model_name,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        extra_body={"provider": {"require_parameters": True}},
    )
    if model is None:
        raise RuntimeError("LLM unavailable: no API key or model configured")
    result = await agent.run(prompt, model=model)
    return result.output


# ---------------------------------------------------------------------------
# Teacher Engine
# ---------------------------------------------------------------------------

class TeacherEngine:
    """
    Orchestrates 3-5 SGR calls per teacher turn.

    Calls:
    1. classify_intent   — IntentAndRoute (when learner sends a message)
    2. plan_teacher_turn — TeacherTurn (main teacher action + message)
    3. evaluate_answer   — AnswerEvaluation (when learner answers a task)
    4. plan_weak_answer  — WeakAnswerPlan (after incorrect/partial answer)
    5. understand_section — SectionUnderstanding (cached, once per section)
    6. synthesize_memory — LearnerMemory (end of session)
    """

    # Timeout defaults (seconds)
    INTENT_TIMEOUT = 8.0
    TURN_TIMEOUT = 12.0
    ANSWER_TIMEOUT = 10.0
    WEAK_ANSWER_TIMEOUT = 8.0
    SECTION_TIMEOUT = 20.0
    MEMORY_TIMEOUT = 15.0

    def __init__(
        self,
        *,
        model: str | None = None,
        answer_check_model: str | None = None,
        section_understanding_model: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.model = model or self.settings.openrouter_model
        self.answer_check_model = answer_check_model or self.model
        self.section_understanding_model = section_understanding_model or self.model

        # Build agents (one per schema type, reusable across calls)
        self._intent_agent = _build_agent(IntentAndRoute)
        self._turn_agent = _build_agent(TeacherTurn)
        self._eval_agent = _build_agent(AnswerEvaluation)
        self._weak_agent = _build_agent(WeakAnswerPlan)
        self._section_agent = _build_agent(SectionUnderstanding)
        self._memory_agent = _build_agent(LearnerMemory)

    def is_available(self) -> bool:
        """Check if the LLM is configured and available."""
        return llm_available(self.settings)

    # ------------------------------------------------------------------
    # 1. Intent & Route
    # ------------------------------------------------------------------

    async def classify_intent(
        self,
        *,
        learner_message: str,
        current_stage: dict[str, Any] | None,
        pending_task: PendingTeacherTask | None,
        recent_proposal: TeacherProposal | None,
        section_understanding: SectionUnderstandingArtifact | None,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None = None,
    ) -> IntentAndRoute:
        """Classify learner intent and decide response route."""
        prompt = build_intent_and_route_prompt(
            learner_message=learner_message,
            current_stage=current_stage,
            pending_task=pending_task,
            recent_proposal=recent_proposal,
            section_understanding=section_understanding,
            conversation_history=conversation_history,
            learner_memory=learner_memory,
        )
        return await _run_sgr(
            self._intent_agent,
            prompt,
            settings=self.settings,
            model_name=self.model,
            timeout_seconds=self.INTENT_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # 2. Teacher Turn
    # ------------------------------------------------------------------

    async def plan_teacher_turn(
        self,
        *,
        trigger: str,
        event_type: TeacherSessionEventType | None = None,
        learner_message: str | None = None,
        current_stage: dict[str, Any] | None,
        section_understanding: SectionUnderstandingArtifact | None,
        section_source_md: str | None = None,
        pending_task: PendingTeacherTask | None,
        conversation_history: list[dict[str, str]],
        learner_memory: dict[str, Any] | None = None,
        revisit_candidates: list[dict[str, Any]] | None = None,
        next_stage: dict[str, Any] | None = None,
        learning_debt: list[dict[str, Any]] | None = None,
        checkpoint_evaluation: CheckpointEvaluation | None = None,
    ) -> TeacherTurn:
        """Decide teacher action and generate message."""
        prompt = build_teacher_turn_prompt(
            trigger=trigger,
            event_type=event_type,
            learner_message=learner_message,
            current_stage=current_stage,
            section_understanding=section_understanding,
            section_source_md=section_source_md,
            pending_task=pending_task,
            conversation_history=conversation_history,
            learner_memory=learner_memory,
            revisit_candidates=revisit_candidates,
            next_stage=next_stage,
            learning_debt=learning_debt,
            checkpoint_evaluation=checkpoint_evaluation,
        )
        return await _run_sgr(
            self._turn_agent,
            prompt,
            settings=self.settings,
            model_name=self.model,
            timeout_seconds=self.TURN_TIMEOUT,
            temperature=0.35,
        )

    # ------------------------------------------------------------------
    # 3. Answer Evaluation
    # ------------------------------------------------------------------

    async def evaluate_answer(
        self,
        *,
        learner_message: str,
        pending_task: PendingTeacherTask,
        conversation_history: list[dict[str, str]],
    ) -> AnswerEvaluation:
        """Evaluate a learner's answer to a pending task."""
        prompt = build_answer_evaluation_prompt(
            learner_message=learner_message,
            pending_task=pending_task,
            conversation_history=conversation_history,
        )
        return await _run_sgr(
            self._eval_agent,
            prompt,
            settings=self.settings,
            model_name=self.answer_check_model,
            timeout_seconds=self.ANSWER_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # 4. Weak Answer Plan
    # ------------------------------------------------------------------

    async def plan_weak_answer(
        self,
        *,
        learner_message: str,
        evaluation: CheckpointEvaluation,
        pending_task: PendingTeacherTask,
        repair_history: RepairHistorySummary | None,
        conversation_history: list[dict[str, str]],
        revisit_candidates: list[dict[str, Any]] | None = None,
    ) -> WeakAnswerPlan:
        """Plan response to incorrect/partial/unresolved answer."""
        prompt = build_weak_answer_plan_prompt(
            learner_message=learner_message,
            evaluation=evaluation,
            pending_task=pending_task,
            repair_history=repair_history,
            conversation_history=conversation_history,
            revisit_candidates=revisit_candidates,
        )
        return await _run_sgr(
            self._weak_agent,
            prompt,
            settings=self.settings,
            model_name=self.answer_check_model,
            timeout_seconds=self.WEAK_ANSWER_TIMEOUT,
            temperature=0.35,
        )

    # ------------------------------------------------------------------
    # 5. Section Understanding (cached externally)
    # ------------------------------------------------------------------

    async def understand_section(
        self,
        *,
        section_id: str,
        title: str | None,
        breadcrumb: list[str],
        source_markdown: str,
        contains_explicit_tasks: bool = False,
        contains_solution_like_content: bool = False,
        contains_review_like_content: bool = False,
    ) -> SectionUnderstanding:
        """Analyze a textbook section for teaching purposes."""
        prompt = build_section_understanding_prompt(
            section_id=section_id,
            title=title,
            breadcrumb=breadcrumb,
            source_markdown=source_markdown,
            contains_explicit_tasks=contains_explicit_tasks,
            contains_solution_like_content=contains_solution_like_content,
            contains_review_like_content=contains_review_like_content,
        )
        return await _run_sgr(
            self._section_agent,
            prompt,
            settings=self.settings,
            model_name=self.section_understanding_model,
            timeout_seconds=self.SECTION_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # 6. Learner Memory Synthesis
    # ------------------------------------------------------------------

    async def synthesize_memory(
        self,
        *,
        current_memory: dict[str, Any] | None,
        session_interactions: list[dict[str, Any]],
        sections_covered: list[str],
        evaluation_results: list[dict[str, Any]],
        learning_debt: list[dict[str, Any]],
    ) -> LearnerMemory:
        """Synthesize or update the persistent learner memory."""
        prompt = build_learner_memory_prompt(
            current_memory=current_memory,
            session_interactions=session_interactions,
            sections_covered=sections_covered,
            evaluation_results=evaluation_results,
            learning_debt=learning_debt,
        )
        return await _run_sgr(
            self._memory_agent,
            prompt,
            settings=self.settings,
            model_name=self.model,
            timeout_seconds=self.MEMORY_TIMEOUT,
            temperature=0.1,
        )


__all__ = ["TeacherEngine"]
