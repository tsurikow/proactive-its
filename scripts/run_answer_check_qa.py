from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.api.dependencies import (
    get_learner_service,
    get_teacher_policy_engine,
    get_section_understanding_service,
    get_teacher_state_service,
)
from app.platform.logging import configure_logging
from app.state.stage_state import template_targets
from app.teacher.models import (
    AnswerCheckQACaseType,
    AnswerCheckQAFixtureRequest,
    AnswerCheckRequest,
    CheckpointEvaluation,
    CheckpointEvaluationStatus,
    PendingTeacherTask,
    PendingTeacherTaskKind,
)
from app.teacher.planning.pending_task_runtime import PendingTaskRuntime
from app.teacher.planning.section_coverage import classify_section_family

QA_LEARNER_ID = "__answer_check_qa__"
DEFAULT_OUTPUT_DIR = Path(".local/codex/notes/qa")
MISMATCH_MATCH = "match"
MISMATCH_COVERAGE_BLOCKED = "coverage_blocked_upstream"
UNRESOLVED_SIGNALS = (
    "vague",
    "underspecified",
    "unclear",
    "not enough",
    "insufficient",
    "ambiguous",
    "off-target",
)
GAP_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run internal curriculum QA over canonical answer-check behavior.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional stage limit for smoke/debug runs. Default is the full template.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for answer-check QA artifacts.",
    )
    parser.add_argument(
        "--stage-timeout-seconds",
        type=float,
        default=180.0,
        help="Per-stage timeout so one slow section does not block the full answer-check QA pass.",
    )
    parser.add_argument(
        "--section-id",
        action="append",
        default=[],
        help="Optional repeated section id filter for targeted replay.",
    )
    parser.add_argument(
        "--stage-index",
        action="append",
        type=int,
        default=[],
        help="Optional repeated stage index filter for targeted replay.",
    )
    parser.add_argument(
        "--mismatch",
        action="append",
        default=[],
        help="Optional repeated mismatch filter resolved from an existing answer-check scorecard.",
    )
    parser.add_argument(
        "--runner-error",
        action="append",
        default=[],
        help="Optional repeated runner-error filter resolved from an existing answer-check scorecard.",
    )
    parser.add_argument(
        "--flush-every",
        type=int,
        default=5,
        help="Write partial scorecard artifacts after every N completed stages.",
    )
    parser.add_argument(
        "--max-stage-concurrency",
        type=int,
        default=2,
        help="Bounded number of stages to process concurrently in the QA runner.",
    )
    return parser.parse_args()


def _task_source_kind(*, section_family: str, task_kind: PendingTeacherTaskKind) -> str:
    if section_family == "review" and task_kind == PendingTeacherTaskKind.CHECKPOINT_QUESTION:
        return "derived_review_checkpoint"
    return "explicit_extraction"


def _tokenize(text: str | None) -> set[str]:
    return {token for token in GAP_TOKEN_RE.findall(str(text or "").lower()) if len(token) >= 4}


def _has_gap_explanation(
    *,
    evaluation: CheckpointEvaluation,
    expected_status: CheckpointEvaluationStatus,
    expected_gap_brief: str,
) -> bool:
    if expected_status == CheckpointEvaluationStatus.CORRECT:
        return True
    gap = str(evaluation.missing_or_wrong_piece or "").strip()
    if not gap:
        return False
    if expected_status == CheckpointEvaluationStatus.UNRESOLVED:
        lowered = gap.lower()
        return any(signal in lowered for signal in UNRESOLVED_SIGNALS)
    expected_tokens = _tokenize(expected_gap_brief)
    observed_tokens = _tokenize(gap)
    if expected_tokens and expected_tokens.intersection(observed_tokens):
        return True
    return len(observed_tokens) >= 3


