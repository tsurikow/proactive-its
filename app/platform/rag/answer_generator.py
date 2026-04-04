from __future__ import annotations

import re
from typing import Any

from pydantic_ai import Agent, NativeOutput

from app.platform.ai import llm_available, run_native_agent
from app.platform.chat.transport_models import GenerationPayloadTransport
from app.platform.chat.utils import chunk_text, clean_chunk_text, extract_figure_links
from app.platform.config import Settings, get_settings
from app.platform.markdown_sanitize import strip_non_figure_links
from app.teacher.artifacts.models import GroundingAnalysis

INSUFFICIENT_EVIDENCE = (
    "I don't have enough evidence in the provided content. Please clarify or narrow your question."
)
GROUNDED_ANSWER_SYSTEM_PROMPT = (
    "You are a grounded tutor. Answer the user question using only provided evidence. "
    "Never invent facts. Keep notation mathematically valid. "
    "Do not include internal anchors or non-figure links."
)
GROUNDED_ANSWER_AGENT: Agent[Any, GenerationPayloadTransport] = Agent(
    None,
    output_type=NativeOutput(GenerationPayloadTransport, strict=True),
    system_prompt=GROUNDED_ANSWER_SYSTEM_PROMPT,
    retries=1,
    defer_model_check=True,
)


