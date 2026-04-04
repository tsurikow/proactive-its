from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.api.dependencies import (
    get_learner_service,
    get_section_understanding_service,
    get_teacher_state_service,
)
from app.platform.logging import configure_logging
from app.state.stage_state import template_targets
from app.teacher.planning.section_coverage import classify_section_family

QA_LEARNER_ID = "__curriculum_qa__"
DEFAULT_OUTPUT_DIR = Path(".local/codex/notes/qa")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run internal curriculum QA over section understanding coverage.")
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
        help="Directory for QA scorecard artifacts.",
    )
    parser.add_argument(
        "--stage-timeout-seconds",
        type=float,
        default=180.0,
        help="Per-stage timeout for the QA runner so one slow section does not block the full scorecard.",
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
        "--issue",
        action="append",
        default=[],
        help="Optional repeated issue filter resolved from the existing scorecard.",
    )
    parser.add_argument(
        "--fallback-reason",
        action="append",
        default=[],
        help="Optional repeated fallback-reason filter resolved from the existing scorecard.",
    )
    return parser.parse_args()


def _fallback_bucket(*, fallback_reason: str | None, source: str | None) -> str | None:
    reason = str(fallback_reason or "").strip()
    source_value = str(source or "").strip()
    if not reason and source_value not in {"runner_timeout", "runner_error"}:
        return None
    if reason == "content_not_ready":
        return "content_readiness"
    if source_value == "runner_timeout" or reason.startswith("stage_timeout_after_"):
        return "runner_timeout"
    return "model_quality"


def _load_scorecard_rows(output_dir: Path) -> list[dict[str, Any]]:
    scorecard_path = output_dir / "section_coverage_scorecard.json"
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
    issues: list[str],
    fallback_reasons: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested_section_ids = {str(item).strip() for item in section_ids if str(item).strip()}
    requested_stage_indexes = {int(item) for item in stage_indexes}
    requested_issues = {str(item).strip() for item in issues if str(item).strip()}
    requested_fallback_reasons = {str(item).strip() for item in fallback_reasons if str(item).strip()}

    if requested_issues or requested_fallback_reasons:
        scorecard_dir = output_dir
        scorecard_path = scorecard_dir / "section_coverage_scorecard.json"
        if not scorecard_path.exists() and output_dir != DEFAULT_OUTPUT_DIR:
            scorecard_dir = DEFAULT_OUTPUT_DIR
        rows = _load_scorecard_rows(scorecard_dir)
        matched_section_ids = set()
        matched_stage_indexes = set()
        for row in rows:
            row_issues = {str(item).strip() for item in (row.get("issue_flags") or []) if str(item).strip()}
            row_issues.update(
                str(item).strip() for item in (row.get("raw_issue_flags") or []) if str(item).strip()
            )
            row_reason = str(row.get("fallback_reason") or "").strip()
            if requested_issues and not row_issues.intersection(requested_issues):
                continue
            if requested_fallback_reasons and row_reason not in requested_fallback_reasons:
                continue
            if str(row.get("section_id") or "").strip():
                matched_section_ids.add(str(row["section_id"]).strip())
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
        "issues": sorted(requested_issues),
        "fallback_reasons": sorted(requested_fallback_reasons),
    }
    return selected, selection_filters


