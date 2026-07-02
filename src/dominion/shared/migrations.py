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
    # Narrative-structure Phase 1: reader-facing chapter kind (prologue/interlude/epilogue/front_matter/
    # back_matter; DEFAULT 'chapter' backfills existing rows) + an optional chapter-opening epigraph.
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS kind TEXT DEFAULT 'chapter'",
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS epigraph TEXT",
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
    # Hot-path indexes for the interaction/refresh queries. Postgres does not auto-index FKs, so every
    # filter/order below was a sequential scan — costly on the wide tables that carry prose/telemetry and
    # on the deployed app where each query is a network round-trip. All non-unique and idempotent,
    # matching the exact filters/orders the routers use on the click path.
    "CREATE INDEX IF NOT EXISTS ix_scenes_chapter_no_version ON scenes (chapter_id, scene_no, version)",
    "CREATE INDEX IF NOT EXISTS ix_scenes_status ON scenes (status)",
    "CREATE INDEX IF NOT EXISTS ix_chapters_book_id ON chapters (book_id)",
    "CREATE INDEX IF NOT EXISTS ix_beats_chapter_id ON beats (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_critiques_scene_id ON critiques (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_scene_packets_chapter_no ON scene_packets (chapter_id, scene_no)",
    "CREATE INDEX IF NOT EXISTS ix_chapter_packets_chapter_id ON chapter_packets (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_annotations_scene_id ON annotations (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_suggestions_scene_id ON suggestions (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_approvals_scene_id ON approvals (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_edit_pairs_scene_id_version ON edit_pairs (scene_id, version)",
    "CREATE INDEX IF NOT EXISTS ix_draft_attempts_scene_id ON draft_attempts (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_run_id ON jobs (run_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_chapter_id ON jobs (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_calls_chapter_id ON llm_calls (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_calls_book_id ON llm_calls (book_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_calls_run_id ON llm_calls (run_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_book_id ON runs (book_id)",
    "CREATE INDEX IF NOT EXISTS ix_chapter_sequences_chapter_id ON chapter_sequences (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_production_runs_chapter_id ON production_runs (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_runs_production_run_id ON agent_runs (production_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_artifacts_production_run_id ON artifacts (production_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_artifacts_type ON artifacts (artifact_type)",
    "CREATE INDEX IF NOT EXISTS ix_agent_events_production_run_id ON agent_events (production_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_issues_production_run_id ON issues (production_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_issues_chapter_scene ON issues (chapter_id, scene_no)",
    "CREATE INDEX IF NOT EXISTS ix_repair_tasks_production_run_id ON repair_tasks (production_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_repair_tasks_scene_id ON repair_tasks (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_repair_attempts_task_id ON repair_attempts (repair_task_id)",
    "CREATE INDEX IF NOT EXISTS ix_repair_verifications_attempt_id ON repair_verifications (repair_attempt_id)",
)


async def apply_lightweight_migrations(conn: AsyncConnection) -> None:
    """Run the idempotent column adds. Call inside an open (begin) connection."""
    for ddl in _COLUMN_ADDS:
        await conn.execute(text(ddl))
    for ddl in _EXTRA_DDL:
        await conn.execute(text(ddl))
