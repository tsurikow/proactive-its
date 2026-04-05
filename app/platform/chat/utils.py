from __future__ import annotations

import re
from typing import Any

FIGURE_MD_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
FIGURE_HTML_RE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
    re.IGNORECASE,
)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")


def chunk_text(payload: dict[str, Any]) -> str:
    text = payload.get("content_text")
    if text:
        return str(text)
    text = payload.get("content")
    if text:
        return str(text)
    return ""


def clean_chunk_text(text: str) -> str:
    clean = str(text or "")
    clean = re.sub(r"<!--openstax:begin[\s\S]*?-->", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<!--openstax:end[\s\S]*?-->", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<a\s+id=\"[^\"]+\"\s*>\s*</a>", " ", clean, flags=re.IGNORECASE)
    clean = MARKDOWN_IMAGE_RE.sub(" ", clean)
    clean = re.sub(r"<img\b[^>]*>", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\*Figure:[^*\n]*(?:\*|$)", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def extract_figure_links(chunks: list[dict[str, Any]]) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        text = chunk_text(chunk)
        for src in FIGURE_MD_RE.findall(text):
            link = src.strip()
            if link and link not in seen:
                seen.add(link)
                links.append(link)
        for src in FIGURE_HTML_RE.findall(text):
            link = src.strip()
            if link and link not in seen:
                seen.add(link)
                links.append(link)
    return links
