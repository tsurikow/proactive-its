from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from app.ingest.models import DocumentRecord

OPENSTAX_COMMENT_RE = re.compile(r"<!--openstax:(?:begin|end).*?-->", flags=re.DOTALL)
ANCHOR_RE = re.compile(r"<a\s+id=\".*?\"></a>\s*", flags=re.DOTALL)


def clean_markdown(content_md: str) -> str:
    cleaned = OPENSTAX_COMMENT_RE.sub("", content_md)
    cleaned = ANCHOR_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def iter_documents(path: str | Path) -> Iterator[DocumentRecord]:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as source:
        for line in source:
            line = line.strip()
            if line:
                yield DocumentRecord.model_validate(json.loads(line))
