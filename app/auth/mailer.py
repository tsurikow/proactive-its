from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.platform.config import Settings
from app.platform.logging import log_event

logger = logging.getLogger(__name__)


class AuthMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send_password_reset(self, *, email: str, reset_link: str) -> None:
        if self.settings.smtp_host and self.settings.smtp_from_email:
            await asyncio.to_thread(self._send_password_reset_sync, email, reset_link)
            return
        if self.settings.auth_dev_log_reset_links:
            log_event(logger, "auth.password_reset_link", email=email, reset_link=reset_link)

    def _send_password_reset_sync(self, email: str, reset_link: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from_email
        message["To"] = email
        message["Subject"] = "Reset your Proactive ITS password"
        message.set_content(
            "Use the link below to reset your password.\n\n"
            f"{reset_link}\n\n"
            "If you did not request this change, you can ignore this email."
        )

        if self.settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=self.settings.smtp_timeout_seconds,
            ) as smtp:
                self._login_if_needed(smtp)
                smtp.send_message(message)
            return

        with smtplib.SMTP(
            self.settings.smtp_host,
            self.settings.smtp_port,
            timeout=self.settings.smtp_timeout_seconds,
        ) as smtp:
            if self.settings.smtp_starttls:
                smtp.starttls()
            self._login_if_needed(smtp)
            smtp.send_message(message)

    def _login_if_needed(self, smtp: smtplib.SMTP) -> None:
        if self.settings.smtp_username and self.settings.smtp_password:
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)
