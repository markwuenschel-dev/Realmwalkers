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
    # ADR-0028 Slice 3b (Q11 tier-C, operator Re-author): two nullable columns on the existing
    # import_adoptions table (create_all won't ALTER it). force_author_token is the immutable operator
    # override + idempotency key (a partial UNIQUE index in _EXTRA_DDL enforces one adoption per token);
    # reauthor_of_adoption_id is the audit self-link (its NOT VALID self-FK is added in _EXTRA_DDL).
    "ALTER TABLE import_adoptions ADD COLUMN IF NOT EXISTS force_author_token UUID",
    "ALTER TABLE import_adoptions ADD COLUMN IF NOT EXISTS reauthor_of_adoption_id UUID",
    # ADR-0032 W0 (D2/D13): the retention-authority axis on the existing import_adoptions table. The
    # TEMPORARY server DEFAULT backfills every existing row to operator_independent (a conservative
    # compatibility choice — legacy rows lack trustworthy liveness provenance and auto-cancelling them
    # would be unsafe) AND stops pre-W1 code minting null-basis rows in the W0->W1 window. W1 drops this
    # default, makes the column NOT NULL, and CHECK-constrains it to the two permitted values.
    "ALTER TABLE import_adoptions ADD COLUMN IF NOT EXISTS liveness_basis TEXT DEFAULT 'operator_independent'",
    # ADR-0031 A1c: the durable Authorization Requirement axis (ceiling_gated | manual_grant), orthogonal
    # to authority_level (blast radius). The server DEFAULT backfills every pre-existing row to the safe
    # value; the `_BACKFILLS` UPDATE below then flips human_required rows to manual_grant. The default
    # stays permanently (the ORM sets the column on every insert; the default only covers a mid-deploy
    # window where an older writer is still running).
    "ALTER TABLE repair_tasks ADD COLUMN IF NOT EXISTS authorization_requirement TEXT DEFAULT 'ceiling_gated'",
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
    # ADR-0031 A1c: derive the Authorization Requirement for rows that predate the column. The mint-time
    # rule is `manual_grant` iff the blast radius is human_required (enums.is_manual_grant); everything
    # else is ceiling_gated, which the column's server DEFAULT already supplied. Self-gating on the
    # current value, so it is a no-op on every boot thereafter and never overwrites a deliberate
    # orthogonal manual_grant that a low-blast-radius task carries.
    """UPDATE repair_tasks SET authorization_requirement = 'manual_grant'
       WHERE authority_level = 'human_required' AND authorization_requirement IS DISTINCT FROM 'manual_grant'""",
    "UPDATE repair_tasks SET authorization_requirement = 'ceiling_gated' WHERE authorization_requirement IS NULL",
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
    # ADR-0028 Slice 3b (Q11 tier-C, operator Re-author): ImportAdoption → ImportAdoption self-FK on the
    # audit-lineage column, added NOT VALID so it enforces every future write while tolerating pre-existing
    # rows (mirrors fk_scene_packets_source_scene / fk_jobs_revision_request). Guarded by a catalog check
    # because Postgres has no ADD CONSTRAINT IF NOT EXISTS. reauthor_of_adoption_id was added above in
    # _COLUMN_ADDS; import_adoptions exists already (create_all).
    """DO $$ BEGIN
         IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_import_adoptions_reauthor_of') THEN
           ALTER TABLE import_adoptions ADD CONSTRAINT fk_import_adoptions_reauthor_of
             FOREIGN KEY (reauthor_of_adoption_id) REFERENCES import_adoptions (id) NOT VALID;
         END IF;
       END $$""",
    # The retry-idempotency invariant: at most ONE adoption per operator force_author_token. Partial
    # (WHERE force_author_token IS NOT NULL) so ordinary (non-force) adoptions are unconstrained, while a
    # concurrent retried Re-author with the same token collides at the DB rather than racing a second
    # spend — this is what makes endpoint idempotency race-safe, not just check-then-insert.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_import_adoptions_force_author_token
       ON import_adoptions (force_author_token)
       WHERE force_author_token IS NOT NULL""",
    # ADR-0032 W0 (D3): the "≤1 active adoption per chapter" invariant as a DATABASE structural guarantee,
    # not merely the lock+read convention start_contract_adoption relies on. Partial over the ACTIVE states
    # so terminal adoptions (contract_proposed/failed/invalidated/cancelled) never permanently block a
    # later valid adoption. The guarded preflight (_preflight_no_duplicate_active_adoptions, run before
    # this block in apply_lightweight_migrations) refuses to build this over a DB that already violates the
    # invariant — failing closed rather than silently discarding a conflicting row.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_import_adoptions_active_chapter
       ON import_adoptions (chapter_id)
       WHERE status IN ('awaiting_start', 'queued', 'running')""",
    # ADR-0032 W1 (D13): tighten liveness_basis now that the adoption seam is the sole writer and always
    # supplies an explicit basis. Drop W0's TEMPORARY default (backfill already ran in _COLUMN_ADDS above),
    # make the column NOT NULL, and CHECK-constrain it to the two permitted values. Safe here because the
    # single-instance deploy recreates the container (no pre-W0 writer overlaps the tightened schema) and
    # the null preflight (run before this block) refuses to proceed on any residual NULL basis. Each ALTER
    # is idempotent; the CHECK is guarded by a catalog lookup (Postgres has no ADD CONSTRAINT IF NOT EXISTS).
    "ALTER TABLE import_adoptions ALTER COLUMN liveness_basis DROP DEFAULT",
    "ALTER TABLE import_adoptions ALTER COLUMN liveness_basis SET NOT NULL",
    """DO $$ BEGIN
         IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_import_adoptions_liveness_basis') THEN
           ALTER TABLE import_adoptions ADD CONSTRAINT ck_import_adoptions_liveness_basis
             CHECK (liveness_basis IN ('request_bound', 'operator_independent'));
         END IF;
       END $$""",
    # ADR-0028 Slice 3b (Q11 tier-C): the DEPLOYED import_adoptions.chapter_packet_id FK was created NO
    # ACTION, so a re-author's packet REPLACE (which DELETEs the chapter's prior packet) is blocked while a
    # prior contract_proposed adoption still links it. Re-create the SAME-NAMED constraint with ON DELETE
    # SET NULL, so a superseded adoption's link nulls instead of blocking the delete. A fresh create_all DB
    # already emits SET NULL from the model (confdeltype='n' → this no-ops); a persistent prod DB has the
    # old NO ACTION ('a') and gets the ALTER exactly once. Guarded on confdeltype so re-runs are a true
    # no-op (no per-boot DROP+re-validate scan). ScenePacket.chapter_packet_id and the ChapterSequence FK
    # are DELIBERATELY left restrictive — only adoption-owned, evidence-only packets become deletable here.
    """DO $$
       DECLARE deltype "char";
       BEGIN
         SELECT confdeltype INTO deltype FROM pg_constraint
           WHERE conname = 'import_adoptions_chapter_packet_id_fkey';
         IF deltype IS NOT NULL AND deltype <> 'n'::"char" THEN
           ALTER TABLE import_adoptions DROP CONSTRAINT import_adoptions_chapter_packet_id_fkey;
           ALTER TABLE import_adoptions ADD CONSTRAINT import_adoptions_chapter_packet_id_fkey
             FOREIGN KEY (chapter_packet_id) REFERENCES chapter_packets (id) ON DELETE SET NULL;
         END IF;
       END $$""",
    # ADR-0031 A1c: close the Authorization Requirement axis. NOT NULL + a CHECK pinning the column to the
    # two AuthorizationRequirement members, so an unrecognized requirement can never reach the gate (which
    # fails closed on one anyway — this is the structural half of that guarantee). The backfill above ran
    # in _BACKFILLS, and `_preflight_repair_authorization_axis` (run before this block) refuses to proceed
    # if any row's retired boolean disagrees with the requirement the gate now derives.
    "ALTER TABLE repair_tasks ALTER COLUMN authorization_requirement SET NOT NULL",
    """DO $$ BEGIN
         IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_repair_tasks_authorization_requirement') THEN
           ALTER TABLE repair_tasks ADD CONSTRAINT ck_repair_tasks_authorization_requirement
             CHECK (authorization_requirement IN ('ceiling_gated', 'manual_grant'));
         END IF;
       END $$""",
    # ADR-0031 A1c: drop the retired boolean. `requires_human_approval` was a MUTABLE column standing in
    # for the authorization requirement; it is now a derived read-only projection on the ORM
    # (`RepairTask.requires_human_approval`) computed from authorization_requirement + authority_level, so
    # the physical column has no writer and no reader. Dropping it is what stops it drifting back into a
    # second source of truth. Gated by the preflight above: the drop only runs over data where the stored
    # boolean and the derived value agree everywhere.
    "ALTER TABLE repair_tasks DROP COLUMN IF EXISTS requires_human_approval",
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


