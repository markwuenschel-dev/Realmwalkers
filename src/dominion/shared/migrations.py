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

import json
from typing import Any

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
    # Activity drawer (Atelier redesign): terminal timestamp for per-job durations + recent feed.
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ",
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
    # SceneFidelity provenance (ADR 0021): generic nullable columns on the existing critiques table so a
    # fidelity Critique can point at its source DraftAttempt + immutable report Artifact and carry a
    # finding_signature for idempotent projection. Soft links (no FK), matching Issue.artifact_id.
    # created_at defaults to now() so new rows are ordered; pre-existing rows fill once at ALTER time.
    "ALTER TABLE critiques ADD COLUMN IF NOT EXISTS draft_attempt_id UUID",
    "ALTER TABLE critiques ADD COLUMN IF NOT EXISTS source_artifact_id UUID",
    "ALTER TABLE critiques ADD COLUMN IF NOT EXISTS finding_signature TEXT",
    "ALTER TABLE critiques ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()",
    # Retrieval provenance kept on the packet so the Desk can show what canon it was built from.
    "ALTER TABLE scene_packets ADD COLUMN IF NOT EXISTS sources JSONB",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS doc_path TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS heading_path TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS owner_topic TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS source_priority INTEGER",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS content_hash TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS embedding_model TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS embedding_version TEXT",
    # Workstream H (stale canon/ledger cleanup): provenance + lifecycle on each canon row. Added
    # WITHOUT a server DEFAULT so existing rows land NULL and are backfilled below by _BACKFILLS
    # (source is doc_path-derived, so a blanket DEFAULT would wrongly stamp repo rows 'manual').
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS source TEXT",
    "ALTER TABLE canon_entities ADD COLUMN IF NOT EXISTS status TEXT",
    # Per-run telemetry scoping: group a derive's calls so the panels can show one run, not a total.
    "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS run_id UUID",
    # Per-scene POV override: optional, null/blank inherits the chapter POV (Beat.pov).
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS pov TEXT",
    # LEDGER: one-shot guard so a beat's relative '+N' deltas commit to the CharacterState ledger exactly
    # once across scene revisions. DEFAULT FALSE for new rows; existing already-approved beats are marked
    # committed by _BACKFILLS below (they already applied their deltas).
    "ALTER TABLE beats ADD COLUMN IF NOT EXISTS deltas_committed BOOLEAN DEFAULT FALSE",
    # Per-call telemetry diagnostics (context budget breakdown, section name, fallback flags, …).
    "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS metadata JSONB",
    # Production attribution: soft link (no FK) tying a draft/repair call's spend to its ProductionRun,
    # so Telemetry can show cost per production run. Existing llm_calls table needs this ADD COLUMN;
    # create_all only provisions fresh DBs.
    "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS production_run_id UUID",
    "ALTER TABLE agent_ops_state ADD COLUMN IF NOT EXISTS globals_json JSONB",
    # Production driver scoping: tie draft jobs (and therefore timeline updates) to a ProductionRun.
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS production_run_id UUID",
    # Approve & apply for human-approval repair tasks: when the human explicitly approved execution.
    "ALTER TABLE repair_tasks ADD COLUMN IF NOT EXISTS human_approved_at TIMESTAMPTZ",
    # Renderer-neutral export foundation: chapters can group into a Part (the new `parts` table is
    # provisioned by create_all; only this FK column needs adding to the existing chapters table), and
    # books carry export/provenance metadata so the emitters stop hard-coding project identity. The
    # metadata columns are added WITHOUT a server DEFAULT so new books land NULL (no implicit Dominion
    # identity); pre-existing Dominion rows are backfilled by the timestamp-guarded UPDATEs below.
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS part_id UUID",
    "ALTER TABLE books ADD COLUMN IF NOT EXISTS series TEXT",
    "ALTER TABLE books ADD COLUMN IF NOT EXISTS book_no INTEGER",
    "ALTER TABLE books ADD COLUMN IF NOT EXISTS subtitle TEXT",
    # Structural export v2: the top grouping tier (Book → Volume → Part → Chapter). The new `volumes`
    # table is provisioned by create_all; parts gain an optional volume link + a label-word kind
    # (part|act). DEFAULT 'part' backfills existing rows so a pre-v2 part reads as an ordinary Part.
    "ALTER TABLE parts ADD COLUMN IF NOT EXISTS volume_id UUID",
    "ALTER TABLE parts ADD COLUMN IF NOT EXISTS kind TEXT DEFAULT 'part'",
    # Per-section-type front/back matter: the specific section (glossary/map/dramatis_personae/…) for a
    # front_matter|back_matter chapter. Nullable free text; ordinary chapters leave it NULL.
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS section_type TEXT",
    # Ordering/number decoupling: `position` is the sole reading-order sort key (see shared/chapter_order.py).
    # (Making `chapter_no` nullable is a column ALTER, not an ADD — it lives in _EXTRA_DDL below.)
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS position INTEGER",
    # Import adoption & durable revision requests (ADR 0028): a revision Job links to its durable
    # RevisionRequest. Feedback is NOT copied onto the Job — the context loader resolves it through this
    # link. The FK constraint (NOT VALID) is added in _EXTRA_DDL. New tables (import_adoptions,
    # import_scene_evidence, revision_requests) are provisioned by create_all.
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS revision_request_id UUID",
    # ADR 0028 Slice 3a′ (R1): the immutable snapshot of the exact prose an ImportSceneEvidence row was
    # extracted from — Scene.prose is the current manuscript (edited in place), so it cannot audit a past
    # evidence identity. The parent table DOES change, so this ADD is required even though create_all
    # provisions a fresh table complete: a prod table created before this column exists gets it here.
    # Nullable in the ALTER (safe on a populated table); the model is NOT NULL, and the layer is inert
    # (0 writers → 0 rows), so every real row sets it. New child table import_scene_evidence_chunks is
    # provisioned wholesale by create_all (its own UniqueConstraint + CASCADE FK ride along).
    "ALTER TABLE import_scene_evidence ADD COLUMN IF NOT EXISTS snapshot_prose TEXT",
    # ADR 0028 Slice 3b (import adoption engine): three nullable columns on tables that ALREADY exist in
    # prod (scene_packets, import_adoptions), so create_all won't add them — these ALTERs must.
    # scene_packets.source_scene_id (Q9): the imported Scene an adoption-derived packet is bound to — the
    # JOIN KEY the waiting RevisionRequest's resume uses. Its FK is added NOT VALID in _EXTRA_DDL below.
    "ALTER TABLE scene_packets ADD COLUMN IF NOT EXISTS source_scene_id UUID",
    # import_adoptions.seed_bindings (Q8): seed→scene lineage written once at ChapterPacket publish.
    "ALTER TABLE import_adoptions ADD COLUMN IF NOT EXISTS seed_bindings JSONB",
    # import_adoptions.author_input_fingerprint (Q11): tiered-idempotency tier B hash over consumed
    # evidence-shard ids + the canon-retrieval snapshot the author saw.
    "ALTER TABLE import_adoptions ADD COLUMN IF NOT EXISTS author_input_fingerprint TEXT",
)

