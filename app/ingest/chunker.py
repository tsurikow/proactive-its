from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_id: str
    content_text: str


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.25))


def _split_oversized_block(block: str, target_tokens: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", block.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        st = estimate_tokens(sentence)
        if st > target_tokens:
            if current:
                chunks.append(" ".join(current).strip())
                current = []
                current_tokens = 0
            words = sentence.split()
            step = max(1, int(target_tokens / 1.25))
            for i in range(0, len(words), step):
                piece = " ".join(words[i : i + step]).strip()
                if piece:
                    chunks.append(piece)
            continue
        if current and current_tokens + st > target_tokens:
            chunks.append(" ".join(current).strip())
            current = [sentence]
            current_tokens = st
        else:
            current.append(sentence)
            current_tokens += st
    if current:
        chunks.append(" ".join(current).strip())
    return [c for c in chunks if c]


def _text_signal_score(text: str) -> int:
    no_images = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", text)
    no_symbols = re.sub(r"[^A-Za-z0-9\s]", "", no_images)
    return len(no_symbols.strip())


def split_markdown_into_chunks(
    doc_id: str,
    text: str,
    target_tokens: int = 900,
    overlap_tokens: int = 150,
    min_signal_chars: int = 80,
) -> list[Chunk]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]

    grouped: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for block in blocks:
        block_tokens = estimate_tokens(block)
        if block_tokens > target_tokens:
            for sub in _split_oversized_block(block, target_tokens):
                if current:
                    grouped.append("\n\n".join(current).strip())
                    current = []
                    current_tokens = 0
                grouped.append(sub)
            continue

        if current and current_tokens + block_tokens > target_tokens:
            grouped.append("\n\n".join(current).strip())
            current = [block]
            current_tokens = block_tokens
        else:
            current.append(block)
            current_tokens += block_tokens

    if current:
        grouped.append("\n\n".join(current).strip())

    chunks: list[Chunk] = []
    for idx, chunk_text in enumerate(grouped):
        if _text_signal_score(chunk_text) < min_signal_chars:
            continue
        if chunks and overlap_tokens > 0:
            tail_words = chunks[-1].content_text.split()[-overlap_tokens:]
            if tail_words:
                chunk_text = " ".join(tail_words) + "\n\n" + chunk_text
        chunks.append(Chunk(chunk_id=f"{doc_id}::chunk{idx}", content_text=chunk_text))

    return chunks
