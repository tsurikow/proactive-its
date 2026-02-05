from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from app.core.config import Settings, get_settings
from app.rag.prompt import build_tutor_prompt

JSON_RE = re.compile(r"\{.*\}", flags=re.DOTALL)


class TutorGenerator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: OpenAI | None = None
        if self.settings.openrouter_api_key:
            self._client = OpenAI(
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url,
            )

    def generate(
        self,
        question: str,
        chunks: list[dict[str, Any]],
        mode: str = "tutor",
    ) -> tuple[str, list[str]]:
        if not chunks:
            return (
                "I could not find enough context to answer. Please narrow your question to a specific section.",
                [],
            )

        if self._client is None:
            return self._fallback(question, chunks)

        prompt = build_tutor_prompt(question=question, chunks=chunks, mode=mode)
        completion = self._client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": "You are a grounded calculus tutor."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        raw = completion.choices[0].message.content or ""
        return self._parse_response(raw, chunks)

    def _fallback(self, question: str, chunks: list[dict[str, Any]]) -> tuple[str, list[str]]:
        top = chunks[0]
        answer = (
            f"I used the best matching section: **{top.get('title', 'source')}**. "
            "Here is a grounded summary:\n\n"
            f"{top.get('content_text', '')[:800]}\n\n"
            "If you want, ask a narrower follow-up and I can explain it step-by-step."
        )
        return answer, [top["chunk_id"]]

    def _parse_response(
        self,
        raw: str,
        chunks: list[dict[str, Any]],
    ) -> tuple[str, list[str]]:
        valid_ids = {c["chunk_id"] for c in chunks}
        candidate = raw.strip()
        if not candidate.startswith("{"):
            match = JSON_RE.search(candidate)
            if match:
                candidate = match.group(0)

        try:
            payload = json.loads(candidate)
            answer = str(payload.get("answer_md", "")).strip()
            citations = [str(c) for c in payload.get("citations", []) if str(c) in valid_ids]
            if not answer:
                answer = raw.strip()
            return answer, citations
        except json.JSONDecodeError:
            ids = [cid for cid in valid_ids if cid in raw]
            return raw.strip(), ids
