from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.platform.vector_store import AsyncVectorStore


@dataclass(frozen=True)
class StageSource:
    section_id: str
    parent_doc_id: str
    source_markdown: str
    source_hash: str


async def resolve_stage_source(store: AsyncVectorStore, stage: dict[str, Any]) -> StageSource:
    section_id = str(stage.get("section_id") or "").strip()
    module_id = str(stage.get("module_id") or "").strip() or None
    if not section_id:
        raise RuntimeError("Current stage is missing section_id.")

    parent = await store.fetch_section_parent(section_id)
    if not parent and module_id and module_id != section_id:
        parent = await store.fetch_section_parent(module_id)
    if not parent:
        raise RuntimeError(
            f"Parent section document is missing for stage '{section_id}'. Reindex the sections collection."
        )

    parent_doc_id = str(parent.get("parent_doc_id") or parent.get("doc_id") or section_id).strip()
    source_markdown = str(parent.get("content_text_full") or "").strip()
    if not source_markdown:
        raise RuntimeError(
            f"Parent section '{parent_doc_id}' has no full source content. Reindex the sections collection."
        )

    return StageSource(
        section_id=section_id,
        parent_doc_id=parent_doc_id,
        source_markdown=source_markdown,
        source_hash=hashlib.sha256(source_markdown.encode("utf-8")).hexdigest(),
    )
