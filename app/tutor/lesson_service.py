from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.state.cache_repository import CacheRepository
from app.tutor.lesson_generation import SectionLessonGenerator
from app.tutor.stage_source import StageSourceService

logger = logging.getLogger(__name__)


class LessonService:
    prewarm_concurrency = 1

    def __init__(
        self,
        repo: CacheRepository,
        source_service: StageSourceService,
        lesson_generator: SectionLessonGenerator,
    ):
        self.repo = repo
        self.source_service = source_service
        self.lesson_generator = lesson_generator
        self._prewarm_keys: set[tuple[str, int]] = set()
        self._prewarm_tasks: set[asyncio.Task[None]] = set()
        self._prewarm_semaphore = asyncio.Semaphore(self.prewarm_concurrency)

    async def get_or_generate(
        self,
        *,
        template_id: str,
        stage: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        source = await self.source_service.resolve(stage)
        cache = await self.repo.get_lesson_cache(
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
        )
        if self._is_valid_cache(cache, source.source_hash):
            lesson = dict(cache["lesson_json"])
            lesson["cached"] = True
            return lesson, self._bind_parent_doc_id(stage, source.parent_doc_id)

        lesson = await self.lesson_generator.generate_lesson(
            section_id=str(stage["section_id"]),
            title=str(stage.get("title") or ""),
            breadcrumb=list(stage.get("breadcrumb") or []),
            parent_doc_id=source.parent_doc_id,
            source_markdown=source.source_markdown,
        )
        await self.repo.upsert_lesson_cache(
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
            lesson_json=lesson,
        )
        lesson["cached"] = False
        return lesson, self._bind_parent_doc_id(stage, source.parent_doc_id)

    def schedule_prewarm(self, *, template_id: str, stage: dict[str, Any] | None) -> None:
        if not stage:
            return
        key = (template_id, int(stage["stage_index"]))
        if key in self._prewarm_keys:
            return
        self._prewarm_keys.add(key)
        task = asyncio.create_task(self._prewarm(template_id=template_id, stage=dict(stage), key=key))
        self._prewarm_tasks.add(task)
        task.add_done_callback(self._prewarm_tasks.discard)

    async def _prewarm(self, *, template_id: str, stage: dict[str, Any], key: tuple[str, int]) -> None:
        try:
            async with self._prewarm_semaphore:
                await self.get_or_generate(template_id=template_id, stage=stage)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Lesson prewarm skipped for stage '%s': %s", stage.get("section_id"), exc)
        finally:
            self._prewarm_keys.discard(key)

    async def close(self) -> None:
        tasks = list(self._prewarm_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._prewarm_tasks.clear()
        self._prewarm_keys.clear()

    def _is_valid_cache(self, cache: dict[str, Any] | None, source_hash: str) -> bool:
        if not cache:
            return False
        lesson_json = dict(cache.get("lesson_json") or {})
        format_version = int(lesson_json.get("format_version", 0))
        if format_version < int(self.lesson_generator.settings.lesson_gen_format_version):
            return False
        if lesson_json.get("generator_version") != self.lesson_generator.generator_version:
            return False
        if lesson_json.get("prompt_profile_version") != self.lesson_generator.prompt_profile_version:
            return False
        return str(lesson_json.get("source_hash", "")) == str(source_hash)

    @staticmethod
    def _bind_parent_doc_id(stage: dict[str, Any], parent_doc_id: str) -> dict[str, Any]:
        enriched = dict(stage)
        enriched["parent_doc_id"] = parent_doc_id
        return enriched