class DuplicateActiveAdoptionError(RuntimeError):
    """Raised (fail-closed) when the ADR-0032 W0 preflight finds more than one ACTIVE adoption for a
    chapter, so `uq_import_adoptions_active_chapter` cannot be built without silently discarding a row.
    The whole migration transaction aborts and rolls back; an operator must resolve the conflict by
    hand (the message carries the offending chapter + each conflicting adoption's identity)."""


async def _preflight_no_duplicate_active_adoptions(conn: AsyncConnection) -> None:
    """ADR-0032 W0 (D13): BEFORE building `uq_import_adoptions_active_chapter`, refuse to proceed if the
    "≤1 active adoption per chapter" invariant is ALREADY violated on disk. Unlike `_dedup_character_state`
    (which merges), this FAILS CLOSED — it deletes nothing and picks no winner, because an adoption is
    durable, leased, spend-bearing work and auto-choosing a survivor could strand or double-bill real
    output. It raises with an operator report (chapter_id + each conflicting adoption's
    id/status/liveness_basis/timestamps) so the conflict is resolved by a human. A no-op on a clean DB;
    runs on every boot before `_EXTRA_DDL` creates the index."""
    dup_chapters = (
        await conn.execute(
            text(
                "SELECT chapter_id, count(*) AS n FROM import_adoptions "
                "WHERE status IN ('awaiting_start', 'queued', 'running') "
                "GROUP BY chapter_id HAVING count(*) > 1 "
                "ORDER BY chapter_id"
            )
        )
    ).all()
    if not dup_chapters:
        return

    lines: list[str] = []
    for chapter_id, n in dup_chapters:
        lines.append(f"  chapter_id={chapter_id}: {n} active adoptions")
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, status, liveness_basis, created_at, updated_at FROM import_adoptions "
                        "WHERE chapter_id = :c AND status IN ('awaiting_start', 'queued', 'running') "
                        "ORDER BY created_at, id"
                    ),
                    {"c": chapter_id},
                )
            )
            .mappings()
            .all()
        )
        for r in rows:
            lines.append(
                f"      id={r['id']} status={r['status']} liveness_basis={r['liveness_basis']} "
                f"created_at={r['created_at']} updated_at={r['updated_at']}"
            )
    raise DuplicateActiveAdoptionError(
        "ADR-0032 W0 uniqueness preflight FAILED CLOSED: import_adoptions already violates "
        "'≤1 active adoption per chapter', so uq_import_adoptions_active_chapter cannot be built without "
        "discarding data. NOTHING was changed. Resolve these conflicts by hand (cancel/terminate the "
        "wrong adoptions), then reboot:\n" + "\n".join(lines)
    )


