from __future__ import annotations

from typing import Any


def template_targets(template: dict[str, Any]) -> list[dict[str, Any]]:
    plan_json = template.get("plan_json") or {}
    targets = plan_json.get("stage_targets") or plan_json.get("targets") or []
    return [dict(item) for item in targets if isinstance(item, dict)]


def template_tree(template: dict[str, Any]) -> dict[str, Any] | None:
    tree = (template.get("plan_json") or {}).get("plan_tree")
    return dict(tree) if isinstance(tree, dict) else None


def current_stage_from_state(state: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not targets or state.get("plan_completed"):
        return None
    index = int(state.get("current_stage_index", 0))
    index = max(0, min(index, len(targets) - 1))
    stage = dict(targets[index])
    stage["stage_index"] = index
    return stage


def public_stage(stage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not stage:
        return None
    public = dict(stage)
    public.pop("prerequisite_section_ids", None)
    return public


def stage_by_section_id(targets: list[dict[str, Any]], section_id: str | None) -> dict[str, Any] | None:
    target_section_id = str(section_id or "").strip()
    if not target_section_id:
        return None
    for index, target in enumerate(targets):
        if str(target.get("section_id") or "") != target_section_id:
            continue
        stage = dict(target)
        stage["stage_index"] = int(target.get("stage_index", index))
        return stage
    return None


def stage_by_index(targets: list[dict[str, Any]], stage_index: int) -> dict[str, Any] | None:
    if stage_index < 0 or stage_index >= len(targets):
        return None
    stage = dict(targets[stage_index])
    stage["stage_index"] = int(stage.get("stage_index", stage_index))
    return stage


def adjacent_stages(
    targets: list[dict[str, Any]],
    current_stage: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not current_stage:
        return None, None
    index = int(current_stage["stage_index"])
    previous = dict(targets[index - 1]) if index > 0 else None
    following = dict(targets[index + 1]) if index + 1 < len(targets) else None
    return previous, following


def next_stage(targets: list[dict[str, Any]], current_stage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not current_stage:
        return None
    index = int(current_stage["stage_index"])
    if index + 1 >= len(targets):
        return None
    return dict(targets[index + 1])


def bind_parent_doc_id(stage: dict[str, Any], parent_doc_id: str) -> dict[str, Any]:
    enriched = dict(stage)
    enriched["parent_doc_id"] = parent_doc_id
    return enriched


__all__ = [
    "template_targets",
    "template_tree",
    "current_stage_from_state",
    "public_stage",
    "stage_by_section_id",
    "stage_by_index",
    "adjacent_stages",
    "next_stage",
    "bind_parent_doc_id",
]
