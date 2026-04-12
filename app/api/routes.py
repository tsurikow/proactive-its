from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.requests import Request as FastAPIRequest
from fastapi.responses import StreamingResponse

from app.api.auth_routes import router as auth_router
from app.api.dependencies import (
    get_chat_service,
    get_content_bootstrap_service,
    get_durable_chat_repository,
    get_redis_cache,
    get_teacher_runtime,
    require_authenticated_learner,
    get_teacher_state_service,
)
from app.teacher.models import (
    SessionHistoryResponse,
    SessionHistoryTurn,
    TeacherSessionRequest,
    TeacherSessionResult,
)

router = APIRouter()
router.include_router(auth_router)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(
    response: Response,
    teacher_state_service=Depends(get_teacher_state_service),
    content_bootstrap_service=Depends(get_content_bootstrap_service),
) -> dict[str, object]:
    try:
        template_status = await teacher_state_service.template_ready_status()
        database_ready = True
    except Exception:
        template_status = {
            "template_ready": False,
            "template_id": None,
            "template_version": None,
        }
        database_ready = False
    try:
        content_status = await content_bootstrap_service.current_status()
        content_ready = content_status.content_ready
        sections_count = content_status.sections_count
        chunks_count = content_status.chunks_count
    except Exception:
        content_ready = False
        sections_count = 0
        chunks_count = 0
    ready = bool(database_ready and template_status["template_ready"] and content_ready)
    response.status_code = 200 if ready else 503
    return {
        "status": "ready" if ready else "not_ready",
        "database_ready": database_ready,
        "template_ready": bool(template_status["template_ready"]),
        "template_id": template_status["template_id"],
        "template_version": template_status["template_version"],
        "content_ready": content_ready,
        "sections_count": sections_count,
        "chunks_count": chunks_count,
    }


@router.get("/teacher/session/history", response_model=SessionHistoryResponse)
async def teacher_session_history(
    learner=Depends(require_authenticated_learner),
    repo=Depends(get_durable_chat_repository),
    limit: int = Query(default=50, ge=1, le=200),
) -> SessionHistoryResponse:
    turns_data, has_more = await repo.list_completed_turns(learner.id, limit=limit)
    pending = await repo.get_pending_turn(learner.id)

    turns = []
    for t in turns_data:
        payload = t.get("request_payload_json") or {}
        try:
            result = TeacherSessionResult.model_validate(t["final_result_json"])
        except Exception:
            continue
        turns.append(SessionHistoryTurn(
            turn_id=t["id"],
            event_type=payload.get("event_type", ""),
            learner_message=payload.get("message"),
            result=result,
            created_at=t["created_at"],
        ))

    return SessionHistoryResponse(
        turns=turns,
        has_more=has_more,
        pending_turn_id=pending["id"] if pending else None,
    )


def _raise_runtime_error(exc: RuntimeError) -> None:
    detail = str(exc)
    status_map = {"message_required": 422, "llm_unavailable": 503, "content_not_ready": 503}
    status = status_map.get(detail, 503)
    msg = "message is required for this session event" if detail == "message_required" else detail
    raise HTTPException(status_code=status, detail=msg) from exc


def _prepare_request(request: TeacherSessionRequest, learner_id: str) -> TeacherSessionRequest:
    if request.learner_id and request.learner_id != learner_id:
        raise HTTPException(status_code=403, detail="learner_mismatch")
    return request.model_copy(update={"learner_id": learner_id})


@router.post("/teacher/session", response_model=TeacherSessionResult)
async def teacher_session(
    request: TeacherSessionRequest,
    raw_request: FastAPIRequest,
    learner=Depends(require_authenticated_learner),
    chat_service=Depends(get_chat_service),
    teacher_runtime=Depends(get_teacher_runtime),
) -> TeacherSessionResult:
    try:
        prepared = _prepare_request(request, learner.id)
        return await teacher_runtime.execute_session(
            prepared,
            chat_service=chat_service,
            request_id=getattr(raw_request.state, "request_id", None),
            client_request_id=raw_request.headers.get("x-request-id"),
        )
    except RuntimeError as exc:
        _raise_runtime_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute teacher session: {exc}") from exc


@router.post("/teacher/session/stream")
async def teacher_session_stream(
    request: TeacherSessionRequest,
    raw_request: FastAPIRequest,
    learner=Depends(require_authenticated_learner),
    chat_service=Depends(get_chat_service),
    teacher_runtime=Depends(get_teacher_runtime),
    repo=Depends(get_durable_chat_repository),
    redis_cache=Depends(get_redis_cache),
) -> StreamingResponse:
    from app.api.sse import sse_event, stream_session_completion

    try:
        prepared = _prepare_request(request, learner.id)
        teacher_runtime.validate_request(prepared)
    except RuntimeError as exc:
        _raise_runtime_error(exc)

    # Pre-subscribe to Redis BEFORE dispatch to avoid race condition
    # (worker may finish before generator starts listening)
    pre_pubsub = None
    pre_channel = None

    try:
        turn_id, immediate_result = await teacher_runtime.dispatch_or_inline(
            prepared,
            chat_service=chat_service,
            client_request_id=raw_request.headers.get("x-request-id"),
        )
    except RuntimeError as exc:
        _raise_runtime_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if immediate_result is None and turn_id and redis_cache is not None and redis_cache.available:
        pre_channel = f"chat_turn:{turn_id}"
        pre_pubsub = await redis_cache.subscribe(pre_channel)

    async def generate():
        try:
            if immediate_result is not None:
                yield sse_event("result", immediate_result.model_dump(mode="json"))
                return
            async for chunk in stream_session_completion(
                turn_id, repo, redis_cache,
                wait_seconds=teacher_runtime.settings.chat_turn_inline_wait_seconds,
                poll_interval=teacher_runtime.settings.chat_turn_poll_interval_seconds,
                pre_pubsub=pre_pubsub,
                pre_channel=pre_channel,
            ):
                yield chunk
        finally:
            if pre_pubsub is not None and pre_channel is not None:
                try:
                    await pre_pubsub.unsubscribe(pre_channel)
                    await pre_pubsub.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


