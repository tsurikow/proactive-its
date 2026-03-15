from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.core.config import Settings, get_settings
from app.state.cache_repository import CacheRepository

logger = logging.getLogger(__name__)


class TutorMessageService:
    profile_version = "start_message_v1"

    def __init__(
        self,
        repo: CacheRepository,
        settings: Settings | None = None,
        llm_client: AsyncOpenAI | None = None,
    ):
        self.repo = repo
        self.settings = settings or get_settings()
        self.llm_client = llm_client
        self.timeout_seconds = 8.0

    async def get_start_message(
        self,
        *,
        learner_id: str,
        template_id: str,
        current_stage: dict[str, object] | None,
        previous_stage: dict[str, object] | None,
        next_stage: dict[str, object] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        default = self.default_start_message(
            current_stage=current_stage,
            previous_stage=previous_stage,
            next_stage=next_stage,
            completed_count=completed_count,
            total_stages=total_stages,
            plan_completed=plan_completed,
        )
        stage_index = int((current_stage or {}).get("stage_index", -1))
        if self.llm_client is None:
            return default

        cached = await self.repo.get_start_message_cache(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=stage_index,
            completed_count=completed_count,
            plan_completed=plan_completed,
            profile_version=self.profile_version,
        )
        if cached and cached.get("message"):
            return str(cached["message"])

        prompt = self._build_prompt(
            current_stage=current_stage,
            previous_stage=previous_stage,
            next_stage=next_stage,
            completed_count=completed_count,
            total_stages=total_stages,
            plan_completed=plan_completed,
        )
        try:
            raw = await self._chat_completion(prompt)
            message = str(raw or "").strip()
            if not message:
                return default
            await self.repo.upsert_start_message_cache(
                learner_id=learner_id,
                template_id=template_id,
                stage_index=stage_index,
                completed_count=completed_count,
                plan_completed=plan_completed,
                profile_version=self.profile_version,
                message=message,
            )
            return message
        except Exception as exc:
            logger.warning("Start message fallback for learner '%s': %s", learner_id, exc)
            return default

    @staticmethod
    def default_start_message(
        *,
        current_stage: dict[str, object] | None,
        previous_stage: dict[str, object] | None,
        next_stage: dict[str, object] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        if plan_completed:
            return (
                "Welcome back. You have completed the full study plan. "
                "We can review any section you want, revisit weak spots, or start a second pass from the beginning."
            )
        if not current_stage:
            return (
                "Welcome. I am your tutor. We will move through the book step by step and keep the pace clear and steady."
            )
        current_title = str(current_stage.get("title") or current_stage.get("section_id") or "current section")
        stage_number = int(current_stage.get("stage_index", 0)) + 1
        previous_title = str(previous_stage.get("title") or "").strip() if previous_stage else ""
        next_title = str(next_stage.get("title") or "").strip() if next_stage else ""
        if completed_count == 0:
            return (
                f"Welcome. I am your tutor, and we are starting with **{current_title}**. "
                "I will explain the section clearly, then we will build forward one stage at a time."
            )
        message = (
            f"Welcome back. Last time you finished **{previous_title or 'the previous stage'}**. "
            f"Now we continue with **{current_title}** (stage {stage_number} of {total_stages}, completed {completed_count}/{total_stages})."
        )
        if next_title:
            message += f" After this, we will move on to **{next_title}**."
        return message

    @staticmethod
    def _build_prompt(
        *,
        current_stage: dict[str, object] | None,
        previous_stage: dict[str, object] | None,
        next_stage: dict[str, object] | None,
        completed_count: int,
        total_stages: int,
        plan_completed: bool,
    ) -> str:
        return (
            "Write a short tutor greeting in Markdown.\n"
            "Sound like a real teacher, not a robotic status message.\n"
            "Keep it to 3-4 sentences.\n"
            "Mention where the learner stopped previously and what you will cover next.\n"
            "Do not mention internal ids. Do not list raw breadcrumbs.\n\n"
            f"Plan completed: {plan_completed}\n"
            f"Completed stages: {completed_count}/{total_stages}\n"
            f"Previous stage title: {str((previous_stage or {}).get('title') or '')}\n"
            f"Current stage title: {str((current_stage or {}).get('title') or '')}\n"
            f"Current stage number: {int((current_stage or {}).get('stage_index', -1)) + 1 if current_stage else 0}\n"
            f"Next stage title: {str((next_stage or {}).get('title') or '')}\n"
        )

    async def _chat_completion(self, user_prompt: str) -> str:
        if self.llm_client is None:
            return ""
        completion = await self.llm_client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a concise, supportive tutor writing short session greetings.",
                },
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            timeout=self.timeout_seconds,
        )
        return str(completion.choices[0].message.content or "").strip()
