from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import get_settings
from app.core.dependencies import (
    get_async_openrouter_client,
    get_async_vector_store,
    get_lesson_service,
    get_runtime_embedding_client,
)
from app.core.logging import configure_logging
from app.state.db import close_db, init_db

configure_logging()
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    try:
        yield
    finally:
        if get_lesson_service.cache_info().currsize:
            await get_lesson_service().close()
        if get_async_vector_store.cache_info().currsize:
            await get_async_vector_store().close()
        if get_runtime_embedding_client.cache_info().currsize:
            await get_runtime_embedding_client().close()
        if get_async_openrouter_client.cache_info().currsize:
            client = get_async_openrouter_client()
            if client is not None:
                await client.close()
        await close_db()


app = FastAPI(title="Proactive ITS", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/v1")
app.mount("/media", StaticFiles(directory=settings.media_dir, check_dir=False), name="media")
