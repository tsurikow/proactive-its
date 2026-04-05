from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.content.planning.plan_builder import (
    apply_prerequisite_graph,
    annotate_plan_tree,
    build_hierarchical_plan,
    load_book_data,
)
from app.platform.config import Settings, get_settings
from app.state.services.learner_service import LearnerService
from app.state.repositories.learner_repository import LearnerStateRepository
from app.state.repositories.session_repository import SessionStateRepository
from app.state.stage_state import current_stage_from_state, stage_by_section_id, template_targets, template_tree


class TeacherStateService:
    default_template_id = "default_calc1"
    default_template_version = 2
    prerequisite_graph_version = "prerequisite_graph_v1"

    def __init__(
        self,
        *,
        learner_repository: LearnerStateRepository,
        session_repository: SessionStateRepository,
        learner_service: LearnerService,
        book_json_path: str,
        settings: Settings | None = None,
    ):
        self.learner_repository = learner_repository
        self.session_repository = session_repository
        self.learner_service = learner_service
        self.book_json_path = book_json_path
        self.settings = settings or get_settings()

    async def close(self) -> None:
        return None

    async def ensure_default_template(self) -> dict[str, Any]:
        existing = await self.session_repository.get_plan_template(self.default_template_id)
        plan_json = (existing or {}).get("plan_json") or {}
        if existing and plan_json.get("stage_targets") and plan_json.get("plan_tree"):
            return existing
        raise RuntimeError(
            "Default plan template is not initialized. Run the runtime bootstrap command before starting the app."
        )

    async def template_ready_status(self) -> dict[str, Any]:
        existing = await self.session_repository.get_plan_template(self.default_template_id)
        plan_json = (existing or {}).get("plan_json") or {}
        template_ready = bool(existing and plan_json.get("stage_targets") and plan_json.get("plan_tree"))
        return {
            "template_ready": template_ready,
            "template_id": None if existing is None else str(existing.get("id") or ""),
            "template_version": None if existing is None else int(existing.get("version") or 0),
        }

    async def bootstrap_default_template(self) -> dict[str, Any]:
        existing = await self.session_repository.get_plan_template(self.default_template_id)
        plan_json = (existing or {}).get("plan_json") or {}
        if (
            existing
            and plan_json.get("stage_targets")
            and plan_json.get("plan_tree")
            and plan_json.get("prerequisite_graph") is not None
            and plan_json.get("prerequisite_graph_meta")
        ):
            return existing
        book_id, toc = load_book_data(self.book_json_path)
        plan = build_hierarchical_plan(toc)
        graph_meta = {
            "source": "bootstrap_deterministic",
            "graph_version": self.prerequisite_graph_version,
            "book_hash": hashlib.sha256(json.dumps(toc, sort_keys=True).encode("utf-8")).hexdigest(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "deterministic_seeded",
        }
        plan = apply_prerequisite_graph(plan, {}, graph_meta)
        return await self.session_repository.upsert_plan_template(
            template_id=self.default_template_id,
            book_id=book_id,
            version=self.default_template_version,
            plan_json={"book_id": book_id, **plan},
            is_active=True,
        )

    async def ensure_learner(self, learner_id: str) -> None:
        await self.learner_repository.ensure_learner(learner_id)

    async def mastery_map(self, learner_id: str) -> dict[str, float]:
        progress = await self.learner_repository.list_topic_progress(learner_id)
        return {row["section_id"]: float(row.get("mastery_score", 0.0)) for row in progress}

    async def ensure_context(
        self,
        learner_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any] | None, Any]:
        await self.ensure_learner(learner_id)
        await self.learner_service.refresh_projection(learner_id)
        template = await self.ensure_default_template()
        targets = template_targets(template)
        state = await self.session_repository.get_or_create_learner_plan_state(
            learner_id=learner_id,
            template_id=template["id"],
            total_stages=len(targets),
        )
        current_stage = current_stage_from_state(state, targets)
        adaptation_context = await self.learner_service.build_adaptation_context(learner_id, current_stage, targets)
        return template, state, targets, current_stage, adaptation_context

    async def build_plan_payload(
        self,
        *,
        template: dict[str, Any],
        state: dict[str, Any],
        current_stage: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tree = annotate_plan_tree(
            template_tree(template),
            completed_count=int(state["completed_count"]),
            current_stage_index=None if current_stage is None else int(current_stage["stage_index"]),
            plan_completed=bool(state["plan_completed"]),
            mastery_map=await self.mastery_map(str(state["learner_id"])),
        )
        return {
            "template_id": str(template["id"]),
            "total_stages": len(template_targets(template)),
            "completed_stages": int(state["completed_count"]),
            "mastery_score": float((tree or {}).get("mastery_score", 0.0)),
            "tree": tree,
        }

    async def advance_stage(
        self,
        learner_id: str,
        force: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any] | None, Any]:
        _ = force
        template, state, targets, _current_stage, _adaptation_context = await self.ensure_context(learner_id)
        if not targets:
            return template, state, targets, None, await self.learner_service.build_adaptation_context(learner_id, None, targets)
        total_stages = len(targets)
        completed_count = int(state["completed_count"])
        current_stage_index = int(state["current_stage_index"])
        if completed_count < total_stages:
            completed_count += 1
        plan_completed = completed_count >= total_stages
        next_stage_index = total_stages - 1 if plan_completed else min(current_stage_index + 1, total_stages - 1)
        state = await self.session_repository.update_learner_plan_state(
            learner_id=learner_id,
            template_id=str(template["id"]),
            current_stage_index=next_stage_index,
            completed_count=completed_count,
            plan_completed=plan_completed,
        )
        current_stage = current_stage_from_state(state, targets)
        adaptation_context = await self.learner_service.build_adaptation_context(learner_id, current_stage, targets)
        return template, state, targets, current_stage, adaptation_context

    async def move_to_stage(
        self,
        learner_id: str,
        *,
        target_section_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any] | None, Any]:
        template, state, targets, _current_stage, _adaptation_context = await self.ensure_context(learner_id)
        target_stage = stage_by_section_id(targets, target_section_id)
        if target_stage is None:
            current_stage = current_stage_from_state(state, targets)
            adaptation_context = await self.learner_service.build_adaptation_context(learner_id, current_stage, targets)
            return template, state, targets, current_stage, adaptation_context

        target_stage_index = int(target_stage.get("stage_index", 0))
        completed_count = min(int(state.get("completed_count", 0)), target_stage_index)
        state = await self.session_repository.update_learner_plan_state(
            learner_id=learner_id,
            template_id=str(template["id"]),
            current_stage_index=target_stage_index,
            completed_count=completed_count,
            plan_completed=False,
        )
        current_stage = stage_by_section_id(targets, target_section_id)
        adaptation_context = await self.learner_service.build_adaptation_context(learner_id, current_stage, targets)
        return template, state, targets, current_stage, adaptation_context
