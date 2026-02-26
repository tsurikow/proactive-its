from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_id: str
    content_text: str
    order_index: int
    chunk_type: str
    subsection_title: str | None


@dataclass
class _Block:
    clean_text: str
    chunk_type: str
    subsection_title: str | None


HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^\)]*\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9\s]")
FIGURE_LINE_RE = re.compile(r"^\s*\*?\s*Figure:.*$", re.IGNORECASE | re.MULTILINE)
HTML_TAG_RE = re.compile(r"</?[^>]+>")

CHUNK_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("definition", re.compile(r"\bdefinition\b", re.IGNORECASE)),
    ("theorem", re.compile(r"\btheorem\b", re.IGNORECASE)),
    ("proof", re.compile(r"\bproof\b", re.IGNORECASE)),
    ("example", re.compile(r"\bexample\b", re.IGNORECASE)),
    ("checkpoint", re.compile(r"\bcheckpoint\b", re.IGNORECASE)),
    ("exercise", re.compile(r"\bexercises?\b", re.IGNORECASE)),
]
TYPE_PRIORITY = ["definition", "theorem", "proof", "example", "checkpoint", "exercise", "concept"]
ATOMIC_TYPES = {"definition", "theorem", "proof", "example", "checkpoint", "exercise"}
LABEL_ONLY_RE = re.compile(
    r"^(?:\*{1,2})?(example|problem|solution|checkpoint|definition|theorem|proof|exercise)"
    r"(?:\*{1,2})?:?$",
    re.IGNORECASE,
)
LOW_VALUE_INSTRUCTION_RE = re.compile(
    r"^(for|in)\s+the\s+following\s+(exercise|exercises|problem|problems)\b",
    re.IGNORECASE,
)


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.25))


def _text_signal_score(text: str) -> int:
    no_images = IMAGE_RE.sub("", text)
    no_images = HTML_IMAGE_RE.sub("", no_images)
    no_symbols = NON_ALNUM_RE.sub("", no_images)
    return len(no_symbols.strip())


def _classify_block_type(text: str) -> str:
    probe = text[:400]
    for chunk_type, pattern in CHUNK_TYPE_PATTERNS:
        if pattern.search(probe):
            return chunk_type
    return "concept"


def _clean_block_for_retrieval(text: str) -> str:
    cleaned = str(text)
    cleaned = IMAGE_RE.sub(" ", cleaned)
    cleaned = HTML_IMAGE_RE.sub(" ", cleaned)
    cleaned = FIGURE_LINE_RE.sub(" ", cleaned)
    cleaned = MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"^\s*>\s?", "", cleaned, flags=re.MULTILINE)
    cleaned = HTML_TAG_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _split_oversized_block(block: _Block, target_tokens: int) -> list[_Block]:
    parts = [p.strip() for p in re.split(r"\n{2,}", block.clean_text.strip()) if p.strip()]
    if not parts:
        return [block]

    out: list[_Block] = []
    current: list[str] = []
    current_tokens = 0

    for part in parts:
        part_tokens = estimate_tokens(part)
        if part_tokens > target_tokens:
            if current:
                joined = "\n\n".join(current).strip()
                out.append(
                    _Block(
                        clean_text=joined,
                        chunk_type=block.chunk_type,
                        subsection_title=block.subsection_title,
                    )
                )
                current = []
                current_tokens = 0

            # Keep oversized math-heavy paragraphs intact to avoid breaking LaTeX.
            if _contains_math(part):
                out.append(
                    _Block(
                        clean_text=part,
                        chunk_type=block.chunk_type,
                        subsection_title=block.subsection_title,
                    )
                )
                continue

            # Fallback split by line; if still oversized keep as-is.
            lines = [line.strip() for line in part.splitlines() if line.strip()]
            if not lines:
                lines = [part]
            line_buf: list[str] = []
            line_tokens = 0
            for line in lines:
                lt = estimate_tokens(line)
                if line_buf and line_tokens + lt > target_tokens:
                    line_joined = "\n".join(line_buf).strip()
                    out.append(
                        _Block(
                            clean_text=line_joined,
                            chunk_type=block.chunk_type,
                            subsection_title=block.subsection_title,
                        )
                    )
                    line_buf = [line]
                    line_tokens = lt
                else:
                    line_buf.append(line)
                    line_tokens += lt
            if line_buf:
                line_joined = "\n".join(line_buf).strip()
                out.append(
                    _Block(
                        clean_text=line_joined,
                        chunk_type=block.chunk_type,
                        subsection_title=block.subsection_title,
                    )
                )
            continue

        if current and current_tokens + part_tokens > target_tokens:
            joined = "\n\n".join(current).strip()
            out.append(
                _Block(
                    clean_text=joined,
                    chunk_type=block.chunk_type,
                    subsection_title=block.subsection_title,
                )
            )
            current = [part]
            current_tokens = part_tokens
        else:
            current.append(part)
            current_tokens += part_tokens

    if current:
        joined = "\n\n".join(current).strip()
        out.append(
            _Block(
                clean_text=joined,
                chunk_type=block.chunk_type,
                subsection_title=block.subsection_title,
            )
        )
    return [b for b in out if b.clean_text]


def _contains_math(text: str) -> bool:
    return "$" in text or r"\(" in text or r"\[" in text or r"\begin{" in text


def _dominant_chunk_type(types: list[str]) -> str:
    if not types:
        return "concept"
    for item in TYPE_PRIORITY:
        if item in types:
            return item
    return types[0]