class AnswerGenerator:
    prompt_profile_version = "rag_answer_prompt_v2"
    generation_temperature = 0.15

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not llm_available(self.settings):
            raise RuntimeError("OPENROUTER_API_KEY is required for answer generation.")

    async def generate(
        self,
        *,
        question: str,
        chunks: list[dict[str, Any]],
        teacher_surface_instruction: str | None = None,
        teacher_policy_brief: str | None = None,
        grounding_analysis: GroundingAnalysis | None = None,
    ) -> tuple[str, list[str], bool]:
        if not chunks:
            return INSUFFICIENT_EVIDENCE, [], False

        figure_links = extract_figure_links(chunks)
        context_block, source_map = self._build_context_block(
            chunks,
            max_chars_per_chunk=self.settings.rag_context_max_chars_per_chunk,
        )
        prompt_text = self._build_grounded_user_prompt(
            question=question,
            context_block=context_block,
            figure_links=figure_links,
            teacher_surface_instruction=teacher_surface_instruction,
            teacher_policy_brief=teacher_policy_brief,
            grounding_analysis=grounding_analysis,
        )
        payload = await self._invoke(prompt_text=prompt_text, error_prefix="Primary")

        answer = self._sanitize_answer(payload.answer_md)
        if not answer:
            raise RuntimeError("LLM returned an empty answer.")
        if answer == INSUFFICIENT_EVIDENCE:
            return answer, [], False

        citations = [source_map[label] for label in payload.citations if label in source_map]
        citation_fallback_used = False
        if not citations:
            top = next((str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")), "")
            if top:
                citations = [top]
                citation_fallback_used = True

        answer = self._append_figure_links(question, answer, payload.figure_links, figure_links)
        return answer, citations, citation_fallback_used

    async def _invoke(
        self,
        *,
        prompt_text: str,
        error_prefix: str,
    ) -> GenerationPayloadTransport:
        try:
            return await run_native_agent(
                GROUNDED_ANSWER_AGENT,
                settings=self.settings,
                prompt=prompt_text,
                model_name=self.settings.rag_answer_model or self.settings.openrouter_model,
                temperature=self.generation_temperature,
                timeout_seconds=self.settings.rag_generation_timeout_seconds,
                extra_body={"provider": {"require_parameters": True}},
            )
        except Exception as exc:
            raise RuntimeError(f"{error_prefix} LLM generation request failed.") from exc

    @staticmethod
    def _sanitize_answer(answer: str) -> str:
        text = str(answer or "").strip()
        text = text.replace("\x00", "")
        text = re.sub(r"```(?:json|markdown)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\.collection:[^\s)\]]+", "", text)
        text = re.sub(r"\b[a-z0-9_-]+::chunk\d+\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bfs-id[0-9a-z_-]*\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bSource\s*[Ss]\d+\b", "", text)
        text = re.sub(r"(?:(?<=\s)|^)[([]?\s*S\d+\s*[\])]?([,;:]?)", r"\1", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"\[\s*]", "", text)
        text = strip_non_figure_links(text)
        return text.strip()

    @staticmethod
    def _append_figure_links(
        question: str,
        answer: str,
        selected_links: list[str],
        valid_links: list[str],
    ) -> str:
        if not re.search(r"\b(figure|graph|plot|image|diagram)\b", question.lower()):
            return answer
        valid_set = set(valid_links)
        filtered = [link for link in selected_links if link in valid_set]
        if not filtered:
            filtered = valid_links[:2]
        if not filtered or any(link in answer for link in filtered):
            return answer
        md_links = ", ".join(f"[Figure]({link})" for link in filtered[:2])
        return f"{answer}\n\nRelevant figure links: {md_links}"

    def _build_context_block(
        self,
        chunks: list[dict[str, Any]],
        max_chars_per_chunk: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        blocks: list[str] = []
        source_map: dict[str, str] = {}
        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id", ""))
            if not chunk_id:
                continue
            title = str(chunk.get("title") or "Untitled")
            chunk_type = str(chunk.get("chunk_type") or "concept")
            subsection = str(chunk.get("subsection_title") or "")
            text = self._prompt_excerpt(chunk_text(chunk), max_chars=self.settings.rag_prompt_excerpt_max_chars)
            if not text:
                continue
            source_label = f"S{len(source_map) + 1}"
            source_map[source_label] = chunk_id
            blocks.append(
                f"[SOURCE {source_label}]\n"
                f"Title: {title}\n"
                f"Type: {chunk_type}\n"
                f"Subsection: {subsection}\n"
                f"Text:\n{text}"
            )
        return "\n\n".join(blocks), source_map

    @staticmethod
    def _prompt_excerpt(text: str, max_chars: int = 850) -> str:
        cleaned = clean_chunk_text(text)
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[:max_chars].rstrip() + "..."

    @staticmethod
    def _build_grounded_user_prompt(
        *,
        question: str,
        context_block: str,
        figure_links: list[str],
        teacher_surface_instruction: str | None = None,
        teacher_policy_brief: str | None = None,
        grounding_analysis: GroundingAnalysis | None = None,
    ) -> str:
        mode_hint = "Directly answer the learner question first. Then provide concise support."
        support_block = ""
        if teacher_surface_instruction:
            support_block = f"Teacher surface instruction: {teacher_surface_instruction}\n\n"
        if teacher_policy_brief:
            support_block += f"Teacher policy brief: {teacher_policy_brief}\n\n"
        if grounding_analysis is not None:
            support_block += (
                "Grounding analysis:\n"
                f"- answer_objective: {grounding_analysis.answer_objective}\n"
                f"- evidence_priorities: {grounding_analysis.evidence_priorities}\n"
                f"- explanation_route: {grounding_analysis.explanation_route}\n"
                f"- misconception_or_confusion_risks: {grounding_analysis.misconception_or_confusion_risks}\n"
                f"- support_emphasis: {grounding_analysis.support_emphasis}\n"
                f"- refusal_posture: {grounding_analysis.refusal_posture}\n"
                f"- citation_priorities: {grounding_analysis.citation_priorities}\n\n"
            )
        figure_block = "\n".join(f"- {link}" for link in figure_links[:10]) if figure_links else "none"
        return (
            "Answer using only the retrieved evidence. Write like a strong tutor, not like a retriever dump.\n"
            "answer_md is the learner-facing Markdown answer. citations must use only provided source labels. figure_links must be a subset of the available figure links.\n"
            f"{mode_hint}\n\n"
            f"{support_block}"
            "Rules:\n"
            "- Use only facts present in retrieved chunks.\n"
            "- Answer the learner directly first, then add concise explanation or one short example if helpful.\n"
            "- Do not quote source metadata, source labels, chunk ids, collection ids, or anchors in the answer text.\n"
            "- Do not paste raw chunks verbatim.\n"
            "- Keep Markdown clean and readable.\n"
            "- Use only valid LaTeX delimiters ($...$ or $$...$$).\n"
            "- Do not output internal anchors/IDs or non-figure links.\n"
            "- citations must be source labels from the retrieved sources only (for example: S1, S2).\n"
            "- If evidence is insufficient, return this exact answer_md:\n"
            f"  {INSUFFICIENT_EVIDENCE}\n"
            "- Include figure_links only when directly relevant.\n\n"
            f"Question:\n{question}\n\n"
            f"Available figure links:\n{figure_block}\n\n"
            "Retrieved sources:\n"
            f"{context_block}"
        )
