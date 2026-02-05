from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


@dataclass
class TocSection:
    section_id: str
    title: str
    module_id: str | None
    breadcrumb: list[str]


def _walk_toc(
    node: dict[str, Any],
    current_module_id: str | None = None,
    path: list[str] | None = None,
) -> list[TocSection]:
    sections: list[TocSection] = []
    path = list(path or [])
    if node.get("title"):
        path.append(str(node.get("title")))
    node_module = node.get("module_id") or current_module_id
    children = node.get("children") or []

    if not children and node.get("id"):
        module_id = node.get("module_id") or current_module_id
        if module_id:
            sections.append(
                TocSection(
                    section_id=str(node.get("id")),
                    title=str(node.get("title", node.get("id"))),
                    module_id=module_id,
                    breadcrumb=path,
                )
            )

    for child in children:
        sections.extend(_walk_toc(child, node_module, path))

    return sections


def load_toc_sections(book_json_path: str) -> list[TocSection]:
    path = Path(book_json_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    toc = data.get("toc") or {}
    return _walk_toc(toc)


def week_start_for(day: date | None = None) -> date:
    today = day or date.today()
    return today - timedelta(days=today.weekday())


def build_linear_plan(toc_sections: list[TocSection], daily_cap: int = 3) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    for section in toc_sections:
        day_offset = len(targets) // daily_cap if daily_cap > 0 else 0
        targets.append(
            {
                "section_id": section.section_id,
                "module_id": section.module_id,
                "title": section.title,
                "breadcrumb": section.breadcrumb,
                "day": int(day_offset),
                "chunk_index": 0,
                "completed": False,
            }
        )

    return {
        "week_start": week_start_for().isoformat(),
        "daily_cap": daily_cap,
        "targets": targets,
    }
