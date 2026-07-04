"""Scene-packet contract system: planner, length guard, derivation, beats, routing, context.

Pure helpers run without a DB; the rest hit real Postgres (skip if unreachable) with the LLM agents
mocked — mirrors tests/test_packet_pipeline.py (router/worker functions called directly with a session).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from dominion.api.routers import scene_packets as sp_router
from dominion.shared.config import settings
from dominion.shared.enums import ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    ChapterPacket,
    Job,
    ScenePacket,
)
from dominion.workers.context import ScenePacketRequiredError, assemble_context
from dominion.workers.scene_packet import approval_policy as sp_policy
from dominion.workers.scene_packet import author as sp_author
from dominion.workers.scene_packet import author_sections as sp_sections
from dominion.workers.scene_packet import derive as sp_derive
from dominion.workers.scene_packet import hash as sp_hash
from dominion.workers.scene_packet import qa as sp_qa


def _seed(seed_id: str, **kw: Any) -> dict[str, Any]:
    return {"seed_id": seed_id, "scene_no": kw.pop("scene_no", 1), **kw}


# --- source hash (staleness) ----------------------------------------------------------------------


def test_source_hash_folds_in_pov_override_without_disturbing_unoverridden():
    # A per-scene POV override re-opens an already-approved packet (changes the hash), but a scene with
    # NO override must keep the exact hash it had before scene_pov existed — so the upgrade doesn't
    # mass-invalidate every packet.
    base = dict(
        chapter_packet_id="cp", chapter_packet_body={"a": 1}, scene_seed={"s": 1}, chapter_word_budget={"target": 1000}
    )
    no_override = sp_hash.source_hash(**base)
    assert sp_hash.source_hash(**base, scene_pov=None) == no_override  # absent override == legacy hash
    assert sp_hash.source_hash(**base, scene_pov="") == no_override  # blank override == legacy hash
    assert sp_hash.source_hash(**base, scene_pov="Mara") != no_override  # setting one re-opens
    # distinct overrides hash distinctly; clearing reverts to the legacy hash (symmetric staleness)
    assert sp_hash.source_hash(**base, scene_pov="Mara") != sp_hash.source_hash(**base, scene_pov="Kell")


# --- DB helpers -----------------------------------------------------------------------------------


async def _seed_book_chapter(s) -> tuple[Book, Chapter]:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline="o")
    s.add(ch)
    await s.flush()
    return book, ch


async def _approved_chapter_packet(
    s, book, ch, seeds: list[dict[str, Any]], *, open_questions: dict[str, Any] | None = None
) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status="approved",
        confidence="green",
        body={
            "scene_seeds": seeds,
            "characters_present": ["Marcus", "Serra"],
            "characters_absent": ["Eriadne"],
            "canon_locks": ["the Realm is real"],
        },
        open_questions=open_questions if open_questions is not None else {"items": []},
    )
    s.add(cp)
    await s.flush()
    return cp


def _scene_body(word_budget: dict[str, Any] | None = None) -> dict[str, Any]:
    mole = "Serra is the mole"
    return {
        "scene_no": 1,
        "scene_job": "Marcus intercepts.",
        "scene_type": "combat",
        "word_budget": word_budget or {"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400},
        "known_before_scene": {"reader": ["the route"], "pov": ["the route"], "omniscient_author": [mole]},
        "learned_during_scene": {
            "reader_must_learn": ["the cohort is converging"],
            "reader_may_learn": [],
            "reader_may_infer_only": [],
        },
        "must_remain_hidden": {"reader": [mole], "pov": [], "all_surface_prose": []},
        "pov_permissions": {
            "may_notice": [],
            "may_infer": [],
            "must_not_know": [mole],
            "may_be_wrong_about": [],
        },
        "intentional_mysteries": [
            {"mystery": "who tipped the cohort", "desired_reader_effect": "unease", "do_not_explain": True},
        ],
        "reviewer_false_positive_traps": ["the missing tip source is intentional"],
        "required_beats": ["land the hit"],
        "forbidden_beats": ["Marcus uses his Aspect"],
        "exit_state": "both wounded",
        "phrases_to_avoid_echoing": ["reader must learn"],
        "reviewer_instructions": {"combat": ["track stamina"], "continuity": []},
    }


def _patch_prefix_primes_noop(monkeypatch) -> None:
    """derive_scene_packets primes shared chapter prefixes before scene work; noop unless a test
    exercises prefix behavior via a fake llm client."""

    async def noop_prime(*args, **kwargs):
        return None

    monkeypatch.setattr(sp_sections, "prime_author_shared_prefix", noop_prime)
    monkeypatch.setattr(sp_qa, "prime_qa_shared_prefix", noop_prime)


def _patch_scene_agents(monkeypatch, body, verdict=ScenePacketVerdict.APPROVE):
    async def fake_author(**kw):
        return dict(body)

    async def fake_qa(_b, **kw):
        return {"verdict": verdict, "residual_risks": [], "issues": []}

    # Fake BOTH author entry points so the derive's faked output is honored on either path (the
    # sectioned author is the default; the monolithic one is the fallback). Without the sectioned patch,
    # a derive on the default path would bypass the fake and hit the real API.
    monkeypatch.setattr(sp_author, "author_scene_packet", fake_author)
    monkeypatch.setattr(sp_sections, "author_scene_packet_sectioned", fake_author)
    monkeypatch.setattr(sp_qa, "qa_scene_packet", fake_qa)
    _patch_prefix_primes_noop(monkeypatch)


# --- derivation -----------------------------------------------------------------------------------


async def test_derive_creates_one_scene_packet_per_seed(db_factory, monkeypatch):
    _patch_scene_agents(monkeypatch, _scene_body())
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        sid = str(uuid.uuid4())
        cp = await _approved_chapter_packet(s, book, ch, [_seed(sid, scene_no=1, scene_type="combat")])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.flush()
        assert counts["created"] == 1
        rows = (await s.execute(select(ScenePacket).where(ScenePacket.chapter_id == ch.id))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == ScenePacketStatus.PROPOSED
        assert row.source_hash  # staleness anchor recorded
        # Planner budget folded in, reconciled against the chapter envelope (lane 3): a single
        # 1500-target scene in a 1500-word chapter scales down so hard_max fits the envelope
        # (previously target=1500/hard_max=2400 overflowed the stored hard_max_words=1500).
        assert row.body["word_budget"]["target"] == 1200
        assert row.body["word_budget"]["hard_max"] == 1500
        assert "known_before_scene" in row.body
        # Workstream-G advisory grade rides along with the QA output — never a gate.
        grade = (row.qa_warnings or {}).get("grade")
        assert grade and grade["artifact_type"] == "scene_packet" and grade["artifact_id"] == str(row.id)
        assert grade["blocking_issues"] == [] and grade["approved_for_next_stage"] is True
        assert set(grade["score"]) == {
            "overall",
            "canon_consistency",
            "reader_clarity",
            "scene_utility",
            "specificity",
            "non_contradiction",
            "actionability",
        }


async def test_derive_blocks_on_thin_body(db_factory, monkeypatch):
    async def thin(**kw):
        return {"scene_no": 1}  # missing required contract sections

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", False)  # exercise the monolithic path
    monkeypatch.setattr(sp_author, "author_scene_packet", thin)
    _patch_prefix_primes_noop(monkeypatch)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        sid = str(uuid.uuid4())
        cp = await _approved_chapter_packet(s, book, ch, [_seed(sid)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        assert counts["blocked"] == 1
        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.BLOCKED


async def test_derive_invalid_provenance_does_not_block(db_factory, monkeypatch):
    """The exact screenshot failure class, end-to-end: a valid ScenePacket body whose ONLY defect is
    invalid claim source ids (OUTLINE / UUID / out-of-range handle) must derive to a PROPOSED packet, run
    QA, normalize the ids to null, and surface a single collapsed provenance warning — never blocked."""
    body = {
        **_scene_body(),
        "claim_sources": [
            {"claim": "follows the approved outline", "source_id": "OUTLINE"},
            {"claim": "uses the seed beat", "source_id": "f332489e-faba-443f-9860-518ea790510b"},
            {"claim": "out-of-range handle", "source_id": "C7"},
        ],
    }
    _patch_scene_agents(monkeypatch, body, verdict=ScenePacketVerdict.APPROVE)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()), scene_no=1)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        assert counts["blocked"] == 0 and counts["created"] == 1
        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.PROPOSED
        assert row.qa_verdict == ScenePacketVerdict.APPROVE  # QA still ran
        # All invalid source ids were normalized to null on the persisted (draftable) body.
        assert all(c["source_id"] is None for c in row.body["claim_sources"])
        # One collapsed, warn-severity provenance violation is surfaced for the editor.
        violations = (row.qa_warnings or {}).get("violations", [])
        prov = [v for v in violations if v["kind"] == "provenance_normalized"]
        assert len(prov) == 1 and prov[0]["severity"] == "warn"
        # A warning-only packet is approvable.
        assert sp_policy.can_approve(row) is None


# --- beat derivation + approval gate (router) ------------------------------------------------------


async def test_approve_scene_packet_derives_beat(db_factory, monkeypatch):
    _patch_scene_agents(monkeypatch, _scene_body())
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        sid = str(uuid.uuid4())
        cp = await _approved_chapter_packet(s, book, ch, [_seed(sid, scene_type="combat")])
        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        sp = (await s.execute(select(ScenePacket))).scalars().one()

        await sp_router.approve_scene_packet(sp.id, s)
        beats = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        assert len(beats) == 1
        beat = beats[0]
        assert beat.scene_packet_id == sp.id
        # Mirrors word_budget.target — 1200 after lane-3 envelope reconciliation (see the derive test).
        assert beat.target_words == 1200
        assert beat.tags == ["combat"]
        assert beat.characters_present == ["Marcus", "Serra"]  # chapter cast minus absent
        # hard constraints are NOT copied onto the beat
        assert not hasattr(beat, "must_remain_hidden")
        assert "must_remain_hidden" not in (beat.beat_text or "")


async def test_derive_beats_prunes_legacy_unlinked_beats(db_factory, monkeypatch):
    # Legacy beat-first rows (scene_packet_id IS NULL) can never draft — the path is disabled — but
    # they counted as approved-yet-unlinked in draft readiness and held the Draft gate closed forever
    # (the observed 'beats_linked 4/8' dead-end). derive_beats must prune them, detaching historical
    # jobs, while sparing a legacy beat still referenced by an ACTIVE job.
    from dominion.shared.enums import JobKind, JobStatus
    from dominion.workers.scene_packet import beats as beats_mod

    _patch_scene_agents(monkeypatch, _scene_body())
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        sid = str(uuid.uuid4())
        cp = await _approved_chapter_packet(s, book, ch, [_seed(sid, scene_type="combat")])
        await sp_derive.derive_scene_packets(s, packet=cp)
        sp = (await s.execute(select(ScenePacket))).scalars().one()
        await sp_router.approve_scene_packet(sp.id, s)

        # Three legacy orphans: plain, one with a FAILED historical job, one held by a QUEUED job.
        plain = Beat(chapter_id=ch.id, scene_no=5, status="approved")
        with_failed_job = Beat(chapter_id=ch.id, scene_no=6, status="approved")
        with_active_job = Beat(chapter_id=ch.id, scene_no=7, status="approved")
        s.add_all([plain, with_failed_job, with_active_job])
        await s.flush()
        s.add_all(
            [
                Job(
                    kind=JobKind.DRAFT,
                    chapter_id=ch.id,
                    beat_id=with_failed_job.id,
                    scene_no=6,
                    token_budget=1000,
                    status=JobStatus.FAILED,
                ),
                Job(
                    kind=JobKind.DRAFT,
                    chapter_id=ch.id,
                    beat_id=with_active_job.id,
                    scene_no=7,
                    token_budget=1000,
                    status=JobStatus.QUEUED,
                ),
            ]
        )
        await s.commit()

        await beats_mod.derive_beats(s, chapter_id=ch.id)
        await s.commit()

        remaining = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        by_link = {b.scene_packet_id for b in remaining}
        assert sp.id in by_link  # the packet-linked beat survives
        legacy_left = [b for b in remaining if b.scene_packet_id is None]
        assert [b.scene_no for b in legacy_left] == [7]  # only the active-job holder survives
        # The historical FAILED job was detached, not deleted.
        failed_job = (await s.execute(select(Job).where(Job.status == JobStatus.FAILED))).scalars().one()
        assert failed_job.beat_id is None


async def _proposed_scene_packet(s, book, ch, cp, *, verdict, warnings) -> ScenePacket:
    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=cp.id,
        scene_seed_id=uuid.uuid4(),
        scene_no=1,
        status=ScenePacketStatus.PROPOSED,
        qa_verdict=verdict,
        qa_warnings=warnings,
        body=_scene_body(),
        source_hash="h",
    )
    s.add(sp)
    await s.flush()
    return sp


async def test_proposed_packet_with_advisory_qa_is_approvable(db_factory, monkeypatch):
    """QA is advisory: a PROPOSED packet with a revise_required verdict — or a legacy block-severity
    issue under approve_warn — is approvable (approve-with-repairs; repairs gate final export, not
    approval). Only a BLOCKED packet still 409s."""
    _patch_scene_agents(monkeypatch, _scene_body())  # approval derives the beat
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])

        revise = await _proposed_scene_packet(
            s,
            book,
            ch,
            cp,
            verdict=ScenePacketVerdict.REVISE_REQUIRED,
            warnings={"residual_risks": [], "issues": []},
        )
        await sp_router.approve_scene_packet(revise.id, s)
        assert (await s.get(ScenePacket, revise.id)).status == ScenePacketStatus.APPROVED

        # approve_warn verdict with a legacy block-severity issue (persisted before the LLM cap) — the
        # issue is repair-level under the new policy, so approval proceeds.
        warn_with_legacy_block_issue = await _proposed_scene_packet(
            s,
            book,
            ch,
            cp,
            verdict=ScenePacketVerdict.APPROVE_WARN,
            warnings={"residual_risks": [], "issues": [{"kind": "leak", "detail": "x", "severity": "block"}]},
        )
        await sp_router.approve_scene_packet(warn_with_legacy_block_issue.id, s)
        assert (await s.get(ScenePacket, warn_with_legacy_block_issue.id)).status == ScenePacketStatus.APPROVED


async def test_blocked_packet_cannot_be_approved(db_factory):
    """Behavior-freeze: a BLOCKED scene packet (deterministic/author/infrastructure gate) still 409s."""
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        blocked = await _proposed_scene_packet(
            s,
            book,
            ch,
            cp,
            verdict=ScenePacketVerdict.BLOCK_DRAFTING,
            warnings={"residual_risks": [], "blocked_reason": "author returned thin body", "blocker_source": "author"},
        )
        blocked.status = ScenePacketStatus.BLOCKED
        await s.flush()
        with pytest.raises(HTTPException) as exc:
            await sp_router.approve_scene_packet(blocked.id, s)
        assert exc.value.status_code == 409


async def test_proposed_packet_with_approve_warn_and_no_block_issue_approves(db_factory, monkeypatch):
    """The flip side: approve_warn with only info/warn issues is NOT gated — approval proceeds and
    derives the beat (so the gate doesn't over-block legitimate warn-level packets)."""
    _patch_scene_agents(monkeypatch, _scene_body())  # only needed for beat derivation inputs
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp = await _proposed_scene_packet(
            s,
            book,
            ch,
            cp,
            verdict=ScenePacketVerdict.APPROVE_WARN,
            warnings={"residual_risks": ["minor echo risk"], "issues": [{"detail": "soft", "severity": "warn"}]},
        )
        await sp_router.approve_scene_packet(sp.id, s)
        assert (await s.get(ScenePacket, sp.id)).status == ScenePacketStatus.APPROVED


# --- context assembly requires an approved scene packet --------------------------------------------


async def _approved_scene_packet_with_beat(s, book, ch, cp) -> tuple[ScenePacket, Beat]:
    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=cp.id,
        scene_seed_id=uuid.uuid4(),
        scene_no=1,
        status=ScenePacketStatus.APPROVED,
        qa_verdict=ScenePacketVerdict.APPROVE,
        body=_scene_body(),
        source_hash="h",
    )
    s.add(sp)
    await s.flush()
    beat = Beat(
        chapter_id=ch.id,
        scene_packet_id=sp.id,
        scene_no=1,
        status="approved",
        characters_present=["Marcus"],
        beat_text="Marcus intercepts.",
        target_words=1500,
    )
    s.add(beat)
    await s.flush()
    return sp, beat


