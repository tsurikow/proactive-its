from __future__ import annotations

from dataclasses import dataclass

import tiktoken


class TokenCounter:
    def count(self, text: str) -> int:
        raise NotImplementedError


@dataclass
class TiktokenCounter(TokenCounter):
    encoding_name: str = "cl100k_base"

    def __post_init__(self) -> None:
        self._encoding = tiktoken.get_encoding(self.encoding_name)

    def count(self, text: str) -> int:
        content = str(text or "")
        if not content:
            return 0
        return len(self._encoding.encode(content))


def build_token_counter() -> TokenCounter:
    return TiktokenCounter()