def _parse_blocks(text: str) -> list[_Block]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    parsed: list[_Block] = []
    current_subsection: str | None = None

    for block in blocks:
        first_line = block.splitlines()[0] if block.splitlines() else block
        heading_match = HEADING_RE.match(first_line)
        if heading_match:
            current_subsection = heading_match.group(1).strip()
            if len(block.splitlines()) == 1:
                continue

        clean_text = _clean_block_for_retrieval(block)
        if not clean_text:
            continue
        parsed.append(
            _Block(
                clean_text=clean_text,
                chunk_type=_classify_block_type(f"{current_subsection or ''}\n{block}"),
                subsection_title=current_subsection,
            )
        )

    # Merge structural label-only blocks (e.g. "**Solution**") with the next block.
    out: list[_Block] = []
    idx = 0
    while idx < len(parsed):
        current = parsed[idx]
        if _is_label_only_block(current) and idx + 1 < len(parsed):
            nxt = parsed[idx + 1]
            out.append(
                _Block(
                    clean_text=f"{current.clean_text}\n\n{nxt.clean_text}".strip(),
                    chunk_type=nxt.chunk_type if nxt.chunk_type != "concept" else current.chunk_type,
                    subsection_title=nxt.subsection_title or current.subsection_title,
                )
            )
            idx += 2
            continue
        out.append(current)
        idx += 1
    return out


def _is_label_only_block(block: _Block) -> bool:
    probe = re.sub(r"\s+", " ", block.clean_text.strip())
    return bool(LABEL_ONLY_RE.fullmatch(probe))


def _is_low_value_instruction_chunk(chunk_type: str, text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip()).lower()
    if not normalized:
        return True
    if chunk_type not in {"concept", "exercise", "checkpoint"}:
        return False
    if not LOW_VALUE_INSTRUCTION_RE.match(normalized):
        return False
    # Skip short directive-only prompts; keep richer exercise blocks.
    return len(normalized) < 240 and "solution" not in normalized


def _group_token_count(group: list[_Block]) -> int:
    return sum(estimate_tokens(item.clean_text) for item in group)


def _group_dominant_type(group: list[_Block]) -> str:
    return _dominant_chunk_type([item.chunk_type for item in group])


def _merge_short_atomic_groups(grouped: list[list[_Block]], target_tokens: int) -> list[list[_Block]]:
    merged: list[list[_Block]] = []
    for group in grouped:
        if not merged:
            merged.append(group[:])
            continue
        prev = merged[-1]
        prev_type = _group_dominant_type(prev)
        cur_type = _group_dominant_type(group)
        prev_sub = next((item.subsection_title for item in prev if item.subsection_title), None)
        cur_sub = next((item.subsection_title for item in group if item.subsection_title), None)
        combined_tokens = _group_token_count(prev) + _group_token_count(group)
        short_pair = _group_token_count(prev) <= int(target_tokens * 0.55) and _group_token_count(group) <= int(
            target_tokens * 0.55
        )
        if (
            prev_type == cur_type
            and prev_type in ATOMIC_TYPES
            and prev_sub
            and prev_sub == cur_sub
            and short_pair
            and combined_tokens <= int(target_tokens * 1.05)
        ):
            prev.extend(group)
        else:
            merged.append(group[:])
    return merged


def split_markdown_into_chunks(
    doc_id: str,
    text: str,
    target_tokens: int = 900,
    overlap_tokens: int = 120,
    min_signal_chars: int = 80,
) -> list[Chunk]:
    _ = overlap_tokens
    raw_blocks = _parse_blocks(text)
    if not raw_blocks:
        return []

    blocks: list[_Block] = []
    for block in raw_blocks:
        if estimate_tokens(block.clean_text) > target_tokens:
            blocks.extend(_split_oversized_block(block, target_tokens))
        else:
            blocks.append(block)

    grouped: list[list[_Block]] = []
    current: list[_Block] = []
    current_tokens = 0

    for block in blocks:
        block_tokens = estimate_tokens(block.clean_text)
        if block.chunk_type in ATOMIC_TYPES:
            if current:
                grouped.append(current)
                current = []
                current_tokens = 0
            grouped.append([block])
            continue

        if current and (current_tokens + block_tokens > target_tokens or len(current) >= 3):
            grouped.append(current)
            current = [block]
            current_tokens = block_tokens
        else:
            current.append(block)
            current_tokens += block_tokens
    if current:
        grouped.append(current)
    grouped = _merge_short_atomic_groups(grouped, target_tokens)

    chunks: list[Chunk] = []
    for group in grouped:
        chunk_text = "\n\n".join(item.clean_text for item in group).strip()
        if not chunk_text:
            continue
        subsection_title = next((b.subsection_title for b in group if b.subsection_title), None)
        chunk_type = _dominant_chunk_type([b.chunk_type for b in group])
        has_math = _contains_math(chunk_text)
        signal_floor = 8 if has_math else (28 if chunk_type in ATOMIC_TYPES else min_signal_chars)
        if _text_signal_score(chunk_text) < signal_floor:
            continue
        if _is_low_value_instruction_chunk(chunk_type, chunk_text):
            continue

        order_index = len(chunks)
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}::chunk{order_index}",
                content_text=chunk_text,
                order_index=order_index,
                chunk_type=chunk_type,
                subsection_title=subsection_title,
            )
        )

    return chunks