def _mismatch_category(
    *,
    expected_status: CheckpointEvaluationStatus,
    predicted_status: CheckpointEvaluationStatus,
    evaluation: CheckpointEvaluation,
    expected_gap_brief: str,
) -> str:
    if predicted_status != expected_status:
        if expected_status == CheckpointEvaluationStatus.CORRECT:
            return "correct_downgraded"
        if expected_status == CheckpointEvaluationStatus.PARTIAL and predicted_status == CheckpointEvaluationStatus.INCORRECT:
            return "partial_flattened_to_incorrect"
        if expected_status == CheckpointEvaluationStatus.PARTIAL and predicted_status == CheckpointEvaluationStatus.UNRESOLVED:
            return "partial_flattened_to_unresolved"
        if expected_status == CheckpointEvaluationStatus.INCORRECT and predicted_status in {
            CheckpointEvaluationStatus.PARTIAL,
            CheckpointEvaluationStatus.CORRECT,
            CheckpointEvaluationStatus.UNRESOLVED,
        }:
            return "incorrect_softened"
        if expected_status == CheckpointEvaluationStatus.UNRESOLVED and predicted_status in {
            CheckpointEvaluationStatus.PARTIAL,
            CheckpointEvaluationStatus.INCORRECT,
            CheckpointEvaluationStatus.CORRECT,
        }:
            return "unresolved_overcommitted"
        return "status_mismatch"
    if not _has_gap_explanation(
        evaluation=evaluation,
        expected_status=expected_status,
        expected_gap_brief=expected_gap_brief,
    ):
        return "missing_gap_explanation"
    return MISMATCH_MATCH


def _select_verifiable_tasks(artifact: Any) -> list[PendingTeacherTask]:
    selected: list[PendingTeacherTask] = []
    contexts_by_ref = {item.item_ref: item for item in artifact.answer_check_contexts}

    for checkpoint in artifact.explicit_checkpoints:
        context = contexts_by_ref.get(checkpoint.checkpoint_ref)
        if context is None or not context.can_verify:
            continue
        selected.append(
            PendingTeacherTask(
                task_kind=PendingTeacherTaskKind.CHECKPOINT_QUESTION,
                section_id=artifact.section_id,
                prompt_excerpt=checkpoint.prompt_excerpt,
                item_ref=checkpoint.checkpoint_ref,
                hidden_answer_ref=checkpoint.hidden_answer_ref,
                answer_check_context=context,
            )
        )
        break

    for exercise in artifact.explicit_exercises:
        context = contexts_by_ref.get(exercise.exercise_ref)
        if context is None or not context.can_verify:
            continue
        selected.append(
            PendingTeacherTask(
                task_kind=PendingTeacherTaskKind.SECTION_EXERCISE,
                section_id=artifact.section_id,
                prompt_excerpt=exercise.prompt_excerpt,
                item_ref=exercise.exercise_ref,
                hidden_answer_ref=exercise.hidden_answer_ref,
                answer_check_context=context,
            )
        )
        break

    return selected


def _coverage_blocked_row(
    *,
    stage: dict[str, Any],
    section_family: str,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "row_type": "coverage_blocked",
        "stage_index": int(stage.get("stage_index", -1)),
        "section_id": str(stage.get("section_id") or ""),
        "title": str(stage.get("title") or ""),
        "section_family": section_family,
        "task_kind": None,
        "task_source_kind": None,
        "item_ref": None,
        "case_type": None,
        "expected_status": None,
        "predicted_status": None,
        "mismatch_category": MISMATCH_COVERAGE_BLOCKED,
        "issue_flags": [str(item) for item in diagnostics.get("final_issue_flags") or []],
        "fallback_reason": None if diagnostics.get("fallback_reason") is None else str(diagnostics.get("fallback_reason")),
        "evaluation_source": str(diagnostics.get("source") or "unknown"),
        "fixture_source": None,
        "runner_error": None,
    }


