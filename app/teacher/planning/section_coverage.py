from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.teacher.models import (
    AnswerCheckContext,
    CheckpointCandidate,
    ExerciseCandidate,
    SectionSemanticType,
    SectionUnderstandingArtifact,
    TeacherActionType,
)

SectionFamily = Literal[
    "introduction",
    "formula_reference",
    "review",
    "worked_example",
    "task_bearing",
    "core_concept",
    "other",
]

IssueFlag = Literal[
    "fallback_understanding_used",
    "lightweight_section_overdwell",
    "lightweight_section_has_tasks",
    "review_section_missing_verifiable_checkpoint",
    "task_section_missing_verification_context",
    "exercise_bank_missing_literal_exercises",
    "checkpoint_context_missing_verification_basis",
]


@dataclass(frozen=True)
class SectionCoverageDiagnostics:
    section_family: SectionFamily
    issue_flags: list[IssueFlag] = field(default_factory=list)


LIGHTWEIGHT_FAMILIES: set[SectionFamily] = {"introduction", "formula_reference"}


def classify_section_family(
    artifact: SectionUnderstandingArtifact | None,
    current_stage: dict[str, object] | None,
) -> SectionFamily:
    if artifact is not None:
        role = artifact.pedagogical_role
        if role == SectionSemanticType.INTRODUCTION:
            return "introduction"
        if role == SectionSemanticType.FORMULA_REFERENCE:
            return "formula_reference"
        if role == SectionSemanticType.REVIEW:
            return "review"
        if role == SectionSemanticType.WORKED_EXAMPLE:
            return "worked_example"
        if role in {SectionSemanticType.CHECKPOINT, SectionSemanticType.EXERCISE_BANK}:
            return "task_bearing"
        if role == SectionSemanticType.CORE_CONCEPT:
            return "core_concept"
        return "other"

    title = str((current_stage or {}).get("title") or "").strip()
    breadcrumb = [str(item).strip() for item in (current_stage or {}).get("breadcrumb") or []]
    text = " ".join([title, *breadcrumb]).lower()
    if "introduction" in text:
        return "introduction"
    if "key equation" in text or "formula" in text:
        return "formula_reference"
    if "review" in text or "key concept" in text:
        return "review"
    if "example" in text:
        return "worked_example"
    if "exercise" in text or "checkpoint" in text:
        return "task_bearing"
    if text:
        return "core_concept"
    return "other"


def normalize_section_understanding_artifact(
    artifact: SectionUnderstandingArtifact,
    *,
    current_stage: dict[str, object] | None,
) -> SectionUnderstandingArtifact:
    family = classify_section_family(artifact, current_stage)
    exercise_map = {item.exercise_ref: item for item in artifact.explicit_exercises}
    checkpoint_map = {item.checkpoint_ref: item for item in artifact.explicit_checkpoints}
    kept_exercises: list[ExerciseCandidate] = list(exercise_map.values())
    kept_checkpoints: list[CheckpointCandidate] = list(checkpoint_map.values())

    normalized_contexts: list[AnswerCheckContext] = []
    seen_context_refs: set[str] = set()
    for context in artifact.answer_check_contexts:
        if context.item_ref in seen_context_refs:
            continue
        if context.item_ref not in exercise_map and context.item_ref not in checkpoint_map:
            continue
        can_verify = bool(
            context.can_verify
            and (
                str(context.hidden_answer_ref or "").strip()
                or str(context.answer_source_excerpt or "").strip()
                or str(context.rubric_brief or "").strip()
            )
        )
        if context.item_type == "checkpoint" and can_verify and not str(context.rubric_brief or "").strip():
            can_verify = False
        normalized_contexts.append(
            context.model_copy(
                update={
                    "can_verify": can_verify,
                }
            )
        )
        seen_context_refs.add(context.item_ref)

    if family in LIGHTWEIGHT_FAMILIES:
        kept_exercises = []
        kept_checkpoints = []
        normalized_contexts = []
        supports_generated_question = family == "formula_reference"
        recommended_actions = [
            item
            for item in artifact.recommended_actions
            if item not in {TeacherActionType.ASSIGN_SECTION_EXERCISE}
        ]
        if not supports_generated_question:
            recommended_actions = [
                item for item in recommended_actions if item != TeacherActionType.ASK_SECTION_QUESTION
            ]
        if not recommended_actions:
            recommended_actions = [TeacherActionType.TEACH_SECTION]
        return artifact.model_copy(
            update={
                "should_dwell": False,
                "supports_generated_question": supports_generated_question,
                "explicit_exercises": kept_exercises,
                "explicit_checkpoints": kept_checkpoints,
                "answer_check_contexts": normalized_contexts,
                "recommended_actions": recommended_actions,
            }
        )

    if family == "review":
        verifiable_checkpoint_refs = {
            context.item_ref
            for context in normalized_contexts
            if context.item_type == "checkpoint"
            and context.can_verify
            and str(context.rubric_brief or "").strip()
        }
        kept_checkpoints = [item for item in kept_checkpoints if item.checkpoint_ref in verifiable_checkpoint_refs]
        allowed_refs = {item.checkpoint_ref for item in kept_checkpoints} | {
            item.exercise_ref for item in kept_exercises
        }
        normalized_contexts = [item for item in normalized_contexts if item.item_ref in allowed_refs]

    if family == "task_bearing":
        contexts_by_ref = {item.item_ref: item for item in normalized_contexts}
        for exercise in kept_exercises:
            if exercise.exercise_ref not in contexts_by_ref:
                placeholder = AnswerCheckContext(
                    item_ref=exercise.exercise_ref,
                    item_type="exercise",
                    hidden_answer_ref=exercise.hidden_answer_ref,
                    can_verify=False,
                )
                normalized_contexts.append(placeholder)
                contexts_by_ref[exercise.exercise_ref] = placeholder
        for checkpoint in kept_checkpoints:
            if checkpoint.checkpoint_ref not in contexts_by_ref:
                placeholder = AnswerCheckContext(
                    item_ref=checkpoint.checkpoint_ref,
                    item_type="checkpoint",
                    hidden_answer_ref=checkpoint.hidden_answer_ref,
                    can_verify=False,
                )
                normalized_contexts.append(placeholder)
                contexts_by_ref[checkpoint.checkpoint_ref] = placeholder

    return artifact.model_copy(
        update={
            "explicit_exercises": kept_exercises,
            "explicit_checkpoints": kept_checkpoints,
            "answer_check_contexts": normalized_contexts,
        }
    )


