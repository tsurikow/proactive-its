from __future__ import annotations

import asyncio
import argparse

from app.state.db import init_db
from app.state.repository import StateRepository


async def _run(learner_id: str, timezone_name: str) -> None:
    await init_db()
    repo = StateRepository()
    await repo.ensure_learner(learner_id, timezone_name=timezone_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a learner record")
    parser.add_argument("learner_id")
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args()

    asyncio.run(_run(args.learner_id, args.timezone))
    print(f"Seeded learner '{args.learner_id}'")


if __name__ == "__main__":
    main()