def _case_row(
    *,
    stage: dict[str, Any],
    section_family: str,
    task: PendingTeacherTask,
    task_source_kind: str,
    case_type: str,
    expected_status: str,
    predicted_status: str,
    mismatch_category: str,
    evaluation_source: str,
    fallback_reason: str | None,
    fixture_source: str,
    learner_response: str,
    expected_gap_brief: str,
    evaluation: CheckpointEvaluation,
) -> dict[str, Any]:
    return {
        "row_type": "case",
        "stage_index": int(stage.get("stage_index", -1)),
        "section_id": str(stage.get("section_id") or ""),
        "title": str(stage.get("title") or ""),
        "section_family": section_family,
        "task_kind": task.task_kind.value,
        "task_source_kind": task_source_kind,
        "item_ref": task.item_ref,
        "case_type": case_type,
        "expected_status": expected_status,
        "predicted_status": predicted_status,
        "mismatch_category": mismatch_category,
        "issue_flags": [],
        "fallback_reason": fallback_reason,
        "evaluation_source": evaluation_source,
        "fixture_source": fixture_source,
        "learner_response": learner_response,
        "expected_gap_brief": expected_gap_brief,
        "teacher_feedback_brief": evaluation.teacher_feedback_brief,
        "missing_or_wrong_piece": evaluation.missing_or_wrong_piece,
        "rationale": evaluation.rationale,
        "runner_error": None,
    }


def _error_row(
    *,
    stage: dict[str, Any],
    section_family: str,
    task: PendingTeacherTask | None,
    task_source_kind: str | None,
    error: str,
    fixture_source: str | None = None,
) -> dict[str, Any]:
    return {
        "row_type": "runner_error",
        "stage_index": int(stage.get("stage_index", -1)),
        "section_id": str(stage.get("section_id") or ""),
        "title": str(stage.get("title") or ""),
        "section_family": section_family,
        "task_kind": None if task is None else task.task_kind.value,
        "task_source_kind": task_source_kind,
        "item_ref": None if task is None else task.item_ref,
        "case_type": None,
        "expected_status": None,
        "predicted_status": None,
        "mismatch_category": None,
        "issue_flags": [],
        "fallback_reason": error,
        "evaluation_source": "runner_error",
        "fixture_source": fixture_source,
        "runner_error": error,
    }


def _runner_error_bucket(error: str | None) -> str | None:
    value = str(error or "").strip()
    if not value:
        return None
    if value.startswith("stage_timeout_after_"):
        return "runner_timeout"
    if value.startswith("answer_check_qa_fixture"):
        return "fixture_generation_error"
    return "runner_error"


def _load_scorecard_rows(output_dir: Path) -> list[dict[str, Any]]:
    scorecard_path = output_dir / "answer_check_scorecard.json"
    if not scorecard_path.exists():
        raise RuntimeError(f"scorecard_not_found:{scorecard_path}")
    payload = json.loads(scorecard_path.read_text(encoding="utf-8"))
    return [dict(item) for item in payload.get("rows") or []]


