from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.core.dependencies import get_rag_service, get_repo, get_tutor_flow
from app.schemas.api import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    FeedbackResponse,
    NextRequest,
    NextResponse,
    StartRequest,
    StartResponse,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/start", response_model=StartResponse)
def start(request: StartRequest) -> StartResponse:
    tutor = get_tutor_flow()
    payload = tutor.start(request.learner_id)
    return StartResponse(**payload)


@router.post("/next", response_model=NextResponse)
def next_item(request: NextRequest) -> NextResponse:
    tutor = get_tutor_flow()
    payload = tutor.advance(request.learner_id, force=request.force)
    return NextResponse(**payload)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    repo = get_repo()
    rag = get_rag_service()
    tutor = get_tutor_flow()
    settings = get_settings()

    repo.ensure_learner(request.learner_id)
    session_id = repo.get_or_create_session(request.learner_id)

    module_id = request.context.current_module_id
    section_id = request.context.current_section_id

    if not module_id and not section_id:
        current = tutor.current_item(request.learner_id, include_tutor_content=False)
        if current:
            module_id = current.get("module_id")
            section_id = current.get("section_id")

    filters = {
        "module_id": module_id,
        "section_id": section_id,
        "doc_type": "section" if section_id else None,
    }

    rag_result = rag.answer(
        message=request.message,
        mode=request.mode,
        filters=filters,
    )

    interaction_id = repo.add_interaction(
        learner_id=request.learner_id,
        session_id=session_id,
        message=request.message,
        answer=rag_result["answer_md"],
        module_id=module_id,
        section_id=section_id,
    )

    repo.add_interaction_sources(
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
def post_feedback(request: FeedbackRequest) -> FeedbackResponse:
    repo = get_repo()
    tutor = get_tutor_flow()

    repo.ensure_learner(request.learner_id)

    interaction = repo.get_interaction(request.interaction_id)
    if not interaction or interaction["learner_id"] != request.learner_id:
        raise HTTPException(status_code=404, detail="interaction not found")

    repo.update_interaction_confidence(request.interaction_id, request.confidence)

    section_id = interaction.get("section_id")
    module_id = interaction.get("module_id")

    payload = tutor.apply_feedback(request.learner_id, section_id, module_id, request.confidence)

    return FeedbackResponse(
        updated=True,
        auto_advanced=payload["auto_advanced"],
        message=payload.get("message"),
        current_item=payload.get("current_item"),
    )
