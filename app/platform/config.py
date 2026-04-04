from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "staging", "production"] = "dev"
    app_debug: bool = False
    service_name: str = "proactive-its-api"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    frontend_public_url: str = "http://localhost"
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("localhost", "127.0.0.1")
    log_level: str = "INFO"
    log_format: Literal["json", "plain"] = "json"
    cors_allow_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://localhost",
        "http://127.0.0.1",
    )
    auth_secret_key: str = "change-me-auth-secret-key"
    auth_cookie_name: str = "proactive_its_session"
    auth_cookie_secure: bool = False
    auth_session_ttl_hours: int = 336
    auth_reset_token_ttl_minutes: int = 60
    auth_dev_log_reset_links: bool = True
    auth_reset_enabled: bool = True
    auth_login_attempt_window_seconds: int = 300
    auth_login_attempt_limit: int = 10
    auth_signup_attempt_window_seconds: int = 900
    auth_signup_attempt_limit: int = 5
    auth_reset_attempt_window_seconds: int = 900
    auth_reset_attempt_limit: int = 5

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: int = 20

    logfire_enabled: bool = False
    logfire_token: str | None = None
    logfire_service_name: str = "proactive-its-api"
    logfire_environment: str = "dev"
    logfire_capture_content: bool = False

    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_structured_strict: bool = True
    openrouter_structured_require_parameters: bool = True
    teacher_reasoning_model: str = "google/gemini-2.5-flash"
    teacher_section_understanding_model: str | None = None
    teacher_answer_check_model: str | None = None
    teacher_feedback_model: str | None = None
    rag_answer_model: str | None = None

    embedding_api_key: str | None = None
    embedding_model: str = "bge-m3:latest"
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_batch_size: int = 8
    embedding_index_concurrency: int = 4
    embedding_max_input_tokens: int = 8192
    embedding_num_ctx: int | None = 8192

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "calc1_chunks"
    qdrant_sections_collection: str = "calc1_sections"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/proactive_its"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    book_json_path: str = "data/book.json"
    documents_json_path: str = "data/documents.jsonl"
    media_dir: str = "data/media"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672//"
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl_seconds: int = 86400
    embedding_cache_enabled: bool = True
    chat_worker_queue: str = "chat_generation"
    chat_turn_inline_wait_seconds: float = 180.0
    chat_turn_poll_interval_seconds: float = 2.0
    chat_turn_retry_window_seconds: float = 180.0
    durable_chat_enabled: bool = True

    rag_top_k_fetch: int = 24
    rag_final_k: int = 6
    rag_mmr_lambda_mult: float = 0.5
    rag_min_score: float = 0.2
    rag_min_evidence_chars: int = 180
    rag_offtopic_min_query_terms: int = 2
    rag_offtopic_score_ceiling: float = 0.55
    rag_generation_timeout_seconds: float = 45.0
    rag_context_max_chars_per_chunk: int = 1100
    rag_prompt_excerpt_max_chars: int = 850
    rag_query_rewrite_enabled: bool = True
    rag_query_rewrite_timeout_seconds: float = 6.0
    rag_query_rewrite_model: str | None = None

    chunk_target_tokens: int = 450
    chunk_overlap_tokens: int = 60

    enable_retrieval_debug: bool = True
    mastery_decay_enabled: bool = True
    mastery_decay_half_life_days: int = 14
    mastery_decay_grace_period_hours: int = 24

    min_text_chars_for_chunk: int = 60

    lesson_gen_enabled: bool = True
    lesson_gen_format_version: int = 6
    lesson_max_section_seconds: float = 90.0

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_cors_allow_origins(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(origin.strip() for origin in value.split(",") if origin.strip())
        return value

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _parse_allowed_hosts(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(host.strip() for host in value.split(",") if host.strip())
        return value

    @field_validator("embedding_num_ctx", mode="before")
    @classmethod
    def _parse_embedding_num_ctx(cls, value: object) -> object:
        if value in (None, ""):
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_email)

    @property
    def auth_reset_available(self) -> bool:
        if not self.auth_reset_enabled:
            return False
        if self.smtp_configured:
            return True
        return not self.is_production and self.auth_dev_log_reset_links

    def validate_runtime_settings(self) -> None:
        if not self.is_production:
            return
        errors: list[str] = []
        if self.auth_secret_key == "change-me-auth-secret-key":
            errors.append("AUTH_SECRET_KEY must be set in production")
        if not self.auth_cookie_secure:
            errors.append("AUTH_COOKIE_SECURE must be true in production")
        if self.frontend_public_url.startswith("http://localhost") or self.frontend_public_url.startswith(
            "http://127.0.0.1"
        ):
            errors.append("FRONTEND_PUBLIC_URL must not point to localhost in production")
        if any(
            origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1")
            for origin in self.cors_allow_origins
        ):
            errors.append("CORS_ALLOW_ORIGINS must not contain localhost origins in production")
        if self.auth_dev_log_reset_links:
            errors.append("AUTH_DEV_LOG_RESET_LINKS must be false in production")
        if self.auth_reset_enabled and not self.smtp_configured:
            errors.append("AUTH_RESET_ENABLED requires SMTP in production")
        if "*" in self.allowed_hosts:
            errors.append("ALLOWED_HOSTS must be explicit in production")
        if errors:
            raise ValueError("; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