def collect_section_issue_flags(
    artifact: SectionUnderstandingArtifact,
    *,
    current_stage: dict[str, object] | None,
    fallback_used: bool,
) -> SectionCoverageDiagnostics:
    family = classify_section_family(artifact, current_stage)
    issue_flags: list[IssueFlag] = []
    if fallback_used:
        issue_flags.append("fallback_understanding_used")
    if family in LIGHTWEIGHT_FAMILIES and artifact.should_dwell:
        issue_flags.append("lightweight_section_overdwell")
    if family in LIGHTWEIGHT_FAMILIES and (
        artifact.explicit_exercises or artifact.explicit_checkpoints or artifact.answer_check_contexts
    ):
        issue_flags.append("lightweight_section_has_tasks")
    if family == "review":
        has_verifiable_checkpoint = False
        contexts_by_ref = {item.item_ref: item for item in artifact.answer_check_contexts}
        for checkpoint in artifact.explicit_checkpoints:
            context = contexts_by_ref.get(checkpoint.checkpoint_ref)
            if context is None:
                continue
            if context.can_verify and str(context.rubric_brief or "").strip():
                has_verifiable_checkpoint = True
                break
        if not has_verifiable_checkpoint:
            issue_flags.append("review_section_missing_verifiable_checkpoint")
    if family == "task_bearing":
        contexts_by_ref = {item.item_ref: item for item in artifact.answer_check_contexts}
        missing_context = any(
            item.exercise_ref not in contexts_by_ref for item in artifact.explicit_exercises
        ) or any(
            item.checkpoint_ref not in contexts_by_ref for item in artifact.explicit_checkpoints
        )
        if missing_context:
            issue_flags.append("task_section_missing_verification_context")
        if (
            artifact.pedagogical_role == SectionSemanticType.EXERCISE_BANK
            and not artifact.explicit_exercises
        ):
            issue_flags.append("exercise_bank_missing_literal_exercises")
    if any(
        item.can_verify
        and not (
            str(item.hidden_answer_ref or "").strip()
            or str(item.answer_source_excerpt or "").strip()
            or str(item.rubric_brief or "").strip()
        )
        for item in artifact.answer_check_contexts
    ):
        issue_flags.append("checkpoint_context_missing_verification_basis")
    if any(
        item.item_type == "checkpoint" and item.can_verify and not str(item.rubric_brief or "").strip()
        for item in artifact.answer_check_contexts
    ):
        if "checkpoint_context_missing_verification_basis" not in issue_flags:
            issue_flags.append("checkpoint_context_missing_verification_basis")
    return SectionCoverageDiagnostics(
        section_family=family,
        issue_flags=issue_flags,
    )
