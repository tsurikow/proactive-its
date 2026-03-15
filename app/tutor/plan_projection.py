from __future__ import annotations

from typing import Any

from app.state.tutor_state_repository import TutorStateRepository
from app.tutor.plan import annotate_plan_tree


class PlanProjectionService:
    def __init__(self, repo: TutorStateRepository):
        self.repo = repo

    async def mastery_map(self, learner_id: str) -> dict[str, float]:
        progress = await self.repo.list_topic_progress(learner_id)
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
