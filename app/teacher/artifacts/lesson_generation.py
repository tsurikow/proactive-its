from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic_ai import Agent, NativeOutput

from pydantic import BaseModel, ConfigDict, Field

from app.platform.ai import llm_available, run_native_agent
from app.platform.config import Settings, get_settings
from app.platform.markdown_sanitize import sanitize_lesson_markdown
from app.teacher.artifacts.models import LessonPlanDraft
from app.teacher.artifacts.document_blocks import (
    IMMUTABLE_BLOCK_TYPES,
    REWRITE_BLOCK_TYPES,
    describe_blocks_for_prompt,
    normalize_section_markdown,
    render_blocks_for_lesson,
    restore_immutable_blocks,
)


class LessonMarkdownTransport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markdown: str = Field(
        description="Final rewritten lesson markdown that preserves immutable blocks and source order.",
    )


LESSON_MARKDOWN_AGENT: Agent[Any, LessonMarkdownTransport] = Agent(
    None,
    output_type=NativeOutput(LessonMarkdownTransport, strict=True),
    system_prompt=(
        "You are a precise tutor-editor. Rewrite prose for pedagogy, preserve structure, "
        "and never alter immutable block tokens."
    ),
    retries=1,
    output_retries=2,
    defer_model_check=True,
)

LESSON_MARKDOWN_TEXT_AGENT: Agent[Any, str] = Agent(
    None,
    system_prompt=(
        "You are a precise tutor-editor. Rewrite prose for pedagogy, preserve structure, "
        "and never alter immutable block tokens."
    ),
    retries=1,
    defer_model_check=True,
)


