from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Iterable

from qdrant_client.http import models as qm

from app.content.parsing.chunker import Chunk, split_markdown_into_chunks
from app.content.parsing.io import clean_markdown, iter_documents
from app.content.models import DocumentRecord
from app.platform.config import Settings, get_settings
from app.platform.embeddings import AsyncEmbeddingClient
from app.platform.vector_store import AsyncVectorStore

logger = logging.getLogger(__name__)


@dataclass
class IndexStats:
    docs_seen: int = 0
    parents_indexed: int = 0
    children_indexed: int = 0


class IndexingService:
    point_batch_size = 256

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.async_embedder = AsyncEmbeddingClient(self.settings)
        self.vector_store = AsyncVectorStore(self.settings)

    def index_jsonl(self, documents_path: str, recreate: bool = False) -> IndexStats:
        docs = iter_documents(documents_path)
        return self.index_documents(docs, recreate=recreate)

    def index_documents(self, documents: Iterable[DocumentRecord], recreate: bool = False) -> IndexStats:
        return asyncio.run(self.index_documents_async(documents, recreate=recreate))

    async def index_jsonl_async(self, documents_path: str, recreate: bool = False) -> IndexStats:
        docs = list(iter_documents(documents_path))
        return await self.index_documents_async(docs, recreate=recreate)

    async def index_documents_async(
        self,
        documents: Iterable[DocumentRecord],
        recreate: bool = False,
    ) -> IndexStats:
        stats = IndexStats()
        chunks_collection_ready = False
        sections_collection_ready = False
        parent_buffer: list[qm.PointStruct] = []
        child_buffer: list[qm.PointStruct] = []
        concurrency = max(1, int(self.settings.embedding_index_concurrency))
        semaphore = asyncio.Semaphore(concurrency)

        try:
            for doc in documents:
                stats.docs_seen += 1
                cleaned = clean_markdown(doc.content_md)
                if not cleaned:
                    continue

                section_id = self._resolve_section_id(doc)
                children = split_markdown_into_chunks(
                    doc_id=doc.doc_id,
                    text=cleaned,
                    target_tokens=self.settings.chunk_target_tokens,
                    overlap_tokens=self.settings.chunk_overlap_tokens,
                    min_signal_chars=self.settings.min_text_chars_for_chunk,
                )
                if not children:
                    continue

                if not sections_collection_ready:
                    if recreate:
                        await self.vector_store.recreate_collection(
                            None,
                            self.settings.qdrant_sections_collection,
                            payload_only=True,
                        )
                    else:
                        await self.vector_store.ensure_collection(
                            None,
                            self.settings.qdrant_sections_collection,
                            payload_only=True,
                        )
                    sections_collection_ready = True

                all_embeddings = await self._embed_with_concurrency(
                    [chunk.content_text for chunk in children],
                    text_labels=[
                        *[f"doc:{doc.doc_id}:chunk:{chunk.chunk_id}" for chunk in children],
                    ],
                    batch_label=f"doc:{doc.doc_id}",
                    semaphore=semaphore,
                )
                child_embeddings = all_embeddings
                vector_size = len(child_embeddings[0])

                if not chunks_collection_ready:
                    if recreate:
                        await self.vector_store.recreate_collection(vector_size, self.settings.qdrant_collection)
                    else:
                        await self.vector_store.ensure_collection(vector_size, self.settings.qdrant_collection)
                    chunks_collection_ready = True

                parent_payload = self._parent_payload(doc, section_id=section_id, content_text_full=cleaned)
                parent_point = qm.PointStruct(
                    id=self._point_id(f"{doc.doc_id}::parent"),
                    vector={},
                    payload=parent_payload,
                )

                child_points: list[qm.PointStruct] = []
                for chunk, vector in zip(children, child_embeddings, strict=True):
                    payload = self._child_payload(
                        doc=doc,
                        section_id=section_id,
                        chunk=chunk,
                    )
                    child_points.append(
                        qm.PointStruct(
                            id=self._point_id(chunk.chunk_id),
                            vector=vector,
                            payload=payload,
                        )
                    )

                parent_buffer.append(parent_point)
                child_buffer.extend(child_points)
                await self._flush_if_needed(parent_buffer, child_buffer)

                stats.parents_indexed += 1
                stats.children_indexed += len(child_points)
                logger.info(
                    "Indexed doc %s: parent=1 children=%d section_id=%s",
                    doc.doc_id,
                    len(child_points),
                    section_id,
                )

            await self._flush_points(parent_buffer, self.settings.qdrant_sections_collection)
            await self._flush_points(child_buffer, self.settings.qdrant_collection)
            return stats
        finally:
            await self.async_embedder.close()

    async def _embed_with_concurrency(
        self,
        texts: list[str],
        *,
        text_labels: list[str],
        batch_label: str,
        semaphore: asyncio.Semaphore,
    ) -> list[list[float]]:
        batch_size = max(1, int(self.settings.embedding_batch_size))
        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
        label_batches = [text_labels[i : i + batch_size] for i in range(0, len(text_labels), batch_size)]

        async def _run_batch(batch: list[str], labels: list[str], batch_index: int) -> list[list[float]]:
            async with semaphore:
                return await self.async_embedder.embed_texts(
                    batch,
                    batch_label=f"{batch_label}:batch:{batch_index}",
                    item_labels=labels,
                )

        results = await asyncio.gather(
            *[
                _run_batch(batch, labels, batch_index)
                for batch_index, (batch, labels) in enumerate(zip(batches, label_batches, strict=True))
            ]
        )
        flattened: list[list[float]] = []
        for batch_vectors in results:
            flattened.extend(batch_vectors)
        return flattened

    async def _flush_if_needed(self, parent_buffer: list[qm.PointStruct], child_buffer: list[qm.PointStruct]) -> None:
        if len(parent_buffer) >= self.point_batch_size:
            await self._flush_points(parent_buffer, self.settings.qdrant_sections_collection)
        if len(child_buffer) >= self.point_batch_size:
            await self._flush_points(child_buffer, self.settings.qdrant_collection)

    async def _flush_points(self, points: list[qm.PointStruct], collection_name: str) -> None:
        if not points:
            return
        await self.vector_store.upsert(points[:], collection_name=collection_name)
        points.clear()

    @staticmethod
    def _point_id(raw_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw_id))

    @staticmethod
    def _resolve_section_id(doc: DocumentRecord) -> str:
        return str(doc.section_id or doc.module_id or doc.doc_id)

    @staticmethod
    def _source_payload(doc: DocumentRecord) -> dict:
        source = doc.source if isinstance(doc.source, dict) else doc.source.model_dump()
        figure_ids = [f.id for f in doc.figures if f.id]
        return {
            "doc_id": doc.doc_id,
            "book_id": doc.book_id,
            "module_id": doc.module_id,
            "doc_type": doc.doc_type,
            "title": doc.title,
            "breadcrumb": doc.breadcrumb,
            "learning_objectives": doc.learning_objectives,
            "terms": doc.terms,
            "figure_ids": figure_ids,
            "has_figures": bool(figure_ids),
            "source_cnxml_path": source.get("cnxml_path"),
            "source_chunk": source.get("chunk"),
            "source_uuid": source.get("uuid"),
        }

    def _parent_payload(self, doc: DocumentRecord, section_id: str, content_text_full: str) -> dict:
        payload = self._source_payload(doc)
        payload.update(
            {
                "parent_doc_id": doc.doc_id,
                "section_id": section_id,
                "content_text_full": content_text_full,
            }
        )
        return payload

    def _child_payload(self, doc: DocumentRecord, section_id: str, chunk: Chunk) -> dict:
        payload = self._source_payload(doc)
        payload.update(
            {
                "parent_doc_id": doc.doc_id,
                "section_id": section_id,
                "chunk_id": chunk.chunk_id,
                "order_index": chunk.order_index,
                "chunk_type": chunk.chunk_type,
                "subsection_title": chunk.subsection_title,
                "content_text": chunk.content_text,
            }
        )
        return payload
