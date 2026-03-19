from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from openai import AsyncOpenAI

from app.platform.config import Settings, get_settings
from app.platform.markdown_sanitize import sanitize_lesson_markdown
from app.tutor.document_blocks import (
    IMMUTABLE_BLOCK_TYPES,
    REWRITE_BLOCK_TYPES,
    describe_blocks_for_prompt,
    normalize_section_markdown,
    render_blocks_for_lesson,
    restore_immutable_blocks,
)


class SectionLessonGenerator:
    generator_version = "llm_block_rewrite_v1"
    prompt_profile_version = "lesson_prompt_v4"
    rewrite_temperature = 0.35

    def __init__(self, settings: Settings | None = None, llm_client: AsyncOpenAI | None = None):
        self.settings = settings or get_settings()
        self.llm_client = llm_client

    async def generate_lesson(
        self,
        *,
        section_id: str,
        title: str,
        breadcrumb: list[str],
        parent_doc_id: str,
        source_markdown: str,
    ) -> dict[str, Any]:
        source = str(source_markdown or "").strip()
        if not source:
            raise RuntimeError(f"Section '{section_id}' has no source content for lesson generation.")
        if not self.settings.lesson_gen_enabled:
            raise RuntimeError("Lesson generation is disabled by configuration.")
        if self.llm_client is None:
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
    ) -> str:
        prompt = (
            "Transform the source section into one coherent teacher-style lesson in Markdown.\n"
            "You are allowed to rewrite prose freely, but you must preserve the exact order of ideas and blocks.\n"
            "Output Markdown only.\n\n"
            "Rules:\n"
            "- Keep every placeholder token like [[BLOCK_0001]] exactly once and in the same order.\n"
            "- Treat placeholder tokens as immutable blocks for tables, figures, code, or raw HTML.\n"
            "- Explain like a strong teacher: clearer than the book, but not shorter than needed.\n"
            "- Preserve mathematical meaning exactly.\n"
            "- If the source math is malformed or awkward, rewrite it into valid KaTeX-ready LaTeX.\n"
            "- Use $...$ for inline math and $$...$$ for display math.\n"
            "- Do not leave formulas as plain text when they should be mathematical notation.\n"
            "- Keep headings in the same order. You may lightly polish wording, but do not remove headings.\n"
            "- Solution-like material should become hints, setup, or scaffolding, not full final solved answers.\n"
            "- Remove non-learner-facing internal references and anchor-like noise.\n"
            "- Keep figure links only if they are already present through placeholders.\n"
            "- Keep lists, examples, and exercises pedagogically useful.\n\n"
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
        if self.llm_client is None:
            raise RuntimeError("LLM client is not configured.")
        try:
            raw = await asyncio.wait_for(
                self._chat_completion(system_prompt, user_prompt, temperature),
                timeout=timeout,
            )
        except Exception as exc:
            raise RuntimeError(f"Lesson LLM step failed ({err_label}): {exc}") from exc
        text = self._clean_markdown_output(str(raw or ""))
        if not text:
            raise RuntimeError(f"Lesson LLM step returned empty output ({err_label}).")
        return text

    async def _chat_completion(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        if self.llm_client is None:
            return ""
        completion = await self.llm_client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            timeout=self.settings.lesson_max_section_seconds,
        )
        return str(completion.choices[0].message.content or "").strip()

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
