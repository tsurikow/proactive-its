from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4.1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-large"
    embedding_base_url: str = "https://api.openai.com/v1"
    fake_embedding_dim: int = 384
    embedding_batch_size: int = 32
    embedding_max_input_tokens: int = 7000

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "calc1_chunks"

    sqlite_path: str = "data/app.db"
    book_json_path: str = "data/book.json"

    rag_top_k: int = 12
    rag_final_k: int = 6

    chunk_target_tokens: int = 900
    chunk_overlap_tokens: int = 150

    enable_retrieval_debug: bool = True

    min_text_chars_for_chunk: int = 80

    @property
    def sqlite_abs_path(self) -> Path:
        return Path(self.sqlite_path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
