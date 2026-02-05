from __future__ import annotations

from typing import Any


def build_tutor_prompt(question: str, chunks: list[dict[str, Any]], mode: str = "tutor") -> str:
    chunk_blocks = []
    for chunk in chunks:
        cid = chunk["chunk_id"]
        title = chunk.get("title", "Untitled")
        text = chunk.get("content_text", "")
        chunk_blocks.append(f"[CHUNK {cid}]\nTitle: {title}\n{text}")

    mode_line = (
        "Use short checkpoint questions before giving final answer."
        if mode == "quiz"
        else "Teach step-by-step with concise math-safe explanations."
    )

    return (
        "You are a calculus tutor. Answer using only the retrieved chunks. "
        "If information is insufficient, say so clearly. "
        f"{mode_line}\n\n"
        "Return valid JSON with this exact schema:\n"
        '{"answer_md": "...", "citations": ["chunk_id1", "chunk_id2"]}\n\n'
        "Choose citation ids only from provided chunks.\n"
        f"Learner question: {question}\n\n"
        "Retrieved chunks:\n"
        + "\n\n".join(chunk_blocks)
    )
