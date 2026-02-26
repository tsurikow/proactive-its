from __future__ import annotations

import argparse

from app.ingest.indexer import IndexingService


def main() -> None:
    parser = argparse.ArgumentParser(description="Index documents.jsonl into Qdrant")
    parser.add_argument("--documents", required=True, help="Path to documents.jsonl")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    args = parser.parse_args()

    service = IndexingService()
    stats = service.index_jsonl(args.documents, recreate=args.recreate)
    print(
        "Indexed "
        f"docs={stats.docs_seen}, parents={stats.parents_indexed}, children={stats.children_indexed}"
    )


if __name__ == "__main__":
    main()
