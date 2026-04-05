from contextlib import asynccontextmanager
import json
import logging
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import router
from app.api.dependencies import (
    get_chat_service,
    get_async_vector_store,
    get_redis_cache,
    get_runtime_embedding_client,
    get_teacher_state_service,
)
from app.platform.config import get_settings
from app.platform.db import close_db, init_db
from app.platform.logging import bind_request_context, configure_logging, log_event, reset_request_context
from app.platform.observability import configure_observability, instrument_fastapi_app

configure_logging()
settings = get_settings()
settings.validate_runtime_settings()
configure_observability(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await get_redis_cache().connect()
    log_event(
        logger,
        "app.startup",
        app_env=settings.app_env,
        frontend_public_url=settings.frontend_public_url,
        auth_reset_available=settings.auth_reset_available,
        logfire_enabled=settings.logfire_enabled,
    )
    try:
        yield
    finally:
        if get_teacher_state_service.cache_info().currsize:
            await get_teacher_state_service().close()
        if get_chat_service.cache_info().currsize:
            _ = get_chat_service()
        if get_async_vector_store.cache_info().currsize:
            await get_async_vector_store().close()
        if get_runtime_embedding_client.cache_info().currsize:
            await get_runtime_embedding_client().close()
        if get_redis_cache.cache_info().currsize:
            await get_redis_cache().close()
        await close_db()


app = FastAPI(title="Proactive ITS", version="0.1.0", lifespan=lifespan)
instrument_fastapi_app(app, settings)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allow_origins),
    allow_credentials=True,
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
    request.state.request_id = request_id
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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    log_event(logger, "http.exception", path=request.url.path, status_code=exc.status_code, detail=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    log_event(logger, "http.validation_error", path=request.url.path, errors=exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log_event(logger, "http.unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})
