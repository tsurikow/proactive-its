from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if self.settings.qdrant_url == ":memory:":
            self.client = QdrantClient(location=":memory:")
        elif self.settings.qdrant_url.startswith("file://"):
            self.client = QdrantClient(path=self.settings.qdrant_url.removeprefix("file://"))
        else:
            self.client = QdrantClient(url=self.settings.qdrant_url)
        self.collection = self.settings.qdrant_collection

    def ensure_collection(self, vector_size: int) -> None:
        exists = self.client.collection_exists(self.collection)
        if exists:
            return
        logger.info("Creating Qdrant collection '%s' with vector size %s", self.collection, vector_size)
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def recreate_collection(self, vector_size: int) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.ensure_collection(vector_size)

    def upsert(self, points: list[qm.PointStruct]) -> None:
        if not points:
            return
        self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def count(self) -> int:
        res = self.client.count(collection_name=self.collection, exact=True)
        return int(res.count)

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        query_filter: qm.Filter | None = None,
    ) -> list[dict[str, Any]]:
        if hasattr(self.client, "search"):
            records = self.client.search(
                collection_name=self.collection,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )
        else:
            response = self.client.query_points(
                collection_name=self.collection,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )
            records = response.points
        out: list[dict[str, Any]] = []
        for r in records:
            payload = dict(r.payload or {})
            payload["score"] = float(r.score)
            out.append(payload)
        return out

    def fetch_first_chunk(
        self,
        section_id: str | None = None,
        module_id: str | None = None,
        doc_type: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any] | None:
        chunks = self.fetch_chunks(
            section_id=section_id,
            module_id=module_id,
            doc_type=doc_type,
            limit=limit,
        )
        return chunks[0] if chunks else None

    def fetch_chunks(
        self,
        section_id: str | None = None,
        module_id: str | None = None,
        doc_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query_filter = self.build_filter(
            {"section_id": section_id, "module_id": module_id, "doc_type": doc_type}
        )
        if not query_filter:
            return []

        collected: list[dict[str, Any]] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                limit=limit,
                with_payload=True,
                with_vectors=False,
                scroll_filter=query_filter,
                offset=offset,
            )
            for point in points:
                payload = dict(point.payload or {})
                if payload:
                    collected.append(payload)
            if offset is None:
                break
            if len(collected) >= limit:
                break

        if not collected:
            return []

        collected.sort(key=self._chunk_sort_key)
        return collected

    @staticmethod
    def _chunk_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        chunk_id = str(item.get("chunk_id", ""))
        if "::chunk" in chunk_id:
            suffix = chunk_id.split("::chunk")[-1]
            try:
                return (int(suffix), chunk_id)
            except ValueError:
                return (10**9, chunk_id)
        return (10**9, chunk_id)

    @staticmethod
    def build_filter(filters: dict[str, str | None]) -> qm.Filter | None:
        conditions: list[qm.FieldCondition] = []
        for key, value in filters.items():
            if value:
                conditions.append(qm.FieldCondition(key=key, match=qm.MatchValue(value=value)))
        if not conditions:
            return None
        return qm.Filter(must=conditions)
