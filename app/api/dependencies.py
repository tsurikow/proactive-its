from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from app.chat.assessment import QuizAssessmentService
from app.chat.answer_generator import AnswerGenerator
from app.chat.query_rewrite import QueryRewriteService
from app.chat.repository import InteractionRepository
from app.chat.retriever import DenseRetriever
from app.chat.service import ChatService, RAGService
from app.learner.repository import LearnerRepository
from app.learner.service import LearnerService
from app.platform.config import get_settings
from app.platform.embeddings import AsyncEmbeddingClient
from app.platform.vector_store import AsyncVectorStore
from app.tutor.lesson_generation import SectionLessonGenerator
from app.tutor.repository import TutorRepository
from app.tutor.service import TutorService


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
def get_async_vector_store() -> AsyncVectorStore:
    return AsyncVectorStore(get_settings())


@lru_cache(maxsize=1)
def get_runtime_embedding_client() -> AsyncEmbeddingClient:
    return AsyncEmbeddingClient(get_settings())


@lru_cache(maxsize=1)
def get_chat_repository() -> InteractionRepository:
    return InteractionRepository()


@lru_cache(maxsize=1)
def get_tutor_repository() -> TutorRepository:
    return TutorRepository()


@lru_cache(maxsize=1)
def get_learner_repository() -> LearnerRepository:
    return LearnerRepository()


@lru_cache(maxsize=1)
def get_learner_service() -> LearnerService:
    return LearnerService(get_learner_repository())


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    settings = get_settings()
    return ChatService(
        chat_repository=get_chat_repository(),
        tutor_repository=get_tutor_repository(),
        rag_service=RAGService(
            settings,
            retriever=DenseRetriever(
                settings,
                embedder=get_runtime_embedding_client(),
                store=get_async_vector_store(),
            ),
            generator=AnswerGenerator(settings, llm_client=get_async_openrouter_client()),
            rewriter=QueryRewriteService(settings, llm_client=get_async_openrouter_client()),
        ),
        assessment_service=QuizAssessmentService(settings, llm_client=get_async_openrouter_client()),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_tutor_service() -> TutorService:
    settings = get_settings()
    return TutorService(
        repository=get_tutor_repository(),
        learner_service=get_learner_service(),
        vector_store=get_async_vector_store(),
        lesson_generator=SectionLessonGenerator(settings, get_async_openrouter_client()),
        llm_client=get_async_openrouter_client(),
        book_json_path=settings.book_json_path,
        settings=settings,
    )
