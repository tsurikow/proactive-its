from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ContentIndexState:
    fingerprint: str
    source_fingerprint: str
    embedding_model: str
    chunk_target_tokens: int
    chunk_overlap_tokens: int
    documents_path: str
    updated_at: datetime


class ContentMetadataRepository:
    async def get_index_state(self, session: AsyncSession) -> ContentIndexState | None:
        result = await session.execute(
            text(
                """
                SELECT
                    fingerprint,
                    source_fingerprint,
                    embedding_model,
                    chunk_target_tokens,
                    chunk_overlap_tokens,
                    documents_path,
                    updated_at
                FROM content_index_state
                WHERE state_key = 'runtime_content'
                """
            )
        )
        row = result.mappings().first()
        if row is None:
            return None
        return ContentIndexState(
            fingerprint=str(row["fingerprint"]),
            source_fingerprint=str(row["source_fingerprint"]),
            embedding_model=str(row["embedding_model"]),
            chunk_target_tokens=int(row["chunk_target_tokens"]),
            chunk_overlap_tokens=int(row["chunk_overlap_tokens"]),
            documents_path=str(row["documents_path"]),
            updated_at=row["updated_at"],
        )

    async def upsert_index_state(
        self,
        *,
        session: AsyncSession,
        fingerprint: str,
        source_fingerprint: str,
        embedding_model: str,
        chunk_target_tokens: int,
        chunk_overlap_tokens: int,
        documents_path: str,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO content_index_state (
                    state_key,
                    fingerprint,
                    source_fingerprint,
                    embedding_model,
                    chunk_target_tokens,
                    chunk_overlap_tokens,
                    documents_path
                ) VALUES (
                    'runtime_content',
                    :fingerprint,
                    :source_fingerprint,
                    :embedding_model,
                    :chunk_target_tokens,
                    :chunk_overlap_tokens,
                    :documents_path
                )
                ON CONFLICT (state_key) DO UPDATE
                SET fingerprint = EXCLUDED.fingerprint,
                    source_fingerprint = EXCLUDED.source_fingerprint,
                    embedding_model = EXCLUDED.embedding_model,
                    chunk_target_tokens = EXCLUDED.chunk_target_tokens,
                    chunk_overlap_tokens = EXCLUDED.chunk_overlap_tokens,
                    documents_path = EXCLUDED.documents_path,
                    updated_at = now()
                """
            ),
            {
                "fingerprint": fingerprint,
                "source_fingerprint": source_fingerprint,
                "embedding_model": embedding_model,
                "chunk_target_tokens": chunk_target_tokens,
                "chunk_overlap_tokens": chunk_overlap_tokens,
                "documents_path": documents_path,
            },
        )
