from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_book_data(book_json_path: str) -> tuple[str, dict[str, Any]]:
    path = Path(book_json_path)
    if not path.exists():
        return ("unknown-book", {})
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data.get("book_id") or "unknown-book"), dict(data.get("toc") or {})


def build_hierarchical_plan(toc: dict[str, Any]) -> dict[str, Any]:
    stage_targets: list[dict[str, Any]] = []

    def walk(
        node: dict[str, Any],
        *,
        depth: int,
        current_module_id: str | None,
        path: list[str],
    ) -> dict[str, Any] | None:
        title = str(node.get("title") or node.get("id") or "").strip()
        if not title:
            return None

        breadcrumb = [*path, title]
        node_module_id = node.get("module_id") or current_module_id
        children = [child for child in (node.get("children") or []) if isinstance(child, dict)]

        if children:
            child_nodes = [
                child_node
                for child in children
                if (child_node := walk(child, depth=depth + 1, current_module_id=node_module_id, path=breadcrumb))
            ]
            if not child_nodes:
                return None
            return {
                "node_type": "book" if depth == 0 else "group",
                "title": title,
                "breadcrumb": breadcrumb,
                "children": child_nodes,
            }

        section_id = str(node.get("id") or "").strip()
        module_id = str(node_module_id).strip() if node_module_id else None
        if not section_id or not module_id:
            return None

        stage_index = len(stage_targets)
        stage_targets.append(
            {
                "stage_index": stage_index,
                "section_id": section_id,
                "module_id": module_id,
                "title": title,
                "breadcrumb": breadcrumb,
                "prerequisite_section_ids": [],
            }
        )
        return {
            "node_type": "stage",
            "title": title,
            "breadcrumb": breadcrumb,
            "children": [],
            "stage_index": stage_index,
            "section_id": section_id,
            "module_id": module_id,
            "prerequisite_section_ids": [],
        }

    root = walk(toc, depth=0, current_module_id=None, path=[])
    if root is None:
        return {"stage_targets": [], "plan_tree": None}
    return {"stage_targets": stage_targets, "plan_tree": root}


def normalize_prerequisite_graph(
    stage_targets: list[dict[str, Any]],
    graph: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    section_to_index = {
        str(target.get("section_id") or ""): int(target.get("stage_index", index))
        for index, target in enumerate(stage_targets)
        if str(target.get("section_id") or "")
    }
    normalized: dict[str, list[str]] = {section_id: [] for section_id in section_to_index}
    for target_section_id, prerequisites in dict(graph or {}).items():
        section_id = str(target_section_id or "").strip()
        if section_id not in section_to_index:
            continue
        target_index = section_to_index[section_id]
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw_prerequisite in prerequisites or []:
            prerequisite_section_id = str(raw_prerequisite or "").strip()
            if not prerequisite_section_id or prerequisite_section_id in seen:
                continue
            prerequisite_index = section_to_index.get(prerequisite_section_id)
            if prerequisite_index is None or prerequisite_index >= target_index:
                continue
            seen.add(prerequisite_section_id)
            cleaned.append(prerequisite_section_id)
            if len(cleaned) >= 3:
                break
        normalized[section_id] = cleaned
    return normalized


def apply_prerequisite_graph(
    plan: dict[str, Any],
    graph: dict[str, list[str]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    stage_targets = [dict(item) for item in plan.get("stage_targets") or [] if isinstance(item, dict)]
    prerequisite_graph = normalize_prerequisite_graph(stage_targets, graph)
    updated_targets: list[dict[str, Any]] = []
    for target in stage_targets:
        updated = dict(target)
        updated["prerequisite_section_ids"] = list(
            prerequisite_graph.get(str(updated.get("section_id") or ""), [])
        )
        updated_targets.append(updated)

    def walk(node: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        updated = dict(node)
        if str(updated.get("node_type") or "") == "stage":
            updated["prerequisite_section_ids"] = list(
                prerequisite_graph.get(str(updated.get("section_id") or ""), [])
            )
        updated["children"] = [
            child
            for raw_child in updated.get("children") or []
            if isinstance(raw_child, dict)
            if (child := walk(raw_child)) is not None
        ]
        return updated

    return {
        **dict(plan),
        "stage_targets": updated_targets,
        "plan_tree": walk(plan.get("plan_tree")),
        "prerequisite_graph": prerequisite_graph,
        "prerequisite_graph_meta": dict(meta),
    }


def annotate_plan_tree(
    template_tree: dict[str, Any] | None,
    *,
    completed_count: int,
    current_stage_index: int | None,
    plan_completed: bool,
    mastery_map: dict[str, float],
) -> dict[str, Any] | None:
    if not template_tree:
        return None

    def walk(node: dict[str, Any]) -> dict[str, Any]:
        node_type = str(node.get("node_type") or "group")
        annotated = {
            "node_type": node_type,
            "title": str(node.get("title") or ""),
            "breadcrumb": [str(item) for item in node.get("breadcrumb") or []],
            "children": [],
            "stage_index": node.get("stage_index"),
            "section_id": node.get("section_id"),
            "module_id": node.get("module_id"),
        }

        if node_type == "stage":
            stage_index = int(node.get("stage_index", -1))
            completed = stage_index >= 0 and stage_index < int(completed_count)
            current = (
                current_stage_index is not None
                and not plan_completed
                and stage_index == int(current_stage_index)
            )
            mastery_score = float(mastery_map.get(str(node.get("section_id") or ""), 0.0))
            annotated.update(
                {
                    "children": [],
                    "completed": completed,
                    "completed_leaf_count": 1 if completed else 0,
                    "total_leaf_count": 1,
                    "mastery_score": mastery_score,
                    "is_current_branch": current,
                    "is_current_stage": current,
                }
            )
            return annotated

        children = [walk(child) for child in node.get("children") or [] if isinstance(child, dict)]
        total_leaf_count = sum(int(child.get("total_leaf_count", 0)) for child in children)
        completed_leaf_count = sum(int(child.get("completed_leaf_count", 0)) for child in children)
        mastery_total = sum(
            float(child.get("mastery_score", 0.0)) * int(child.get("total_leaf_count", 0))
            for child in children
        )
        mastery_score = mastery_total / total_leaf_count if total_leaf_count else 0.0
        is_current_branch = any(bool(child.get("is_current_branch")) for child in children)
        annotated.update(
            {
                "children": children,
                "completed": total_leaf_count > 0 and completed_leaf_count == total_leaf_count,
                "completed_leaf_count": completed_leaf_count,
                "total_leaf_count": total_leaf_count,
                "mastery_score": mastery_score,
                "is_current_branch": is_current_branch,
                "is_current_stage": False,
            }
        )
        return annotated

    return walk(template_tree)
