"""N1 red-capable coverage: ChapterSequence mutation endpoints must return a well-formed
ChapterSequenceOut on the success path, not a `MissingGreenlet` 500.

derive (re-derive UPDATE) / update (PUT) / approve / revise each mutate the sequence row, commit, then
serialize via `ChapterSequenceOut.model_validate(sequence)`. Without a post-commit
`session.refresh(sequence)` the server-side `updated_at` (onupdate) is expired at flush and the
serialize triggers a sync lazy-load on the async session → MissingGreenlet. Red on the unpatched
routers, green with the refresh. (align-scene-count already carries the reference refresh.)

See docs/plans/n1-greenlet-enrich-after-commit-contract.md (candidate N1).
"""

from __future__ import annotations

# Reuse the existing chapter seeder — no parallel harness.
from test_production_runs import _seed_chapter  # noqa: E402

from dominion.api.routers import production as production_router
from dominion.shared.schemas import ChapterSequenceOut, ChapterSequenceUpdateIn


def _assert_sequence_out(out) -> None:
    assert isinstance(out, ChapterSequenceOut)
    assert out.updated_at is not None  # the column that greenlet-500s when unrefreshed


async def test_derive_chapter_sequence_rederive_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, chapter, _scene, _packet = await _seed_chapter(s, seed_count=2, add_critique=False)
        # First derive INSERTs (server-defaults come back via RETURNING → safe even unpatched).
        await production_router.derive_chapter_sequence(chapter.id, s)
        # Force the UPDATE path: change the packet's declared target so re-derive rewrites the row.
        from sqlalchemy import select

        from dominion.shared.models import ChapterPacket

        packet = (await s.execute(select(ChapterPacket))).scalars().one()
        packet.body = {**packet.body, "target_scene_count": 9}
        await s.flush()
        out = await production_router.derive_chapter_sequence(chapter.id, s)  # re-derive → UPDATE
        _assert_sequence_out(out)


async def test_update_chapter_sequence_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, chapter, _scene, _packet = await _seed_chapter(s, seed_count=2, add_critique=False)
        seq = await production_router.derive_chapter_sequence(chapter.id, s)
        out = await production_router.update_chapter_sequence(
            seq.id, ChapterSequenceUpdateIn(body=seq.body, reason="n1 edit"), s
        )
        _assert_sequence_out(out)


async def test_approve_chapter_sequence_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        from dominion.shared.models import ChapterSequence

        _book, chapter, _scene, _packet = await _seed_chapter(s, seed_count=2, add_critique=False)
        seq = await production_router.derive_chapter_sequence(chapter.id, s)
        # derive already lands the sequence APPROVED (non-blocking QA), so approve would be a no-op
        # UPDATE (nothing dirtied → no onupdate expiry). Force a real transition so the endpoint's
        # UPDATE actually fires — the state a human hits when approving a still-PROPOSED sequence.
        db_seq = await s.get(ChapterSequence, seq.id)
        db_seq.status = "proposed"
        await s.commit()
        out = await production_router.approve_chapter_sequence(seq.id, s)
        _assert_sequence_out(out)
        assert out.status == "approved"


async def test_revise_chapter_sequence_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, chapter, _scene, _packet = await _seed_chapter(s, seed_count=2, add_critique=False)
        seq = await production_router.derive_chapter_sequence(chapter.id, s)
        out = await production_router.revise_chapter_sequence(
            seq.id, ChapterSequenceUpdateIn(body=seq.body, reason="n1 revise"), s
        )
        _assert_sequence_out(out)
