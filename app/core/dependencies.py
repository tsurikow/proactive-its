from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.rag.service import RAGService
from app.rag.vector_store import VectorStore
from app.state.repository import StateRepository
from app.tutor.flow import TutorFlow


@lru_cache(maxsize=1)
def get_repo() -> StateRepository:
    return StateRepository()


@lru_cache(maxsize=1)
def get_rag_service() -> RAGService:
    settings = get_settings()
    return RAGService(settings)


@lru_cache(maxsize=1)
def get_tutor_flow() -> TutorFlow:
    settings = get_settings()
    store = VectorStore(settings)
    return TutorFlow(get_repo(), settings.book_json_path, store, settings)
