from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.core.config import Settings, get_settings
from app.core.markdown_sanitize import strip_non_figure_links
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$"
)
_CODE_FENCE_RE = re.compile(r"^\s*```")
_FIGURE_CAPTION_RE = re.compile(r"^\s*\*Figure:.*\*\s*$", flags=re.IGNORECASE)
_BEGIN_ENV_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_ENV_TEMPLATE = r"\\end\{%s\}"


@dataclass
class ProtectedBlock:
    token: str
    content: str
    kind: str


class SectionLessonGenerator:
    generator_version = "llm_single_stage_v4"

    def __init__(self, settings: Settings | None = None, llm_client: OpenAI | None = None):
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

        source_hash = self._hash(source)
        protected_text, blocks = self._protect_blocks(source)
        candidate = await self._generate_single_stage_lesson(
            section_markdown=protected_text,
            title=title,
            breadcrumb=breadcrumb,
        )
        candidate = await self._refine_lesson_output(
            draft_markdown=candidate,
            title=title,
            breadcrumb=breadcrumb,
        )

        final_markdown = self._restore_blocks(candidate, blocks).strip()
        final_markdown = strip_non_figure_links(final_markdown)
        if not final_markdown:
            raise RuntimeError("Lesson generation returned empty markdown.")

        lesson = {
            "format_version": int(self.settings.lesson_gen_format_version),
            "generator_version": self.generator_version,
            "source_hash": source_hash,
            "generation_mode": "llm_single_stage",
            "preservation_report": None,
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
        return lesson

    async def _generate_single_stage_lesson(
        self,
        *,
        section_markdown: str,
        title: str,
        breadcrumb: list[str],
    ) -> str:
        prompt = self._build_single_stage_prompt(
            section_markdown=section_markdown,
            title=title,
            breadcrumb=breadcrumb,
        )
        return await self._run_llm_markdown(
            system_prompt=(
                "You are an expert teacher-editor. Keep all source structure and data, "
                "but explain with clear pedagogy and natural teacher voice."
            ),
            user_prompt=prompt,
            timeout=self.settings.lesson_max_section_seconds,
            temperature=0.25,
            err_label="single-stage generation",
        )

    async def _refine_lesson_output(
        self,
        *,
        draft_markdown: str,
        title: str,
        breadcrumb: list[str],
    ) -> str:
        prompt = (
            "Refine this lesson markdown for pedagogy quality and KaTeX safety.\n"
            "Output markdown only.\n\n"
            "Hard constraints:\n"
            "- Preserve all [[BLOCK_xxxx]] tokens exactly.\n"
            "- Keep heading order unchanged.\n"
            "- Remove internal IDs/references like CNX_* and fs-id* from visible text.\n"
            "- Keep figure links only.\n"
            "- Fix malformed LaTeX for KaTeX (delimiter/syntax cleanup only; keep meaning).\n"
            "- Do not show full worked solutions to students.\n"
            "- If there is a Solution block, convert it into short 'Try it first' hints without final numeric/algebraic answer.\n"
            "- Keep definitions/theorems/examples/exercises present.\n\n"
            f"Section title: {title}\n"
            f"Path: {' -> '.join(breadcrumb)}\n\n"
            f"Draft markdown:\n\n{draft_markdown}"
        )
        try:
            return await self._run_llm_markdown(
                system_prompt=(
                    "You are a strict lesson quality editor for math education. "
                    "Improve clarity while preserving source content and structure."
                ),
                user_prompt=prompt,
                timeout=max(20.0, float(self.settings.lesson_max_section_seconds) * 0.5),
                temperature=0.0,
                err_label="lesson refinement",
            )
        except Exception:
            return draft_markdown

    @staticmethod
    def _build_single_stage_prompt(*, section_markdown: str, title: str, breadcrumb: list[str]) -> str:
        return (
            "Rewrite this full section as a clear, friendly teacher lesson.\n"
            "You can improve explanation style and pedagogical flow, but keep complete technical coverage.\n"
            "Output markdown only.\n\n"
            "Hard constraints:\n"
            "- Preserve every [[BLOCK_xxxx]] token exactly as-is, unchanged text.\n"
            "- Keep every heading and keep heading order exactly.\n"
            "- Keep figure links only; remove all other links and internal anchor IDs.\n"
            "- Never show raw internal identifiers such as CNX_* or fs-id*.\n"
            "- Keep mathematical meaning unchanged.\n"
            "- Fix LaTeX delimiter/syntax issues conservatively so KaTeX can render.\n"
            "- Preserve tables and non-prose content structure.\n"
            "- Do not remove definitions, examples, theorems, proofs, checkpoints, or exercises.\n"
            "- Do not expose full worked solutions; convert Solution parts to hints without final answers.\n\n"
            "Mandatory self-check before final output:\n"
            "1) Every heading from source exists in output in same order.\n"
            "2) Every [[BLOCK_xxxx]] appears exactly once in same order.\n"
            "3) No placeholder text other than valid [[BLOCK_xxxx]] tokens.\n\n"
            f"Section title: {title}\n"
            f"Path: {' -> '.join(breadcrumb)}\n"
            f"Source markdown:\n\n{section_markdown}"
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
                asyncio.to_thread(
                    self._chat_completion,
                    system_prompt,
                    user_prompt,
                    temperature,
                ),
                timeout=timeout,
            )
        except Exception as exc:
            raise RuntimeError(f"Lesson LLM step failed ({err_label}): {exc}") from exc
        text = self._clean_markdown_output(str(raw or ""))
        if not text:
            raise RuntimeError(f"Lesson LLM step returned empty output ({err_label}).")
        return text

    def _chat_completion(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        if self.llm_client is None:
            return ""
        completion = self.llm_client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return str(completion.choices[0].message.content or "").strip()

    @staticmethod
    def _clean_markdown_output(raw: str) -> str:
        text = str(raw or "").strip()
        text = re.sub(r"^```(?:markdown)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
        return text

    def _protect_blocks(self, markdown: str) -> tuple[str, list[ProtectedBlock]]:
        lines = markdown.splitlines(keepends=True)
        blocks: list[ProtectedBlock] = []
        out: list[str] = []
        idx = 0

        def add_block(content: str, kind: str) -> None:
            token = f"[[BLOCK_{len(blocks) + 1:04d}]]"
            blocks.append(ProtectedBlock(token=token, content=content, kind=kind))
            out.append(f"{token}\n")

        while idx < len(lines):
            line = lines[idx]
            if _CODE_FENCE_RE.match(line):
                j = idx + 1
                while j < len(lines):
                    if _CODE_FENCE_RE.match(lines[j]):
                        j += 1
                        break
                    j += 1
                add_block("".join(lines[idx:j]), "code_fence")
                idx = j
                continue

            if "$$" in line:
                if line.count("$$") >= 2:
                    add_block(line, "display_math")
                    idx += 1
                    continue
                j = idx + 1
                while j < len(lines):
                    if "$$" in lines[j]:
                        j += 1
                        break
                    j += 1
                add_block("".join(lines[idx:j]), "display_math")
                idx = j
                continue

            if r"\[" in line:
                if r"\]" in line:
                    add_block(line, "display_math")
                    idx += 1
                    continue
                j = idx + 1
                while j < len(lines):
                    if r"\]" in lines[j]:
                        j += 1
                        break
                    j += 1
                add_block("".join(lines[idx:j]), "display_math")
                idx = j
                continue

            env_match = _BEGIN_ENV_RE.search(line)
            if env_match:
                env_name = env_match.group(1)
                end_pattern = re.compile(_END_ENV_TEMPLATE % re.escape(env_name))
                if end_pattern.search(line):
                    add_block(line, "latex_env")
                    idx += 1
                    continue
                j = idx + 1
                while j < len(lines):
                    if end_pattern.search(lines[j]):
                        j += 1
                        break
                    j += 1
                add_block("".join(lines[idx:j]), "latex_env")
                idx = j
                continue

            if self._is_table_start(lines, idx):
                j = idx + 1
                while j < len(lines) and self._looks_like_table_line(lines[j]):
                    j += 1
                add_block("".join(lines[idx:j]), "table")
                idx = j
                continue

            if _MARKDOWN_IMAGE_RE.search(line) or _HTML_IMAGE_RE.search(line):
                j = idx + 1
                if j < len(lines) and _FIGURE_CAPTION_RE.match(lines[j].strip()):
                    j += 1
                add_block("".join(lines[idx:j]), "image")
                idx = j
                continue

            out.append(line)
            idx += 1

        return "".join(out).strip(), blocks

    @staticmethod
    def _is_table_start(lines: list[str], idx: int) -> bool:
        if idx + 1 >= len(lines):
            return False
        current = lines[idx].strip()
        next_line = lines[idx + 1].strip()
        if "|" not in current:
            return False
        return bool(_TABLE_SEPARATOR_RE.match(next_line))

    @staticmethod
    def _looks_like_table_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        return "|" in stripped

    @staticmethod
    def _restore_blocks(text: str, blocks: list[ProtectedBlock]) -> str:
        restored = text
        for block in blocks:
            restored = restored.replace(block.token, block.content.strip("\n"))
        return restored

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