class NullLivenessBasisError(RuntimeError):
    """Raised (fail-closed) when the ADR-0032 W1 preflight finds import_adoptions rows with a NULL
    liveness_basis just before the column is tightened to NOT NULL. W0's temporary default should have
    backfilled every row; a residual NULL means a writer bypassed the seam, so the migration aborts with a
    count rather than letting the raw `SET NOT NULL` fail cryptically."""


async def _preflight_no_null_liveness_basis(conn: AsyncConnection) -> None:
    """ADR-0032 W1 (D13): BEFORE the `SET NOT NULL` / CHECK in `_EXTRA_DDL`, refuse to proceed if any
    import_adoptions row still has a NULL liveness_basis (W0's `_COLUMN_ADDS` default backfilled existing
    rows above, so this should be zero). Fails closed with a count; a no-op on a clean DB."""
    n = (await conn.execute(text("SELECT count(*) FROM import_adoptions WHERE liveness_basis IS NULL"))).scalar_one()
    if n:
        raise NullLivenessBasisError(
            f"ADR-0032 W1 preflight FAILED CLOSED: {n} import_adoptions row(s) have a NULL liveness_basis, "
            "so the column cannot be tightened to NOT NULL. W0's temporary default should have backfilled "
            "them — a residual NULL means the seam was bypassed. Backfill these rows by hand, then reboot."
        )


