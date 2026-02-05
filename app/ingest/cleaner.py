from __future__ import annotations

import re

OPENSTAX_COMMENT_RE = re.compile(r"<!--openstax:(?:begin|end).*?-->", flags=re.DOTALL)
ANCHOR_RE = re.compile(r"<a\s+id=\".*?\"></a>\s*", flags=re.DOTALL)


def clean_markdown(content_md: str) -> str:
    cleaned = OPENSTAX_COMMENT_RE.sub("", content_md)
    cleaned = ANCHOR_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
