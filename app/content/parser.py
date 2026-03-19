from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token


MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
LATEX_ENV_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
FIGURE_CAPTION_RE = re.compile(r"^\s*\*?\s*Figure:.*$", flags=re.IGNORECASE)
HEADING_TEXT_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("definition", re.compile(r"\bdefinition\b", re.IGNORECASE)),
    ("theorem", re.compile(r"\btheorem\b", re.IGNORECASE)),
    ("proof", re.compile(r"\bproof\b", re.IGNORECASE)),
    ("example", re.compile(r"\b(example|worked example|try it)\b", re.IGNORECASE)),
    ("checkpoint", re.compile(r"\b(checkpoint|practice)\b", re.IGNORECASE)),
    ("exercise", re.compile(r"\b(exercise|problem|problems|exercises)\b", re.IGNORECASE)),
]
HTML_BLOCK_TYPES = {
    "html_block",
    "html_inline",
}
NON_PROSE_TYPES = {"image", "table", "code_block", "html_block", "math_block"}


@dataclass
class ParsedBlock:
    block_type: str
    raw_markdown: str
    text_content: str
    meta: dict[str, Any] = field(default_factory=dict)


_md = MarkdownIt("commonmark").enable("table")


def parse_markdown_blocks(markdown: str) -> list[ParsedBlock]:
    lines = str(markdown or "").splitlines(keepends=True)
    if not lines:
        return []

    tokens = _md.parse(markdown)
    blocks: list[ParsedBlock] = []
    idx = 0

    while idx < len(tokens):
        token = tokens[idx]
        token_type = token.type

        if token.nesting == -1 or token_type == "inline":
            idx += 1
            continue

        if token_type == "heading_open":
            close_idx = _find_matching_close(tokens, idx)
            raw = _slice_token_lines(lines, token, tokens[close_idx])
            text = _inline_content(tokens, idx + 1)
            level = int(token.tag[1]) if token.tag.startswith("h") and token.tag[1:].isdigit() else None
            blocks.append(
                ParsedBlock(
                    block_type="heading",
                    raw_markdown=raw,
                    text_content=text,
                    meta={
                        "level": level,
                        "chunk_type": infer_chunk_type(text),
                    },
                )
            )
            idx = close_idx + 1
            continue

        if token_type in {"paragraph_open", "blockquote_open", "bullet_list_open", "ordered_list_open", "table_open"}:
            close_idx = _find_matching_close(tokens, idx)
            raw = _slice_token_lines(lines, token, tokens[close_idx])
            block_type = _map_container_type(token_type, raw, tokens, idx, close_idx)
            text = normalize_text(raw)
            blocks.append(
                ParsedBlock(
                    block_type=block_type,
                    raw_markdown=raw,
                    text_content=text,
                    meta={"chunk_type": infer_chunk_type(text)},
                )
            )
            idx = close_idx + 1
            continue

        if token_type in {"fence", "code_block"}:
            raw = _slice_single_token(lines, token)
            blocks.append(
                ParsedBlock(
                    block_type="code_block",
                    raw_markdown=raw,
                    text_content=normalize_text(raw),
                    meta={"chunk_type": "concept"},
                )
            )
            idx += 1
            continue

        if token_type in HTML_BLOCK_TYPES:
            raw = _slice_single_token(lines, token)
            block_type = "image" if HTML_IMAGE_RE.search(raw) else "html_block"
            blocks.append(
                ParsedBlock(
                    block_type=block_type,
                    raw_markdown=raw,
                    text_content=normalize_text(raw),
                    meta={"chunk_type": "concept"},
                )
            )
            idx += 1
            continue

        raw = _slice_single_token(lines, token)
        if raw.strip():
            blocks.append(
                ParsedBlock(
                    block_type=_infer_fallback_block_type(raw),
                    raw_markdown=raw,
                    text_content=normalize_text(raw),
                    meta={"chunk_type": infer_chunk_type(raw)},
                )
            )
        idx += 1

    return _merge_image_captions(blocks)


