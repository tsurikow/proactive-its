from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.requests import Request as FastAPIRequest

from app.api.auth_routes import router as auth_router
from app.api.dependencies import (
    get_chat_service,
    get_content_bootstrap_service,
    get_teacher_runtime,
    require_authenticated_learner,
    get_teacher_state_service,
)
from app.teacher.models import (
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


@router.post("/teacher/session", response_model=TeacherSessionResult)
async def teacher_session(
    request: TeacherSessionRequest,
    raw_request: FastAPIRequest,
    learner=Depends(require_authenticated_learner),
    chat_service=Depends(get_chat_service),
    teacher_runtime=Depends(get_teacher_runtime),
) -> TeacherSessionResult:
    try:
        if request.learner_id and request.learner_id != learner.id:
            raise HTTPException(status_code=403, detail="learner_mismatch")
        return await teacher_runtime.execute_session(
            request.model_copy(update={"learner_id": learner.id}),
            chat_service=chat_service,
            request_id=getattr(raw_request.state, "request_id", None),
            client_request_id=raw_request.headers.get("x-request-id"),
        )
    except RuntimeError as exc:
        detail = str(exc)
        if detail == "message_required":
            raise HTTPException(status_code=422, detail="message is required for this session event") from exc
        if detail == "llm_unavailable":
            raise HTTPException(status_code=503, detail="llm_unavailable") from exc
        if detail == "content_not_ready":
            raise HTTPException(status_code=503, detail="content_not_ready") from exc
        raise HTTPException(status_code=503, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute teacher session: {exc}") from exc


