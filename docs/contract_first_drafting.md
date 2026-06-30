# Contract-First Drafting

Canonical design for draft job queueing. Every path that creates, requeues, or retries draft jobs must obey these invariants.

## Canonical workflow

1. Propose ChapterPacket
2. Approve ChapterPacket
3. Derive ScenePackets
4. Approve ScenePackets
5. Beats derive from approved ScenePackets (`derive_beats`)
6. **Draft Chapter** queues jobs stamped with `scene_packet_id`
7. Draft worker assembles context from approved ScenePacket

Beats are downstream artifacts, not the author-facing drafting gate.

## Draft job invariant

Every queued or running draft job must satisfy:

- `job.kind == "draft"`
- `job.chapter_id`, `job.beat_id`, `job.scene_packet_id` are non-null
- `Beat.id == job.beat_id`
- `ScenePacket.id == job.scene_packet_id`
- `Beat.chapter_id == job.chapter_id == ScenePacket.chapter_id`
- `Beat.scene_no == ScenePacket.scene_no`
- `Beat.status == approved`
- `ScenePacket.status == approved`
- `ScenePacket.stale_reason` is null

## Blocker reasons

Fixed strings used in `DraftQueueBlocker.reason`:

| Reason | Meaning |
|--------|---------|
| `no_approved_scene_packet` | No approved non-stale ScenePacket for chapter/scene |
| `scene_packet_stale` | Linked packet is stale |
| `scene_packet_not_approved` | Linked packet is not approved |
| `beat_not_approved` | Beat is not approved |
| `beat_scene_packet_mismatch` | Beat link does not match chapter/scene |
| `duplicate_approved_scene_packets` | Multiple approved packets for same scene |
| `already_queued` | Active draft job exists for this ScenePacket |
| `already_drafted` | Scene prose already exists (new draft only) |
| `missing_scene_no` | Cannot determine scene number |
| `legacy_job_unreconcilable` | Failed job cannot be reconciled to current contract |

## Shared result types

All queue/requeue endpoints return structured results from `dominion.workers.draft_queue`:

- `DraftScheduleResult`: `queued_job_ids`, `skipped`, `repaired_beats`
- `RequeueResult`: `requested`, `queued`, `skipped`

## Legacy path policy

Beat-first drafting (`approve_beats` → queue jobs, batch `auto_draft`) is **disabled**. Use `POST /chapters/{id}/draft` after ScenePacket approval.

## Requeue behavior

Requeue never clones failed jobs. It resolves the current approved ScenePacket and beat, creates a new job, and archives the failed row.

## Implementation map

| Module | Role |
|--------|------|
| `workers/draft_queue.py` | Resolver, scheduler, requeue logic |
| `workers/job_routing.py` | Job row construction (whitelist for `Job(kind=draft)`) |
| `workers/job_scheduler.py` | Thin wrappers delegating to `draft_queue` |
| `tools/audit_draft_jobs.py` | Read-only audit of malformed jobs |
| `tools/repair_draft_queue.py` | Apply beat-link repairs and cancel invalid jobs |

## Recovery

```bash
uv run python -m dominion.tools.audit_draft_jobs --chapter-id <id> --dry-run
uv run python -m dominion.tools.repair_draft_queue --chapter-id <id> --apply
```

Then use `POST /chapters/{id}/draft` — not blind `retry-failed`. To dismiss failed jobs without re-queueing, use `POST /jobs/clear-failed`.
