from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.infra.embeddings import AsyncEmbeddingClient
from app.infra.qdrant_store import AsyncVectorStore
from app.rag.generate import AnswerGenerator
from app.rag.retrieve import DenseRetriever
from app.rag.rewrite import QueryRewriteService
from app.rag.service import RAGService
from app.state.cache_repository import CacheRepository
from app.state.interaction_repository import InteractionRepository
from app.state.tutor_state_repository import TutorStateRepository
from app.tutor.lesson_generation import SectionLessonGenerator
from app.tutor.lesson_service import LessonService
from app.tutor.plan_projection import PlanProjectionService
from app.tutor.session import TutorSessionService
from app.tutor.stage_source import StageSourceService
from app.tutor.start_message import TutorMessageService


@lru_cache(maxsize=1)
def get_interaction_repository() -> InteractionRepository:
    return InteractionRepository()


@lru_cache(maxsize=1)
def get_tutor_state_repository() -> TutorStateRepository:
    return TutorStateRepository()


@lru_cache(maxsize=1)
def get_cache_repository() -> CacheRepository:
    return CacheRepository()


@lru_cache(maxsize=1)
def get_query_rewrite_service() -> QueryRewriteService:
    return QueryRewriteService(get_settings(), llm_client=get_async_openrouter_client())


@lru_cache(maxsize=1)
def get_rag_service() -> RAGService:
    settings = get_settings()
    return RAGService(
        settings,
        retriever=DenseRetriever(
            settings,
            embedder=get_runtime_embedding_client(),
            store=get_async_vector_store(),
        ),
        generator=AnswerGenerator(settings),
        rewriter=get_query_rewrite_service(),
    )


@lru_cache(maxsize=1)
def get_async_vector_store() -> AsyncVectorStore:
    return AsyncVectorStore(get_settings())


@lru_cache(maxsize=1)
def get_runtime_embedding_client() -> AsyncEmbeddingClient:
    return AsyncEmbeddingClient(get_settings())


@lru_cache(maxsize=1)
def get_async_openrouter_client() -> AsyncOpenAI | None:
    settings = get_settings()
    if not settings.openrouter_api_key:
        return None
    return AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )


@lru_cache(maxsize=1)
def get_plan_projection_service() -> PlanProjectionService:
    return PlanProjectionService(get_tutor_state_repository())


@lru_cache(maxsize=1)
def get_tutor_message_service() -> TutorMessageService:
    return TutorMessageService(get_cache_repository(), get_settings(), get_async_openrouter_client())


@lru_cache(maxsize=1)
def get_tutor_session_service() -> TutorSessionService:
    settings = get_settings()
    return TutorSessionService(
        get_tutor_state_repository(),
        get_plan_projection_service(),
        get_tutor_message_service(),
        settings.book_json_path,
        settings,
    )


@lru_cache(maxsize=1)
def get_stage_source_service() -> StageSourceService:
    return StageSourceService(get_async_vector_store())


@lru_cache(maxsize=1)
def get_lesson_service() -> LessonService:
    return LessonService(
        get_cache_repository(),
        get_stage_source_service(),
        SectionLessonGenerator(get_settings(), get_async_openrouter_client()),
    )
