from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_interaction_repository,
    get_lesson_service,
    get_plan_projection_service,
    get_rag_service,
    get_tutor_session_service,
    get_tutor_state_repository,
)
from app.rag.service import RAGService
from app.schemas.api import (
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


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/start", response_model=StartResponse)
async def start(
    request: StartRequest,
    session_service=Depends(get_tutor_session_service),
) -> StartResponse:
    try:
        payload = await session_service.start_payload(request.learner_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start session: {exc}") from exc
    return StartResponse(**payload)


@router.get("/start-message", response_model=StartMessageResponse)
async def start_message(
    learner_id: str = Query(..., min_length=1),
    session_service=Depends(get_tutor_session_service),
) -> StartMessageResponse:
    try:
        payload = await session_service.start_message_payload(learner_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get start message: {exc}") from exc
    return StartMessageResponse(**payload)


@router.get("/lesson/current", response_model=LessonCurrentResponse)
async def lesson_current(
    learner_id: str = Query(..., min_length=1),
    session_service=Depends(get_tutor_session_service),
    plan_projection=Depends(get_plan_projection_service),
    lesson_service=Depends(get_lesson_service),
) -> LessonCurrentResponse:
    try:
        template, state, targets, current_stage = await session_service.ensure_context(learner_id)
        plan = await plan_projection.build_plan_payload(
            template=template,
            state=state,
            current_stage=current_stage,
        )
        if not current_stage:
            payload = {
                "current_stage": None,
                "lesson": None,
                "plan": plan,
                "plan_completed": True,
            }
        else:
            lesson, stage_with_parent = await lesson_service.get_or_generate(
                template_id=str(template["id"]),
                stage=current_stage,
            )
            next_stage = session_service.next_stage(targets, current_stage)
            lesson_service.schedule_prewarm(template_id=str(template["id"]), stage=next_stage)
            payload = {
                "current_stage": stage_with_parent,
                "lesson": lesson,
                "plan": plan,
                "plan_completed": bool(state["plan_completed"]),
            }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get current lesson: {exc}") from exc
    return LessonCurrentResponse(**payload)


@router.post("/next", response_model=NextResponse)
async def next_item(
    request: NextRequest,
    session_service=Depends(get_tutor_session_service),
    lesson_service=Depends(get_lesson_service),
) -> NextResponse:
    try:
        payload, next_stage, template_id = await session_service.advance_payload(
            request.learner_id,
            force=request.force,
        )
        lesson_service.schedule_prewarm(template_id=template_id, stage=next_stage)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to advance session: {exc}") from exc
    return NextResponse(**payload)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    interaction_repo=Depends(get_interaction_repository),
    rag: RAGService = Depends(get_rag_service),
    settings: Settings = Depends(get_settings),
    tutor_state_repo=Depends(get_tutor_state_repository),
) -> ChatResponse:

    await tutor_state_repo.ensure_learner(request.learner_id)
    session_id = await interaction_repo.get_or_create_session(request.learner_id)

    module_id = request.context.current_module_id
    section_id = request.context.current_section_id

    filters = {
        "module_id": None,
        "section_id": None,
        "doc_type": "section",
    }

    try:
        rag_result = await rag.answer(
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

    interaction_id = await interaction_repo.create_interaction_with_sources(
        learner_id=request.learner_id,
        session_id=session_id,
        message=request.message,
        answer=rag_result["answer_md"],
        module_id=module_id,
        section_id=section_id,
        sources=[
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
async def post_feedback(
    request: FeedbackRequest,
    interaction_repo=Depends(get_interaction_repository),
    session_service=Depends(get_tutor_session_service),
    tutor_state_repo=Depends(get_tutor_state_repository),
) -> FeedbackResponse:

    await tutor_state_repo.ensure_learner(request.learner_id)

    interaction = await interaction_repo.get_interaction(request.interaction_id)
    if not interaction or interaction["learner_id"] != request.learner_id:
        raise HTTPException(status_code=404, detail="interaction not found")

    await interaction_repo.update_interaction_confidence(request.interaction_id, request.confidence)

    section_id = interaction.get("section_id")
    module_id = interaction.get("module_id")

    try:
        payload = await session_service.apply_feedback(
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
