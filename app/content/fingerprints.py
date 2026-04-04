from __future__ import annotations

import hashlib
from pathlib import Path

from app.platform.config import Settings


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def content_index_fingerprint(*, settings: Settings, source_fingerprint: str) -> str:
    payload = "|".join(
        [
            source_fingerprint,
            settings.embedding_model,
            str(settings.chunk_target_tokens),
            str(settings.chunk_overlap_tokens),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