# One-time backfills for freshly-added nullable columns. Each is gated on `IS NULL`, so it fills only
# rows that predate the column and is a no-op on every boot thereafter (new rows carry the ORM default).
_BACKFILLS: tuple[str, ...] = (
    # Workstream H: infer provenance for pre-existing canon rows from the doc_path heuristic ingest used
    # before a real `source` column existed (repo-ingested chunks carry a doc_path; hand-authored do not),
    # and mark every legacy row `active` so status-aware retrieval keeps returning them.
    """UPDATE canon_entities
       SET source = CASE WHEN doc_path IS NOT NULL THEN 'repo_ingested' ELSE 'manual' END
       WHERE source IS NULL""",
    "UPDATE canon_entities SET status = 'active' WHERE status IS NULL",
    # Severity unification: the issue pipeline's legacy "hard" is the packet contract's "block"
    # (one vocabulary: info|warn|repair|block). Idempotent via the WHERE clause; JSON snapshots
    # inside artifact bodies keep the old spelling forever, so readers tolerate both.
    "UPDATE issues SET severity = 'block' WHERE severity = 'hard'",
    "UPDATE critiques SET severity = 'block' WHERE severity = 'hard'",
    # LEDGER: a beat whose slot already has an APPROVED scene had its declared deltas committed (the buggy
    # code committed on first approval), so mark it committed — otherwise the first post-migration
    # re-approval would apply the delta once more. New/unapproved beats stay FALSE. Idempotent.
    """UPDATE beats SET deltas_committed = TRUE
       WHERE deltas_committed IS NOT TRUE
         AND EXISTS (
           SELECT 1 FROM scenes
           WHERE scenes.chapter_id = beats.chapter_id AND scenes.scene_no = beats.scene_no
             AND scenes.status = 'approved'
         )""",
    # Export-metadata backfill: stamp the Dominion identity onto books that predate the metadata columns,
    # WITHOUT stamping it onto books created afterward. The IS NULL gate alone can't do that here — new
    # books also carry NULL (no server default) and would be wrongly backfilled on the next boot. So the
    # guard is a FIXED created_at cutoff (the migration's authoring date): every existing book is older
    # than it and gets the Dominion values once; any book created after the cutoff never matches, so a
    # standalone/new-series book keeps its NULL identity. Idempotent (IS NULL fails once filled).
    """UPDATE books SET series = 'Dominion Realm'
       WHERE series IS NULL AND created_at < TIMESTAMPTZ '2026-07-07 00:00:00+00'""",
    """UPDATE books SET book_no = 1
       WHERE book_no IS NULL AND created_at < TIMESTAMPTZ '2026-07-07 00:00:00+00'""",
    # Reading-order backfill: derive `position` for every pre-`position` chapter with the SAME band
    # scheme as shared/chapter_order.chapter_position (front < prologue < chapters-by-number < epilogue <
    # back-matter), so legacy rows and app-written rows interleave correctly. Existing prologues (kind set,
    # chapter_no 0) leap ahead of chapter 1 exactly as before; plain chapters keep their numeric order.
    # Idempotent via IS NULL. Keep in sync with chapter_order.py if the bands ever change.
    """UPDATE chapters SET position = CASE kind
           WHEN 'front_matter' THEN 100000
           WHEN 'prologue' THEN 1100000
           WHEN 'epilogue' THEN 3100000
           WHEN 'back_matter' THEN 4100000
           ELSE 2000000 + COALESCE(chapter_no, 0)
       END
       WHERE position IS NULL""",
)

