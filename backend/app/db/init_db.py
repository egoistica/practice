from __future__ import annotations

import asyncio

from app.core.database import Base, engine
import app.models  # noqa: F401  # Ensure models are imported into metadata registry.


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(init_db())