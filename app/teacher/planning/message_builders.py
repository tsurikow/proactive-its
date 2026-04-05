from __future__ import annotations

from typing import Any


def feedback_followup_prompt(
    *,
    current_stage: dict[str, Any] | None,
    assessment_decision: str | None,
    post_feedback_mastery: float | None,
    applied_progression_variant: str,
    target_stage: dict[str, Any] | None,
    learner_teaching_brief: str | None,
    next_step_summary: str,
) -> str:
    return (
        "Produce one structured teacher acknowledgement after learner feedback was recorded.\n"
        "teacher_message must be Markdown and stay to 1-2 sentences.\n"
        "acknowledgement_focus should summarize what learner signal or next-step emphasis the message is acknowledging.\n"
        "Acknowledge the learner signal and say it will shape the next step in the same teacher conversation.\n"
        "Do not claim the stage has already changed. Do not promise auto-advance. Do not mention internal variants or raw enum values.\n\n"
        f"Next-step summary: {next_step_summary}\n"
        f"Applied progression variant: {applied_progression_variant}\n"
        f"Assessment decision: {assessment_decision}\n"
        f"Post-feedback mastery: {post_feedback_mastery}\n"
        f"Current stage title: {str((current_stage or {}).get('title') or '')}\n"
        f"Target stage title: {str((target_stage or {}).get('title') or '')}\n"
        f"Learner teaching brief: {learner_teaching_brief or 'none'}\n"
    )


def feedback_acknowledgement_message(
    *,
    confidence: int,
    assessment_decision: str | None,
) -> str:
    normalized_decision = str(assessment_decision or "").strip()
    if normalized_decision in {"misconception", "procedural_error", "off_topic", "insufficient_evidence"}:
        return "Feedback saved. I’ll use that signal to slow down and shape the next step."
    if confidence >= 4:
        return "Feedback saved. I’ll use that signal to decide the next step."
    return "Feedback saved. I’ll use that signal to adjust the next step."


def feedback_message_for_progression(
    *,
    progression_variant: str,
    remediation_progression_variant: str,
    revisit_progression_variant: str,
    auto_advance_progression_variant: str,
    assessment_decision: str | None,
    target_stage: dict[str, Any] | None,
    plan_completed: bool,
) -> str:
    if progression_variant == auto_advance_progression_variant:
        if plan_completed:
            return "Strong result recorded. You completed the current plan."
        return "Strong result recorded. Moving you to the next stage."
    if progression_variant == revisit_progression_variant:
        title = str((target_stage or {}).get("title") or (target_stage or {}).get("section_id") or "the prerequisite stage")
        return (
            f"Feedback saved. We are stepping back to **{title}** first so we can strengthen the foundation "
            "before moving forward again."
        )
    if progression_variant != remediation_progression_variant:
        return "Feedback saved. Continue when ready."
    if assessment_decision == "procedural_error":
        return "Feedback saved. We will stay on this stage and repair the procedure step by step before moving on."
    return "Feedback saved. We will stay on this stage and rebuild the core idea before moving on."


__all__ = [
    "feedback_acknowledgement_message",
    "feedback_followup_prompt",
    "feedback_message_for_progression",
]
