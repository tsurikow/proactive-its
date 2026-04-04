from __future__ import annotations

import re
from typing import Any

from pydantic_ai import Agent, NativeOutput

from app.platform.ai import llm_available, run_native_agent
from app.platform.chat.transport_models import QueryRewriteTransport
from app.platform.config import Settings, get_settings


QUERY_REWRITE_SYSTEM_PROMPT = (
    "Rewrite the user question for semantic retrieval only. "
    "Fix likely spelling mistakes, normalize wording, and expand obvious abbreviations. "
    "Keep math notation, formulas, variables, and symbols intact. "
    "Do not answer the question. Do not explain. "
    "rewritten_query must be exactly one concise retrieval query and nothing else."
)
QUERY_REWRITE_AGENT: Agent[Any, QueryRewriteTransport] = Agent(
    None,
    output_type=NativeOutput(QueryRewriteTransport, strict=True),
    system_prompt=QUERY_REWRITE_SYSTEM_PROMPT,
    retries=1,
    defer_model_check=True,
)


class QueryRewriteService:
    prompt_profile_version = "rag_query_rewrite_v1"
    rewrite_temperature = 0.0

    def __init__(
        self,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()

    async def rewrite(self, question: str) -> str | None:
        original = str(question or "").strip()
        if (
            not original
            or not self.settings.rag_query_rewrite_enabled
            or not llm_available(self.settings)
        ):
            return None
        payload = await run_native_agent(
            QUERY_REWRITE_AGENT,
            settings=self.settings,
            prompt=f"Original question:\n{original}",
            model_name=self.settings.rag_query_rewrite_model or self.settings.openrouter_model,
            temperature=self.rewrite_temperature,
            timeout_seconds=self.settings.rag_query_rewrite_timeout_seconds,
            extra_body={"provider": {"require_parameters": True}},
        )
        return self._sanitize_query(payload.rewritten_query, original)

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
