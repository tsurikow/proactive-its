from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.platform.vector_store import AsyncVectorStore

CONTENT_NOT_READY = "content_not_ready"
LIST_LINE_RE = re.compile(r"^([-*+•]|\d+[.)]|[a-zA-Z][.)])\s+")
LABEL_LINE_RE = re.compile(
    r"^(problem|solution|answer|checkpoint|exercise|example|key idea|key concept|review)\s*:\s*",
    re.IGNORECASE,
)
HEADING_LINE_RE = re.compile(r"^#{1,6}\s+")


@dataclass(frozen=True)
class TeacherSourceContext:
    formatted_source: str
    block_ids: list[str]
    block_texts: list[str]
    contains_explicit_tasks: bool
    contains_solution_like_content: bool
    contains_review_like_content: bool


@dataclass(frozen=True)
class StageSource:
    section_id: str
    parent_doc_id: str
    title: str | None
    breadcrumb: list[str]
    source_markdown: str
    source_hash: str


def _is_structural_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(
        LIST_LINE_RE.match(stripped)
        or LABEL_LINE_RE.match(stripped)
        or HEADING_LINE_RE.match(stripped)
    )


def _segment_source_lines(source: str) -> list[list[str]]:
    lines = [line.rstrip() for line in str(source or "").splitlines()]
    segments: list[list[str]] = []
    current: list[str] = []

    def flush_current() -> None:
        nonlocal current
        if current:
            segments.append(current)
        current = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            flush_current()
            continue
        if _is_structural_line(stripped) and current and not _is_structural_line(current[-1]):
            flush_current()
        current.append(stripped)
    flush_current()
    return segments


def _split_long_segment(segment: list[str], *, max_block_chars: int) -> list[list[str]]:
    joined = "\n".join(segment).strip()
    if len(joined) <= max_block_chars:
        return [segment]

    chunks: list[list[str]] = []
    current_words: list[str] = []
    current_length = 0
    for word in joined.split():
        candidate_length = current_length + (1 if current_words else 0) + len(word)
        if current_words and candidate_length > max_block_chars:
            chunks.append([" ".join(current_words)])
            current_words = [word]
            current_length = len(word)
        else:
            current_words.append(word)
            current_length = candidate_length
    if current_words:
        chunks.append([" ".join(current_words)])
    return chunks


def build_teacher_source_context(
    *,
    source_markdown: str,
    title: str | None = None,
    breadcrumb: list[str] | None = None,
    max_block_chars: int = 500,
) -> TeacherSourceContext:
    cleaned_source = str(source_markdown or "").strip()
    segmented_source = _segment_source_lines(cleaned_source) or [[cleaned_source[:max_block_chars].strip()]]
    normalized_blocks: list[list[str]] = []
    for segment in segmented_source:
        normalized_blocks.extend(_split_long_segment(segment, max_block_chars=max_block_chars))

    if not normalized_blocks:
        normalized_blocks = [[cleaned_source[:max_block_chars].strip()]]

    block_ids = [f"block_{index:02d}" for index in range(1, len(normalized_blocks) + 1)]
    block_texts = ["\n".join(block_lines).strip() for block_lines in normalized_blocks]
    breadcrumb_items = [str(item).strip() for item in breadcrumb or [] if str(item).strip()]

    formatted_lines = ["Normalized source blocks:"]
    if title:
        formatted_lines.append(f"Section title: {title}")
    if breadcrumb_items:
        formatted_lines.append(f"Section breadcrumb: {' > '.join(breadcrumb_items)}")
    if len(formatted_lines) > 1:
        formatted_lines.append("")
    for block_id, block_lines in zip(block_ids, normalized_blocks, strict=True):
        formatted_lines.append(f"[{block_id}]")
        formatted_lines.extend(block_lines)
        if len(block_lines) > 1 or any(_is_structural_line(line) for line in block_lines):
            for line_index, line_text in enumerate(block_lines, start=1):
                formatted_lines.append(f"[{block_id}#line_{line_index:02d}] {line_text}")
        formatted_lines.append("")

    combined_text = "\n".join(block_texts).lower()
    hint_text = " ".join([str(title or "").lower(), " ".join(breadcrumb_items).lower(), combined_text])
    contains_explicit_tasks = any(
        token in hint_text
        for token in ["problem", "exercise", "checkpoint", "show that", "find ", "compute ", "determine "]
    )
    contains_solution_like_content = any(
        token in hint_text
        for token in ["solution", "answer", "therefore", "thus", "hence", "we get"]
    )
    contains_review_like_content = any(
        token in hint_text
        for token in ["review", "summary", "key concept", "key idea", "recap"]
    )

    return TeacherSourceContext(
        formatted_source="\n".join(formatted_lines).strip(),
        block_ids=block_ids,
        block_texts=block_texts,
        contains_explicit_tasks=contains_explicit_tasks,
        contains_solution_like_content=contains_solution_like_content,
        contains_review_like_content=contains_review_like_content,
    )


async def resolve_stage_source(store: AsyncVectorStore, stage: dict[str, Any]) -> StageSource:
    section_id = str(stage.get("section_id") or "").strip()
    module_id = str(stage.get("module_id") or "").strip() or None
    if not section_id:
        raise RuntimeError(CONTENT_NOT_READY)

    try:
        parent = await store.fetch_section_parent(section_id)
        if not parent and module_id and module_id != section_id:
            parent = await store.fetch_section_parent(module_id)
    except Exception as exc:
        raise RuntimeError(CONTENT_NOT_READY) from exc
    if not parent:
        raise RuntimeError(CONTENT_NOT_READY)

    parent_doc_id = str(parent.get("parent_doc_id") or parent.get("doc_id") or section_id).strip()
    source_markdown = str(parent.get("content_text_full") or "").strip()
    if not source_markdown:
        raise RuntimeError(CONTENT_NOT_READY)

    return StageSource(
        section_id=section_id,
        parent_doc_id=parent_doc_id,
        title=None if parent.get("title") is None else str(parent.get("title") or "").strip() or None,
        breadcrumb=[str(item).strip() for item in parent.get("breadcrumb") or [] if str(item).strip()],
        source_markdown=source_markdown,
        source_hash=hashlib.sha256(source_markdown.encode("utf-8")).hexdigest(),
    )
