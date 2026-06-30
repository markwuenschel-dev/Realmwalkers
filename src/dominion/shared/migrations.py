"""Lightweight, idempotent column adds for tables that predate a new ORM column.

No Alembic yet (the schema still moves; explicit migrations come when it stabilises). `create_all`
NEVER alters an existing table, so every new *nullable* column added to `models.py` must also be
listed here. Each statement is `ADD COLUMN IF NOT EXISTS`, so applying this is safe on a fresh DB and
on every boot/test alike.

Single source of truth: both the boot provisioner (`scripts/init_db.py`) and the test fixture
(`tests/conftest.py`) call `apply_lightweight_migrations`, so the test schema can't silently drift
from what production runs.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# Append a line here whenever you add a nullable column to an EXISTING table in models.py.
# (contract-first drafting Phase 2 links beats back to their packet scene_seed; the scene QA columns
# arrive in a later phase. Phase 1's brand-new `chapter_packets` table is provisioned by create_all.)
_COLUMN_ADDS: tuple[str, ...] = (
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS title TEXT",
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS scene_seed_id UUID",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_error TEXT",
    # Scene-packet contract system: new nullable links + per-scene fields. New tables
    # (scene_packets, draft_attempts) are provisioned by create_all.
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS scene_packet_id UUID",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS book_id UUID",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS chapter_id UUID",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS beat_id UUID",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS scene_packet_id UUID",
    "ALTER TABLE scenes ADD COLUMN IF NOT EXISTS scene_packet_id UUID",
    "ALTER TABLE scenes ADD COLUMN IF NOT EXISTS word_count INTEGER",
    "ALTER TABLE scenes ADD COLUMN IF NOT EXISTS length_status TEXT",
    "ALTER TABLE critiques ADD COLUMN IF NOT EXISTS scene_packet_id UUID",
    # Retrieval provenance kept on the packet so the Desk can show what canon it was built from.
    "ALTER TABLE scene_packets ADD COLUMN IF NOT EXISTS sources JSONB",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS doc_path TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS heading_path TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS owner_topic TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS source_priority INTEGER",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS content_hash TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS embedding_model TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS embedding_version TEXT",
    # Per-run telemetry scoping: group a derive's calls so the panels can show one run, not a total.
    "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS run_id UUID",
    # Per-scene POV override: optional, null/blank inherits the chapter POV (Beat.pov).
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS pov TEXT",
    # Per-call telemetry diagnostics (context budget breakdown, section name, fallback flags, …).
    "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS metadata JSONB",
    "ALTER TABLE agent_ops_state ADD COLUMN IF NOT EXISTS globals_json JSONB",
)

# Idempotent indexes for contract-first draft job dedupe (CHECK deferred — app layer enforces).
_EXTRA_DDL: tuple[str, ...] = (
    "ALTER TABLE jobs DROP CONSTRAINT IF EXISTS draft_jobs_require_scene_packet",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_active_draft_per_scene_packet
       ON jobs (scene_packet_id)
       WHERE kind = 'draft' AND status IN ('queued', 'running') AND target_scene_id IS NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_active_redraft_per_scene
       ON jobs (target_scene_id, scene_packet_id)
       WHERE kind = 'draft' AND status IN ('queued', 'running') AND target_scene_id IS NOT NULL""",
)


async def apply_lightweight_migrations(conn: AsyncConnection) -> None:
    """Run the idempotent column adds. Call inside an open (begin) connection."""
    for ddl in _COLUMN_ADDS:
        await conn.execute(text(ddl))
    for ddl in _EXTRA_DDL:
        await conn.execute(text(ddl))
