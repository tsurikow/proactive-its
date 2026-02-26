from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
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
        self.chunks_collection = self.settings.qdrant_collection
        self.sections_collection = self.settings.qdrant_sections_collection

    def ensure_collection(self, vector_size: int, collection_name: str) -> None:
        exists = self.client.collection_exists(collection_name)
        if exists:
            return
        logger.info("Creating Qdrant collection '%s' with vector size %s", collection_name, vector_size)
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def ensure_collections(self, vector_size: int) -> None:
        self.ensure_collection(vector_size, self.chunks_collection)
        self.ensure_collection(vector_size, self.sections_collection)

    def recreate_collection(self, vector_size: int, collection_name: str) -> None:
        if self.client.collection_exists(collection_name):
            self.client.delete_collection(collection_name)
        self.ensure_collection(vector_size, collection_name)

    def recreate_collections(self, vector_size: int) -> None:
        self.recreate_collection(vector_size, self.chunks_collection)
        self.recreate_collection(vector_size, self.sections_collection)

    def upsert(self, points: list[qm.PointStruct], collection_name: str | None = None) -> None:
        if not points:
            return
        self.client.upsert(
            collection_name=collection_name or self.chunks_collection,
            points=points,
            wait=True,
        )

    def count(self, collection_name: str | None = None) -> int:
        res = self.client.count(collection_name=collection_name or self.chunks_collection, exact=True)
        return int(res.count)

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        query_filter: qm.Filter | None = None,
        collection_name: str | None = None,
    ) -> list[dict[str, Any]]:
        target = collection_name or self.chunks_collection
        try:
            if hasattr(self.client, "search"):
                records = self.client.search(
                    collection_name=target,
                    query_vector=query_vector,
                    query_filter=query_filter,
                    limit=top_k,
                    with_payload=True,
                    with_vectors=False,
                )
            else:
                response = self.client.query_points(
                    collection_name=target,
                    query=query_vector,
                    query_filter=query_filter,
                    limit=top_k,
                    with_payload=True,
                    with_vectors=False,
                )
                records = response.points
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                logger.warning("Qdrant collection '%s' not found.", target)
                return []
            raise
        out: list[dict[str, Any]] = []
        for record in records:
            payload = dict(record.payload or {})
            payload["score"] = float(record.score)
            out.append(payload)
        return out

    def fetch_section_parent(self, section_id: str) -> dict[str, Any] | None:
        query_filter = self.build_filter({"section_id": section_id})
        if not query_filter:
            return None
        try:
            points, _ = self.client.scroll(
                collection_name=self.sections_collection,
                limit=1,
                with_payload=True,
                with_vectors=False,
                scroll_filter=query_filter,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                logger.warning("Qdrant collection '%s' not found.", self.sections_collection)
                return None
            raise
        if not points:
            return None
        return dict(points[0].payload or {})

    def fetch_section_children(self, section_id: str, module_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        filters = {"section_id": section_id}
        if module_id:
            filters["module_id"] = module_id
        query_filter = self.build_filter(filters)
        if not query_filter:
            return []

        collected: list[dict[str, Any]] = []
        offset = None
        try:
            while True:
                points, offset = self.client.scroll(
                    collection_name=self.chunks_collection,
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
                if offset is None or len(collected) >= limit:
                    break
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                logger.warning("Qdrant collection '%s' not found.", self.chunks_collection)
                return []
            raise

        collected.sort(key=self._child_sort_key)
        return collected

    def scroll_payloads(
        self,
        collection_name: str,
        query_filter: qm.Filter | None = None,
        limit: int = 4000,
        page_size: int = 256,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        offset = None
        try:
            while True:
                points, offset = self.client.scroll(
                    collection_name=collection_name,
                    limit=page_size,
                    with_payload=True,
                    with_vectors=False,
                    scroll_filter=query_filter,
                    offset=offset,
                )
                for point in points:
                    payload = dict(point.payload or {})
                    if payload:
                        collected.append(payload)
                if offset is None or len(collected) >= limit:
                    break
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                logger.warning("Qdrant collection '%s' not found.", collection_name)
                return []
            raise
        return collected[:limit]

    @staticmethod
    def _child_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        order_index = item.get("order_index")
        if isinstance(order_index, int):
            return (order_index, str(item.get("chunk_id", "")))
        chunk_id = str(item.get("chunk_id", ""))
        if "::chunk" in chunk_id:
            suffix = chunk_id.split("::chunk")[-1]
            try:
                return (int(suffix), chunk_id)
            except ValueError:
                pass
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
