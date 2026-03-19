from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_chat_service, get_tutor_service
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    FeedbackResponse,
    LessonCurrentResponse,
    NextRequest,
    NextResponse,
    StartMessageResponse,
    StartRequest,
    StartResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/start", response_model=StartResponse)
async def start(
    request: StartRequest,
    tutor_service=Depends(get_tutor_service),
) -> StartResponse:
    try:
        payload = await tutor_service.start_payload(request.learner_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start session: {exc}") from exc
    return StartResponse(**payload)


@router.get("/start-message", response_model=StartMessageResponse)
async def start_message(
    learner_id: str = Query(..., min_length=1),
    tutor_service=Depends(get_tutor_service),
) -> StartMessageResponse:
    try:
        payload = await tutor_service.start_message_payload(learner_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get start message: {exc}") from exc
    return StartMessageResponse(**payload)


@router.get("/lesson/current", response_model=LessonCurrentResponse)
async def lesson_current(
    learner_id: str = Query(..., min_length=1),
    tutor_service=Depends(get_tutor_service),
) -> LessonCurrentResponse:
    try:
        payload = await tutor_service.current_lesson_payload(learner_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get current lesson: {exc}") from exc
    return LessonCurrentResponse(**payload)


@router.post("/next", response_model=NextResponse)
async def next_item(
    request: NextRequest,
    tutor_service=Depends(get_tutor_service),
) -> NextResponse:
    try:
        payload = await tutor_service.next_payload(request.learner_id, force=request.force)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to advance session: {exc}") from exc
    return NextResponse(**payload)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    chat_service=Depends(get_chat_service),
) -> ChatResponse:
    try:
        payload = await chat_service.chat(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(**payload)


@router.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(
    request: FeedbackRequest,
    chat_service=Depends(get_chat_service),
    tutor_service=Depends(get_tutor_service),
) -> FeedbackResponse:
    await tutor_service.ensure_learner(request.learner_id)
    feedback_context = await chat_service.get_feedback_context(request.learner_id, request.interaction_id)
    if feedback_context is None:
        raise HTTPException(status_code=404, detail="interaction not found")
    interaction = feedback_context["interaction"]
    assessment = feedback_context["assessment"]

    await chat_service.record_feedback_confidence(request.interaction_id, request.confidence)

    section_id = interaction.get("section_id")
    module_id = interaction.get("module_id")

    try:
        payload = await tutor_service.apply_feedback(
            request.learner_id,
            request.interaction_id,
            section_id,
            module_id,
            request.confidence,
            assessment,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return FeedbackResponse(
        updated=True,
        auto_advanced=payload["auto_advanced"],
        message=payload.get("message"),
        current_stage=payload.get("current_stage"),
    )
