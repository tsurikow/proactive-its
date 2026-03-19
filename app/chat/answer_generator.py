from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_function
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.chat.utils import chunk_text, clean_chunk_text, extract_figure_links
from app.platform.config import Settings, get_settings
from app.platform.markdown_sanitize import strip_non_figure_links

INSUFFICIENT_EVIDENCE = (
    "I don't have enough evidence in the provided content. Please clarify or narrow your question."
)


class GenerationPayload(BaseModel):
    answer_md: str
    citations: list[str] = Field(default_factory=list)
    figure_links: list[str] = Field(default_factory=list)


class AnswerGenerator:
    prompt_profile_version = "rag_answer_prompt_v1"
    generation_temperature = 0.15
    structured_output_method = "json_schema"

    def __init__(self, settings: Settings | None = None, llm_client: AsyncOpenAI | None = None):
        self.settings = settings or get_settings()
        if not self.settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for answer generation.")
        self._client = llm_client or AsyncOpenAI(
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
        )
        schema = convert_to_openai_function(GenerationPayload, strict=True)
        self._response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": str(schema["name"]),
                "strict": bool(schema.get("strict", True)),
                "schema": dict(schema["parameters"]),
            },
        }

    async def generate(
        self,
        *,
        question: str,
        chunks: list[dict[str, Any]],
        mode: str,
    ) -> tuple[str, list[str], bool]:
        if not chunks:
            return INSUFFICIENT_EVIDENCE, [], False

        figure_links = extract_figure_links(chunks)
        context_block, source_map = self._build_context_block(chunks)
        prompt_text = self._build_grounded_user_prompt(
            question=question,
            mode=mode,
            context_block=context_block,
            figure_links=figure_links,
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
    ) -> GenerationPayload:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a grounded tutor. Answer the user question using only provided evidence. "
                    "Never invent facts. Keep notation mathematically valid. "
                    "Do not include internal anchors or non-figure links."
                ),
            },
            {"role": "user", "content": prompt_text},
        ]
        try:
            completion = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self.settings.openrouter_model,
                    messages=messages,
                    temperature=self.generation_temperature,
                    timeout=self.settings.rag_generation_timeout_seconds,
                    response_format=self._response_format,
                ),
                timeout=self.settings.rag_generation_timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError(f"{error_prefix} LLM generation request failed.") from exc
        message = completion.choices[0].message if completion.choices else None
        if message is None:
            raise RuntimeError(f"{error_prefix} LLM returned no message choices.")
        payload_text = self._extract_message_text(message.content)
        if not payload_text:
            raise RuntimeError(f"{error_prefix} LLM returned empty structured content.")
        try:
            return GenerationPayload.model_validate(json.loads(payload_text))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"{error_prefix} LLM returned invalid structured JSON.") from exc

    @staticmethod
    def _extract_message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
                    parts.append(str(item["text"]))
                continue
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts).strip()

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
        max_chars_per_chunk: int = 1100,
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
            text = self._prompt_excerpt(chunk_text(chunk), max_chars=max_chars_per_chunk)
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
        mode: str,
        context_block: str,
        figure_links: list[str],
    ) -> str:
        mode_hint = (
            "After answering, add one short checkpoint question."
            if mode == "quiz"
            else "Directly answer the learner question first. Then provide concise support."
        )
        figure_block = "\n".join(f"- {link}" for link in figure_links[:10]) if figure_links else "none"
        return (
            "Answer using only the retrieved evidence. Write like a strong tutor, not like a retriever dump.\n"
            f"{mode_hint}\n\n"
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