def _make_row(
    *,
    stage: dict[str, Any],
    diagnostics: dict[str, Any],
    artifact: Any | None,
    error: str | None = None,
) -> dict[str, Any]:
    final_family = str(diagnostics.get("section_family") or classify_section_family(artifact, stage))
    raw_family = str(diagnostics.get("raw_section_family") or final_family)
    raw_issue_flags = [str(item) for item in diagnostics.get("raw_issue_flags") or []]
    final_issue_flags = [str(item) for item in diagnostics.get("final_issue_flags") or []]
    title = str(stage.get("title") or "")
    breadcrumb = [str(item) for item in stage.get("breadcrumb") or []]
    fallback_bucket = _fallback_bucket(
        fallback_reason=None if diagnostics.get("fallback_reason") is None else str(diagnostics.get("fallback_reason")),
        source=str(diagnostics.get("source") or "unknown"),
    )
    return {
        "stage_index": int(stage.get("stage_index", -1)),
        "section_id": str(stage.get("section_id") or ""),
        "module_id": None if stage.get("module_id") is None else str(stage.get("module_id")),
        "title": title,
        "breadcrumb": breadcrumb,
        "raw_section_family": raw_family,
        "section_family": final_family,
        "pedagogical_role": None if artifact is None else artifact.pedagogical_role.value,
        "should_dwell": None if artifact is None else bool(artifact.should_dwell),
        "supports_generated_question": None if artifact is None else bool(artifact.supports_generated_question),
        "explicit_checkpoint_count": 0 if artifact is None else len(artifact.explicit_checkpoints),
        "explicit_exercise_count": 0 if artifact is None else len(artifact.explicit_exercises),
        "answer_check_context_count": 0 if artifact is None else len(artifact.answer_check_contexts),
        "fallback_used": bool(diagnostics.get("fallback_used")),
        "cache_hit": bool(diagnostics.get("cache_hit")),
        "source": str(diagnostics.get("source") or "unknown"),
        "fallback_reason": None if diagnostics.get("fallback_reason") is None else str(diagnostics.get("fallback_reason")),
        "fallback_bucket": fallback_bucket,
        "raw_issue_flags": raw_issue_flags,
        "issue_flags": final_issue_flags,
        "runner_error": error,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    family_counts: Counter[str] = Counter()
    raw_issue_counts: Counter[str] = Counter()
    final_issue_counts: Counter[str] = Counter()
    issue_examples: dict[str, list[str]] = defaultdict(list)
    fallback_bucket_counts: Counter[str] = Counter()
    fallback_bucket_examples: dict[str, list[str]] = defaultdict(list)
    fallback_count = 0
    cache_hit_count = 0
    runner_error_count = 0

    for row in rows:
        family_counts[str(row["section_family"])] += 1
        if row.get("fallback_used"):
            fallback_count += 1
        fallback_bucket = str(row.get("fallback_bucket") or "").strip()
        if fallback_bucket:
            fallback_bucket_counts[fallback_bucket] += 1
            if len(fallback_bucket_examples[fallback_bucket]) < 5:
                fallback_bucket_examples[fallback_bucket].append(
                    f"{row['stage_index']}: {row['section_id']} | {row['title'] or row['section_id']}"
                )
        if row.get("cache_hit"):
            cache_hit_count += 1
        if row.get("runner_error"):
            runner_error_count += 1
        for issue in row.get("raw_issue_flags") or []:
            raw_issue_counts[str(issue)] += 1
        for issue in row.get("issue_flags") or []:
            issue_name = str(issue)
            final_issue_counts[issue_name] += 1
            if len(issue_examples[issue_name]) < 5:
                issue_examples[issue_name].append(
                    f"{row['stage_index']}: {row['section_id']} | {row['title'] or row['section_id']}"
                )

    issue_deltas: dict[str, dict[str, int]] = {}
    for issue_name in sorted(set(raw_issue_counts) | set(final_issue_counts)):
        raw_count = int(raw_issue_counts.get(issue_name, 0))
        final_count = int(final_issue_counts.get(issue_name, 0))
        issue_deltas[issue_name] = {
            "raw": raw_count,
            "final": final_count,
            "delta": raw_count - final_count,
        }

    return {
        "family_counts": dict(sorted(family_counts.items())),
        "raw_issue_counts": dict(sorted(raw_issue_counts.items())),
        "final_issue_counts": dict(sorted(final_issue_counts.items())),
        "issue_deltas": issue_deltas,
        "issue_examples": dict(issue_examples),
        "fallback_bucket_counts": dict(sorted(fallback_bucket_counts.items())),
        "fallback_bucket_examples": dict(fallback_bucket_examples),
        "fallback_count": fallback_count,
        "cache_hit_count": cache_hit_count,
        "runner_error_count": runner_error_count,
    }


def _render_summary(
    *,
    template_id: str,
    total_stages: int,
    summary: dict[str, Any],
) -> str:
    generated_at = datetime.now(UTC).isoformat()
    lines = [
        "# Section Coverage QA Summary",
        "",
        f"- generated_at: `{generated_at}`",
        f"- template_id: `{template_id}`",
        f"- total_stages: `{total_stages}`",
        f"- fallback_count: `{summary['fallback_count']}`",
        f"- cache_hit_count: `{summary['cache_hit_count']}`",
        f"- runner_error_count: `{summary['runner_error_count']}`",
        "",
        "## Fallback Split",
        "",
    ]
    for bucket, count in summary["fallback_bucket_counts"].items():
        lines.append(f"- `{bucket}`: `{count}`")

    lines.extend(
        [
            "",
        "## Counts by Section Family",
        "",
        ]
    )
    for family, count in summary["family_counts"].items():
        lines.append(f"- `{family}`: `{count}`")

    lines.extend(
        [
            "",
            "## Top Issue Buckets",
            "",
        ]
    )
    ranked_issues = sorted(
        summary["issue_deltas"].items(),
        key=lambda item: (-int(item[1]["raw"]), item[0]),
    )
    for issue_name, payload in ranked_issues[:8]:
        lines.append(
            f"- `{issue_name}`: raw `{payload['raw']}` -> final `{payload['final']}` (reduced `{payload['delta']}`)"
        )

    lines.extend(
        [
            "",
            "## Representative Remaining Issues",
            "",
        ]
    )
    remaining = [
        issue_name
        for issue_name, payload in ranked_issues
        if int(payload["final"]) > 0
    ]
    if not remaining:
        lines.append("- No remaining issue buckets after the current normalization pass.")
    else:
        for issue_name in remaining[:6]:
            examples = summary["issue_examples"].get(issue_name) or []
            lines.append(f"- `{issue_name}`: {', '.join(examples) if examples else 'no examples captured'}")

    if summary["fallback_bucket_examples"]:
        lines.extend(
            [
                "",
                "## Fallback Buckets",
                "",
            ]
        )
        for bucket, examples in summary["fallback_bucket_examples"].items():
            lines.append(f"- `{bucket}`: {', '.join(examples) if examples else 'no examples captured'}")

    lines.extend(
        [
            "",
            "## Next Slice Hint",
            "",
            "- Use the remaining issue buckets above to choose the next bounded `P13` coverage or answer-check reliability pass.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


async def _run(
    limit: int | None,
    output_dir: Path,
    *,
    stage_timeout_seconds: float,
    section_ids: list[str],
    stage_indexes: list[int],
    issues: list[str],
    fallback_reasons: list[str],
) -> None:
    state_service = get_teacher_state_service()
    learner_service = get_learner_service()
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
        issues=issues,
        fallback_reasons=fallback_reasons,
    )

    await state_service.ensure_learner(QA_LEARNER_ID)
    await learner_service.refresh_projection(QA_LEARNER_ID)

    rows: list[dict[str, Any]] = []
    total = len(selected_targets)
    for index, stage in enumerate(selected_targets, start=1):
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
            rows.append(
                _make_row(
                    stage=stage,
                    diagnostics=diagnostics,
                    artifact=artifact,
                )
            )
        except TimeoutError:
            timeout_message = f"stage_timeout_after_{stage_timeout_seconds:g}s"
            rows.append(
                _make_row(
                    stage=stage,
                    diagnostics={
                        "source": "runner_timeout",
                        "fallback_used": True,
                        "cache_hit": False,
                        "section_family": classify_section_family(None, stage),
                        "raw_issue_flags": ["fallback_understanding_used"],
                        "final_issue_flags": ["fallback_understanding_used"],
                        "fallback_reason": timeout_message,
                    },
                    artifact=None,
                    error=timeout_message,
                )
            )
        except Exception as exc:
            rows.append(
                _make_row(
                    stage=stage,
                    diagnostics={
                        "source": "runner_error",
                        "fallback_used": True,
                        "cache_hit": False,
                        "section_family": classify_section_family(None, stage),
                        "raw_issue_flags": ["fallback_understanding_used"],
                        "final_issue_flags": ["fallback_understanding_used"],
                        "fallback_reason": str(exc),
                    },
                    artifact=None,
                    error=str(exc),
                )
            )
        if index == total or index % 10 == 0:
            print(f"[curriculum-qa] processed {index}/{total}")

    summary = _summarize(rows)
    scorecard = {
        "generated_at": datetime.now(UTC).isoformat(),
        "template_id": template_id,
        "context_version": section_understanding_service.context_version,
        "qa_learner_id": QA_LEARNER_ID,
        "stage_timeout_seconds": stage_timeout_seconds,
        "selection_filters": selection_filters,
        "total_stages": total,
        "rows": rows,
        **summary,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    scorecard_path = output_dir / "section_coverage_scorecard.json"
    summary_path = output_dir / "section_coverage_summary.md"
    scorecard_path.write_text(json.dumps(scorecard, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(
        _render_summary(template_id=template_id, total_stages=total, summary=summary),
        encoding="utf-8",
    )
    print(f"[curriculum-qa] wrote {scorecard_path}")
    print(f"[curriculum-qa] wrote {summary_path}")


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
            issues=list(args.issue or []),
            fallback_reasons=list(args.fallback_reason or []),
        )
    )


if __name__ == "__main__":
    main()
