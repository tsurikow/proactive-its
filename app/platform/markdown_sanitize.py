from __future__ import annotations

import re

_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1[^>]*>", flags=re.IGNORECASE)
_AUTO_LINK_RE = re.compile(r"<(https?://[^>]+)>", flags=re.IGNORECASE)
_EMPTY_ANCHOR_RE = re.compile(r"<a\s+id=\"[^\"]+\"\s*>\s*</a>", flags=re.IGNORECASE)
_SEE_FRAGMENT_PAREN_RE = re.compile(r"\(\s*see\s+[#a-z0-9:_-]+\s*\)", flags=re.IGNORECASE)
_SEE_FRAGMENT_BARE_RE = re.compile(r"\bsee\s+[#a-z0-9:_-]+\b", flags=re.IGNORECASE)
_FRAGMENT_TOKEN_RE = re.compile(r"\b(?:fs-id[0-9a-z-]+|cnx_[a-z0-9_]+)\b", flags=re.IGNORECASE)
_INTERNAL_ID_TOKEN_RE = re.compile(r"\b[a-z]{2,8}[-_][a-z0-9_-]{6,}\b", flags=re.IGNORECASE)
_HFILL_RE = re.compile(r"\\hfill")
_PROTECTED_FIGURE_TOKEN_RE = re.compile(r"\[\[FIGURE_TARGET_\d{4}]]")


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
    protected = _protect_figure_targets(str(markdown or ""))
    text = protected["text"]

    def replace_markdown_link(match: re.Match[str]) -> str:
        label = (match.group(1) or "").strip()
        target = _normalize_link_target(match.group(2) or "")
        if _PROTECTED_FIGURE_TOKEN_RE.fullmatch(target):
            return match.group(0)
        if target.startswith("#") and label.lower().startswith("fs-id"):
            return ""
        return label

    text = _MARKDOWN_LINK_RE.sub(replace_markdown_link, text)
    text = _AUTO_LINK_RE.sub("", text)
    text = strip_internal_reference_noise(text)
    return _restore_figure_targets(text, protected["tokens"])


def strip_internal_reference_noise(markdown: str) -> str:
    protected = _protect_figure_targets(str(markdown or ""))
    text = protected["text"]
    text = _EMPTY_ANCHOR_RE.sub("", text)
    text = _SEE_FRAGMENT_PAREN_RE.sub("", text)
    text = _SEE_FRAGMENT_BARE_RE.sub("", text)
    text = _FRAGMENT_TOKEN_RE.sub("", text)
    text = _HFILL_RE.sub("", text)
    text = re.sub(r"\(\s*#?[a-z0-9:_-]+\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![/\w-])#[a-z0-9:_-]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[a-z]{2,8}:[a-z0-9:_-]{6,}\b", "", text, flags=re.IGNORECASE)
    text = _INTERNAL_ID_TOKEN_RE.sub(_preserve_human_tokens, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return _restore_figure_targets(text, protected["tokens"])


def sanitize_lesson_markdown(markdown: str) -> str:
    text = strip_non_figure_links(markdown)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _protect_figure_targets(text: str) -> dict[str, object]:
    tokens: dict[str, str] = {}
    counter = 0

    def next_token(value: str) -> str:
        nonlocal counter
        counter += 1
        token = f"[[FIGURE_TARGET_{counter:04d}]]"
        tokens[token] = value
        return token

    def replace_markdown_image(match: re.Match[str]) -> str:
        alt = match.group(1) or ""
        target = match.group(2) or ""
        normalized = _normalize_link_target(target)
        if not is_figure_link(normalized):
            return match.group(0)
        return f"![{alt}]({next_token(target)})"

    def replace_markdown_link(match: re.Match[str]) -> str:
        label = match.group(1) or ""
        target = match.group(2) or ""
        normalized = _normalize_link_target(target)
        if not is_figure_link(normalized):
            return match.group(0)
        return f"[{label}]({next_token(target)})"

    def replace_html_image(match: re.Match[str]) -> str:
        quote = match.group(1) or '"'
        src = match.group(2) or ""
        normalized = _normalize_link_target(src)
        if not is_figure_link(normalized):
            return match.group(0)
        token = next_token(src)
        return match.group(0).replace(f"src={quote}{src}{quote}", f"src={quote}{token}{quote}", 1)

    text = _MARKDOWN_IMAGE_RE.sub(replace_markdown_image, text)
    text = _MARKDOWN_LINK_RE.sub(replace_markdown_link, text)
    text = _HTML_IMAGE_RE.sub(replace_html_image, text)
    return {"text": text, "tokens": tokens}


def _restore_figure_targets(text: str, tokens: dict[str, str]) -> str:
    restored = text
    for token, value in tokens.items():
        restored = restored.replace(token, value)
    return restored


def _preserve_human_tokens(match: re.Match[str]) -> str:
    token = match.group(0)
    if token.lower().startswith(("http", "media", "figure")):
        return token
    if re.search(r"[A-Z]", token):
        return ""
    return token
