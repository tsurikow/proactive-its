from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.dependencies import get_rag_service, get_repo, get_tutor_flow
from app.schemas.api import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    FeedbackResponse,
    LessonCurrentResponse,
    NextRequest,
    NextResponse,
    StartRequest,
    StartResponse,
)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/start", response_model=StartResponse)
async def start(request: StartRequest) -> StartResponse:
    tutor = get_tutor_flow()
    try:
        payload = await tutor.start(request.learner_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start session: {exc}") from exc
    return StartResponse(**payload)


@router.get("/lesson/current", response_model=LessonCurrentResponse)
async def lesson_current(learner_id: str = Query(..., min_length=1)) -> LessonCurrentResponse:
    tutor = get_tutor_flow()
    try:
        payload = await tutor.get_current_lesson(learner_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get current lesson: {exc}") from exc
    return LessonCurrentResponse(**payload)


@router.post("/next", response_model=NextResponse)
async def next_item(request: NextRequest) -> NextResponse:
    tutor = get_tutor_flow()
    try:
        payload = await tutor.advance(request.learner_id, force=request.force)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to advance session: {exc}") from exc
    return NextResponse(**payload)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    repo = get_repo()
    rag = get_rag_service()
    settings = get_settings()

    await repo.ensure_learner(request.learner_id)
    session_id = await repo.get_or_create_session(request.learner_id)

    module_id = request.context.current_module_id
    section_id = request.context.current_section_id

    filters = {
        "module_id": None,
        "section_id": None,
        "doc_type": "section",
    }

    try:
        rag_result = await run_in_threadpool(
            rag.answer,
            message=request.message,
            mode=request.mode,
            filters=filters,
            context={
                "module_id": module_id,
                "section_id": section_id,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    interaction_id = await repo.add_interaction(
        learner_id=request.learner_id,
        session_id=session_id,
        message=request.message,
        answer=rag_result["answer_md"],
        module_id=module_id,
        section_id=section_id,
    )

    await repo.add_interaction_sources(
        interaction_id,
        [
            {
                "chunk_id": c["chunk_id"],
                "score": c.get("score"),
                "rank": idx,
            }
            for idx, c in enumerate(rag_result["chunks"])
        ],
    )

    retrieval_debug = rag_result["debug"] if settings.enable_retrieval_debug else None

    return ChatResponse(
        interaction_id=interaction_id,
        answer_md=rag_result["answer_md"],
        citations=rag_result["citations"],
        retrieval_debug=retrieval_debug,
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(request: FeedbackRequest) -> FeedbackResponse:
    repo = get_repo()
    tutor = get_tutor_flow()

    await repo.ensure_learner(request.learner_id)

    interaction = await repo.get_interaction(request.interaction_id)
    if not interaction or interaction["learner_id"] != request.learner_id:
        raise HTTPException(status_code=404, detail="interaction not found")

    await repo.update_interaction_confidence(request.interaction_id, request.confidence)

    section_id = interaction.get("section_id")
    module_id = interaction.get("module_id")

    try:
        payload = await tutor.apply_feedback(
            request.learner_id,
            section_id,
            module_id,
            request.confidence,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return FeedbackResponse(
        updated=True,
        auto_advanced=payload["auto_advanced"],
        message=payload.get("message"),
        current_stage=payload.get("current_stage"),
    )
