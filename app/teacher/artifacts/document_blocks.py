from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.platform.markdown_sanitize import strip_internal_reference_noise

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")
CODE_FENCE_RE = re.compile(r"^\s*```")
BEGIN_ENV_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
FIGURE_CAPTION_RE = re.compile(r"^\s*\*?\s*Figure:.*$", flags=re.IGNORECASE)
SOLUTION_RE = re.compile(
    r"\b(solution|answer|worked example|worked solution|complete solution)\b",
    flags=re.IGNORECASE,
)

REWRITE_BLOCK_TYPES = {"heading", "paragraph", "list", "blockquote", "math_block"}
IMMUTABLE_BLOCK_TYPES = {"table", "image", "code_block", "html_block"}


@dataclass
class DocumentBlock:
    block_id: str
    block_type: str
    raw_content: str
    normalized_text: str
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_section_markdown(markdown: str) -> list[DocumentBlock]:
    lines = str(markdown or "").splitlines(keepends=True)
    blocks: list[DocumentBlock] = []
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        if not stripped:
            idx += 1
            continue

        if CODE_FENCE_RE.match(line):
            content, idx = _consume_code_fence(lines, idx)
            blocks.append(_build_block("code_block", content))
            continue

        if _starts_math_block(line):
            content, idx = _consume_math_block(lines, idx)
            blocks.append(_build_block("math_block", content))
            continue

        if _starts_latex_env(line):
            content, idx = _consume_latex_env(lines, idx)
            blocks.append(_build_block("math_block", content))
            continue

        if _is_table_start(lines, idx):
            content, idx = _consume_table(lines, idx)
            blocks.append(_build_block("table", content))
            continue

        if MARKDOWN_IMAGE_RE.search(line) or HTML_IMAGE_RE.search(line):
            content, idx = _consume_image(lines, idx)
            blocks.append(_build_block("image", content))
            continue

        if _is_html_block(line):
            content, idx = _consume_html_block(lines, idx)
            blocks.append(_build_block("html_block", content))
            continue

        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            blocks.append(
                _build_block(
                    "heading",
                    line,
                    level=len(heading_match.group(1)),
                    solution_like=_is_solution_like(heading_match.group(2)),
                )
            )
            idx += 1
            continue

        if _is_list_item(stripped):
            content, idx = _consume_list(lines, idx)
            blocks.append(_build_block("list", content, solution_like=_is_solution_like(content)))
            continue

        if stripped.startswith(">"):
            content, idx = _consume_blockquote(lines, idx)
            blocks.append(_build_block("blockquote", content, solution_like=_is_solution_like(content)))
            continue

        content, idx = _consume_paragraph(lines, idx)
        blocks.append(_build_block("paragraph", content, solution_like=_is_solution_like(content)))

    for order, block in enumerate(blocks):
        block.block_id = f"block-{order:04d}"
        block.meta.setdefault("order_index", order)
    return blocks


def render_blocks_for_lesson(blocks: list[DocumentBlock]) -> tuple[str, dict[str, DocumentBlock]]:
    rendered: list[str] = []
    placeholders: dict[str, DocumentBlock] = {}

    for position, block in enumerate(blocks, start=1):
        if block.block_type in IMMUTABLE_BLOCK_TYPES:
            token = f"[[BLOCK_{position:04d}]]"
            placeholders[token] = block
            rendered.append(token)
            continue
        rendered.append(block.raw_content.strip())

    return "\n\n".join(part for part in rendered if part.strip()), placeholders


def restore_immutable_blocks(markdown: str, placeholders: dict[str, DocumentBlock]) -> str:
    restored = str(markdown or "")
    for token, block in placeholders.items():
        restored = restored.replace(token, block.raw_content.strip("\n"))
    return restored


