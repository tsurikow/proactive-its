from __future__ import annotations

import re
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.markdown_sanitize import strip_non_figure_links
from app.rag.utils import chunk_text, clean_chunk_text, extract_figure_links

INSUFFICIENT_EVIDENCE = (
    "I don't have enough evidence in the provided content. Please clarify or narrow your question."
)


class GenerationPayload(BaseModel):
    answer_md: str
    citations: list[str] = Field(default_factory=list)
    figure_links: list[str] = Field(default_factory=list)


class AnswerGenerator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for answer generation.")
        self._parser = JsonOutputParser(pydantic_object=GenerationPayload)
        self._llm = ChatOpenAI(
            model=self.settings.openrouter_model,
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
            temperature=0.05,
            timeout=self.settings.rag_generation_timeout_seconds,
            max_retries=1,
        )
        self._primary_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "You are a grounded tutor. Answer the user question using only provided evidence. "
                        "Never invent facts. Keep notation mathematically valid. "
                        "Do not include internal anchors or non-figure links."
                    ),
                ),
                ("user", "{prompt_text}"),
            ]
        )
        self._repair_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "You repair invalid JSON outputs without changing intended meaning."),
                ("user", "{prompt_text}"),
            ]
        )

    def generate(
        self,
        *,
        question: str,
        chunks: list[dict[str, Any]],
        mode: str,
    ) -> tuple[str, list[str]]:
        if not chunks:
            return INSUFFICIENT_EVIDENCE, []

        format_instructions = self._parser.get_format_instructions()
        figure_links = extract_figure_links(chunks)
        context_block = self._build_context_block(chunks)
        prompt_text = self._build_grounded_user_prompt(
            question=question,
            mode=mode,
            context_block=context_block,
            format_instructions=format_instructions,
            figure_links=figure_links,
        )
        raw = self._invoke(prompt=self._primary_prompt, prompt_text=prompt_text, error_prefix="Primary")
        payload = self._parse_payload(raw)
        if payload is None:
            repair_text = self._build_repair_user_prompt(
                previous_output=raw,
                format_instructions=format_instructions,
            )
            repaired = self._invoke(
                prompt=self._repair_prompt,
                prompt_text=repair_text,
                error_prefix="Repair",
            )
            payload = self._parse_payload(repaired)
        if payload is None:
            raise RuntimeError("LLM returned invalid JSON after repair attempt.")

        answer = self._sanitize_answer(payload.answer_md)
        if not answer:
            raise RuntimeError("LLM returned an empty answer.")
        if answer == INSUFFICIENT_EVIDENCE:
            return answer, []

        valid_ids = {str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")}
        citations = [cid for cid in payload.citations if cid in valid_ids]
        if not citations:
            top = next((str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")), "")
            if top:
                citations = [top]

        answer = self._append_figure_links(question, answer, payload.figure_links, figure_links)
        return answer, citations

    def _invoke(self, *, prompt: ChatPromptTemplate, prompt_text: str, error_prefix: str) -> str:
        chain = prompt | self._llm
        try:
            response = chain.invoke({"prompt_text": prompt_text})
        except Exception as exc:
            raise RuntimeError(f"{error_prefix} LLM generation request failed.") from exc
        content = getattr(response, "content", "")
        if isinstance(content, list):
            return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        return str(content or "")

    def _parse_payload(self, raw: str) -> GenerationPayload | None:
        if not raw.strip():
            return None
        try:
            parsed = self._parser.parse(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            try:
                return GenerationPayload.model_validate(parsed)
            except Exception:
                return None
        if isinstance(parsed, GenerationPayload):
            return parsed
        return None

    @staticmethod
    def _sanitize_answer(answer: str) -> str:
        text = str(answer or "").strip()
        text = re.sub(r"```(?:json|markdown)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return strip_non_figure_links(text)

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
    ) -> str:
        blocks: list[str] = []
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
            blocks.append(
                f"[CHUNK {chunk_id}]\n"
                f"Title: {title}\n"
                f"Type: {chunk_type}\n"
                f"Subsection: {subsection}\n"
                f"Text:\n{text}"
            )
        return "\n\n".join(blocks)

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
        format_instructions: str,
        figure_links: list[str],
    ) -> str:
        mode_hint = (
            "After answering, add one short checkpoint question."
            if mode == "quiz"
            else "Directly answer the learner question first. Then provide concise support."
        )
        figure_block = "\n".join(f"- {link}" for link in figure_links[:10]) if figure_links else "none"
        return (
            "Answer using only the retrieved evidence. Do not paste raw chunks verbatim.\n"
            f"{mode_hint}\n\n"
            f"{format_instructions}\n\n"
            "Rules:\n"
            "- Use only facts present in retrieved chunks.\n"
            "- Keep Markdown clean and readable.\n"
            "- Use only valid LaTeX delimiters ($...$ or $$...$$).\n"
            "- Do not output internal anchors/IDs or non-figure links.\n"
            "- citations must be chunk ids from retrieved chunks only.\n"
            "- If evidence is insufficient, return this exact answer_md:\n"
            f"  {INSUFFICIENT_EVIDENCE}\n"
            "- Include figure_links only when directly relevant.\n\n"
            f"Question:\n{question}\n\n"
            f"Available figure links:\n{figure_block}\n\n"
            "Retrieved chunks:\n"
            f"{context_block}"
        )

    @staticmethod
    def _build_repair_user_prompt(
        *,
        previous_output: str,
        format_instructions: str,
    ) -> str:
        return (
            "Your previous output was invalid.\n"
            "Return valid JSON that matches the required schema exactly.\n"
            f"{format_instructions}\n\n"
            "Constraints:\n"
            "- Preserve the original meaning.\n"
            "- citations must be an array of chunk ids.\n"
            f"- Use exact refusal text when evidence is missing: {INSUFFICIENT_EVIDENCE}\n"
            "- No markdown fences.\n\n"
            "Previous output:\n"
            f"{previous_output}"
        )
