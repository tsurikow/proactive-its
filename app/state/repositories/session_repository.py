from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.platform.db import get_session
from app.state.orm_models import LearnerMemoryRecord, LearnerPlanState, LearningDebtRecord, PlanTemplate, TeacherSessionEventRecord
from app.state.serializers import (
    learner_memory_row_to_dict,
    learning_debt_row_to_dict,
    plan_state_row_to_dict,
    teacher_session_event_row_to_dict,
    template_row_to_dict,
)


CONFUSION_MARKERS = (
    "confused", "lost", "not sure", "don't get", "dont get",
    "unclear", "overloaded", "overwhelmed", "too much", "hard to follow", "stuck",
)
PACING_MARKERS = (
    "slow down", "too fast", "stay here", "not so fast", "one more step", "a bit longer",
)
READY_MARKERS = (
    "got it", "i get it", "i understand", "that makes sense",
    "clear now", "ready to move", "move on", "okay now",
)
WEAK_CHECKPOINT_STATUSES = {"partial", "incorrect", "unresolved"}

_DEBT_RANK = {
    "unanswered_checkpoint": 0, "unattempted_exercise": 1, "unresolved_exercise": 2,
    "moved_on_weak": 3, "skipped_section": 4, "refused_revisit": 5,
}


def revisit_reason_summary(debt_kind: str) -> str:
    return {
        "unanswered_checkpoint": "There is still an unanswered checkpoint here.",
        "unattempted_exercise": "There is still an unattempted exercise here.",
        "unresolved_exercise": "There is still unresolved exercise work here.",
        "moved_on_weak": "This area still looks weak enough to revisit.",
        "refused_revisit": "This was previously flagged for revisit.",
    }.get(debt_kind, "This area was skipped before it was fully secured.")


def debt_priority(*, debt_kind: str, same_module: bool) -> tuple[int, int]:
    return (0 if same_module else 1, _DEBT_RANK.get(debt_kind, 6))


