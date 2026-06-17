"""Create the pgvector extension and all tables. `python scripts/init_db.py`.

For now this is create_all from the ORM metadata. When the schema stabilizes, switch to Alembic
migrations (the schema will change a lot during the build, so explicit migrations come later).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from dominion.shared.db import engine
from dominion.shared.models import Base


async def init() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    print("db initialized: pgvector extension + all tables")


if __name__ == "__main__":
    asyncio.run(init())
