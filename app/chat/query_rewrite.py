from __future__ import annotations

import re

from openai import AsyncOpenAI

from app.platform.config import Settings, get_settings


class QueryRewriteService:
    prompt_profile_version = "rag_query_rewrite_v1"
    rewrite_temperature = 0.0

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        llm_client: AsyncOpenAI | None = None,
    ):
        self.settings = settings or get_settings()
        self.llm_client = llm_client

    async def rewrite(self, question: str) -> str | None:
        original = str(question or "").strip()
        if (
            not original
            or not self.settings.rag_query_rewrite_enabled
            or self.llm_client is None
        ):
            return None

        completion = await self.llm_client.chat.completions.create(
            model=self.settings.rag_query_rewrite_model or self.settings.openrouter_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Rewrite the user question for semantic retrieval only. "
                        "Fix likely spelling mistakes, normalize wording, and expand obvious abbreviations. "
                        "Keep math notation, formulas, variables, and symbols intact. "
                        "Do not answer the question. Do not explain. "
                        "Return exactly one concise retrieval query and nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Original question:\n{original}",
                },
            ],
            temperature=self.rewrite_temperature,
            timeout=self.settings.rag_query_rewrite_timeout_seconds,
        )
        content = str(completion.choices[0].message.content or "").strip()
        return self._sanitize_query(content, original)

    @staticmethod
    def _sanitize_query(candidate: str, original: str) -> str | None:
        text = str(candidate or "").strip()
        text = re.sub(r"```(?:text|markdown)?", "", text, flags=re.IGNORECASE).strip()
        text = text.replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip(" \"'")
        if not text:
            return None
        if text.lower() == original.lower():
            return None
        return text[:280].strip()
