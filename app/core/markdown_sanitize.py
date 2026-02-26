from __future__ import annotations

import re

_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
_AUTO_LINK_RE = re.compile(r"<(https?://[^>]+)>", flags=re.IGNORECASE)
_FS_ID_PAREN_RE = re.compile(r"\(\s*see\s+fs-id[0-9a-z-]+\s*\)", flags=re.IGNORECASE)
_FS_ID_BARE_RE = re.compile(r"\bsee\s+fs-id[0-9a-z-]+\b", flags=re.IGNORECASE)
_FS_ID_TOKEN_RE = re.compile(r"\bfs-id[0-9a-z-]+\b", flags=re.IGNORECASE)
_CNX_TOKEN_RE = re.compile(r"\bCNX_[A-Za-z0-9_]+\b")
_HFILL_RE = re.compile(r"\\hfill")


def _normalize_link_target(target: str) -> str:
    value = target.strip()
    if not value:
        return value
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if " " in value:
        value = value.split(" ", 1)[0].strip()
    return value


def is_figure_link(target: str) -> bool:
    url = _normalize_link_target(target).lower()
    if not url:
        return False
    if url.startswith("media/") or url.startswith("/media/"):
        return True
    base = url.split("?", 1)[0].split("#", 1)[0]
    return base.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))


def strip_non_figure_links(markdown: str) -> str:
    text = str(markdown or "")

    def replace_markdown_link(match: re.Match[str]) -> str:
        label = (match.group(1) or "").strip()
        target = _normalize_link_target(match.group(2) or "")
        if is_figure_link(target):
            return match.group(0)
        if target.startswith("#") and label.lower().startswith("fs-id"):
            return ""
        return label

    text = _MARKDOWN_LINK_RE.sub(replace_markdown_link, text)
    text = _AUTO_LINK_RE.sub("", text)
    text = _FS_ID_PAREN_RE.sub("", text)
    text = _FS_ID_BARE_RE.sub("", text)
    text = _FS_ID_TOKEN_RE.sub("", text)
    text = _CNX_TOKEN_RE.sub("", text)
    text = _HFILL_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
