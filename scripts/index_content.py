from __future__ import annotations

import argparse
import asyncio

from app.content.bootstrap import ContentBootstrapService


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Index documents.jsonl into Qdrant")
    parser.add_argument("--documents", required=True, help="Path to documents.jsonl")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    parser.add_argument("--force", action="store_true", help="Run indexing even when the fingerprint is unchanged")
    args = parser.parse_args()

    service = ContentBootstrapService()
    before = await service.current_status()
    stats = await service.index_runtime_content_async(
        recreate=args.recreate,
        documents_path=args.documents,
        force=args.force,
    )
    after = await service.current_status()
    if not stats.indexed:
        print(
            "Skipped indexing "
            f"reason={stats.skipped_reason or 'not_required'}, "
            f"sections_before={before.sections_count}, chunks_before={before.chunks_count}, "
            f"sections_after={after.sections_count}, chunks_after={after.chunks_count}"
        )
        return

    print(
        "Indexed "
        f"docs={stats.stats.docs_seen if stats.stats is not None else 0}, "
        f"parents={stats.stats.parents_indexed if stats.stats is not None else 0}, "
        f"children={stats.stats.children_indexed if stats.stats is not None else 0}, "
        f"sections_before={before.sections_count}, chunks_before={before.chunks_count}, "
        f"sections_after={after.sections_count}, chunks_after={after.chunks_count}"
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
