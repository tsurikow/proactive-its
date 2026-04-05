from __future__ import annotations

import asyncio

from app.api.dependencies import get_teacher_state_service
from app.platform.db import assert_no_stale_teacher_turn_columns, run_migrations
from app.platform.logging import configure_logging


async def _run() -> None:
    await run_migrations()
    await assert_no_stale_teacher_turn_columns()
    await get_teacher_state_service().bootstrap_default_template()


def main() -> None:
    configure_logging()
    asyncio.run(_run())
    print("Runtime bootstrap completed.")


if __name__ == "__main__":
    main()
