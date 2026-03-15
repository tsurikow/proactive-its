from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingest.parser import ParsedBlock, infer_chunk_type, is_math_heavy, parse_markdown_blocks
from app.ingest.token_count import TokenCounter, build_token_counter


@dataclass
class Chunk:
    chunk_id: str
    content_text: str
    order_index: int
    chunk_type: str
    subsection_title: str | None


NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9\s]")
ATOMIC_CHUNK_TYPES = {"definition", "theorem", "proof", "example", "checkpoint", "exercise"}
STRUCTURAL_BLOCK_TYPES = {"image", "table", "code_block", "html_block", "math_block"}


def split_markdown_into_chunks(
    doc_id: str,
    text: str,
    target_tokens: int = 900,
    overlap_tokens: int = 120,
    min_signal_chars: int = 80,
    token_counter: TokenCounter | None = None,
) -> list[Chunk]:
    _ = overlap_tokens
    counter = token_counter or build_token_counter()
    blocks = parse_markdown_blocks(text)
    if not blocks:
        return []

    chunks: list[Chunk] = []
    current_blocks: list[ParsedBlock] = []
    current_tokens = 0
    current_heading: str | None = None

    def flush() -> None:
        nonlocal current_blocks, current_tokens
        chunk = _build_chunk(
            doc_id=doc_id,
            blocks=current_blocks,
            order_index=len(chunks),
            subsection_title=current_heading,
            min_signal_chars=min_signal_chars,
        )
        if chunk is not None:
            chunks.append(chunk)
        current_blocks = []
        current_tokens = 0

    for block in blocks:
        if block.block_type == "heading":
            if current_blocks:
                flush()
            current_heading = block.text_content or current_heading
            continue

        block_tokens = counter.count(block.raw_markdown)
        block_chunk_type = str(block.meta.get("chunk_type") or infer_chunk_type(block.text_content))

        if _is_atomic_block(block, block_chunk_type) or block_tokens >= target_tokens:
            if current_blocks:
                flush()
            current_blocks = [block]
            current_tokens = block_tokens
            flush()
            continue

        if current_blocks and _should_flush(current_blocks, current_tokens, block, block_tokens, target_tokens):
            flush()

        current_blocks.append(block)
        current_tokens += block_tokens

    if current_blocks:
        flush()
    return chunks


def _build_chunk(
    *,
    doc_id: str,
    blocks: list[ParsedBlock],
    order_index: int,
    subsection_title: str | None,
    min_signal_chars: int,
) -> Chunk | None:
    if not blocks:
        return None
    content_text = "\n\n".join(block.raw_markdown.strip() for block in blocks if block.raw_markdown.strip()).strip()
    if not content_text:
        return None
    signal_score = _text_signal_score(content_text)
    contains_math = any(is_math_heavy(block) for block in blocks)
    signal_floor = 8 if contains_math else min_signal_chars
    if signal_score < signal_floor:
        return None

    chunk_type = _dominant_chunk_type([str(block.meta.get("chunk_type") or "concept") for block in blocks])
    return Chunk(
        chunk_id=f"{doc_id}::chunk{order_index}",
        content_text=content_text,
        order_index=order_index,
        chunk_type=chunk_type,
        subsection_title=subsection_title,
    )


def _should_flush(
    current_blocks: list[ParsedBlock],
    current_tokens: int,
    next_block: ParsedBlock,
    next_tokens: int,
    target_tokens: int,
) -> bool:
    if current_tokens + next_tokens > target_tokens:
        return True
    if current_blocks[-1].block_type in STRUCTURAL_BLOCK_TYPES and next_block.block_type in STRUCTURAL_BLOCK_TYPES:
        return True
    return False


def _is_atomic_block(block: ParsedBlock, chunk_type: str) -> bool:
    return block.block_type in STRUCTURAL_BLOCK_TYPES or chunk_type in ATOMIC_CHUNK_TYPES


def _dominant_chunk_type(types: list[str]) -> str:
    priority = ["definition", "theorem", "proof", "example", "checkpoint", "exercise", "concept"]
    for item in priority:
        if item in types:
            return item
    return types[0] if types else "concept"


def _text_signal_score(text: str) -> int:
    no_symbols = NON_ALNUM_RE.sub("", text)
    return len(no_symbols.strip())