async def test_assemble_context_loads_scene_contract_without_run_id(db_factory):
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp, beat = await _approved_scene_packet_with_beat(s, book, ch, cp)
        job = Job(
            kind="draft",
            status="queued",
            token_budget=40000,
            book_id=book.id,
            chapter_id=ch.id,
            beat_id=beat.id,
            scene_packet_id=sp.id,
        )
        s.add(job)
        await s.flush()

        ctx = await assemble_context(s, job)
        assert ctx.scene_packet_id == sp.id
        assert ctx.scene_contract and ctx.reader_state_contract and ctx.word_budget
        assert ctx.word_budget["target"] == 1500
        assert ctx.reviewer_contract["forbidden_beats"] == ["Marcus uses his Aspect"]
        # flat drafter contract carries scene reveal rules + chapter locks
        assert "Serra is the mole" in ctx.contract["forbidden_reveals"]
        assert ctx.contract["canon_locks"] == ["the Realm is real"]


async def test_assemble_context_fails_closed_on_unapproved_scene_packet(db_factory):
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp = ScenePacket(
            book_id=book.id,
            chapter_id=ch.id,
            chapter_packet_id=cp.id,
            scene_no=1,
            status=ScenePacketStatus.PROPOSED,
            body=_scene_body(),
            source_hash="h",
        )
        s.add(sp)
        await s.flush()
        beat = Beat(chapter_id=ch.id, scene_packet_id=sp.id, scene_no=1, status="approved")
        s.add(beat)
        await s.flush()
        job = Job(
            kind="draft",
            status="queued",
            token_budget=40000,
            book_id=book.id,
            chapter_id=ch.id,
            beat_id=beat.id,
            scene_packet_id=sp.id,
        )
        s.add(job)
        await s.flush()
        with pytest.raises(ScenePacketRequiredError):
            await assemble_context(s, job)