def normalize_text(markdown: str) -> str:
    text = str(markdown or "")
    text = MARKDOWN_IMAGE_RE.sub(" ", text)
    text = HTML_IMAGE_RE.sub(" ", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"</?[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def infer_chunk_type(text: str) -> str:
    probe = str(text or "")[:240]
    for chunk_type, pattern in TYPE_PATTERNS:
        if pattern.search(probe):
            return chunk_type
    return "concept"


def is_math_heavy(block: ParsedBlock) -> bool:
    text = block.raw_markdown
    return "$" in text or r"\(" in text or r"\[" in text or bool(LATEX_ENV_RE.search(text))


def _find_matching_close(tokens: list[Token], open_idx: int) -> int:
    depth = 0
    for idx in range(open_idx, len(tokens)):
        depth += tokens[idx].nesting
        if depth == 0:
            return idx
    return open_idx


def _slice_token_lines(lines: list[str], start_token: Token, end_token: Token) -> str:
    start = start_token.map[0] if start_token.map else None
    end = start_token.map[1] if start_token.map else None
    if end is None and end_token.map:
        end = end_token.map[1]
    if start is None or end is None:
        return start_token.content or ""
    return "".join(lines[start:end]).strip()


def _slice_single_token(lines: list[str], token: Token) -> str:
    if token.map:
        return "".join(lines[token.map[0] : token.map[1]]).strip()
    return str(token.content or "").strip()


def _inline_content(tokens: list[Token], inline_idx: int) -> str:
    if inline_idx >= len(tokens):
        return ""
    token = tokens[inline_idx]
    if token.type != "inline":
        return ""
    return str(token.content or "").strip()


def _map_container_type(
    token_type: str,
    raw: str,
    tokens: list[Token],
    start_idx: int,
    close_idx: int,
) -> str:
    if token_type == "table_open":
        return "table"
    if token_type == "blockquote_open":
        return "blockquote"
    if token_type in {"bullet_list_open", "ordered_list_open"}:
        return "list"
    if _is_image_only_block(tokens, start_idx, close_idx, raw):
        return "image"
    if _is_math_block(raw):
        return "math_block"
    return "paragraph"


def _is_image_only_block(tokens: list[Token], start_idx: int, close_idx: int, raw: str) -> bool:
    if MARKDOWN_IMAGE_RE.search(raw) or HTML_IMAGE_RE.search(raw):
        stripped = raw.strip()
        if stripped.startswith("![") or stripped.startswith("<img"):
            return True
    for idx in range(start_idx, close_idx + 1):
        child_tokens = tokens[idx].children or []
        if any(child.type == "image" for child in child_tokens):
            raw_without_images = MARKDOWN_IMAGE_RE.sub("", raw)
            raw_without_images = HTML_IMAGE_RE.sub("", raw_without_images)
            return not normalize_text(raw_without_images)
    return False


def _is_math_block(raw: str) -> bool:
    stripped = raw.strip()
    return (
        stripped.startswith("$$")
        or stripped.startswith(r"\[")
        or bool(LATEX_ENV_RE.search(stripped))
    )


def _infer_fallback_block_type(raw: str) -> str:
    if _is_math_block(raw):
        return "math_block"
    if MARKDOWN_IMAGE_RE.search(raw) or HTML_IMAGE_RE.search(raw):
        return "image"
    return "paragraph"


def _merge_image_captions(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    merged: list[ParsedBlock] = []
    idx = 0
    while idx < len(blocks):
        current = blocks[idx]
        if current.block_type == "image" and idx + 1 < len(blocks):
            nxt = blocks[idx + 1]
            if nxt.block_type == "paragraph" and FIGURE_CAPTION_RE.match(nxt.raw_markdown.strip()):
                merged.append(
                    ParsedBlock(
                        block_type="image",
                        raw_markdown=f"{current.raw_markdown}\n\n{nxt.raw_markdown}".strip(),
                        text_content=normalize_text(f"{current.raw_markdown}\n\n{nxt.raw_markdown}"),
                        meta=current.meta,
                    )
                )
                idx += 2
                continue
        merged.append(current)
        idx += 1
    return merged
