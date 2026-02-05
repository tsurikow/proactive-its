from __future__ import annotations

from typing import Any


def validate_citations(citation_ids: list[str], retrieved_chunks: list[dict[str, Any]]) -> list[str]:
    allowed = {c["chunk_id"] for c in retrieved_chunks}
    invalid = [cid for cid in citation_ids if cid not in allowed]
    if invalid:
        raise ValueError(f"Invalid citations not found in retrieval set: {invalid}")
    return citation_ids


def build_citation_payload(citation_ids: list[str], retrieved_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {c["chunk_id"]: c for c in retrieved_chunks}
    payload: list[dict[str, Any]] = []
    for cid in citation_ids:
        chunk = by_id.get(cid)
        if not chunk:
            continue
        payload.append(
            {
                "chunk_id": cid,
                "doc_id": chunk.get("doc_id", ""),
                "title": chunk.get("title", ""),
                "breadcrumb": list(chunk.get("breadcrumb") or []),
                "quote": str(chunk.get("content_text", ""))[:280],
            }
        )
    return payload
