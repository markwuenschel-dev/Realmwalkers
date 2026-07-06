"""Repair draft queue tests — the read-only audit AND the mutating apply path.

`draft_audit.audit_chapter` classifies a chapter's beats/jobs (read-only); `repair_draft_queue._apply`
is the high-consequence half that actually rewrites the queue (relinks beats, cancels malformed jobs,
commits). `_apply` opens its own `SessionFactory()` session, so the apply tests bind that name to the
test factory (mirroring `test_background_work`) and assert against a *separately reopened* session, so a
missing commit would be caught. The apply path had zero coverage before these tests (candidate C7).
"""

from __future__ import annotations

from conftest import seed_scene_packet

from dominion.shared.enums import BeatStatus, JobKind, JobStatus
from dominion.shared.models import Beat, Book, Chapter, Job
from dominion.tools.draft_audit import audit_chapter
from dominion.tools.repair_draft_queue import _apply


async def _chapter(s):
    book = Book(title="Repair")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="A")
    s.add(ch)
    await s.flush()
    return ch


async def _repairable_beat(s, ch):
    """An APPROVED beat with no packet link + exactly one matching approved ScenePacket at its scene_no
    — the shape `audit_chapter` reports as repairable and `_apply` relinks. Returns (beat, scene_packet)."""
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
    s.add(beat)
    await s.flush()
    sp = await seed_scene_packet(s, chapter=ch, beat=None)
    beat.scene_packet_id = None
    return beat, sp


async def test_audit_reports_unlinked_beats(db_factory):
    async with db_factory() as s:
        ch = await _chapter(s)
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
        s.add(beat)
        await s.flush()
        report = await audit_chapter(s, ch.id)
        assert len(report.unlinked_beats) == 1


async def test_audit_finds_repairable_beat(db_factory):
    async with db_factory() as s:
        ch = await _chapter(s)
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
        s.add(beat)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=None)
        beat.scene_packet_id = None
        await s.flush()
        report = await audit_chapter(s, ch.id)
        assert len(report.repairable_beats) == 1
        assert report.repairable_beats[0]["scene_packet_id"] == str(sp.id)


# --- apply/mutation path (repair_draft_queue._apply) — candidate C7 -------------------------------


async def test_apply_relinks_repairable_beat_and_commits(db_factory, monkeypatch):
    # Rule: --apply must persist the repaired beat->packet link through the tool's OWN session/commit.
    monkeypatch.setattr("dominion.tools.repair_draft_queue.SessionFactory", db_factory)
    async with db_factory() as s:
        ch = await _chapter(s)
        beat, sp = await _repairable_beat(s, ch)
        await s.commit()
        ch_id, beat_id, sp_id = ch.id, beat.id, sp.id

    result = await _apply(ch_id, dry_run=False)

    assert result["dry_run"] is False
    assert [row["beat_id"] for row in result["repaired_beats"]] == [str(beat_id)]
    async with db_factory() as s:  # reopened session proves the mutation was committed, not just flushed
        assert (await s.get(Beat, beat_id)).scene_packet_id == sp_id


async def test_apply_cancels_malformed_draft_job(db_factory, monkeypatch):
    # Rule: --apply must fail-cancel a live DRAFT job with no scene_packet/beat and record why.
    monkeypatch.setattr("dominion.tools.repair_draft_queue.SessionFactory", db_factory)
    async with db_factory() as s:
        ch = await _chapter(s)
        job = Job(
            chapter_id=ch.id,
            kind=JobKind.DRAFT,
            status=JobStatus.QUEUED,
            scene_no=1,
            token_budget=1000,
            scene_packet_id=None,
            beat_id=None,
        )
        s.add(job)
        await s.commit()
        ch_id, job_id = ch.id, job.id

    result = await _apply(ch_id, dry_run=False)

    assert str(job_id) in result["cancelled_jobs"]
    async with db_factory() as s:
        job = await s.get(Job, job_id)
        assert job.status == JobStatus.FAILED
        assert "cancelled: invalid scene_packet_id" in (job.last_error or "")


async def test_apply_dry_run_reports_but_never_mutates(db_factory, monkeypatch):
    # Rule: --dry-run is the safety gate — it reports the same repairs but must leave the queue untouched.
    monkeypatch.setattr("dominion.tools.repair_draft_queue.SessionFactory", db_factory)
    async with db_factory() as s:
        ch = await _chapter(s)
        beat, _sp = await _repairable_beat(s, ch)
        await s.commit()
        ch_id, beat_id = ch.id, beat.id

    result = await _apply(ch_id, dry_run=True)

    assert result["dry_run"] is True
    assert len(result["would_repair_beats"]) == 1
    async with db_factory() as s:
        assert (await s.get(Beat, beat_id)).scene_packet_id is None  # dry-run must not touch the queue
