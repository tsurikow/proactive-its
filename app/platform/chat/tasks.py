from __future__ import annotations
from functools import lru_cache
import logging

from app.platform.config import get_settings
from app.platform.observability import configure_observability
from app.platform.async_runner import run_async

logger = logging.getLogger(__name__)

try:
    from celery import Celery
except ImportError:  # pragma: no cover - exercised via fallback path in tests
    Celery = None


@lru_cache(maxsize=1)
def get_celery_app():
    if Celery is None:
        return None
    settings = get_settings()
    configure_observability(settings)
    app = Celery("proactive_its")
    app.conf.update(
        broker_url=settings.rabbitmq_url,
        task_default_queue=settings.chat_worker_queue,
        imports=("app.platform.chat.tasks",),
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=True,
        worker_prefetch_multiplier=1,
    )
    return app


def enqueue_teacher_session_task(*, turn_id: str, job_id: str) -> str:
    celery_app = get_celery_app()
    if celery_app is None:
        raise RuntimeError("celery_unavailable")
    result = celery_app.send_task(
        "app.platform.chat.tasks.run_teacher_session",
        args=[turn_id],
        task_id=job_id,
        queue=get_settings().chat_worker_queue,
    )
    return str(result.id)


def enqueue_memory_synthesis_task(
    *,
    learner_id: str,
    template_id: str,
    event_type: str,
    conversation_history: list[dict[str, str]],
    learner_memory: dict | None,
) -> str:
    celery_app = get_celery_app()
    if celery_app is None:
        raise RuntimeError("celery_unavailable")
    result = celery_app.send_task(
        "app.platform.chat.tasks.run_memory_synthesis",
        kwargs={
            "learner_id": learner_id,
            "template_id": template_id,
            "event_type": event_type,
            "conversation_history": conversation_history,
            "learner_memory": learner_memory,
        },
        queue=get_settings().chat_worker_queue,
    )
    return str(result.id)


async def _notify_turn_complete(turn_id: str) -> None:
    """Publish a Redis notification so the API process stops waiting."""
    try:
        from app.api.dependencies import get_redis_cache

        cache = get_redis_cache()
        await cache.publish(f"chat_turn:{turn_id}", "done")
    except Exception:
        logger.debug("redis.notify_failed", extra={"turn_id": turn_id})


async def _run_teacher_session_async(turn_id: str) -> None:
    from app.api.dependencies import get_teacher_runtime, get_chat_service, get_durable_chat_repository

    repo = get_durable_chat_repository()
    claim = await repo.claim_chat_turn_execution(turn_id)
    status = str(claim.get("status") or "")
    if status in ("completed", "busy", "missing"):
        return

    turn = claim.get("turn") or {}
    request_payload = turn.get("request_payload_json") or {}

    from app.teacher.models import TeacherSessionRequest
    request = TeacherSessionRequest.model_validate(request_payload)

    runtime = get_teacher_runtime()
    chat_service = get_chat_service()

    try:
        result = await runtime.execute_session_inner(
            request,
            chat_service=chat_service,
        )
        result_json = result.model_dump(mode="json")
    except Exception as exc:
        await repo.mark_chat_turn_failed(turn_id, error_message=str(exc))
        raise

    await repo.complete_chat_turn(
        turn_id=turn_id,
        final_interaction_id=None,
        final_result_json=result_json,
        worker_metadata_json={"job_kind": "teacher_session"},
    )
    await _notify_turn_complete(turn_id)


async def _run_memory_synthesis_async(
    *,
    learner_id: str,
    template_id: str,
    event_type: str,
    conversation_history: list[dict[str, str]],
    learner_memory: dict | None,
) -> None:
    from app.api.dependencies import get_teacher_runtime
    from app.teacher.models import TeacherSessionEventType

    runtime = get_teacher_runtime()
    await runtime._maybe_synthesize_memory(
        learner_id=learner_id,
        template_id=template_id,
        event_type=TeacherSessionEventType(event_type),
        conversation_history=conversation_history,
        learner_memory=learner_memory,
    )


celery_app = get_celery_app()

if celery_app is not None:

    @celery_app.task(  # type: ignore[misc]
        name="app.platform.chat.tasks.run_teacher_session",
        bind=True,
        autoretry_for=(Exception,),
        retry_backoff=True,
        retry_jitter=True,
        retry_kwargs={"max_retries": 3},
    )
    def run_teacher_session(self, turn_id: str) -> None:
        _ = self
        run_async(_run_teacher_session_async(turn_id))

    @celery_app.task(  # type: ignore[misc]
        name="app.platform.chat.tasks.run_memory_synthesis",
        bind=True,
        autoretry_for=(Exception,),
        retry_backoff=True,
        retry_jitter=True,
        retry_kwargs={"max_retries": 2},
    )
    def run_memory_synthesis(
        self,
        *,
        learner_id: str,
        template_id: str,
        event_type: str,
        conversation_history: list[dict[str, str]],
        learner_memory: dict | None,
    ) -> None:
        _ = self
        run_async(
            _run_memory_synthesis_async(
                learner_id=learner_id,
                template_id=template_id,
                event_type=event_type,
                conversation_history=conversation_history,
                learner_memory=learner_memory,
            )
        )