# --- staleness ------------------------------------------------------------------------------------


async def test_chapter_packet_edit_marks_scene_packets_stale(db_factory, monkeypatch):
    from dominion.api.routers import packets as packets_router
    from dominion.shared.schemas import PacketUpdateIn

    _patch_scene_agents(monkeypatch, _scene_body())
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        sid = str(uuid.uuid4())
        cp = await _approved_chapter_packet(s, book, ch, [_seed(sid, scene_type="combat")])
        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        sp = (await s.execute(select(ScenePacket))).scalars().one()
        await sp_router.approve_scene_packet(sp.id, s)
        assert (await s.get(ScenePacket, sp.id)).status == ScenePacketStatus.APPROVED

        # editing the chapter-packet body changes the derived inputs -> scene packet goes stale
        new_body = dict(cp.body)
        new_body["canon_locks"] = ["the Realm is real", "a NEW lock"]
        await packets_router.update_packet(ch.id, PacketUpdateIn(body=new_body), s)
        assert (await s.get(ScenePacket, sp.id)).status == ScenePacketStatus.STALE


def test_qa_prefix_excludes_derived_and_audit_sections():
    # Pure function, no DB. The QA prefix is the chapter packet as "macro authority" — the derived
    # `_surface_contract` projection and the embedded `qa` audit blob are NOT authority and roughly
    # double the prefix (observed: a 167KB body → ~35k prefix tokens, an outright PromptBudgetExceeded
    # on every manual QA re-run). Same exclusion rule as packet.qa.build_prompt.
    body = {
        "chapter_contract": {"spine": "the real thing"},
        "canon_locks": ["lock A"],
        "_surface_contract": {"huge": "derived duplicate"},
        "qa": {"grade": {"verdict": "fail"}},
    }
    prefix = sp_qa.build_prefix(body)
    assert prefix is not None
    assert "the real thing" in prefix
    assert "lock A" in prefix
    assert "derived duplicate" not in prefix
    assert '"grade"' not in prefix
