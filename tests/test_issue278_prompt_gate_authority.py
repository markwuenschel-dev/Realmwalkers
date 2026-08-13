"""#278 — the draft gate must not be decided by prompt-derived model behaviour.

**The failure this pins.** A human's RESOLVED AUTHOR RULING is rendered into both the scene-author and
the scene-QA prompt by `scene_packet/author.py:format_chapter_rulings`, carrying a suppression
instruction ("settled — do NOT re-litigate or contradict"), and `qa.py:_SYSTEM` repeats it ("do NOT flag
it as an unresolved open question"). `parse.py:parse_scene_qa` mapped the returned verdict straight
through; `derive.py` persisted it raw on `ScenePacket.qa_verdict`; `draft_readiness.py` counted the rows
whose verdict equalled `BLOCK_DRAFTING` and fed that count into `resolve_draft_gate` as gate #3. So one
sentence of prose was the *only* thing enforcing a drafting gate, and the failure direction was
PERMISSIVE: a model that ignored the sentence (or simply changed its mind) shipped unreviewed prose.

**What this suite pins.**
  * The headline oracle — a NON-COMPLIANT model. The deterministic layer must reach the same gate
    decision no matter what the model said, in both directions: an `APPROVE` verdict on a contract that
    contradicts itself must still refuse drafting, and a `BLOCK_DRAFTING` verdict on a clean contract
    must not.
  * Verdict invariance end-to-end: `compute_draft_readiness` over real rows returns an IDENTICAL
    `(can_draft, disabled_reason)` across all four verdicts when only the verdict changes.
  * Structural enforcement, not a remembered rule: every field of `DraftGateInputs` is deterministic —
    the gate has no model-derived input to re-wire.
  * The nomination channel survives: an LLM `BLOCK_DRAFTING` is not discarded, it is recorded as a
    `repair` issue (export-gating, never draft-gating) — `severity.normalize_llm_issue`'s existing house
    rule, extended to the sibling field that was missed.
  * ADR-0028 — the scene-packet PUT and the one-scene redraft take the chapter workflow lock.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.shared.chapter_lock import BUSY_DETAIL, acquire_chapter_workflow_lock
from dominion.shared.enums import BeatStatus, ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import Beat, Book, Chapter, ChapterPacket, ScenePacket
from dominion.workers.draft_readiness import DraftGateInputs, compute_draft_readiness

_HIDDEN_FACT = "Mara set the signal fire."
_FORBIDDEN_FACT = "Roth is the one who betrayed the garrison."


def _clean_body(scene_no: int = 1) -> dict:
    """A scene contract with no self-contradiction: the hidden fact lives ONLY in author-only fields."""
    return {
        "scene_no": scene_no,
        "known_before_scene": {"reader": ["The garrison fell."], "pov": [], "omniscient_author": [_HIDDEN_FACT]},
        "learned_during_scene": {"reader_must_learn": [], "reader_may_learn": [], "reader_may_infer_only": []},
        "must_remain_hidden": {"reader": [_HIDDEN_FACT], "pov": [], "all_surface_prose": []},
        "required_beats": ["The gate is barred."],
        "exit_state": "The watch is doubled.",
    }


def _self_contradictory_body(scene_no: int = 1) -> dict:
    """The SAME fact is declared hidden from the reader AND asserted as already reader-known.

    This is the first failure mode `qa.py:_SYSTEM` asks the model to catch ("a hidden/author-only fact
    that has ALSO leaked into a reader-known ... field"), on a field pair the deterministic
    `canon_contract_leak_blockers` never cross-checked — it read `learned_during_scene` only.
    """
    body = _clean_body(scene_no)
    body["known_before_scene"]["reader"] = ["The garrison fell.", _HIDDEN_FACT]
    return body


async def _seed_chapter(s, *, packet_body: dict, qa_verdict: str, seed_count: int = 1) -> Chapter:
    """One book + chapter + approved ChapterPacket + ONE approved, beat-linked ScenePacket.

    Every other draft gate is deliberately satisfied, so the only thing that can move `can_draft` is the
    scene contract's content or the model's verdict — which is exactly the axis under test.
    """
    book = Book(title="Issue278")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status="approved",
        confidence="green",
        body={
            "scene_seeds": [{"seed_id": str(uuid.uuid4()), "scene_no": n} for n in range(1, seed_count + 1)],
            "forbidden_reveals": [_FORBIDDEN_FACT],
        },
        open_questions={"items": [], "resolved": [{"q": "Who lit the fire?", "resolution": _HIDDEN_FACT}]},
    )
    s.add(cp)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
    s.add(beat)
    await s.flush()
    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=cp.id,
        scene_no=1,
        status=ScenePacketStatus.APPROVED,
        qa_verdict=qa_verdict,
        body=packet_body,
        source_hash="test",
    )
    s.add(sp)
    await s.flush()
    beat.scene_packet_id = sp.id
    await s.flush()
    return ch


# ---------- 1. the headline oracle: a NON-COMPLIANT model


async def test_model_approves_a_self_contradictory_contract_and_the_gate_still_refuses(db_factory):
    """THE test. The model was told the ruling is settled fact and returned APPROVE; the contract
    nonetheless asserts as reader-known the very fact it declares hidden from the reader. Drafting must
    refuse on the DETERMINISTIC finding, with no help from the verdict."""
    async with db_factory() as s:
        ch = await _seed_chapter(s, packet_body=_self_contradictory_body(), qa_verdict=ScenePacketVerdict.APPROVE.value)
        readiness = await compute_draft_readiness(s, ch.id)

    assert readiness.can_draft is False, "a self-contradictory contract must never be draftable"
    assert readiness.disabled_reason is not None
    assert _HIDDEN_FACT in readiness.disabled_reason
    kinds = [b.kind for b in readiness.structural_blockers]
    assert "canon_contract_leak" in kinds, kinds


async def test_model_blocks_a_clean_contract_and_the_gate_still_allows(db_factory):
    """The other direction: BLOCK_DRAFTING on a contract no deterministic check faults must NOT gate.
    A verdict is a nomination; the gate is decided from the contract, not from the model."""
    async with db_factory() as s:
        ch = await _seed_chapter(s, packet_body=_clean_body(), qa_verdict=ScenePacketVerdict.BLOCK_DRAFTING.value)
        readiness = await compute_draft_readiness(s, ch.id)

    assert readiness.can_draft is True, readiness.disabled_reason


# ---------- 2. full-path invariance: only the verdict changes, the gate does not


async def test_draft_gate_is_identical_across_every_verdict(db_factory):
    """End-to-end over real rows: flip ONLY `ScenePacket.qa_verdict` through all four values and the
    authoritative gate output must not move."""
    outcomes: dict[str, tuple[bool, str | None]] = {}
    for verdict in (v.value for v in ScenePacketVerdict):
        async with db_factory() as s:
            ch = await _seed_chapter(s, packet_body=_clean_body(), qa_verdict=verdict)
            readiness = await compute_draft_readiness(s, ch.id)
            outcomes[verdict] = (readiness.can_draft, readiness.disabled_reason)
    assert len(set(outcomes.values())) == 1, outcomes


async def test_verdict_invariance_holds_on_a_faulted_contract_too(db_factory):
    """Invariance is not "always draftable" — it is "the verdict is not an input". A contract that a
    deterministic check faults must refuse under every verdict, including APPROVE."""
    outcomes: dict[str, tuple[bool, str | None]] = {}
    for verdict in (v.value for v in ScenePacketVerdict):
        async with db_factory() as s:
            ch = await _seed_chapter(s, packet_body=_self_contradictory_body(), qa_verdict=verdict)
            readiness = await compute_draft_readiness(s, ch.id)
            outcomes[verdict] = (readiness.can_draft, readiness.disabled_reason)
    assert len(set(outcomes.values())) == 1, outcomes
    assert all(can is False for can, _ in outcomes.values())


# ---------- 3. structural enforcement: the gate has no model-derived input at all


def test_draft_gate_inputs_carry_no_model_derived_field():
    """`reviewer_trust.py:16-19`'s rationale, applied here: a rule each consumer has to remember is a
    rule one of them will forget. The gate cannot be re-wired to model output if model output is not a
    field of its input struct."""
    import dataclasses

    deterministic = {
        "chapter_packet_approved",
        "structural_blockers",
        "scene_packets_derived",
        "scene_packets_approved",
        "missing_scene_packets",
        "scene_packets_stale",
        "approved_beats",
        "unlinked_beats",
        "queue_blocker_messages",
        "active_draft_jobs",
        "draftable_scenes",
        "missing_scene_drafts",
        "provider_rate_limited",
    }
    actual = {f.name for f in dataclasses.fields(DraftGateInputs)}
    assert actual == deterministic, f"unexpected draft-gate input fields: {actual ^ deterministic}"


# ---------- 4. the nomination channel survives (a model may nominate, never mint)


def test_block_drafting_verdict_is_recorded_as_an_export_gating_repair_task():
    """Severing the gate must not discard the signal. `severity.py:8` already says only a deterministic
    check may emit `block`; `normalize_llm_issue` enforces that for issue severity. The same rule now
    covers the verdict: BLOCK_DRAFTING lands in the repair queue (blocks final export), never the draft
    gate."""
    from dominion.workers.scene_packet.parse import parse_scene_qa

    parsed = parse_scene_qa('{"verdict": "BLOCK_DRAFTING", "issues": [], "residual_risks": []}')
    assert parsed is not None
    nominations = [i for i in parsed["issues"] if i.get("kind") == "qa_verdict_nomination"]
    assert len(nominations) == 1, parsed["issues"]
    assert nominations[0]["severity"] == "repair"
    assert nominations[0]["blocks_drafting"] is False
    assert nominations[0]["blocks_final_export"] is True


def test_an_approve_verdict_nominates_nothing():
    from dominion.workers.scene_packet.parse import parse_scene_qa

    parsed = parse_scene_qa('{"verdict": "APPROVE", "issues": [], "residual_risks": []}')
    assert parsed is not None
    assert [i for i in parsed["issues"] if i.get("kind") == "qa_verdict_nomination"] == []


def test_a_model_supplied_block_issue_is_still_capped_and_not_duplicated():
    """The pre-existing cap keeps working, and a verdict nomination does not double-file a finding the
    model already reported."""
    from dominion.workers.scene_packet.parse import parse_scene_qa

    raw = (
        '{"verdict": "BLOCK_DRAFTING", "residual_risks": [], '
        '"issues": [{"kind": "canon_leak", "detail": "leaked", "severity": "block"}]}'
    )
    parsed = parse_scene_qa(raw)
    assert parsed is not None
    assert all(i["blocks_drafting"] is False for i in parsed["issues"])
    assert [i for i in parsed["issues"] if i.get("kind") == "qa_verdict_nomination"] == []


# ---------- 5. ADR-0028: the two unlocked chapter mutations


async def _seed_lockable(s) -> tuple[Chapter, ScenePacket, Beat]:
    ch = await _seed_chapter(s, packet_body=_clean_body(), qa_verdict=ScenePacketVerdict.APPROVE.value)
    sp = (await s.execute(select(ScenePacket).where(ScenePacket.chapter_id == ch.id))).scalars().one()
    beat = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().one()
    return ch, sp, beat


async def test_scene_packet_put_under_a_held_chapter_lock_is_409_and_writes_nothing(
    app_client, db_factory, monkeypatch
):
    """`update_scene_packet` rewrites the body, can flip the packet in/out of the APPROVED set, and
    reconciles the chapter's beats — an authority-changing chapter mutation that ran with no lock."""
    from dominion.api.routers import scene_packets as scene_packets_router

    monkeypatch.setattr(scene_packets_router, "LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        _, sp, _ = await _seed_lockable(s)
        await s.commit()
        packet_id, chapter_id = sp.id, sp.chapter_id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        resp = await app_client.put(f"/scene-packets/{packet_id}", json={"status": "proposed"})
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == BUSY_DETAIL

        async with db_factory() as probe:
            status = (await probe.execute(select(ScenePacket.status).where(ScenePacket.id == packet_id))).scalar_one()
        assert status == ScenePacketStatus.APPROVED  # the row was never reached

        await holder.rollback()

    retry = await app_client.put(f"/scene-packets/{packet_id}", json={"status": "proposed"})
    assert retry.status_code == 200, retry.text
    async with db_factory() as probe:
        assert (
            await probe.execute(select(ScenePacket.status).where(ScenePacket.id == packet_id))
        ).scalar_one() == ScenePacketStatus.PROPOSED


async def test_one_scene_redraft_under_a_held_chapter_lock_is_409_and_writes_nothing(
    app_client, db_factory, monkeypatch
):
    """`redraft_scene` re-approves a STALE contract and queues a draft Job — two authority-changing
    writes. `run_under_chapter_workflow` appeared ZERO times in routers/chapters.py."""
    from dominion.api.routers import chapters as chapters_router
    from dominion.workers import background_work

    monkeypatch.setattr(chapters_router, "LOCK_TIMEOUT_MS", 250)
    monkeypatch.setattr(background_work, "drain_queued_jobs", _noop_drain)
    async with db_factory() as s:
        ch, sp, _ = await _seed_lockable(s)
        sp.status = ScenePacketStatus.STALE
        sp.stale_reason = "scene deleted"
        await s.commit()
        packet_id, chapter_id = sp.id, ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        resp = await app_client.post(f"/chapters/{chapter_id}/scenes/1/redraft")
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == BUSY_DETAIL

        async with db_factory() as probe:
            status = (await probe.execute(select(ScenePacket.status).where(ScenePacket.id == packet_id))).scalar_one()
        assert status == ScenePacketStatus.STALE  # never re-approved

        await holder.rollback()

    retry = await app_client.post(f"/chapters/{chapter_id}/scenes/1/redraft")
    assert retry.status_code == 200, retry.text
    async with db_factory() as probe:
        assert (
            await probe.execute(select(ScenePacket.status).where(ScenePacket.id == packet_id))
        ).scalar_one() == ScenePacketStatus.APPROVED


async def _noop_drain() -> None:
    return None