def describe_blocks_for_prompt(blocks: list[DocumentBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        solution_hint = "yes" if block.meta.get("solution_like") else "no"
        parts.append(
            f"- id={block.block_id} type={block.block_type} solution_like={solution_hint}"
        )
    return "\n".join(parts)


def _build_block(block_type: str, raw_content: str, **meta: Any) -> DocumentBlock:
    return DocumentBlock(
        block_id="",
        block_type=block_type,
        raw_content=raw_content,
        normalized_text=_normalize_text(raw_content),
        meta=meta,
    )


def _normalize_text(text: str) -> str:
    normalized = strip_internal_reference_noise(str(text or ""))
    normalized = re.sub(r"</?[^>]+>", " ", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _consume_code_fence(lines: list[str], start: int) -> tuple[str, int]:
    end = start + 1
    while end < len(lines):
        if CODE_FENCE_RE.match(lines[end]):
            end += 1
            break
        end += 1
    return "".join(lines[start:end]), end


def _starts_math_block(line: str) -> bool:
    return "$$" in line or r"\[" in line


def _consume_math_block(lines: list[str], start: int) -> tuple[str, int]:
    line = lines[start]
    if "$$" in line and line.count("$$") >= 2:
        return line, start + 1
    if r"\[" in line and r"\]" in line:
        return line, start + 1

    end = start + 1
    closing = "$$" if "$$" in line else r"\]"
    while end < len(lines):
        if closing in lines[end]:
            end += 1
            break
        end += 1
    return "".join(lines[start:end]), end


def _starts_latex_env(line: str) -> bool:
    return bool(BEGIN_ENV_RE.search(line))


def _consume_latex_env(lines: list[str], start: int) -> tuple[str, int]:
    match = BEGIN_ENV_RE.search(lines[start])
    if not match:
        return lines[start], start + 1
    env_name = match.group(1)
    end_re = re.compile(rf"\\end\{{{re.escape(env_name)}}}")
    if end_re.search(lines[start]):
        return lines[start], start + 1
    end = start + 1
    while end < len(lines):
        if end_re.search(lines[end]):
            end += 1
            break
        end += 1
    return "".join(lines[start:end]), end


def _is_table_start(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    current = lines[idx].strip()
    next_line = lines[idx + 1].strip()
    return "|" in current and bool(TABLE_SEPARATOR_RE.match(next_line))


def _consume_table(lines: list[str], start: int) -> tuple[str, int]:
    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if not stripped or "|" not in stripped:
            break
        end += 1
    return "".join(lines[start:end]), end


def _consume_image(lines: list[str], start: int) -> tuple[str, int]:
    end = start + 1
    if end < len(lines) and FIGURE_CAPTION_RE.match(lines[end].strip()):
        end += 1
    return "".join(lines[start:end]), end


def _is_html_block(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("<") and stripped.endswith(">") and not HTML_IMAGE_RE.search(stripped)


def _consume_html_block(lines: list[str], start: int) -> tuple[str, int]:
    return lines[start], start + 1


def _is_list_item(stripped: str) -> bool:
    return bool(re.match(r"^([-*+]|\d+\.)\s+", stripped))


def _consume_list(lines: list[str], start: int) -> tuple[str, int]:
    end = start
    while end < len(lines):
        stripped = lines[end].strip()
        if not stripped:
            break
        if not (_is_list_item(stripped) or lines[end].startswith("  ") or lines[end].startswith("\t")):
            break
        end += 1
    return "".join(lines[start:end]), end


def _consume_blockquote(lines: list[str], start: int) -> tuple[str, int]:
    end = start
    while end < len(lines):
        stripped = lines[end].strip()
        if not stripped or not stripped.startswith(">"):
            break
        end += 1
    return "".join(lines[start:end]), end


def _consume_paragraph(lines: list[str], start: int) -> tuple[str, int]:
    end = start
    while end < len(lines):
        stripped = lines[end].strip()
        if not stripped:
            break
        if (
            CODE_FENCE_RE.match(lines[end])
            or _starts_math_block(lines[end])
            or _starts_latex_env(lines[end])
            or _is_table_start(lines, end)
            or MARKDOWN_IMAGE_RE.search(lines[end])
            or HTML_IMAGE_RE.search(lines[end])
            or _is_list_item(stripped)
            or stripped.startswith(">")
            or HEADING_RE.match(stripped)
            or _is_html_block(lines[end])
        ):
            break
        end += 1
    return "".join(lines[start:end]), end


def _is_solution_like(text: str) -> bool:
    return bool(SOLUTION_RE.search(strip_internal_reference_noise(text)))
