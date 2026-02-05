from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from app.ingest.models import DocumentRecord


def iter_documents(path: str | Path) -> Iterator[DocumentRecord]:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield DocumentRecord.model_validate(json.loads(line))