# Idempotent indexes for contract-first draft job dedupe (CHECK deferred — app layer enforces).
_EXTRA_DDL: tuple[str, ...] = (
    # CHAR-UNIQ: one CharacterState row per (book, character), CASE-INSENSITIVE — the single-row-per-key
    # invariant ledger.py's docstring asserts and every reader relies on. A functional unique index keeps
    # the stored display case while enforcing uniqueness on lower(character); readers/writers case-fold
    # their lookups. Any pre-existing duplicates are collapsed by `_dedup_character_state` (called before
    # this block) so the index can be built. This is a STRONGER invariant than CanonEntity has (that only
    # does case-insensitive lookups) — deliberately, since CharacterState must be single-valued.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_character_state_book_lower_char "
    "ON character_state (book_id, lower(character))",
    # `chapter_no` is DISPLAY-only and nullable now (a numberless prologue/epilogue/front-/back-matter needs
    # no number to collide on); reading order keys off `position`. Idempotent — a no-op once already nullable.
    "ALTER TABLE chapters ALTER COLUMN chapter_no DROP NOT NULL",
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
    "CREATE INDEX IF NOT EXISTS ix_chapters_part_id ON chapters (part_id)",
    "CREATE INDEX IF NOT EXISTS ix_parts_book_id ON parts (book_id)",
    "CREATE INDEX IF NOT EXISTS ix_parts_volume_id ON parts (volume_id)",
    "CREATE INDEX IF NOT EXISTS ix_volumes_book_id ON volumes (book_id)",
    "CREATE INDEX IF NOT EXISTS ix_beats_chapter_id ON beats (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_critiques_scene_id ON critiques (scene_id)",
    # SceneFidelity report-projection idempotency (ADR 0021): one fidelity Critique per (report finding).
    # Partial so it constrains ONLY fully-populated scene_fidelity rows — legacy critiques and NULLs are
    # untouched. A re-projection of the same finding from the same report Artifact collides here.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_scene_fidelity_critique_report_finding
       ON critiques (reviewer, source_artifact_id, finding_signature)
       WHERE reviewer = 'scene_fidelity'
         AND source_artifact_id IS NOT NULL
         AND finding_signature IS NOT NULL""",
    # SceneFidelity triage/UI load path: fidelity critiques for a DraftAttempt, newest first.
    """CREATE INDEX IF NOT EXISTS ix_scene_fidelity_critique_draft_chrono
       ON critiques (reviewer, draft_attempt_id, created_at)
       WHERE reviewer = 'scene_fidelity'""",
    "CREATE INDEX IF NOT EXISTS ix_scene_packets_chapter_no ON scene_packets (chapter_id, scene_no)",
    "CREATE INDEX IF NOT EXISTS ix_chapter_packets_chapter_id ON chapter_packets (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_annotations_scene_id ON annotations (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_suggestions_scene_id ON suggestions (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_approvals_scene_id ON approvals (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_edit_pairs_scene_id_version ON edit_pairs (scene_id, version)",
    "CREATE INDEX IF NOT EXISTS ix_draft_attempts_scene_id ON draft_attempts (scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)",
    # Book-ownership invariant (ADR 0027): the hot per-book queue queries filter (book_id, status).
    "CREATE INDEX IF NOT EXISTS ix_jobs_book_status ON jobs (book_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_run_id ON jobs (run_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_chapter_id ON jobs (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_production_run_id ON jobs (production_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_calls_chapter_id ON llm_calls (chapter_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_calls_book_id ON llm_calls (book_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_calls_run_id ON llm_calls (run_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_calls_production_run_id ON llm_calls (production_run_id)",
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
    # Import adoption & durable revision requests (ADR 0028). The new tables come from create_all; these
    # are the invariants + hot-path indexes create_all can't express.
    #
    # At most ONE active RevisionRequest per target scene (the "singular active request" contract): a
    # repeat revise must supersede, not race a second job to the same scene. Partial over active states.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_active_revision_request_per_scene
       ON revision_requests (target_scene_id)
       WHERE status IN ('awaiting_contract', 'queued', 'running')""",
    # Job → RevisionRequest FK, added NOT VALID so it enforces every future write immediately while
    # tolerating pre-existing rows (same pattern as ADR 0027's ownership constraints). Guarded by a
    # catalog check because Postgres has no ADD CONSTRAINT IF NOT EXISTS. revision_requests exists by now
    # (create_all ran first) and jobs.revision_request_id was added above in _COLUMN_ADDS.
    """DO $$ BEGIN
         IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_jobs_revision_request') THEN
           ALTER TABLE jobs ADD CONSTRAINT fk_jobs_revision_request
             FOREIGN KEY (revision_request_id) REFERENCES revision_requests (id) NOT VALID;
         END IF;
       END $$""",
    "CREATE INDEX IF NOT EXISTS ix_jobs_revision_request_id ON jobs (revision_request_id)",
    # Evidence identity/reuse: an ImportSceneEvidence shard is immutable and reused across adoptions,
    # keyed by exact source identity + extractor version. The unique index makes "already extracted?"
    # a lookup and blocks duplicate shards for the same identity.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_import_scene_evidence_identity
       ON import_scene_evidence (scene_id, scene_version, prose_hash, extractor_schema_version)""",
    # Adoption claim/scan path (FOR UPDATE SKIP LOCKED over claimable rows) + per-chapter lookup.
    "CREATE INDEX IF NOT EXISTS ix_import_adoptions_book_status ON import_adoptions (book_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_import_adoptions_chapter_id ON import_adoptions (chapter_id)",
    # RevisionRequest read model: a scene's active request, a chapter's active requests, book scope.
    "CREATE INDEX IF NOT EXISTS ix_revision_requests_target_scene ON revision_requests (target_scene_id)",
    "CREATE INDEX IF NOT EXISTS ix_revision_requests_chapter_status ON revision_requests (chapter_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_revision_requests_book_id ON revision_requests (book_id)",
    "CREATE INDEX IF NOT EXISTS ix_import_scene_evidence_chapter ON import_scene_evidence (chapter_id)",
    # ADR 0028 Slice 3b: ScenePacket → Scene FK on the adoption-binding column, added NOT VALID so it
    # enforces every future write immediately while tolerating pre-existing rows (mirrors
    # fk_jobs_revision_request exactly). Guarded by a catalog check because Postgres has no ADD CONSTRAINT
    # IF NOT EXISTS. scene_packets.source_scene_id was added above in _COLUMN_ADDS; scenes exists already.
    """DO $$ BEGIN
         IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_scene_packets_source_scene') THEN
           ALTER TABLE scene_packets ADD CONSTRAINT fk_scene_packets_source_scene
             FOREIGN KEY (source_scene_id) REFERENCES scenes (id) NOT VALID;
         END IF;
       END $$""",
    "CREATE INDEX IF NOT EXISTS ix_scene_packets_source_scene_id ON scene_packets (source_scene_id)",
)


def _as_stats(value: object) -> dict[str, Any]:
    """A CharacterState.stats_json value as a plain dict (asyncpg may hand back a dict or a JSON str)."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}
    return {}


def merge_character_state_group(rows: list[dict[str, Any]]) -> tuple[Any, dict[str, Any]]:
    """CHAR-UNIQ dedup rule (pure, so it is unit-testable). Given the rows of one
    (book_id, lower(character)) group — each a mapping with ``id``, ``stats_json``, ``as_of_scene_id`` —
    pick the survivor and compute its merged stats.

    Survivor: prefer a non-null ``as_of_scene_id`` (a real ledger row over a manual/NULL one), then the
    most stats keys, then the lowest ``id`` (deterministic). Merge: the survivor keeps its own scalar
    values but inherits any keys its siblings had and unions list values — so complementary stats
    survive. Scalar conflicts are resolved in the survivor's favour (it is the best-evidenced row);
    reconciling divergent numeric histories is deliberately out of scope for a boot migration.
    """
    ordered = sorted(
        rows,
        key=lambda r: (
            -(0 if r["as_of_scene_id"] is None else 1),
            -len(_as_stats(r["stats_json"])),
            str(r["id"]),
        ),
    )
    survivor = ordered[0]
    merged = _as_stats(survivor["stats_json"])
    for r in ordered[1:]:
        for key, value in _as_stats(r["stats_json"]).items():
            if key not in merged:
                merged[key] = value
            elif isinstance(merged[key], list) and isinstance(value, list):
                merged[key] = [*merged[key], *[x for x in value if x not in merged[key]]]
            # else: scalar conflict — the survivor keeps its value.
    return survivor["id"], merged


async def _dedup_character_state(conn: AsyncConnection) -> None:
    """CHAR-UNIQ: collapse any pre-existing (book_id, lower(character)) duplicate CharacterState rows into
    one, so the case-insensitive unique index can be built (a functional unique index cannot be created
    while duplicates exist). Idempotent and a no-op on a clean DB; runs on every boot before the index."""
    groups = (
        await conn.execute(
            text(
                "SELECT book_id, lower(character) AS lc FROM character_state "
                "GROUP BY book_id, lower(character) HAVING count(*) > 1"
            )
        )
    ).all()
    for book_id, lc in groups:
        rows = [
            dict(r)
            for r in (
                await conn.execute(
                    text(
                        "SELECT id, stats_json, as_of_scene_id FROM character_state "
                        "WHERE book_id = :b AND lower(character) = :lc"
                    ),
                    {"b": book_id, "lc": lc},
                )
            ).mappings()
        ]
        survivor_id, merged = merge_character_state_group(rows)
        await conn.execute(
            text("UPDATE character_state SET stats_json = CAST(:s AS jsonb) WHERE id = :id"),
            {"s": json.dumps(merged), "id": survivor_id},
        )
        await conn.execute(
            text("DELETE FROM character_state WHERE book_id = :b AND lower(character) = :lc AND id <> :id"),
            {"b": book_id, "lc": lc, "id": survivor_id},
        )


async def apply_lightweight_migrations(conn: AsyncConnection) -> None:
    """Run the idempotent column adds. Call inside an open (begin) connection."""
    for ddl in _COLUMN_ADDS:
        await conn.execute(text(ddl))
    for ddl in _BACKFILLS:
        await conn.execute(text(ddl))
    # CHAR-UNIQ: collapse (book_id, lower(character)) duplicates BEFORE _EXTRA_DDL builds the functional
    # unique index on them (it would fail if duplicates still existed). No-op on a clean DB.
    await _dedup_character_state(conn)
    for ddl in _EXTRA_DDL:
        await conn.execute(text(ddl))
    # Book-ownership invariant (ADR 0027): backfill book_id (chapter->run, reject conflicts), quarantine
    # ownerless live jobs, add NOT VALID book_id constraints (enforce all future writes), and promote to
    # physical NOT NULL once no NULL-book rows remain. Idempotent and self-healing on every boot.
    from dominion.shared.job_integrity import reconcile_job_ownership

    await reconcile_job_ownership(conn)
