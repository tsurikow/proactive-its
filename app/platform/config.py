from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    log_format: Literal["json", "plain"] = "json"

    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-5-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    embedding_api_key: str | None = None
    embedding_model: str = "bge-m3:latest"
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_batch_size: int = 8
    embedding_max_input_tokens: int = 8192
    embedding_num_ctx: int | None = 8192

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "calc1_chunks"
    qdrant_sections_collection: str = "calc1_sections"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/proactive_its"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    book_json_path: str = "data/book.json"
    media_dir: str = "data/media"

    rag_top_k_fetch: int = 24
    rag_final_k: int = 6
    rag_mmr_lambda_mult: float = 0.5
    rag_min_score: float = 0.2
    rag_min_evidence_chars: int = 180
    rag_offtopic_min_query_terms: int = 2
    rag_offtopic_score_ceiling: float = 0.55
    rag_generation_timeout_seconds: float = 45.0
    rag_query_rewrite_enabled: bool = True
    rag_query_rewrite_timeout_seconds: float = 6.0
    rag_query_rewrite_model: str | None = None
    assessment_timeout_seconds: float = 20.0
    assessment_model: str | None = "openai/gpt-4.1-mini"

    chunk_target_tokens: int = 450
    chunk_overlap_tokens: int = 60

    enable_retrieval_debug: bool = True

    min_text_chars_for_chunk: int = 60

    lesson_gen_enabled: bool = True
    lesson_gen_format_version: int = 6
    lesson_max_section_seconds: float = 90.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