def _select_stage_targets(
    stage_targets: list[dict[str, Any]],
    *,
    limit: int | None,
    output_dir: Path,
    section_ids: list[str],
    stage_indexes: list[int],
    mismatches: list[str],
    runner_errors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested_section_ids = {str(item).strip() for item in section_ids if str(item).strip()}
    requested_stage_indexes = {int(item) for item in stage_indexes}
    requested_mismatches = {str(item).strip() for item in mismatches if str(item).strip()}
    requested_runner_errors = {str(item).strip() for item in runner_errors if str(item).strip()}

    if requested_mismatches or requested_runner_errors:
        scorecard_dir = output_dir
        scorecard_path = scorecard_dir / "answer_check_scorecard.json"
        if not scorecard_path.exists() and output_dir != DEFAULT_OUTPUT_DIR:
            scorecard_dir = DEFAULT_OUTPUT_DIR
        rows = _load_scorecard_rows(scorecard_dir)
        matched_section_ids: set[str] = set()
        matched_stage_indexes: set[int] = set()
        for row in rows:
            mismatch_value = str(row.get("mismatch_category") or "").strip()
            runner_error_value = str(row.get("runner_error") or "").strip()
            if requested_mismatches and mismatch_value not in requested_mismatches:
                continue
            if requested_runner_errors and runner_error_value not in requested_runner_errors:
                continue
            section_id = str(row.get("section_id") or "").strip()
            if section_id:
                matched_section_ids.add(section_id)
            if row.get("stage_index") is not None:
                matched_stage_indexes.add(int(row["stage_index"]))
        requested_section_ids.update(matched_section_ids)
        requested_stage_indexes.update(matched_stage_indexes)

    selected = stage_targets
    if requested_section_ids:
        selected = [item for item in selected if str(item.get("section_id") or "").strip() in requested_section_ids]
    if requested_stage_indexes:
        selected = [item for item in selected if int(item.get("stage_index", -1)) in requested_stage_indexes]
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    selection_filters = {
        "section_ids": sorted(requested_section_ids),
        "stage_indexes": sorted(requested_stage_indexes),
        "mismatches": sorted(requested_mismatches),
        "runner_errors": sorted(requested_runner_errors),
    }
    return selected, selection_filters


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    case_rows = [row for row in rows if row.get("row_type") == "case"]
    canonical_mismatch_count = sum(
        1 for row in case_rows if str(row.get("mismatch_category") or "") != MISMATCH_MATCH
    )
    mismatch_counts = Counter(str(row.get("mismatch_category") or "") for row in case_rows)
    expected_status_counts = Counter(str(row.get("expected_status") or "") for row in case_rows)
    predicted_status_counts = Counter(str(row.get("predicted_status") or "") for row in case_rows)
    section_family_counts = Counter(str(row.get("section_family") or "") for row in case_rows)
    task_kind_counts = Counter(str(row.get("task_kind") or "") for row in case_rows if row.get("task_kind"))
    task_source_kind_counts = Counter(str(row.get("task_source_kind") or "") for row in case_rows if row.get("task_source_kind"))
    selected_task_count = len(
        {
            (int(row.get("stage_index", -1)), str(row.get("item_ref") or ""))
            for row in case_rows
            if row.get("item_ref")
        }
    )
    examples: dict[str, list[str]] = defaultdict(list)
    for row in case_rows:
        category = str(row.get("mismatch_category") or "")
        if len(examples[category]) < 6:
            examples[category].append(
                f"{row['stage_index']}: {row['title'] or row['section_id']} | {row['task_kind']} | {row['case_type']}"
            )

    blocker_rows = [row for row in rows if row.get("row_type") == "coverage_blocked"]
    runner_error_rows = [row for row in rows if row.get("row_type") == "runner_error"]
    runner_error_bucket_counts = Counter(
        _runner_error_bucket(str(row.get("runner_error") or "")) or "runner_error"
        for row in runner_error_rows
    )
    runner_error_bucket_examples: dict[str, list[str]] = defaultdict(list)
    for row in runner_error_rows:
        bucket = _runner_error_bucket(str(row.get("runner_error") or "")) or "runner_error"
        if len(runner_error_bucket_examples[bucket]) < 6:
            runner_error_bucket_examples[bucket].append(
                f"{row['stage_index']}: {row['title'] or row['section_id']} | {row.get('runner_error') or 'runner_error'}"
            )
    return {
        "total_rows": len(rows),
        "total_cases": len(case_rows),
        "selected_task_count": selected_task_count,
        "canonical_mismatch_count": canonical_mismatch_count,
        "coverage_blocked_count": len(blocker_rows),
        "runner_error_count": len(runner_error_rows),
        "match_count": int(mismatch_counts.get(MISMATCH_MATCH, 0)),
        "mismatch_counts": dict(sorted(mismatch_counts.items())),
        "expected_status_counts": dict(sorted(expected_status_counts.items())),
        "predicted_status_counts": dict(sorted(predicted_status_counts.items())),
        "section_family_counts": dict(sorted(section_family_counts.items())),
        "task_kind_counts": dict(sorted(task_kind_counts.items())),
        "task_source_kind_counts": dict(sorted(task_source_kind_counts.items())),
        "mismatch_examples": dict(examples),
        "runner_error_bucket_counts": dict(sorted(runner_error_bucket_counts.items())),
        "runner_error_bucket_examples": dict(runner_error_bucket_examples),
        "coverage_blocked_examples": [
            f"{row['stage_index']}: {row['title'] or row['section_id']} | {', '.join(row.get('issue_flags') or []) or 'no_issue_flags'}"
            for row in blocker_rows[:8]
        ],
        "runner_error_examples": [
            f"{row['stage_index']}: {row['title'] or row['section_id']} | {row.get('runner_error') or 'runner_error'}"
            for row in runner_error_rows[:8]
        ],
    }


def _render_summary(*, template_id: str, summary: dict[str, Any]) -> str:
    lines = [
        "# Answer Check QA Summary",
        "",
        f"- generated_at: `{datetime.now(UTC).isoformat()}`",
        f"- template_id: `{template_id}`",
        f"- total_cases: `{summary['total_cases']}`",
        f"- selected_task_count: `{summary['selected_task_count']}`",
        f"- match_count: `{summary['match_count']}`",
        f"- canonical_mismatch_count: `{summary['canonical_mismatch_count']}`",
        f"- coverage_blocked_count: `{summary['coverage_blocked_count']}`",
        f"- runner_error_count: `{summary['runner_error_count']}`",
        "",
        "## Runner Error Split",
        "",
    ]
    for name, count in summary["runner_error_bucket_counts"].items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(
        [
        "",
        "## Mismatch Counts",
        "",
        ]
    )
    for name, count in summary["mismatch_counts"].items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(
        [
            "",
            "## Expected Status Counts",
            "",
        ]
    )
    for name, count in summary["expected_status_counts"].items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(
        [
            "",
            "## Predicted Status Counts",
            "",
        ]
    )
    for name, count in summary["predicted_status_counts"].items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(
        [
            "",
            "## Task Kind Counts",
            "",
        ]
    )
    for name, count in summary["task_kind_counts"].items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(
        [
            "",
            "## Representative Mismatch Buckets",
            "",
        ]
    )
    for name, examples in summary["mismatch_examples"].items():
        if name == MISMATCH_MATCH:
            continue
        lines.append(f"- `{name}`: {', '.join(examples) if examples else 'no examples captured'}")
    if summary["coverage_blocked_examples"]:
        lines.extend(
            [
                "",
                "## Coverage Blocked Upstream",
                "",
            ]
        )
        for example in summary["coverage_blocked_examples"]:
            lines.append(f"- {example}")
    if summary["runner_error_examples"]:
        lines.extend(
            [
                "",
                "## Runner Errors",
                "",
            ]
        )
        for example in summary["runner_error_examples"]:
            lines.append(f"- {example}")
    if summary["runner_error_bucket_examples"]:
        lines.extend(
            [
                "",
                "## Runner Error Buckets",
                "",
            ]
        )
        for bucket, examples in summary["runner_error_bucket_examples"].items():
            lines.append(f"- `{bucket}`: {', '.join(examples) if examples else 'no examples captured'}")
    return "\n".join(lines).strip() + "\n"


def _write_outputs(
    *,
    output_dir: Path,
    template_id: str,
    context_version: str,
    total_stages: int,
    completed_stages: int,
    selection_filters: dict[str, Any],
    rows: list[dict[str, Any]],
    partial: bool,
) -> None:
    summary = _summarize(rows)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "template_id": template_id,
        "qa_learner_id": QA_LEARNER_ID,
        "section_understanding_context_version": context_version,
        "total_stages": total_stages,
        "completed_stages": completed_stages,
        "is_partial": partial,
        "selection_filters": selection_filters,
        "rows": rows,
        **summary,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    scorecard_path = output_dir / "answer_check_scorecard.json"
    summary_path = output_dir / "answer_check_summary.md"
    scorecard_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(_render_summary(template_id=template_id, summary=summary), encoding="utf-8")


async def _run(
    limit: int | None,
    output_dir: Path,
    *,
    stage_timeout_seconds: float,
    section_ids: list[str],
    stage_indexes: list[int],
    mismatches: list[str],
    runner_errors: list[str],
    flush_every: int,
    max_stage_concurrency: int,
) -> None:
    state_service = get_teacher_state_service()
    learner_service = get_learner_service()
    policy_engine = get_teacher_policy_engine()
    section_understanding_service = get_section_understanding_service()

    template = await state_service.ensure_default_template()
    template_id = str(template["id"])
    stage_targets = template_targets(template)
    selected_targets, selection_filters = _select_stage_targets(
        stage_targets,
        limit=limit,
        output_dir=output_dir,
        section_ids=section_ids,
        stage_indexes=stage_indexes,
        mismatches=mismatches,
        runner_errors=runner_errors,
    )

    await state_service.ensure_learner(QA_LEARNER_ID)
    await learner_service.refresh_projection(QA_LEARNER_ID)

    total = len(selected_targets)
    stage_order = {int(stage.get("stage_index", -1)): index for index, stage in enumerate(selected_targets)}
    rows_by_stage: dict[int, list[dict[str, Any]]] = {}
    completed_stages = 0
    flush_interval = max(1, int(flush_every))
    stage_semaphore = asyncio.Semaphore(max(1, int(max_stage_concurrency)))

    async def process_stage(stage: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        section_family = classify_section_family(None, stage)
        stage_index = int(stage.get("stage_index", -1))
        async with stage_semaphore:
            stage_rows: list[dict[str, Any]] = []
            try:
                adaptation_context = await asyncio.wait_for(
                    learner_service.build_adaptation_context(
                        QA_LEARNER_ID,
                        dict(stage),
                        stage_targets,
                    ),
                    timeout=stage_timeout_seconds,
                )
                artifact, _semantics, diagnostics = await asyncio.wait_for(
                    section_understanding_service.get_or_create_section_understanding_with_diagnostics(
                        learner_id=QA_LEARNER_ID,
                        template_id=template_id,
                        current_stage=dict(stage),
                        adaptation_context=adaptation_context,
                    ),
                    timeout=stage_timeout_seconds,
                )
                if artifact is None:
                    return stage_index, [
                        _coverage_blocked_row(stage=stage, section_family=section_family, diagnostics=diagnostics)
                    ]
                section_family = str(diagnostics.get("section_family") or classify_section_family(artifact, stage))
                selected_tasks = _select_verifiable_tasks(artifact)
                if not selected_tasks:
                    issue_flags = [str(item) for item in diagnostics.get("final_issue_flags") or []]
                    if issue_flags:
                        stage_rows.append(
                            _coverage_blocked_row(stage=stage, section_family=section_family, diagnostics=diagnostics)
                        )
                    return stage_index, stage_rows

                async def process_task(task: PendingTeacherTask) -> list[dict[str, Any]]:
                    task_source_kind = _task_source_kind(section_family=section_family, task_kind=task.task_kind)
                    context = task.answer_check_context
                    if context is None:
                        return [_coverage_blocked_row(stage=stage, section_family=section_family, diagnostics=diagnostics)]
                    fixture_request = AnswerCheckQAFixtureRequest(
                        section_id=task.section_id,
                        section_title=str(stage.get("title") or "") or None,
                        section_family=section_family,
                        task_kind=task.task_kind,
                        task_source_kind=task_source_kind,
                        item_ref=task.item_ref,
                        prompt_excerpt=task.prompt_excerpt,
                        rubric_brief=context.rubric_brief,
                        answer_source_excerpt=context.answer_source_excerpt,
                        hidden_answer_ref=task.hidden_answer_ref,
                    )
                    fixture_bundle, fixture_source, fixture_fallback_reason = (
                        await policy_engine.generate_answer_check_qa_fixtures(fixture_request)
                    )
                    if fixture_bundle is None:
                        return [
                            _error_row(
                                stage=stage,
                                section_family=section_family,
                                task=task,
                                task_source_kind=task_source_kind,
                                error=fixture_fallback_reason or "answer_check_qa_fixture_unavailable",
                                fixture_source=fixture_source,
                            )
                        ]

                    async def evaluate_case(case: Any) -> dict[str, Any]:
                        fallback_evaluation = PendingTaskRuntime.fallback_checkpoint_evaluation(task=task)
                        evaluation, evaluation_source, fallback_reason = await policy_engine.evaluate_answer(
                            AnswerCheckRequest(
                                learner_id=QA_LEARNER_ID,
                                current_stage=dict(stage),
                                adaptation_context=adaptation_context,
                                pending_task=task,
                                learner_message=case.learner_response,
                            ),
                            fallback_evaluation=fallback_evaluation,
                        )
                        mismatch = _mismatch_category(
                            expected_status=case.expected_status,
                            predicted_status=evaluation.status,
                            evaluation=evaluation,
                            expected_gap_brief=case.expected_gap_brief,
                        )
                        return _case_row(
                            stage=stage,
                            section_family=section_family,
                            task=task,
                            task_source_kind=task_source_kind,
                            case_type=case.case_type.value,
                            expected_status=case.expected_status.value,
                            predicted_status=evaluation.status.value,
                            mismatch_category=mismatch,
                            evaluation_source=evaluation_source,
                            fallback_reason=fallback_reason,
                            fixture_source=fixture_source,
                            learner_response=case.learner_response,
                            expected_gap_brief=case.expected_gap_brief,
                            evaluation=evaluation,
                        )

                    return list(await asyncio.gather(*(evaluate_case(case) for case in fixture_bundle.cases)))

                task_row_groups = await asyncio.gather(*(process_task(task) for task in selected_tasks))
                for task_rows in task_row_groups:
                    stage_rows.extend(task_rows)
            except TimeoutError:
                stage_rows.append(
                    _error_row(
                        stage=stage,
                        section_family=section_family,
                        task=None,
                        task_source_kind=None,
                        error=f"stage_timeout_after_{stage_timeout_seconds:g}s",
                    )
                )
            except Exception as exc:
                stage_rows.append(
                    _error_row(
                        stage=stage,
                        section_family=section_family,
                        task=None,
                        task_source_kind=None,
                        error=str(exc),
                    )
                )
            return stage_index, stage_rows

    tasks = [asyncio.create_task(process_stage(stage)) for stage in selected_targets]
    for completed in asyncio.as_completed(tasks):
        stage_index, stage_rows = await completed
        rows_by_stage[stage_index] = stage_rows
        completed_stages += 1
        if completed_stages == total or completed_stages % 10 == 0:
            print(f"[answer-check-qa] processed {completed_stages}/{total}")
        if completed_stages == total or completed_stages % flush_interval == 0:
            ordered_rows: list[dict[str, Any]] = []
            for ordered_stage_index in sorted(rows_by_stage, key=lambda value: stage_order.get(value, value)):
                ordered_rows.extend(rows_by_stage[ordered_stage_index])
            _write_outputs(
                output_dir=output_dir,
                template_id=template_id,
                context_version=section_understanding_service.context_version,
                total_stages=total,
                completed_stages=completed_stages,
                selection_filters=selection_filters,
                rows=ordered_rows,
                partial=completed_stages < total,
            )

    ordered_rows = []
    for ordered_stage_index in sorted(rows_by_stage, key=lambda value: stage_order.get(value, value)):
        ordered_rows.extend(rows_by_stage[ordered_stage_index])
    _write_outputs(
        output_dir=output_dir,
        template_id=template_id,
        context_version=section_understanding_service.context_version,
        total_stages=total,
        completed_stages=completed_stages,
        selection_filters=selection_filters,
        rows=ordered_rows,
        partial=False,
    )
    scorecard_path = output_dir / "answer_check_scorecard.json"
    summary_path = output_dir / "answer_check_summary.md"
    print(f"[answer-check-qa] wrote {scorecard_path}")
    print(f"[answer-check-qa] wrote {summary_path}")


def main() -> None:
    configure_logging()
    args = _parse_args()
    asyncio.run(
        _run(
            args.limit,
            args.output_dir,
            stage_timeout_seconds=float(args.stage_timeout_seconds),
            section_ids=list(args.section_id or []),
            stage_indexes=list(args.stage_index or []),
            mismatches=list(args.mismatch or []),
            runner_errors=list(args.runner_error or []),
            flush_every=int(args.flush_every),
            max_stage_concurrency=int(args.max_stage_concurrency),
        )
    )


if __name__ == "__main__":
    main()