def compute_session_event_summary(
    rows: list[dict[str, Any]],
    current_stage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure computation: analyse recent teacher session events."""
    current_section_id = None if current_stage is None else str(current_stage.get("section_id") or "")
    current_module_id = None
    if current_stage is not None and current_stage.get("module_id") is not None:
        current_module_id = str(current_stage.get("module_id"))
    matching_rows = [
        row for row in rows
        if (current_section_id and str(row.get("section_id") or "") == current_section_id)
        or (current_module_id and str(row.get("module_id") or "") == current_module_id)
    ]
    scoped_rows = matching_rows or rows
    event_types = [str(row.get("event_type") or "") for row in scoped_rows]
    proposal_types = [str(row.get("proposal_type") or "") for row in scoped_rows if row.get("proposal_type")]
    ordinary_rows = [
        row for row in scoped_rows
        if str(row.get("event_type") or "") == "learner_reply"
        and isinstance(dict(row.get("event_payload_json") or {}).get("interaction_route"), dict)
    ]
    ordinary_turn_count = len(ordinary_rows[:4])
    route_counts = {"grounded_reply": 0, "pedagogical_reply": 0, "clarify_before_retrieval": 0}
    confusion_like_count = 0
    pacing_hesitation_count = 0
    ready_to_move_count = 0
    last_route_type: str | None = None
    last_message_signal: str | None = None
    for row in ordinary_rows[:4]:
        payload = dict(row.get("event_payload_json") or {})
        route_payload = dict(payload.get("interaction_route") or {})
        route_type = str(route_payload.get("route_type") or "").strip()
        if route_type in route_counts:
            route_counts[route_type] += 1
            if last_route_type is None:
                last_route_type = route_type
        message = str(row.get("message") or "").strip().lower()
        if any(marker in message for marker in CONFUSION_MARKERS):
            confusion_like_count += 1
            if last_message_signal is None:
                last_message_signal = "confusion"
        elif any(marker in message for marker in PACING_MARKERS):
            pacing_hesitation_count += 1
            if last_message_signal is None:
                last_message_signal = "pacing"
        elif any(marker in message for marker in READY_MARKERS):
            ready_to_move_count += 1
            if last_message_signal is None:
                last_message_signal = "ready"

    ordinary_progress_signal = "none"
    ordinary_progress_summary = None
    clarify_count = route_counts["clarify_before_retrieval"]
    if ordinary_turn_count == 0:
        ordinary_progress_signal = "none"
    elif confusion_like_count > 0 or pacing_hesitation_count > 0 or clarify_count >= 2:
        ordinary_progress_signal = "support_needed"
        if clarify_count >= 2:
            ordinary_progress_summary = "Recent turns have been clarification-heavy, so the current section still looks unsettled."
        elif pacing_hesitation_count > 0:
            ordinary_progress_summary = "Recent turns suggest the learner wants a slower pace on the current section."
        else:
            ordinary_progress_summary = "Recent turns suggest the learner is still unsure about the current section."
    elif ready_to_move_count > 0 and clarify_count == 0 and confusion_like_count == 0:
        ordinary_progress_signal = "ready_to_move"
        ordinary_progress_summary = "Recent turns suggest the learner now has the main idea in place."
    elif route_counts["pedagogical_reply"] or route_counts["grounded_reply"]:
        ordinary_progress_signal = "steady"
        ordinary_progress_summary = "Recent turns suggest the learner is engaging steadily with the current section."
    else:
        ordinary_progress_signal = "mixed"
        ordinary_progress_summary = "Recent turns on the current section are mixed, so movement would be premature."
    return {
        "count": len(scoped_rows),
        "recent_event_types": event_types[:6],
        "recent_proposal_types": proposal_types[:4],
        "has_recent_move_on": "request_move_on" in event_types[:6],
        "has_recent_refusal": "refuse_proposal" in event_types[:6],
        "has_recent_acceptance": "accept_proposal" in event_types[:6],
        "ordinary_turn_count": ordinary_turn_count,
        "ordinary_route_counts": route_counts,
        "last_ordinary_route_type": last_route_type,
        "last_ordinary_message_signal": last_message_signal,
        "clarify_heavy": clarify_count >= 2,
        "ordinary_progress_signal": ordinary_progress_signal,
        "ordinary_progress_summary": ordinary_progress_summary,
    }


def compute_repair_history(
    rows: list[dict[str, Any]],
    current_stage: dict[str, Any] | None,
    item_ref: str,
) -> dict[str, Any]:
    """Pure computation: analyse repair trajectory for a specific task."""
    current_section_id = None if current_stage is None else str(current_stage.get("section_id") or "")
    current_module_id = None
    if current_stage is not None and current_stage.get("module_id") is not None:
        current_module_id = str(current_stage.get("module_id"))
    scoped_rows: list[dict[str, Any]] = []
    for row in rows:
        row_section_id = str(row.get("section_id") or "")
        row_module_id = str(row.get("module_id") or "")
        if current_section_id and row_section_id != current_section_id:
            continue
        if not current_section_id and current_module_id and row_module_id != current_module_id:
            continue
        payload = dict(row.get("event_payload_json") or {})
        pending_payload = dict(payload.get("pending_task") or {})
        if pending_payload and str(pending_payload.get("item_ref") or "") != item_ref:
            continue
        evaluation_payload = payload.get("checkpoint_evaluation")
        if not isinstance(evaluation_payload, dict):
            continue
        status = str(evaluation_payload.get("status") or "").strip()
        if status not in WEAK_CHECKPOINT_STATUSES:
            continue
        scoped_rows.append(row)

    recent_entries: list[dict[str, Any]] = []
    recent_clarify_count = 0
    for row in scoped_rows[:3]:
        payload = dict(row.get("event_payload_json") or {})
        weak_answer_payload = payload.get("weak_answer_response")
        repair_mode = None
        if isinstance(weak_answer_payload, dict):
            candidate_mode = str(weak_answer_payload.get("repair_mode") or "").strip()
            if candidate_mode in {"explain_brief", "hint_brief", "reask_tighter"}:
                repair_mode = candidate_mode
            clarification_used = str(weak_answer_payload.get("decision_kind") or "").strip() == "clarify"
        else:
            repair_payload = payload.get("repair_plan")
            if isinstance(repair_payload, dict):
                candidate_mode = str(repair_payload.get("repair_mode") or "").strip()
                if candidate_mode in {"explain_brief", "hint_brief", "reask_tighter"}:
                    repair_mode = candidate_mode
            clarification_used = isinstance(payload.get("clarify_plan"), dict)
        if clarification_used:
            recent_clarify_count += 1
        evaluation_payload = dict(payload.get("checkpoint_evaluation") or {})
        recent_entries.append({
            "status": str(evaluation_payload.get("status") or "").strip(),
            "repair_mode": repair_mode,
            "clarification_used": clarification_used,
        })

    last_entry = recent_entries[0] if recent_entries else {}
    previous_entry = recent_entries[1] if len(recent_entries) > 1 else {}
    recent_repair_modes = [
        mode for mode in [e.get("repair_mode") for e in recent_entries]
        if isinstance(mode, str) and mode
    ]
    recent_weak_statuses = [
        status for status in [e.get("status") for e in recent_entries]
        if isinstance(status, str) and status in WEAK_CHECKPOINT_STATUSES
    ]
    last_repair_mode = None if not isinstance(last_entry.get("repair_mode"), str) else last_entry["repair_mode"]
    last_weak_status = None if not isinstance(last_entry.get("status"), str) else last_entry["status"]
    previous_weak_status = None if not isinstance(previous_entry.get("status"), str) else previous_entry["status"]
    last_clarification_used = bool(last_entry.get("clarification_used"))
    stayed_vague_after_reask = last_repair_mode == "reask_tighter" and last_weak_status == "unresolved"
    stayed_wrong_after_hint = last_repair_mode == "hint_brief" and last_weak_status == "incorrect"
    repeated_same_mode_risk = last_repair_mode is not None
    trajectory_summary = None
    if stayed_vague_after_reask:
        trajectory_summary = "The last repair tightened the question, but the learner still stayed vague."
    elif stayed_wrong_after_hint:
        trajectory_summary = "The last repair gave a hint, but the learner is still answering with the wrong idea."
    elif last_repair_mode == "explain_brief" and last_weak_status == "partial":
        trajectory_summary = "The learner is closer now, but still needs a tighter finish after a brief explanation."
    elif last_repair_mode == "hint_brief" and last_weak_status == "partial":
        trajectory_summary = "The hint helped somewhat, but the learner still needs a more precise finish."
    elif last_repair_mode == "reask_tighter" and last_weak_status == "partial":
        trajectory_summary = "The tighter re-ask helped, but the answer still needs more precision."
    return {
        "weak_attempt_ordinal": len(scoped_rows) + 1,
        "recent_repair_modes": recent_repair_modes,
        "recent_weak_statuses": recent_weak_statuses,
        "last_repair_mode": last_repair_mode,
        "last_weak_status": last_weak_status,
        "previous_weak_status": previous_weak_status,
        "stayed_vague_after_reask": stayed_vague_after_reask,
        "stayed_wrong_after_hint": stayed_wrong_after_hint,
        "repeated_same_mode_risk": repeated_same_mode_risk,
        "recent_clarify_count": recent_clarify_count,
        "last_clarification_used": last_clarification_used,
        "trajectory_summary": trajectory_summary,
    }


def compute_learning_debt_summary(
    rows: list[dict[str, Any]],
    current_stage: dict[str, Any] | None = None,
    targets: list[dict[str, Any]] | None = None,
    weak_related_section_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Pure computation: analyse open learning debt for revisit candidates."""
    current_section_id = None if current_stage is None else str(current_stage.get("section_id") or "")
    current_module_id = None
    current_stage_index = None if current_stage is None else int(current_stage.get("stage_index", -1))
    if current_stage is not None and current_stage.get("module_id") is not None:
        current_module_id = str(current_stage.get("module_id"))
    current_rows = [
        row for row in rows
        if (current_section_id and str(row.get("section_id") or "") == current_section_id)
        or (current_module_id and str(row.get("module_id") or "") == current_module_id)
    ]
    scoped_rows = current_rows or rows
    debt_kinds = [str(row.get("debt_kind") or "") for row in scoped_rows]
    target_lookup: dict[str, dict[str, Any]] = {}
    for item in targets or []:
        section_id = str(item.get("section_id") or "").strip()
        if section_id:
            target_lookup[section_id] = dict(item)

    revisit_entries: dict[str, dict[str, Any]] = {}
    for row in rows:
        section_id = str(row.get("section_id") or "").strip()
        if not section_id or section_id == current_section_id:
            continue
        target = target_lookup.get(section_id)
        if target is None:
            continue
        stage_index = int(target.get("stage_index", -1))
        if current_stage_index is not None and current_stage_index >= 0 and (
            stage_index < 0 or stage_index >= current_stage_index
        ):
            continue
        same_module = bool(current_module_id and str(row.get("module_id") or "") == current_module_id)
        candidate = {
            "target_section_id": section_id,
            "target_module_id": None if target.get("module_id") is None else str(target.get("module_id")),
            "target_title": str(target.get("title") or "") or None,
            "stage_index": stage_index,
            "reason_source": "learning_debt",
            "reason_summary": revisit_reason_summary(str(row.get("debt_kind") or "")),
            "_sort_key": debt_priority(debt_kind=str(row.get("debt_kind") or ""), same_module=same_module) + (-(stage_index or 0),),
        }
        existing = revisit_entries.get(section_id)
        if existing is None or tuple(candidate["_sort_key"]) < tuple(existing["_sort_key"]):
            revisit_entries[section_id] = candidate

    for section_id in weak_related_section_ids or []:
        normalized = str(section_id or "").strip()
        if not normalized or normalized == current_section_id or normalized in revisit_entries:
            continue
        target = target_lookup.get(normalized)
        if target is None:
            continue
        stage_index = int(target.get("stage_index", -1))
        if current_stage_index is not None and current_stage_index >= 0 and (
            stage_index < 0 or stage_index >= current_stage_index
        ):
            continue
        revisit_entries[normalized] = {
            "target_section_id": normalized,
            "target_module_id": None if target.get("module_id") is None else str(target.get("module_id")),
            "target_title": str(target.get("title") or "") or None,
            "stage_index": stage_index,
            "reason_source": "weak_topic",
            "reason_summary": "This earlier idea still looks weak enough to revisit.",
            "_sort_key": (2, 0, -(stage_index or 0)),
        }

    revisit_candidates = [
        {k: v for k, v in c.items() if not k.startswith("_")}
        for c in sorted(revisit_entries.values(), key=lambda x: tuple(x.get("_sort_key") or (99, 99, 0)))[:3]
    ]
    return {
        "open_count": len(scoped_rows),
        "debt_kinds": debt_kinds[:8],
        "has_skipped_current_area": "skipped_section" in debt_kinds,
        "has_refused_revisit_current_area": "refused_revisit" in debt_kinds,
        "current_section_open_count": sum(
            1 for row in rows if current_section_id and str(row.get("section_id") or "") == current_section_id
        ),
        "revisit_candidates": revisit_candidates,
    }


class SessionStateRepository:

    async def get_plan_template(self, template_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(select(PlanTemplate).where(PlanTemplate.id == template_id).limit(1))
        if row is None:
            return None
        return template_row_to_dict(row)

    async def get_active_plan_template(self) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(PlanTemplate)
                .where(PlanTemplate.is_active.is_(True))
                .order_by(PlanTemplate.updated_at.desc())
                .limit(1)
            )
        if row is None:
            return None
        return template_row_to_dict(row)

    async def upsert_plan_template(
        self,
        template_id: str,
        book_id: str,
        version: int,
        plan_json: dict[str, Any],
        is_active: bool = True,
    ) -> dict[str, Any]:
        async with get_session() as session:
            if is_active:
                await session.execute(
                    update(PlanTemplate)
                    .where(PlanTemplate.id != template_id)
                    .values(is_active=False, updated_at=func.now())
                )
            stmt = (
                pg_insert(PlanTemplate)
                .values(
                    id=template_id,
                    book_id=book_id,
                    version=version,
                    plan_json=plan_json,
                    is_active=is_active,
                )
                .on_conflict_do_update(
                    index_elements=[PlanTemplate.id],
                    set_={
                        "book_id": book_id,
                        "version": version,
                        "plan_json": plan_json,
                        "is_active": is_active,
                        "updated_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)
            row = await session.scalar(select(PlanTemplate).where(PlanTemplate.id == template_id).limit(1))
        if row is None:
            raise RuntimeError("Failed to persist plan template")
        return template_row_to_dict(row)

    async def get_or_create_learner_plan_state(
        self,
        learner_id: str,
        template_id: str,
        total_stages: int,
    ) -> dict[str, Any]:
        async with get_session() as session:
            stmt = (
                pg_insert(LearnerPlanState)
                .values(
                    learner_id=learner_id,
                    template_id=template_id,
                    current_stage_index=0,
                    plan_completed=False,
                    completed_count=0,
                )
                .on_conflict_do_nothing(index_elements=[LearnerPlanState.learner_id])
            )
            await session.execute(stmt)
            row = await session.scalar(
                select(LearnerPlanState).where(LearnerPlanState.learner_id == learner_id).limit(1)
            )
            if row is None:
                raise RuntimeError("Failed to load learner plan state")

            max_index = max(0, total_stages - 1)
            needs_update = False
            next_stage_index = int(row.current_stage_index)
            next_completed_count = int(row.completed_count)
            next_plan_completed = bool(row.plan_completed)
            next_template_id = str(row.template_id)

            if next_template_id != template_id:
                next_template_id = template_id
                next_stage_index = 0
                next_completed_count = 0
                next_plan_completed = False
                needs_update = True

            if total_stages == 0:
                if not next_plan_completed:
                    next_plan_completed = True
                    needs_update = True
                next_stage_index = 0
                next_completed_count = 0
            else:
                if next_stage_index < 0:
                    next_stage_index = 0
                    needs_update = True
                if next_stage_index > max_index:
                    next_stage_index = max_index
                    needs_update = True
                if next_completed_count < 0:
                    next_completed_count = 0
                    needs_update = True
                if next_completed_count > total_stages:
                    next_completed_count = total_stages
                    needs_update = True
                if next_completed_count >= total_stages and not next_plan_completed:
                    next_plan_completed = True
                    needs_update = True

            if needs_update:
                await session.execute(
                    update(LearnerPlanState)
                    .where(LearnerPlanState.learner_id == learner_id)
                    .values(
                        template_id=next_template_id,
                        current_stage_index=next_stage_index,
                        completed_count=next_completed_count,
                        plan_completed=next_plan_completed,
                        updated_at=func.now(),
                    )
                )
                row = await session.scalar(
                    select(LearnerPlanState).where(LearnerPlanState.learner_id == learner_id).limit(1)
                )
        if row is None:
            raise RuntimeError("Failed to load learner plan state")
        return plan_state_row_to_dict(row)

    async def update_learner_plan_state(
        self,
        learner_id: str,
        template_id: str,
        current_stage_index: int,
        completed_count: int,
        plan_completed: bool,
    ) -> dict[str, Any]:
        async with get_session() as session:
            await session.execute(
                update(LearnerPlanState)
                .where(LearnerPlanState.learner_id == learner_id)
                .values(
                    template_id=template_id,
                    current_stage_index=current_stage_index,
                    completed_count=completed_count,
                    plan_completed=plan_completed,
                    updated_at=func.now(),
                )
            )
            row = await session.scalar(
                select(LearnerPlanState).where(LearnerPlanState.learner_id == learner_id).limit(1)
            )
        if row is None:
            raise RuntimeError("Failed to update learner plan state")
        return plan_state_row_to_dict(row)

    async def append_teacher_session_event(
        self,
        *,
        learner_id: str,
        template_id: str,
        event_type: str,
        event_payload_json: dict[str, Any],
        interaction_id: int | None = None,
        proposal_type: str | None = None,
        stage_index: int | None = None,
        section_id: str | None = None,
        module_id: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        async with get_session() as session:
            row = TeacherSessionEventRecord(
                learner_id=learner_id,
                template_id=template_id,
                interaction_id=interaction_id,
                event_type=event_type,
                proposal_type=proposal_type,
                stage_index=stage_index,
                section_id=section_id,
                module_id=module_id,
                message=message,
                event_payload_json=event_payload_json,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return teacher_session_event_row_to_dict(row)

    async def list_recent_teacher_session_events(
        self,
        learner_id: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        async with get_session() as session:
            rows = (
                await session.scalars(
                    select(TeacherSessionEventRecord)
                    .where(TeacherSessionEventRecord.learner_id == learner_id)
                    .order_by(TeacherSessionEventRecord.created_at.desc(), TeacherSessionEventRecord.id.desc())
                    .limit(limit)
                )
            ).all()
        return [teacher_session_event_row_to_dict(row) for row in rows]

    async def get_latest_teacher_proposal(
        self,
        learner_id: str,
        *,
        proposal_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        rows = await self.list_recent_teacher_session_events(learner_id, limit=limit)
        for row in rows:
            payload = dict(row.get("event_payload_json") or {})
            proposal = payload.get("proposal")
            if not isinstance(proposal, dict):
                continue
            if proposal_type is not None and str(proposal.get("proposal_type") or "") != proposal_type:
                continue
            return proposal
        return None

    async def summarize_recent_teacher_session_events(
        self,
        learner_id: str,
        *,
        current_stage: dict[str, Any] | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        rows = await self.list_recent_teacher_session_events(learner_id, limit=limit)
        return compute_session_event_summary(rows, current_stage)

    async def summarize_repair_history_for_task(
        self,
        learner_id: str,
        *,
        current_stage: dict[str, Any] | None,
        item_ref: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        rows = await self.list_recent_teacher_session_events(learner_id, limit=limit)
        return compute_repair_history(rows, current_stage, item_ref)

    async def append_learning_debt(
        self,
        *,
        learner_id: str,
        template_id: str,
        section_id: str,
        debt_kind: str,
        rationale: str,
        module_id: str | None = None,
        source_event_id: int | None = None,
        source_interaction_id: int | None = None,
    ) -> dict[str, Any]:
        async with get_session() as session:
            row = LearningDebtRecord(
                learner_id=learner_id,
                template_id=template_id,
                section_id=section_id,
                module_id=module_id,
                debt_kind=debt_kind,
                rationale=rationale,
                source_event_id=source_event_id,
                source_interaction_id=source_interaction_id,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return learning_debt_row_to_dict(row)

    async def list_open_learning_debt(
        self,
        learner_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        async with get_session() as session:
            rows = (
                await session.scalars(
                    select(LearningDebtRecord)
                    .where(LearningDebtRecord.learner_id == learner_id, LearningDebtRecord.status == "open")
                    .order_by(LearningDebtRecord.created_at.desc(), LearningDebtRecord.id.desc())
                    .limit(limit)
                )
            ).all()
        return [learning_debt_row_to_dict(row) for row in rows]

    async def summarize_open_learning_debt(
        self,
        learner_id: str,
        *,
        current_stage: dict[str, Any] | None = None,
        targets: list[dict[str, Any]] | None = None,
        weak_related_section_ids: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        rows = await self.list_open_learning_debt(learner_id, limit=limit)
        return compute_learning_debt_summary(rows, current_stage, targets, weak_related_section_ids)

    async def resolve_learning_debt(self, debt_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            await session.execute(
                update(LearningDebtRecord)
                .where(LearningDebtRecord.id == debt_id)
                .values(status="resolved", resolved_at=func.now())
            )
            row = await session.scalar(select(LearningDebtRecord).where(LearningDebtRecord.id == debt_id).limit(1))
        if row is None:
            return None
        return learning_debt_row_to_dict(row)

    # ------------------------------------------------------------------
    # Learner Memory
    # ------------------------------------------------------------------

    async def get_learner_memory(
        self,
        learner_id: str,
        *,
        template_id: str,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(LearnerMemoryRecord).where(
                    LearnerMemoryRecord.learner_id == learner_id,
                    LearnerMemoryRecord.template_id == template_id,
                )
            )
        if row is None:
            return None
        return learner_memory_row_to_dict(row)

    async def upsert_learner_memory(
        self,
        *,
        learner_id: str,
        template_id: str,
        memory_json: dict[str, Any],
    ) -> dict[str, Any]:
        async with get_session() as session:
            stmt = pg_insert(LearnerMemoryRecord).values(
                learner_id=learner_id,
                template_id=template_id,
                memory_json=memory_json,
                session_count=1,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["learner_id", "template_id"],
                set_={
                    "memory_json": memory_json,
                    "session_count": LearnerMemoryRecord.session_count + 1,
                    "updated_at": func.now(),
                },
            )
            await session.execute(stmt)
            row = await session.scalar(
                select(LearnerMemoryRecord).where(
                    LearnerMemoryRecord.learner_id == learner_id,
                    LearnerMemoryRecord.template_id == template_id,
                )
            )
        return learner_memory_row_to_dict(row)
