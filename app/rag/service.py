from __future__ import annotations

from typing import Any

from app.core.config import Settings, get_settings
from app.rag.citations import build_citation_payload, validate_citations
from app.rag.generator import TutorGenerator
from app.rag.retriever import Retriever


class RAGService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.retriever = Retriever(self.settings)
        self.generator = TutorGenerator(self.settings)

    def answer(
        self,
        message: str,
        mode: str,
        filters: dict[str, str | None],
    ) -> dict[str, Any]:
        chunks, debug = self.retriever.retrieve(query=message, filters=filters)
        answer_md, citation_ids = self.generator.generate(message, chunks, mode=mode)

        if citation_ids:
            try:
                validate_citations(citation_ids, chunks)
            except ValueError:
                citation_ids = []

        if not citation_ids and chunks:
            citation_ids = [chunks[0]["chunk_id"]]

        citations = build_citation_payload(citation_ids, chunks)
        return {
            "answer_md": answer_md,
            "citations": citations,
            "debug": debug,
            "chunks": chunks,
        }
