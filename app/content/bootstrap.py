from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.content.fingerprints import content_index_fingerprint, file_fingerprint
from app.content.indexing.indexer import IndexStats, IndexingService
from app.content.repository import ContentMetadataRepository
from app.platform.config import Settings, get_settings
from app.platform.db import get_session
from app.platform.logging import log_event
from app.platform.vector_store import AsyncVectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContentBootstrapStatus:
    indexed: bool
    sections_count: int
    chunks_count: int
    stats: IndexStats | None = None
    skipped_reason: str | None = None


@dataclass(frozen=True)
class ContentReadinessStatus:
    sections_count: int
    chunks_count: int
    content_ready: bool


class ContentBootstrapService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        indexer: IndexingService | None = None,
        vector_store: AsyncVectorStore | None = None,
        metadata_repo: ContentMetadataRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.indexer = indexer or IndexingService(self.settings)
        self.vector_store = vector_store or AsyncVectorStore(self.settings)
        self.metadata_repo = metadata_repo or ContentMetadataRepository()

    async def current_status(self) -> ContentReadinessStatus:
        sections_count = await self.vector_store.count(self.settings.qdrant_sections_collection)
        chunks_count = await self.vector_store.count(self.settings.qdrant_collection)
        return ContentReadinessStatus(
            sections_count=sections_count,
            chunks_count=chunks_count,
            content_ready=sections_count > 0 and chunks_count > 0,
        )

    async def index_runtime_content_async(
        self,
        *,
        recreate: bool = False,
        documents_path: str | None = None,
        force: bool = False,
    ) -> ContentBootstrapStatus:
        status = await self.current_status()
        sections_count = status.sections_count
        chunks_count = status.chunks_count
        documents_source = Path(documents_path or self.settings.documents_json_path)
        if not documents_source.exists():
            raise FileNotFoundError(f"Content documents source not found: {documents_source}")
        source_fp = file_fingerprint(documents_source)
        fingerprint = content_index_fingerprint(settings=self.settings, source_fingerprint=source_fp)
        existing_state = await self._load_index_state()

        collections_populated = sections_count > 0 and chunks_count > 0
        if (
            not force
            and not recreate
            and existing_state is not None
            and existing_state.fingerprint == fingerprint
            and collections_populated
        ):
            log_event(
                logger,
                "content.index_skipped",
                reason="fingerprint_unchanged",
                sections_collection=self.settings.qdrant_sections_collection,
                chunks_collection=self.settings.qdrant_collection,
                sections_count=sections_count,
                chunks_count=chunks_count,
                documents_path=str(documents_source),
            )
            return ContentBootstrapStatus(
                indexed=False,
                sections_count=sections_count,
                chunks_count=chunks_count,
                skipped_reason="fingerprint_unchanged",
            )

        log_event(
            logger,
            "content.index_started",
            documents_path=str(documents_source),
            sections_collection=self.settings.qdrant_sections_collection,
            chunks_collection=self.settings.qdrant_collection,
            existing_sections_count=sections_count,
            existing_chunks_count=chunks_count,
            force=force,
        )
        stats = await self.indexer.index_jsonl_async(str(documents_source), recreate=recreate)
        sections_count = await self.vector_store.count(self.settings.qdrant_sections_collection)
        chunks_count = await self.vector_store.count(self.settings.qdrant_collection)
        if sections_count <= 0 or chunks_count <= 0:
            raise RuntimeError(
                "Content indexing completed without populating both Qdrant collections "
                f"(sections={sections_count}, chunks={chunks_count})."
            )
        await self._store_index_state(
            fingerprint=fingerprint,
            source_fingerprint=source_fp,
            documents_path=str(documents_source),
        )
        log_event(
            logger,
            "content.index_completed",
            documents_path=str(documents_source),
            docs_seen=stats.docs_seen,
            parents_indexed=stats.parents_indexed,
            children_indexed=stats.children_indexed,
            sections_collection=self.settings.qdrant_sections_collection,
            chunks_collection=self.settings.qdrant_collection,
            sections_count=sections_count,
            chunks_count=chunks_count,
        )
        return ContentBootstrapStatus(
            indexed=True,
            sections_count=sections_count,
            chunks_count=chunks_count,
            stats=stats,
        )

    async def _load_index_state(self):
        async with get_session() as session:
            return await self.metadata_repo.get_index_state(session)

    async def _store_index_state(
        self,
        *,
        fingerprint: str,
        source_fingerprint: str,
        documents_path: str,
    ) -> None:
        async with get_session() as session:
            await self.metadata_repo.upsert_index_state(
                session=session,
                fingerprint=fingerprint,
                source_fingerprint=source_fingerprint,
                embedding_model=self.settings.embedding_model,
                chunk_target_tokens=self.settings.chunk_target_tokens,
                chunk_overlap_tokens=self.settings.chunk_overlap_tokens,
                documents_path=documents_path,
            )
