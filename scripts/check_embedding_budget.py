from __future__ import annotations

import argparse
from dataclasses import dataclass

from app.content.chunker import split_markdown_into_chunks
from app.content.io import clean_markdown, iter_documents
from app.content.token_count import build_token_counter
from app.platform.config import get_settings


@dataclass
class ParentBudgetRow:
    doc_id: str
    title: str
    doc_type: str
    module_id: str | None
    section_id: str | None
    est_tokens: int


@dataclass
class ChildBudgetRow:
    chunk_id: str
    doc_id: str
    module_id: str | None
    section_id: str | None
    order_index: int
    est_tokens: int


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preflight check for embedding budget (parent sections + child chunks)"
    )
    parser.add_argument("--documents", required=True, help="Path to documents.jsonl")
    parser.add_argument("--max-tokens", type=int, default=None, help="Override embedding token budget")
    parser.add_argument("--top", type=int, default=20, help="Show top N longest documents")
    parser.add_argument(
        "--fail-on-overflow",
        action="store_true",
        help="Exit with non-zero code if any document exceeds max token budget",
    )
    args = parser.parse_args()

    settings = get_settings()
    max_tokens = args.max_tokens or int(settings.embedding_max_input_tokens)
    token_counter = build_token_counter()

    parent_rows: list[ParentBudgetRow] = []
    child_rows: list[ChildBudgetRow] = []
    docs_seen = 0
    docs_with_text = 0
    children_total = 0

    for doc in iter_documents(args.documents):
        docs_seen += 1
        cleaned = clean_markdown(doc.content_md)
        if not cleaned:
            continue
        chunks = split_markdown_into_chunks(
            doc_id=doc.doc_id,
            text=cleaned,
            target_tokens=settings.chunk_target_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            min_signal_chars=settings.min_text_chars_for_chunk,
        )
        if not chunks:
            continue

        docs_with_text += 1
        children_total += len(chunks)

        parent_rows.append(
            ParentBudgetRow(
                doc_id=doc.doc_id,
                title=doc.title,
                doc_type=doc.doc_type,
                module_id=doc.module_id,
                section_id=doc.section_id,
                est_tokens=token_counter.count(cleaned),
            )
        )
        resolved_section = doc.section_id or doc.module_id or doc.doc_id
        for chunk in chunks:
            child_rows.append(
                ChildBudgetRow(
                    chunk_id=chunk.chunk_id,
                    doc_id=doc.doc_id,
                    module_id=doc.module_id,
                    section_id=resolved_section,
                    order_index=chunk.order_index,
                    est_tokens=token_counter.count(chunk.content_text),
                )
            )

    parent_rows.sort(key=lambda item: item.est_tokens, reverse=True)
    child_rows.sort(key=lambda item: item.est_tokens, reverse=True)
    over_parents = [row for row in parent_rows if row.est_tokens > max_tokens]
    over_children = [row for row in child_rows if row.est_tokens > max_tokens]

    print(f"docs_seen={docs_seen}")
    print(f"docs_with_text={docs_with_text}")
    print(f"children_total={children_total}")
    print(f"max_tokens={max_tokens}")
    print(f"overflow_parents={len(over_parents)}")
    print(f"overflow_children={len(over_children)}")

    if parent_rows:
        biggest_parent = parent_rows[0]
        print(f"largest_parent={biggest_parent.doc_id} est_tokens={biggest_parent.est_tokens}")
    if child_rows:
        biggest_child = child_rows[0]
        print(f"largest_child={biggest_child.chunk_id} est_tokens={biggest_child.est_tokens}")

    top_n = max(1, args.top)
    print("\nTop parent docs:")
    for row in parent_rows[:top_n]:
        print(
            f"- est_tokens={row.est_tokens} doc_id={row.doc_id} "
            f"doc_type={row.doc_type} module_id={row.module_id} "
            f"section_id={row.section_id} title={row.title}"
        )

    print("\nTop child chunks:")
    for row in child_rows[:top_n]:
        print(
            f"- est_tokens={row.est_tokens} chunk_id={row.chunk_id} doc_id={row.doc_id} "
            f"module_id={row.module_id} section_id={row.section_id} order_index={row.order_index}"
        )

    if over_parents:
        print("\nParent docs over budget:")
        for row in over_parents[:top_n]:
            print(
                f"- est_tokens={row.est_tokens} doc_id={row.doc_id} "
                f"module_id={row.module_id} section_id={row.section_id} title={row.title}"
            )

    if over_children:
        print("\nChild chunks over budget:")
        for row in over_children[:top_n]:
            print(
                f"- est_tokens={row.est_tokens} chunk_id={row.chunk_id} "
                f"doc_id={row.doc_id} section_id={row.section_id} order_index={row.order_index}"
            )

    if args.fail_on_overflow and (over_parents or over_children):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
