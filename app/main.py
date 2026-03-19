from contextlib import asynccontextmanager
import logging
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.dependencies import (
    get_async_openrouter_client,
    get_chat_service,
    get_async_vector_store,
    get_runtime_embedding_client,
    get_tutor_service,
)
from app.platform.config import get_settings
from app.platform.db import close_db, init_db
from app.platform.logging import bind_request_context, configure_logging, log_event, reset_request_context

configure_logging()
settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    try:
        yield
    finally:
        if get_tutor_service.cache_info().currsize:
            await get_tutor_service().close()
        if get_chat_service.cache_info().currsize:
            _ = get_chat_service()
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


async def _extract_learner_id(request: Request) -> str | None:
    learner_id = request.query_params.get("learner_id")
    if learner_id:
        return learner_id
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if request.method not in {"POST", "PUT", "PATCH"} or content_type != "application/json":
        return None
    body = await request.body()
    if not body:
        return None
    try:
        import json

        payload = json.loads(body)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    learner_id = payload.get("learner_id")
    return learner_id if isinstance(learner_id, str) and learner_id else None


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    learner_id = await _extract_learner_id(request)
    token = bind_request_context(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        learner_id=learner_id,
    )
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        log_event(
            logger,
            "http.request_failed",
            status_code=500,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            error=str(exc),
        )
        raise
    else:
        log_event(
            logger,
            "http.request_completed",
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
        return response
    finally:
        reset_request_context(token)

app.include_router(router, prefix="/v1")
app.mount("/media", StaticFiles(directory=settings.media_dir, check_dir=False), name="media")
