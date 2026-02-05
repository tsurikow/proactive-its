from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.core.config import Settings
from app.rag.vector_store import VectorStore
from app.state.repository import StateRepository
from app.tutor.plan import build_linear_plan, load_toc_sections, week_start_for


class TutorFlow:
    def __init__(self, repo: StateRepository, book_json_path: str, store: VectorStore, settings: Settings):
        self.repo = repo
        self.book_json_path = book_json_path
        self.store = store
        self.settings = settings
        self._client: OpenAI | None = None
        if settings.openrouter_api_key:
            self._client = OpenAI(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
            )

    def start(self, learner_id: str) -> dict[str, Any]:
        self.repo.ensure_learner(learner_id)
        plan = self._ensure_plan(learner_id, reset=True)
        item = self._current_item_from_plan(learner_id, plan, include_tutor_content=False)
        outline = self._plan_outline(plan)
        message, lesson = self._generate_intro_message(outline, plan, item)
        if item and lesson:
            item["content_tutor"] = lesson
        elif item and item.get("content_text"):
            item["content_tutor"] = self._generate_tutor_content(
                item.get("content_text") or "",
                item.get("breadcrumb") or [],
                item.get("title"),
            )
        return {
            "message": message,
            "plan": plan,
            "current_item": item,
        }

    def advance(self, learner_id: str, force: bool = False) -> dict[str, Any]:
        plan = self._ensure_plan(learner_id)
        current = self._current_target(plan)
        if not current:
            return {"message": "Plan completed.", "current_item": None}

        mastery = self._section_mastery(learner_id, current["section_id"])

        if mastery >= 0.8 or force:
            self.repo.upsert_topic_progress(
                learner_id=learner_id,
                section_id=current["section_id"],
                module_id=current.get("module_id"),
                status="completed" if mastery >= 0.8 else "needs_review",
                mastery_score=mastery,
            )
            current["completed"] = True
            self.repo.save_study_plan(learner_id, plan["week_start"], plan)
            next_target = self._current_target(plan)
            if not next_target:
                return {"message": "Plan completed.", "current_item": None}
            item = self._learning_item(next_target, include_tutor_content=True)
            return {"message": "Next topic.", "current_item": item}

        chunk_step = self._advance_chunk_within_section(learner_id, plan, current)
        if chunk_step["item"]:
            return {"message": "Continuing this topic.", "current_item": chunk_step["item"]}

        if chunk_step["section_completed"]:
            next_target = self._current_target(plan)
            if not next_target:
                return {"message": "Plan completed.", "current_item": None}
            item = self._learning_item(next_target, include_tutor_content=True)
            return {"message": "Next topic.", "current_item": item}

        item = self._learning_item(current, include_tutor_content=True)
        return {
            "message": "Let’s review this topic a bit more before moving on. You can ask questions or type Next to continue.",
            "current_item": item,
        }

    def apply_feedback(self, learner_id: str, section_id: str | None, module_id: str | None, confidence: int) -> dict[str, Any]:
        if not section_id:
            return {"auto_advanced": False, "current_item": None, "message": None}

        mastery = self._section_mastery(learner_id, section_id)
        delta = 0.2 if confidence >= 4 else (0.05 if confidence == 3 else -0.1)
        mastery = self._clamp(mastery + delta)

        status = "in_progress"
        if mastery >= 0.8:
            status = "completed"
        elif confidence <= 2:
            status = "needs_review"

        self.repo.upsert_topic_progress(
            learner_id=learner_id,
            section_id=section_id,
            module_id=module_id,
            status=status,
            mastery_score=mastery,
        )

        if mastery >= 0.8:
            advance = self.advance(learner_id, force=False)
            return {"auto_advanced": True, "message": advance["message"], "current_item": advance["current_item"]}

        return {
            "auto_advanced": False,
            "message": "Keep going on this topic. Ask questions if anything is unclear.",
            "current_item": self._learning_item(
                {"section_id": section_id, "module_id": module_id, "title": None},
                include_tutor_content=True,
            ),
        }

    def current_item(self, learner_id: str, include_tutor_content: bool = False) -> dict[str, Any] | None:
        plan = self._ensure_plan(learner_id)
        target = self._current_target(plan)
        if not target:
            return None
        return self._learning_item(target, include_tutor_content=include_tutor_content)

    def _ensure_plan(self, learner_id: str, reset: bool = False) -> dict[str, Any]:
        week_start = week_start_for().isoformat()
        if reset:
            self.repo.clear_topic_progress(learner_id)
            self.repo.clear_study_plans(learner_id)
        existing = None if reset else self.repo.get_active_study_plan(learner_id, week_start)
        if existing and existing.get("plan", {}).get("targets"):
            plan = existing["plan"]
            changed = False
            for target in plan.get("targets", []):
                if "chunk_index" not in target:
                    target["chunk_index"] = 0
                    changed = True
                if "breadcrumb" not in target:
                    target["breadcrumb"] = []
                    changed = True
            if changed:
                self.repo.save_study_plan(learner_id, week_start, plan)
            return plan
        sections = load_toc_sections(self.book_json_path)
        plan = build_linear_plan(sections)
        saved = self.repo.save_study_plan(learner_id, week_start, plan)
        return saved["plan"]

    def _current_target(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        for target in plan.get("targets", []):
            if not target.get("completed"):
                return target
        return None

    def _current_item_from_plan(
        self,
        learner_id: str,
        plan: dict[str, Any],
        include_tutor_content: bool = False,
    ) -> dict[str, Any] | None:
        target = self._current_target(plan)
        if not target:
            return None
        return self._learning_item(target, include_tutor_content=include_tutor_content)

    def _learning_item(self, target: dict[str, Any], include_tutor_content: bool = False) -> dict[str, Any] | None:
        section_id = target.get("section_id")
        if not section_id:
            return None
        module_id = target.get("module_id")
        chunk_index = int(target.get("chunk_index") or 0)
        chunks = []
        if section_id:
            chunks = self.store.fetch_chunks(section_id=section_id, doc_type="section")
        if not chunks and module_id:
            chunks = self.store.fetch_chunks(module_id=module_id, doc_type="module")
        chunk = chunks[chunk_index] if chunk_index < len(chunks) else None
        item = {
            "section_id": section_id,
            "module_id": module_id,
            "title": target.get("title"),
            "breadcrumb": target.get("breadcrumb") or [],
            "chunk_id": chunk.get("chunk_id") if chunk else None,
            "content_text": chunk.get("content_text") if chunk else None,
            "chunk_index": chunk_index,
            "chunk_total": len(chunks),
        }
        if include_tutor_content and item.get("content_text"):
            item["content_tutor"] = self._generate_tutor_content(
                item.get("content_text") or "",
                item.get("breadcrumb") or [],
                item.get("title"),
            )
        return item

    def _section_mastery(self, learner_id: str, section_id: str) -> float:
        progress = self.repo.list_topic_progress(learner_id)
        current = next((p for p in progress if p["section_id"] == section_id), None)
        return float(current["mastery_score"]) if current else 0.0

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _advance_chunk_within_section(
        self,
        learner_id: str,
        plan: dict[str, Any],
        current: dict[str, Any],
    ) -> dict[str, Any]:
        section_id = current.get("section_id")
        module_id = current.get("module_id")
        if not section_id:
            return {"item": None, "section_completed": False}

        chunks = self.store.fetch_chunks(section_id=section_id, doc_type="section")
        if not chunks and module_id:
            chunks = self.store.fetch_chunks(module_id=module_id, doc_type="module")
        if not chunks:
            current["completed"] = True
            self.repo.upsert_topic_progress(
                learner_id=learner_id,
                section_id=section_id,
                module_id=module_id,
                status="needs_review",
                mastery_score=self._section_mastery(learner_id, section_id),
            )
            self.repo.save_study_plan(learner_id, plan["week_start"], plan)
            return {"item": None, "section_completed": True}

        current_index = int(current.get("chunk_index") or 0)
        if current_index + 1 < len(chunks):
            current["chunk_index"] = current_index + 1
            self.repo.save_study_plan(learner_id, plan["week_start"], plan)
            return {"item": self._learning_item(current, include_tutor_content=True), "section_completed": False}

        mastery = self._section_mastery(learner_id, section_id)
        current["completed"] = True
        self.repo.upsert_topic_progress(
            learner_id=learner_id,
            section_id=section_id,
            module_id=module_id,
            status="completed" if mastery >= 0.8 else "needs_review",
            mastery_score=mastery,
        )
        self.repo.save_study_plan(learner_id, plan["week_start"], plan)
        return {"item": None, "section_completed": True}

    def _plan_outline(self, plan: dict[str, Any], limit: int = 10) -> list[str]:
        titles = []
        for target in plan.get("targets", [])[:limit]:
            title = target.get("title")
            if title:
                titles.append(str(title))
        return titles

    def _generate_intro_message(
        self,
        outline: list[str],
        plan: dict[str, Any],
        item: dict[str, Any] | None,
    ) -> tuple[str, str | None]:
        total = len(plan.get("targets", []))
        plan_lines = "\n".join(f"{idx + 1}. {title}" for idx, title in enumerate(outline))
        chunk_text = item.get("content_text") if item else None

        if self._client is None:
            raise RuntimeError("OPENROUTER_API_KEY is required for /v1/start")

        breadcrumb = " > ".join(item.get("breadcrumb", [])) if item else ""
        prompt = (
            "You are a friendly, concise calculus teacher. Introduce yourself, present the study plan "
            "as a short numbered list, then end with the exact line: \"Let’s begin:\". "
            "Immediately after that line, write a short explanation (4–7 sentences) of the first topic "
            "based on the provided raw chunk. Do not paste the raw chunk. "
            "Keep the whole response under 220 words.\n\n"
            f"Total sections: {total}\n"
            f"Plan outline:\n{plan_lines}\n\n"
            "First topic full path:\n"
            f"{breadcrumb}\n\n"
            "Raw chunk:\n"
            f"{chunk_text or ''}"
        )
        response = self._client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": "You are a concise calculus tutor."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        intro = response.choices[0].message.content or ""
        intro = intro.strip()
        lesson = None
        markers = ("Let’s begin:", "Let's begin:")
        for marker in markers:
            if marker in intro:
                lesson = intro.split(marker, 1)[1].strip()
                break
        return intro, lesson

    def _generate_tutor_content(self, chunk_text: str, breadcrumb: list[str], title: str | None) -> str:
        if self._client is None:
            raise RuntimeError("OPENROUTER_API_KEY is required for tutor content")
        path = " > ".join(breadcrumb) if breadcrumb else (title or "")
        prompt = (
            "You are a calculus tutor. Use ONLY the information in the raw chunk below. "
            "Do not add any facts, definitions, or examples that are not explicitly present. "
            "Paraphrase the chunk in clear teaching language. If the chunk is mostly figure captions or images, "
            "say that and summarize what the captions describe, without adding extra math content. "
            "Keep math notation intact.\\n\\n"
            f"Topic: {path}\\n\\n"
            "Raw chunk:\\n"
            f"{chunk_text}"
        )
        response = self._client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": "You are a calculus tutor who must not use outside knowledge."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        return (response.choices[0].message.content or "").strip()
