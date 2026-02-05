from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Iterable

from qdrant_client.http import models as qm

from app.core.config import Settings, get_settings
from app.ingest.chunker import split_markdown_into_chunks
from app.ingest.cleaner import clean_markdown
from app.ingest.loader import iter_documents
from app.ingest.models import DocumentRecord
from app.rag.embeddings import EmbeddingClient
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class IndexStats:
    docs_seen: int = 0
    chunks_indexed: int = 0


class IndexingService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.embedder = EmbeddingClient(self.settings)
        self.vector_store = VectorStore(self.settings)

    def index_jsonl(self, documents_path: str, recreate: bool = False) -> IndexStats:
        docs = iter_documents(documents_path)
        return self.index_documents(docs, recreate=recreate)

    def index_documents(self, documents: Iterable[DocumentRecord], recreate: bool = False) -> IndexStats:
        stats = IndexStats()
        first_batch = True

        for doc in documents:
            stats.docs_seen += 1
            cleaned = clean_markdown(doc.content_md)
            chunks = split_markdown_into_chunks(
                doc_id=doc.doc_id,
                text=cleaned,
                target_tokens=self.settings.chunk_target_tokens,
                overlap_tokens=self.settings.chunk_overlap_tokens,
                min_signal_chars=self.settings.min_text_chars_for_chunk,
            )
            if not chunks:
                continue

            embeddings = self.embedder.embed_texts([c.content_text for c in chunks])
            if first_batch:
                dim = len(embeddings[0])
                if recreate:
                    self.vector_store.recreate_collection(dim)
                else:
                    self.vector_store.ensure_collection(dim)
                first_batch = False

            points: list[qm.PointStruct] = []
            for chunk, vector in zip(chunks, embeddings, strict=True):
                payload = self._payload(doc, chunk.chunk_id, chunk.content_text)
                point_id = self._point_id(chunk.chunk_id)
                points.append(qm.PointStruct(id=point_id, vector=vector, payload=payload))
                stats.chunks_indexed += 1

            self.vector_store.upsert(points)
            logger.info("Indexed doc %s with %d chunks", doc.doc_id, len(points))

        return stats

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        # Qdrant point IDs must be uint or UUID; use stable UUID derived from chunk_id.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    @staticmethod
    def _payload(doc: DocumentRecord, chunk_id: str, content_text: str) -> dict:
        source = doc.source if isinstance(doc.source, dict) else doc.source.model_dump()
        figure_ids = [f.id for f in doc.figures if f.id]
        return {
            "chunk_id": chunk_id,
            "doc_id": doc.doc_id,
            "book_id": doc.book_id,
            "module_id": doc.module_id,
            "section_id": doc.section_id,
            "doc_type": doc.doc_type,
            "title": doc.title,
            "breadcrumb": doc.breadcrumb,
            "content_text": content_text,
            "learning_objectives": doc.learning_objectives,
            "terms": doc.terms,
            "figure_ids": figure_ids,
            "has_figures": bool(figure_ids),
            "source_cnxml_path": source.get("cnxml_path"),
            "source_chunk": source.get("chunk"),
            "source_uuid": source.get("uuid"),
        }
