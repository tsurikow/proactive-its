"""
Narrative prompt builder for SGR calls.

Replaces JSON-dump prompts with structured prose sections.
Each builder produces a user prompt for one SGR schema call.
"""

from __future__ import annotations

from typing import Any

from app.teacher.models import (
    CheckpointEvaluation,
    PendingTeacherTask,
    RepairHistorySummary,
    SectionUnderstandingArtifact,
    TeacherProposal,
    TeacherSessionEventType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section(title: str, body: str) -> str:
    """Format a named section in the prompt."""
    body = body.strip()
    if not body:
        return ""
    return f"## {title}\n{body}\n\n"


def _field(label: str, value: Any) -> str:
    """Format a key-value line. Skips None values."""
    if value is None:
        return ""
    return f"- **{label}**: {value}\n"


def _conversation_block(history: list[dict[str, str]]) -> str:
    """Format recent conversation turns."""
    if not history:
        return "_No conversation history yet._"
    lines = []
    for turn in history:
        role = turn.get("role", "?")
        text = turn.get("text", "").strip()
        if role == "learner":
            lines.append(f"**Student**: {text}")
        else:
            lines.append(f"**Teacher**: {text}")
    return "\n".join(lines)


def _last_teacher_message(history: list[dict[str, str]]) -> str | None:
    """Extract the last teacher message from history."""
    for turn in reversed(history):
        if turn.get("role") == "teacher":
            text = turn.get("text", "").strip()
            if text:
                return text[:500]
    return None


def _stage_description(stage: dict[str, Any] | None) -> str:
    """Describe the current stage/section."""
    if not stage:
        return "No active section."
    title = stage.get("title") or "untitled"
    section_id = stage.get("section_id") or "?"
    breadcrumb = " → ".join(stage.get("breadcrumb") or [])
    parts = [f"{title} ({section_id})"]
    if breadcrumb:
        parts.append(f"Path: {breadcrumb}")
    return "\n".join(parts)


def _understanding_summary(su: SectionUnderstandingArtifact | None) -> str:
    """Summarize section understanding for prompt context."""
    if su is None:
        return "Section understanding not yet available."
    lines = [
        f"- **Pedagogical role**: {su.pedagogical_role.value}",
        f"- **Teaching intent**: {su.teaching_intent}",
        f"- **Should dwell**: {su.should_dwell}",
        f"- **Supports generated questions**: {su.supports_generated_question}",
    ]
    if su.explicit_exercises:
        refs = [e.exercise_ref for e in su.explicit_exercises]
        lines.append(f"- **Exercises available**: {', '.join(refs)}")
    if su.explicit_checkpoints:
        refs = [c.checkpoint_ref for c in su.explicit_checkpoints]
        lines.append(f"- **Checkpoints available**: {', '.join(refs)}")
    actions = [a.value for a in su.recommended_actions]
    if actions:
        lines.append(f"- **Recommended actions**: {', '.join(actions)}")
    return "\n".join(lines)


def _pending_task_description(task: PendingTeacherTask | None) -> str:
    """Describe the pending task for context."""
    if task is None:
        return "No pending task."
    lines = [
        f"- **Type**: {task.task_kind.value}",
        f"- **Item**: {task.item_ref} (section {task.section_id})",
        f"- **Prompt**: {task.prompt_excerpt}",
        f"- **Attempts so far**: {task.attempt_count}",
    ]
    if task.answer_check_context:
        ctx = task.answer_check_context
        if ctx.rubric_brief:
            lines.append(f"- **Rubric**: {ctx.rubric_brief}")
        if ctx.answer_source_excerpt:
            lines.append(f"- **Answer source**: {ctx.answer_source_excerpt}")
    return "\n".join(lines)


def _learner_memory_summary(memory: dict[str, Any] | None) -> str:
    """Format the persistent learner memory."""
    if not memory:
        return "No learner history available yet (first session)."
    parts = []
    for key in ("strengths", "misconceptions", "pace_observation",
                "engagement_level", "teaching_recommendations",
                "priority_revisit_topics", "learning_debt_summary"):
        val = memory.get(key)
        if val:
            label = key.replace("_", " ").title()
            if isinstance(val, list):
                parts.append(f"- **{label}**: {', '.join(str(v) for v in val)}")
            else:
                parts.append(f"- **{label}**: {val}")
    return "\n".join(parts) if parts else "No learner history available yet."


# ---------------------------------------------------------------------------
# Prompt builders — one per SGR schema
# ---------------------------------------------------------------------------

def build_intent_and_route_prompt(
    *,
    learner_message: str,
    current_stage: dict[str, Any] | None,
    pending_task: PendingTeacherTask | None,
    recent_proposal: TeacherProposal | None,
    section_understanding: SectionUnderstandingArtifact | None,
    conversation_history: list[dict[str, str]],
    learner_memory: dict[str, Any] | None = None,
) -> str:
    """Build the user prompt for IntentAndRoute SGR call."""
    parts = []

    parts.append(_section("Learner message", learner_message or "(empty)"))

    parts.append(_section("Current section", _stage_description(current_stage)))

    if pending_task is not None:
        parts.append(_section(
            "Active pending task (learner should be answering this)",
            _pending_task_description(pending_task),
        ))

    if recent_proposal is not None:
        parts.append(_section(
            "Active teacher proposal (learner may accept or refuse)",
            (
                f"- **Type**: {recent_proposal.proposal_type.value}\n"
                f"- **Target**: {recent_proposal.target_section_id or recent_proposal.target_title or 'current'}\n"
                f"- **Rationale**: {recent_proposal.rationale}\n"
                f"- **Can defer**: {recent_proposal.can_defer}"
            ),
        ))

    parts.append(_section("Section understanding", _understanding_summary(section_understanding)))

    parts.append(_section("Recent conversation", _conversation_block(conversation_history)))

    if learner_memory:
        parts.append(_section("Learner profile", _learner_memory_summary(learner_memory)))

    parts.append(_section(
        "Your task",
        "Classify the learner's intent and decide how to respond. "
        "Fill the reasoning fields first, then make your decisions.",
    ))

    return "".join(parts)


def build_teacher_turn_prompt(
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
) -> str:
    """Build the user prompt for TeacherTurn SGR call."""
    parts = []

    # What triggered this turn
    trigger_text = trigger
    if event_type:
        trigger_text = f"{trigger} (event: {event_type.value})"
    if learner_message:
        trigger_text += f"\n\nLearner said: \"{learner_message}\""
    if event_type == TeacherSessionEventType.OPEN_SESSION:
        trigger_text += (
            "\n\nThis is a SESSION START. Your message should be a brief greeting "
            "or recap of where you left off. Do NOT deliver learning material yet — "
            "wait for the student's response first."
        )
    parts.append(_section("What happened", trigger_text))

    parts.append(_section("Current section", _stage_description(current_stage)))

    parts.append(_section("Section understanding", _understanding_summary(section_understanding)))

    if section_source_md:
        # Truncate very long sections to avoid context overflow
        source = section_source_md if len(section_source_md) < 6000 else section_source_md[:6000] + "\n\n[...truncated]"
        parts.append(_section("Section source material", source))

    if pending_task is not None:
        parts.append(_section("Pending task", _pending_task_description(pending_task)))

    if checkpoint_evaluation is not None:
        parts.append(_section(
            "Answer evaluation result",
            (
                f"- **Status**: {checkpoint_evaluation.status.value}\n"
                f"- **Feedback**: {checkpoint_evaluation.teacher_feedback_brief}\n"
                f"- **Rationale**: {checkpoint_evaluation.rationale}"
            ),
        ))

    # Navigation options
    nav_parts = []
    if next_stage:
        nav_parts.append(f"- **Next section**: {_stage_description(next_stage)}")
    if revisit_candidates:
        for rc in revisit_candidates[:3]:
            nav_parts.append(
                f"- **Revisit candidate**: {rc.get('target_title', rc.get('target_section_id', '?'))} "
                f"— {rc.get('reason_summary', 'no reason given')}"
            )
    if learning_debt:
        debts = [f"{d.get('debt_kind', '?')} in {d.get('section_id', '?')}" for d in learning_debt[:5]]
        nav_parts.append(f"- **Outstanding learning debt**: {'; '.join(debts)}")
    if nav_parts:
        parts.append(_section("Navigation options", "\n".join(nav_parts)))

    parts.append(_section("Recent conversation", _conversation_block(conversation_history)))

    # Surface last teacher message to prevent repetition
    last_teacher_msg = _last_teacher_message(conversation_history)
    if last_teacher_msg:
        parts.append(_section(
            "Your last message (DO NOT repeat)",
            last_teacher_msg,
        ))

    if learner_memory:
        parts.append(_section("Learner profile", _learner_memory_summary(learner_memory)))

    parts.append(_section(
        "Your task",
        "Decide the best pedagogical action and write your message to the student. "
        "Fill reasoning fields first. Your message should be natural, teacher-like, "
        "and grounded in the section material. Never reveal answers unless asked.",
    ))

    return "".join(parts)


def build_answer_evaluation_prompt(
    *,
    learner_message: str,
    pending_task: PendingTeacherTask,
    conversation_history: list[dict[str, str]],
) -> str:
    """Build the user prompt for AnswerEvaluation SGR call."""
    parts = []

    parts.append(_section("Learner's answer", learner_message))

    parts.append(_section("Task being answered", _pending_task_description(pending_task)))

    parts.append(_section("Recent conversation", _conversation_block(conversation_history)))

    parts.append(_section(
        "Your task",
        "Evaluate the learner's answer against the source-backed verification basis. "
        "Fill reasoning fields step by step before deciding the verdict. "
        "Be fair: accept correct paraphrases. Judge by concepts, not exact wording. "
        "Do NOT reveal the answer in your feedback — guide the learner instead.",
    ))

    return "".join(parts)


def build_weak_answer_plan_prompt(
    *,
    learner_message: str,
    evaluation: CheckpointEvaluation,
    pending_task: PendingTeacherTask,
    repair_history: RepairHistorySummary | None,
    conversation_history: list[dict[str, str]],
    revisit_candidates: list[dict[str, Any]] | None = None,
) -> str:
    """Build the user prompt for WeakAnswerPlan SGR call."""
    parts = []

    parts.append(_section(
        "Evaluation result",
        (
            f"- **Status**: {evaluation.status.value}\n"
            f"- **Missing/wrong**: {evaluation.missing_or_wrong_piece or 'n/a'}\n"
            f"- **Feedback**: {evaluation.teacher_feedback_brief}\n"
            f"- **Confidence**: {evaluation.confidence}"
        ),
    ))

    parts.append(_section("Learner's answer", learner_message))

    parts.append(_section("Task", _pending_task_description(pending_task)))

    if repair_history:
        lines = [
            f"- **Attempt number**: {repair_history.weak_attempt_ordinal}",
            f"- **Previous repair modes**: {[m.value for m in repair_history.recent_repair_modes]}",
            f"- **Previous statuses**: {repair_history.recent_weak_statuses}",
        ]
        if repair_history.trajectory_summary:
            lines.append(f"- **Trajectory**: {repair_history.trajectory_summary}")
        parts.append(_section("Repair history", "\n".join(lines)))

    if revisit_candidates:
        rc_lines = [
            f"- {rc.get('target_title', rc.get('target_section_id', '?'))}: {rc.get('reason_summary', '')}"
            for rc in revisit_candidates[:3]
        ]
        parts.append(_section("Revisit candidates (if giving up)", "\n".join(rc_lines)))

    parts.append(_section("Recent conversation", _conversation_block(conversation_history)))

    parts.append(_section(
        "Your task",
        "Decide how to help the learner after their incorrect/partial/unresolved answer. "
        "Fill reasoning fields first. Choose repair if the learner is making progress, "
        "clarify if one specific detail is missing, revisit only as last resort. "
        "Your message should encourage the learner and guide them without giving away the answer.",
    ))

    return "".join(parts)


def build_section_understanding_prompt(
    *,
    section_id: str,
    title: str | None,
    breadcrumb: list[str],
    source_markdown: str,
    contains_explicit_tasks: bool,
    contains_solution_like_content: bool,
    contains_review_like_content: bool,
) -> str:
    """Build the user prompt for SectionUnderstanding SGR call."""
    parts = []

    meta_lines = [
        f"- **Section ID**: {section_id}",
        f"- **Title**: {title or 'untitled'}",
    ]
    if breadcrumb:
        meta_lines.append(f"- **Path**: {' → '.join(breadcrumb)}")
    meta_lines.extend([
        f"- **Contains explicit tasks**: {contains_explicit_tasks}",
        f"- **Contains solution-like content**: {contains_solution_like_content}",
        f"- **Contains review-like content**: {contains_review_like_content}",
    ])
    parts.append(_section("Section metadata", "\n".join(meta_lines)))

    # Full source — this is the main input
    parts.append(_section("Section source", source_markdown))

    parts.append(_section(
        "Your task",
        "Analyze this textbook section for teaching. "
        "Fill reasoning fields first, then classify the section and extract tasks. "
        "Only extract exercises and checkpoints that are ACTUALLY in the source. "
        "Do not invent tasks, answers, or verification criteria that aren't source-backed. "
        "For derived review checkpoints, only include them if you can provide "
        "a source-backed verification basis.",
    ))

    return "".join(parts)


def build_learner_memory_prompt(
    *,
    current_memory: dict[str, Any] | None,
    session_interactions: list[dict[str, Any]],
    sections_covered: list[str],
    evaluation_results: list[dict[str, Any]],
    learning_debt: list[dict[str, Any]],
) -> str:
    """Build the user prompt for LearnerMemory SGR call."""
    parts = []

    if current_memory:
        parts.append(_section("Current learner profile", _learner_memory_summary(current_memory)))
    else:
        parts.append(_section("Current learner profile", "This is the first session — no prior history."))

    # Session summary
    interaction_lines = []
    for ix in session_interactions[-20:]:  # Last 20 interactions
        role = ix.get("role", "?")
        text = (ix.get("text") or "")[:200]
        if ix.get("evaluation_status"):
            text += f" [evaluation: {ix['evaluation_status']}]"
        interaction_lines.append(f"- **{role}**: {text}")
    if interaction_lines:
        parts.append(_section("Session interactions", "\n".join(interaction_lines)))

    if sections_covered:
        parts.append(_section("Sections covered this session", ", ".join(sections_covered)))

    if evaluation_results:
        eval_lines = [
            f"- {e.get('item_ref', '?')}: {e.get('status', '?')} (confidence {e.get('confidence', '?')})"
            for e in evaluation_results
        ]
        parts.append(_section("Answer evaluations this session", "\n".join(eval_lines)))

    if learning_debt:
        debt_lines = [
            f"- {d.get('debt_kind', '?')} in section {d.get('section_id', '?')}"
            for d in learning_debt
        ]
        parts.append(_section("Outstanding learning debt", "\n".join(debt_lines)))

    parts.append(_section(
        "Your task",
        "Update the learner profile based on this session's evidence. "
        "Fill reasoning fields first. Be specific about what the learner "
        "demonstrated or struggled with. Recommendations should be concrete "
        "and actionable for the next session's teacher.",
    ))

    return "".join(parts)
