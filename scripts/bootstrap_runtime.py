from __future__ import annotations

import asyncio

from app.api.dependencies import get_tutor_service
from app.platform.db import run_migrations


async def _run() -> None:
    await run_migrations()
    await get_tutor_service().bootstrap_default_template()


def main() -> None:
    asyncio.run(_run())
    print("Runtime bootstrap completed.")


if __name__ == "__main__":
    main()
