"""Create the pgvector extension and all tables. `python scripts/init_db.py`.

For now this is create_all from the ORM metadata. When the schema stabilizes, switch to Alembic
migrations (the schema will change a lot during the build, so explicit migrations come later).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from dominion.shared.db import engine
from dominion.shared.migrations import apply_lightweight_migrations
from dominion.shared.models import Base


async def init() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # create_all never ALTERs an existing table, so apply the idempotent column adds that bring an
        # older DB's tables up to the current ORM (single source of truth in shared/migrations.py).
        await apply_lightweight_migrations(conn)
    # Close the connection pool inside the loop so the process exits cleanly. Without this, lingering
    # asyncpg connections tied to the now-closing loop can hang interpreter exit — which, when this runs
    # as `init_db && hypercorn ...` at container boot, means the server never starts.
    await engine.dispose()
    print("db initialized: pgvector extension + all tables")


if __name__ == "__main__":
    asyncio.run(init())