class AuthorizationAxisDriftError(RuntimeError):
    """Raised (fail-closed) when the ADR-0031 A1c preflight finds `repair_tasks` rows whose retired
    `requires_human_approval` boolean disagrees with the value the derived projection now computes. The
    boolean was only ever written from `authority_level` at mint, so a disagreement means some path wrote
    it independently — dropping the column would then silently change that task's gate."""


async def _preflight_repair_authorization_axis(conn: AsyncConnection) -> None:
    """ADR-0031 A1c: BEFORE `_EXTRA_DDL` drops `repair_tasks.requires_human_approval`, prove the drop is
    lossless on THIS database. The derived projection is
    `authority_level NOT IN (span_only, scene_local, scene_structural) OR authorization_requirement <>
    'ceiling_gated'` (see `shared/authorization.requires_explicit_authorization`); every row's stored
    boolean must already equal it. A no-op once the column is gone, and on a fresh create_all DB (which
    never had it). This is the only check standing between the ADR's "the boolean was always derived"
    claim and real deployed rows — D12 forbids inspecting prod ahead of time, so it runs at migration."""
    exists = (
        await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'repair_tasks' AND column_name = 'requires_human_approval'"
            )
        )
    ).first()
    if exists is None:
        return
    rows = (
        await conn.execute(
            text(
                """SELECT authority_level, authorization_requirement, requires_human_approval, count(*) AS n
                     FROM repair_tasks
                    WHERE requires_human_approval IS DISTINCT FROM (
                            authority_level NOT IN ('span_only', 'scene_local', 'scene_structural')
                            OR authorization_requirement IS DISTINCT FROM 'ceiling_gated')
                    GROUP BY 1, 2, 3"""
            )
        )
    ).mappings()
    drift = list(rows)
    if not drift:
        return
    detail = "\n".join(
        f"      authority_level={r['authority_level']} requirement={r['authorization_requirement']} "
        f"requires_human_approval={r['requires_human_approval']} rows={r['n']}"
        for r in drift
    )
    raise AuthorizationAxisDriftError(
        "ADR-0031 A1c preflight FAILED CLOSED: repair_tasks.requires_human_approval disagrees with the "
        "derived Authorization Requirement projection on the rows below, so dropping the column would "
        "silently change how those tasks are gated. NOTHING was changed. Reconcile them by hand (set "
        "authority_level/authorization_requirement to match the intended gate), then reboot:\n" + detail
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
    # ADR-0032 W0 (D13): refuse to build uq_import_adoptions_active_chapter over a DB that already has
    # >1 active adoption per chapter — fail closed with an operator report rather than silently pick a
    # winner. MUST run before _EXTRA_DDL (which creates that index). Unlike the CHAR-UNIQ dedup above,
    # this repairs nothing; a raise here aborts (and rolls back) the whole migration transaction.
    await _preflight_no_duplicate_active_adoptions(conn)
    # ADR-0032 W1 (D13): refuse to tighten liveness_basis to NOT NULL over any residual NULL row (W0's
    # temp default backfilled existing rows in _COLUMN_ADDS above). MUST run before _EXTRA_DDL, which drops
    # the default and does SET NOT NULL / the value CHECK.
    await _preflight_no_null_liveness_basis(conn)
    # ADR-0031 A1c: refuse to DROP repair_tasks.requires_human_approval while any row's stored boolean
    # disagrees with the derived projection. MUST run before _EXTRA_DDL, which performs that drop.
    await _preflight_repair_authorization_axis(conn)
    for ddl in _EXTRA_DDL:
        await conn.execute(text(ddl))
    # Book-ownership invariant (ADR 0027): backfill book_id (chapter->run, reject conflicts), quarantine
    # ownerless live jobs, add NOT VALID book_id constraints (enforce all future writes), and promote to
    # physical NOT NULL once no NULL-book rows remain. Idempotent and self-healing on every boot.
    from dominion.shared.job_integrity import reconcile_job_ownership

    await reconcile_job_ownership(conn)
