from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from openai import OpenAI

from app.core.config import Settings, get_settings
from app.infra.qdrant_store import VectorStore
from app.state.repository import StateRepository
from app.tutor.lesson_generation import SectionLessonGenerator
from app.tutor.plan import build_stage_targets, load_book_data

logger = logging.getLogger(__name__)

COMPLETED_STATUS = "completed"
IN_PROGRESS_STATUS = "in_progress"


class TutorFlow:
    def __init__(
        self,
        repo: StateRepository,
        book_json_path: str,
        store: VectorStore,
        settings: Settings | None = None,
    ):
        self.repo = repo
        self.book_json_path = book_json_path
        self.store = store
        self.settings = settings or get_settings()
        self.mastery_threshold = 0.8
        self.default_template_id = "default_calc1"
        self.default_template_version = 1
        self.start_llm_timeout_seconds = 12.0
        self._llm_client: OpenAI | None = None
        if self.settings.openrouter_api_key:
            self._llm_client = OpenAI(
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url,
            )
        self.lesson_generator = SectionLessonGenerator(self.settings, self._llm_client)

    async def ensure_default_template(self) -> dict[str, Any]:
        existing = await self.repo.get_plan_template(self.default_template_id)
        if existing and existing.get("plan_json", {}).get("targets"):
            return existing
        return await self._create_default_template()

    async def start(self, learner_id: str) -> dict[str, Any]:
        await self.repo.ensure_learner(learner_id)
        template, state = await self._ensure_template_and_state(learner_id)
        targets = self._template_targets(template)
        total_stages = len(targets)
        current_stage = self._with_parent_binding(self._current_stage_from_state(state, targets))

        message = await self._render_start_message(
            learner_id=learner_id,
            current_stage=current_stage,
            completed_count=int(state["completed_count"]),
            total_stages=total_stages,
            plan_completed=bool(state["plan_completed"]),
        )
        return {
            "message": message,
            "plan": {
                "template_id": template["id"],
                "total_stages": total_stages,
                "completed_stages": int(state["completed_count"]),
            },
            "current_stage": current_stage,
            "plan_completed": bool(state["plan_completed"]),
        }

    async def get_current_lesson(self, learner_id: str) -> dict[str, Any]:
        await self.repo.ensure_learner(learner_id)
        template, state = await self._ensure_template_and_state(learner_id)
        targets = self._template_targets(template)
        current_stage = self._with_parent_binding(self._current_stage_from_state(state, targets))
        if not current_stage:
            return {
                "current_stage": None,
                "lesson": None,
                "plan_completed": True,
            }

        parent, parent_doc_id, full_content = self._resolve_stage_source(current_stage)
        source_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest() if full_content else ""
        cache = await self.repo.get_lesson_cache(
            learner_id=learner_id,
            template_id=template["id"],
            stage_index=int(current_stage["stage_index"]),
        )
        if self._is_valid_lesson_cache(cache, source_hash):
            lesson = dict(cache["lesson_json"])
            lesson["cached"] = True
            return {
                "current_stage": current_stage,
                "lesson": lesson,
                "plan_completed": bool(state["plan_completed"]),
            }

        lesson = await self.lesson_generator.generate_lesson(
            section_id=str(current_stage["section_id"]),
            title=str(current_stage.get("title") or ""),
            breadcrumb=list(current_stage.get("breadcrumb") or []),
            parent_doc_id=parent_doc_id,
            source_markdown=full_content,
        )
        await self.repo.upsert_lesson_cache(
            learner_id=learner_id,
            template_id=str(template["id"]),
            stage_index=int(current_stage["stage_index"]),
            lesson_json=lesson,
        )
        lesson["cached"] = False
        return {
            "current_stage": current_stage,
            "lesson": lesson,
            "plan_completed": bool(state["plan_completed"]),
        }

    async def current_item(self, learner_id: str, include_tutor_content: bool = False) -> dict[str, Any] | None:
        _ = include_tutor_content
        await self.repo.ensure_learner(learner_id)
        template, state = await self._ensure_template_and_state(learner_id)
        targets = self._template_targets(template)
        return self._with_parent_binding(self._current_stage_from_state(state, targets))

    async def advance(self, learner_id: str, force: bool = False) -> dict[str, Any]:
        _ = force
        await self.repo.ensure_learner(learner_id)
        template, state = await self._ensure_template_and_state(learner_id)
        targets = self._template_targets(template)
        total_stages = len(targets)
        if total_stages == 0:
            return {
                "message": "Plan completed.",
                "current_stage": None,
                "plan_completed": True,
            }

        completed_count = int(state["completed_count"])
        current_stage_index = int(state["current_stage_index"])
        if completed_count < total_stages:
            completed_count += 1

        plan_completed = completed_count >= total_stages
        next_stage_index = total_stages - 1 if plan_completed else min(current_stage_index + 1, total_stages - 1)
        state = await self.repo.update_learner_plan_state(
            learner_id=learner_id,
            template_id=template["id"],
            current_stage_index=next_stage_index,
            completed_count=completed_count,
            plan_completed=plan_completed,
        )
        current_stage = self._with_parent_binding(self._current_stage_from_state(state, targets))
        return {
            "message": "Plan completed." if plan_completed else "Moved to next stage.",
            "current_stage": current_stage,
            "plan_completed": plan_completed,
        }

    async def apply_feedback(
        self,
        learner_id: str,
        section_id: str | None,
        module_id: str | None,
        confidence: int,
    ) -> dict[str, Any]:
        stage = await self.current_item(learner_id, include_tutor_content=False)
        current_section_id = str(section_id or (stage or {}).get("section_id") or "")
        current_module_id = module_id or (stage or {}).get("module_id")
        if current_section_id:
            mastery_map = await self._mastery_map(learner_id)
            current_mastery = mastery_map.get(current_section_id, 0.0)
            new_mastery = self._clamp(current_mastery + self._confidence_delta(confidence))
            status = COMPLETED_STATUS if new_mastery >= self.mastery_threshold else IN_PROGRESS_STATUS
            await self.repo.upsert_topic_progress(
                learner_id=learner_id,
                section_id=current_section_id,
                module_id=current_module_id,
                status=status,
                mastery_score=new_mastery,
            )
        return {
            "auto_advanced": False,
            "message": "Feedback saved. Continue when ready.",
            "current_stage": stage,
        }

    async def _ensure_template_and_state(self, learner_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        template = await self.ensure_default_template()
        targets = self._template_targets(template)
        state = await self.repo.get_or_create_learner_plan_state(
            learner_id=learner_id,
            template_id=template["id"],
            total_stages=len(targets),
        )
        return template, state

    async def _create_default_template(self) -> dict[str, Any]:
        book_id, sections = load_book_data(self.book_json_path)
        targets = build_stage_targets(sections)
        template = await self.repo.upsert_plan_template(
            template_id=self.default_template_id,
            book_id=book_id,
            version=self.default_template_version,
            plan_json={"book_id": book_id, "targets": targets},
            is_active=True,
        )
        logger.info("Created default plan template '%s' with %d stages.", template["id"], len(targets))
        return template

    @staticmethod
    def _template_targets(template: dict[str, Any]) -> list[dict[str, Any]]:
        targets = (template.get("plan_json") or {}).get("targets") or []
        return [dict(item) for item in targets if isinstance(item, dict)]

    @staticmethod
    def _current_stage_from_state(state: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not targets or state.get("plan_completed"):
            return None
        index = int(state.get("current_stage_index", 0))
        index = max(0, min(index, len(targets) - 1))
        stage = dict(targets[index])
        stage["stage_index"] = index
        return stage

    def _resolve_stage_parent(self, stage: dict[str, Any]) -> dict[str, Any] | None:
        section_id = str(stage.get("section_id") or "")
        module_id = stage.get("module_id")
        if not section_id:
            return None
        parent = self.store.fetch_section_parent(section_id)
        if not parent and module_id and module_id != section_id:
            parent = self.store.fetch_section_parent(str(module_id))
        return parent

    def _resolve_stage_source(self, stage: dict[str, Any]) -> tuple[dict[str, Any] | None, str, str]:
        section_id = str(stage.get("section_id") or "")
        module_id = stage.get("module_id")
        parent = self._resolve_stage_parent(stage)
        parent_doc_id = str((parent or {}).get("parent_doc_id") or (parent or {}).get("doc_id") or section_id)
        full_content = str((parent or {}).get("content_text_full") or "").strip()
        if full_content:
            return parent, parent_doc_id, full_content

        children = self.store.fetch_section_children(section_id, module_id=module_id)
        if not children and module_id and module_id != section_id:
            children = self.store.fetch_section_children(str(module_id), module_id=module_id)
        full_content = "\n\n".join(
            str(item.get("content_text", "")).strip()
            for item in children
            if item.get("content_text")
        ).strip()
        return parent, parent_doc_id, full_content

    def _with_parent_binding(self, stage: dict[str, Any] | None) -> dict[str, Any] | None:
        if not stage:
            return None
        enriched = dict(stage)
        parent = self._resolve_stage_parent(enriched)
        parent_doc_id = str((parent or {}).get("parent_doc_id") or (parent or {}).get("doc_id") or "").strip()
        enriched["parent_doc_id"] = parent_doc_id or None
        if not enriched.get("title") and parent and parent.get("title"):
            enriched["title"] = str(parent["title"])
        if (not enriched.get("breadcrumb")) and parent and isinstance(parent.get("breadcrumb"), list):
            enriched["breadcrumb"] = [str(item) for item in parent["breadcrumb"]]
        return enriched

    def _is_valid_lesson_cache(self, cache: dict[str, Any] | None, source_hash: str) -> bool:
        if not cache:
            return False
        lesson_json = dict(cache.get("lesson_json") or {})
        format_version = int(lesson_json.get("format_version", 0))
        if format_version < int(self.settings.lesson_gen_format_version):
            return False
        if lesson_json.get("generator_version") != self.lesson_generator.generator_version:
            return False
        return str(lesson_json.get("source_hash", "")) == str(source_hash)

    async def _render_start_message(
        self,
        learner_id: str,
        current_stage: dict[str, Any] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        if self._llm_client is None:
            return self._default_start_message(current_stage, completed_count, total_stages, plan_completed)
        prompt = (
            "Write a short tutor greeting in Markdown.\n"
            "Tone: warm, teacher-like, and natural (not robotic).\n"
            "If learner is returning, explicitly say welcome back and that you will continue from current stage.\n"
            "Mention what you will do next in 1 sentence.\n"
            "Keep it practical (3-5 sentences).\n\n"
            f"Learner ID: {learner_id}\n"
            f"Completed stages: {completed_count}\n"
            f"Total stages: {total_stages}\n"
            f"Plan completed: {plan_completed}\n"
            f"Current stage: {json.dumps(current_stage or {}, ensure_ascii=True)}\n"
        )
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self._chat_completion,
                    "You are a concise and supportive calculus tutor.",
                    prompt,
                    0.2,
                ),
                timeout=self.start_llm_timeout_seconds,
            )
            message = raw.strip()
            if message:
                return message
        except Exception as exc:
            logger.warning("Start intro fallback for learner '%s': %s", learner_id, exc)
        return self._default_start_message(current_stage, completed_count, total_stages, plan_completed)

    def _chat_completion(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        if self._llm_client is None:
            return ""
        completion = self._llm_client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return str(completion.choices[0].message.content or "").strip()

    @staticmethod
    def _default_start_message(
        current_stage: dict[str, Any] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        if plan_completed:
            return (
                "Welcome back. I am your calculus tutor, and you have completed the full plan "
                f"({completed_count}/{total_stages}). "
                "We can now review any topic you want or restart from the beginning for a stronger second pass."
            )
        if not current_stage:
            return (
                "Welcome. I am your calculus tutor. "
                "We will work through the course step by step, and I will explain each section clearly."
            )
        title = current_stage.get("title") or current_stage.get("section_id")
        stage_number = int(current_stage.get("stage_index", 0)) + 1
        return (
            "Welcome back. I am your calculus tutor, and we will continue from where you stopped.\n"
            f"Today we are on stage {stage_number} of {total_stages}: **{title}** "
            f"(completed: {completed_count}/{total_stages}).\n"
            "I will guide you through this section, then we will move to the next stage when you are ready."
        )

    async def _mastery_map(self, learner_id: str) -> dict[str, float]:
        progress = await self.repo.list_topic_progress(learner_id)
        return {row["section_id"]: float(row.get("mastery_score", 0.0)) for row in progress}

    @staticmethod
    def _confidence_delta(confidence: int) -> float:
        if confidence >= 4:
            return 0.20
        if confidence == 3:
            return 0.05
        return -0.10

    @staticmethod
    def _clamp(value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value