class SectionLessonGenerator:
    generator_version = "llm_block_rewrite_v1"
    prompt_profile_version = "lesson_prompt_v8"
    rewrite_temperature = 0.35

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def generate_lesson(
        self,
        *,
        section_id: str,
        title: str,
        breadcrumb: list[str],
        parent_doc_id: str,
        source_markdown: str,
        lesson_instruction: str,
        lesson_render_signature: str,
        stage_signal: str,
        adaptation_brief: str,
        lesson_plan_draft: LessonPlanDraft | None = None,
        learner_teaching_brief: str | None = None,
    ) -> dict[str, Any]:
        source = str(source_markdown or "").strip()
        if not source:
            raise RuntimeError(f"Section '{section_id}' has no source content for lesson generation.")
        if not self.settings.lesson_gen_enabled:
            raise RuntimeError("Lesson generation is disabled by configuration.")
        if not llm_available(self.settings):
            raise RuntimeError("OPENROUTER_API_KEY is required for lesson generation.")

        blocks = normalize_section_markdown(source)
        if not blocks:
            raise RuntimeError(f"Section '{section_id}' could not be normalized into document blocks.")

        lesson_frame, placeholders = render_blocks_for_lesson(blocks)
        generated = await self._generate_lesson_markdown(
            title=title,
            breadcrumb=breadcrumb,
            lesson_frame=lesson_frame,
            block_outline=describe_blocks_for_prompt(blocks),
            lesson_instruction=lesson_instruction,
            lesson_render_signature=lesson_render_signature,
            stage_signal=stage_signal,
            adaptation_brief=adaptation_brief,
            lesson_plan_draft=lesson_plan_draft,
            learner_teaching_brief=learner_teaching_brief,
        )
        final_markdown = restore_immutable_blocks(generated, placeholders)
        final_markdown = self._drop_unresolved_tokens(final_markdown)
        final_markdown = sanitize_lesson_markdown(final_markdown)
        if not final_markdown.strip():
            raise RuntimeError("Lesson generation returned empty markdown.")

        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return {
            "format_version": int(self.settings.lesson_gen_format_version),
            "generator_version": self.generator_version,
            "prompt_profile_version": self.prompt_profile_version,
            "source_hash": source_hash,
            "lesson_plan_used": lesson_plan_draft is not None,
            "generation_mode": "llm_block_rewrite_v1",
            "preservation_report": {
                "immutable_block_count": sum(1 for block in blocks if block.block_type in IMMUTABLE_BLOCK_TYPES),
                "rewrite_block_count": sum(1 for block in blocks if block.block_type in REWRITE_BLOCK_TYPES),
            },
            "section_summary_md": None,
            "lesson_steps": [
                {
                    "step_id": f"{section_id}::content",
                    "step_type": "concept",
                    "title": title or "Section lesson",
                    "content_md": final_markdown,
                    "source_chunk_ids": [parent_doc_id],
                    "order_index": 0,
                }
            ],
        }

    async def _generate_lesson_markdown(
        self,
        *,
        title: str,
        breadcrumb: list[str],
        lesson_frame: str,
        block_outline: str,
        lesson_instruction: str,
        lesson_render_signature: str,
        stage_signal: str,
        adaptation_brief: str,
        lesson_plan_draft: LessonPlanDraft | None,
        learner_teaching_brief: str | None,
    ) -> str:
        lesson_plan_block = "none"
        if lesson_plan_draft is not None:
            lesson_plan_block = (
                f"Objective: {lesson_plan_draft.lesson_objective}\n"
                f"Explanation arc: {lesson_plan_draft.explanation_arc}\n"
                f"Example plan: {lesson_plan_draft.example_plan}\n"
                f"Checkpoint plan: {lesson_plan_draft.checkpoint_plan}\n"
                f"Caution flags: {lesson_plan_draft.caution_flags}\n"
                f"Support emphasis: {lesson_plan_draft.support_emphasis}\n"
                f"Progression note: {lesson_plan_draft.progression_note}\n"
            )
        prompt = (
            "Transform the source section into one coherent teacher-style lesson in Markdown.\n"
            "You are allowed to rewrite prose freely, but you must preserve the exact order of ideas and blocks.\n"
            "Use the structured response to return the final markdown lesson body.\n\n"
            "Rules:\n"
            "- Keep every placeholder token like [[BLOCK_0001]] exactly once and in the same order.\n"
            "- Treat placeholder tokens as immutable blocks for tables, figures, code, or raw HTML.\n"
            "- Explain like a strong teacher: clearer than the book, but not shorter than needed.\n"
            "- Preserve mathematical meaning exactly.\n"
            "- If the source math is malformed or awkward, rewrite it into valid KaTeX-ready LaTeX.\n"
            "- Use $...$ for inline math and $$...$$ for display math.\n"
            "- NEVER use bare $ characters in regular text. Every $ must be part of a $...$ or $$...$$ pair.\n"
            "- Never nest $ inside $...$. Ensure all delimiters are properly paired.\n"
            "- Do not leave formulas as plain text when they should be mathematical notation.\n"
            "- Keep headings in the same order. You may lightly polish wording, but do not remove headings.\n"
            "- NEVER include checkpoint solutions, exercise answers, or worked-out final answers. "
            "Remove them entirely. Only keep the problem statement.\n"
            "- Solution-like material (hints, setup, scaffolding) may be kept only if it does NOT reveal the final answer.\n"
            "- Remove non-learner-facing internal references and anchor-like noise.\n"
            "- Keep figure links only if they are already present through placeholders.\n"
            "- Keep lists, examples, and exercises pedagogically useful.\n\n"
            f"Lesson render signature: {lesson_render_signature}\n"
            f"Stage signal: {stage_signal}\n"
            f"Adaptation brief: {adaptation_brief}\n"
            f"Learner teaching brief: {learner_teaching_brief or 'none'}\n"
            f"Lesson instruction: {lesson_instruction}\n\n"
            f"Structured lesson plan draft:\n{lesson_plan_block}\n"
            f"Section title: {title}\n"
            f"Section path: {' -> '.join(breadcrumb)}\n\n"
            f"Block outline:\n{block_outline}\n\n"
            f"Source lesson frame:\n\n{lesson_frame}"
        )
        return await self._run_llm_markdown(
            system_prompt=(
                "You are a precise tutor-editor. Rewrite prose for pedagogy, preserve structure, "
                "and never alter immutable block tokens."
            ),
            user_prompt=prompt,
            timeout=self.settings.lesson_max_section_seconds,
            temperature=self.rewrite_temperature,
            err_label="lesson generation",
        )

    async def _run_llm_markdown(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout: float,
        temperature: float,
        err_label: str,
    ) -> str:
        try:
            payload = await run_native_agent(
                LESSON_MARKDOWN_AGENT,
                settings=self.settings,
                prompt=user_prompt,
                model_name=self.settings.openrouter_model,
                temperature=temperature,
                timeout_seconds=timeout,
                extra_body={"provider": {"require_parameters": True}},
            )
            raw = payload.markdown
        except Exception as exc:
            if not self._needs_plain_markdown_fallback(exc):
                raise RuntimeError(f"Lesson LLM step failed ({err_label}): {exc}") from exc
            try:
                raw = await run_native_agent(
                    LESSON_MARKDOWN_TEXT_AGENT,
                    settings=self.settings,
                    prompt=(
                        f"{user_prompt}\n\n"
                        "Return only the final markdown lesson body.\n"
                        "Do not wrap the answer in JSON.\n"
                        "Do not use code fences.\n"
                    ),
                    model_name=self.settings.openrouter_model,
                    temperature=temperature,
                    timeout_seconds=timeout,
                    extra_body={"provider": {"require_parameters": True}},
                )
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"Lesson LLM step failed ({err_label}): {exc}; plain_markdown_fallback_failed: {fallback_exc}"
                ) from fallback_exc
        text = self._clean_markdown_output(str(raw or ""))
        if not text:
            raise RuntimeError(f"Lesson LLM step returned empty output ({err_label}).")
        return text

    @staticmethod
    def _clean_markdown_output(raw: str) -> str:
        text = str(raw or "").strip()
        text = re.sub(r"^```(?:markdown)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
        return text

    @staticmethod
    def _drop_unresolved_tokens(markdown: str) -> str:
        text = str(markdown or "")
        text = re.sub(r"^\s*\[\[BLOCK_\d{4}]]\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"\[\[BLOCK_\d{4}]]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _needs_plain_markdown_fallback(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return "output validation" in message or "maximum retries" in message
