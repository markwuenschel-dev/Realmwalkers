"""Import-adoption authoring: `propose_packet_from_evidence` + the evidence machinery (ADR 0028 Slice 3b,
Lane A2).

Two layers:
  * pure unit tests for `workers/packet/evidence.py` (M# bundle, ledger render, claim_precedence-gated
    conflict candidates, provenance resolution, precedence audit) — no DB / LLM / network;
  * DB oracles for `propose_packet_from_evidence` against real Postgres (skips if unreachable), with the
    author + QA agents mocked and the canon retriever injected, exercising the three required outcomes:
      - evidence -> a proposed ChapterPacket (maps to adoption `contract_proposed`);
      - a manuscript-vs-canon conflict -> still proposed, but an approval-blocking open question;
      - fail-closed authoring (thin packet / no evidence) -> a blocked ChapterPacket (adoption `failed`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.shared import claim_precedence
from dominion.shared.enums import ClaimSource, PacketStatus, PacketVerdict
from dominion.shared.manuscript_conflict import is_conflict_question, parse_conflict
from dominion.shared.models import Book, Chapter, ChapterPacket
from dominion.workers import packet as packet_pipeline
from dominion.workers.packet import approval_policy
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import evidence as evidence_mod
from dominion.workers.packet import qa as qa_mod


def _se(scene_no: int, ledger: dict, *, snapshot_prose_len: int | None = 200, **kw) -> evidence_mod.SceneEvidence:
    return evidence_mod.SceneEvidence(
        scene_id=kw.get("scene_id", uuid.uuid4()),
        scene_no=scene_no,
        scene_version=kw.get("scene_version", 1),
        prose_hash=kw.get("prose_hash", "a" * 64),
        ledger=ledger,
        snapshot_prose_len=snapshot_prose_len,
    )


# =============================== pure unit tests: evidence.py ======================================


def test_build_manuscript_handles_orders_by_scene_no():
    se2 = _se(2, {"events": [{"summary": "b"}]})
    se1 = _se(1, {"events": [{"summary": "a"}]})
    handles = evidence_mod.build_manuscript_handles([se2, se1])
    assert list(handles) == ["M1", "M2"]
    assert handles["M1"] is se1 and handles["M2"] is se2  # M1 is the earliest scene, not input order


def test_render_ledger_shows_populated_sections_and_skips_empty():
    ledger = {
        "pov": "Marcus",
        "setting": "the vault",
        "events": [{"summary": "the gate opens", "span": [0, 12]}],
        "withholds": [],
        "canon_conflicts": [],
    }
    text = evidence_mod.render_ledger(ledger)
    assert "pov: Marcus" in text
    assert "setting: the vault" in text
    assert "events:" in text and "the gate opens [0,12]" in text
    assert "withholds:" not in text  # empty section omitted


def test_render_ledger_empty_is_explicit():
    assert evidence_mod.render_ledger({}) == "(no evidence extracted for this scene)"


def test_candidate_conflicts_are_gated_by_claim_precedence():
    # The routing policy lives in claim_precedence, not here: candidates exist iff a manuscript x
    # locked-canon conflict needs a human open question (it always does — the pin below proves the wiring).
    assert claim_precedence.conflict_needs_open_question(ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.LOCKED_CANON)
    se = _se(1, {"canon_conflicts": [{"assertion": "the prose shows the gate breached", "span": [3, 40]}]})
    handles = evidence_mod.build_manuscript_handles([se])
    candidates = evidence_mod.candidate_conflicts(handles)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.handle == "M1"
    assert c.scene_id == str(se.scene_id)
    assert c.scene_version == se.scene_version
    assert c.prose_hash == se.prose_hash
    assert c.span == (3, 40)
    assert "breached" in c.assertion
    assert c.snapshot_prose_len == 200


def test_candidate_conflict_without_span_is_still_a_candidate():
    # A hint with no usable span is not dropped — it becomes an unanchored candidate that Lane A3 fails
    # closed (an approval block), never a silently-ignored conflict.
    se = _se(1, {"canon_conflicts": [{"assertion": "no span here"}]})
    candidates = evidence_mod.candidate_conflicts(evidence_mod.build_manuscript_handles([se]))
    assert len(candidates) == 1 and candidates[0].span is None


def test_no_conflict_section_yields_no_candidates():
    se = _se(1, {"events": [{"summary": "nothing contentious"}]})
    assert evidence_mod.candidate_conflicts(evidence_mod.build_manuscript_handles([se])) == []


def test_resolve_evidence_provenance_maps_canon_manuscript_and_unknown():
    canon_id = uuid.uuid4()
    se = _se(2, {"events": []})
    handles = {"M1": se}
    canon_handles = {"C1": {"id": canon_id, "name": "The Gate", "body": "canon body text " * 40}}
    packet = {
        "claims": [
            {"claim": "gate exists", "source_strength": "LOCKED_CANON", "source_id": "C1"},
            {"claim": "gate was breached", "source_strength": "DERIVED_FROM_MANUSCRIPT", "source_id": "M1"},
            {"claim": "a guess", "source_strength": "PLAUSIBLE_INFERENCE", "source_id": "X9"},
        ]
    }
    evidence_mod.resolve_evidence_provenance(packet, canon_handles=canon_handles, manuscript_handles=handles)
    canon_claim, ms_claim, inf_claim = packet["claims"]
    assert canon_claim["source_id"] == str(canon_id) and canon_claim["source_title_or_file"] == "The Gate"
    assert len(canon_claim["excerpt"]) == 240  # bounded excerpt
    assert ms_claim["source_id"] == str(se.scene_id)
    assert ms_claim["source_handle"] == "M1"  # M# retained so the manuscript span stays traceable
    assert ms_claim["source_title_or_file"] == "imported scene 2"
    assert ms_claim["excerpt"] is None
    assert inf_claim["source_id"] is None and inf_claim["source_title_or_file"] is None


def test_precedence_adjudication_ranks_authored_sources():
    claims = [
        {"source_strength": "DERIVED_FROM_MANUSCRIPT"},
        {"source_strength": "DERIVED_FROM_MANUSCRIPT"},
        {"source_strength": "LOCKED_CANON"},
        {"source_strength": "not_a_source"},  # ignored
        "junk",  # ignored
    ]
    audit = evidence_mod.precedence_adjudication(claims)
    assert audit["policy"] == "claim_precedence"
    assert audit["strongest_source"] == "locked_canon"
    assert audit["order"][:2] == ["locked_canon", "derived_from_manuscript"]
    by_source = {row["source"]: row for row in audit["by_source"]}
    assert by_source["locked_canon"]["rank"] == 0 and by_source["locked_canon"]["claims"] == 1
    assert by_source["derived_from_manuscript"]["rank"] == 1 and by_source["derived_from_manuscript"]["claims"] == 2
    # strongest source is listed first (sorted by precedence rank)
    assert audit["by_source"][0]["source"] == "locked_canon"


def test_evidence_query_pulls_setting_facts_events():
    se = _se(
        1,
        {
            "setting": "the sealed vault",
            "asserted_facts": [{"assertion": "the sigil glows"}],
            "events": [{"summary": "the door opens"}],
        },
    )
    query = evidence_mod.evidence_query([se])
    assert "sealed vault" in query and "sigil glows" in query and "door opens" in query


def test_fail_closed_question_names_handle_scene_and_detail():
    q = evidence_mod.fail_closed_question("M2", "scene-xyz", "no_current_canon", "nothing re-anchors")
    assert "M2" in q and "scene-xyz" in q and "nothing re-anchors" in q


# ================================ DB oracles: propose_packet_from_evidence ==========================


async def _seed_chapter(s, pov: str = "Marcus") -> Chapter:
    book = Book(title="Imported Work")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov=pov, outline=None)
    s.add(ch)
    await s.flush()
    return ch


def _author_packet(open_q: list[str] | None = None) -> dict:
    return {
        "confidence": "green",
        "chapter_job": "Reconstruct the prologue contract from the imported prose",
        "exit_state": "the gate stands open",
        "scene_seeds": [{"scene_no": 1, "scene_job": "Establish the breach at the vault gate."}],
        "claims": [
            {"claim": "the gate exists", "source_strength": "LOCKED_CANON", "source_id": "C1"},
            {"claim": "the gate was breached", "source_strength": "DERIVED_FROM_MANUSCRIPT", "source_id": "M1"},
        ],
        "open_questions": open_q or [],
    }


def _qa(verdict: PacketVerdict = PacketVerdict.APPROVE, issues: list | None = None) -> dict:
    return {"verdict": verdict, "residual_risks": ["keep the antagonist unnamed"], "issues": issues or []}


def _fixed_retriever(hits):
    async def _retrieve(_query):
        return list(hits)

    return _retrieve


def _patch(monkeypatch, author_result, qa_result) -> None:
    async def fake_author(**kwargs):
        return author_result

    async def fake_qa(_packet, **kwargs):
        return qa_result

    monkeypatch.setattr(author_mod, "author_packet_from_evidence", fake_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)


async def test_evidence_happy_path_proposes_contract(db_factory, monkeypatch):
    """Evidence -> a proposed ChapterPacket (Lane A4 maps this to adoption `contract_proposed`): seed ids
    minted, canonical body, adoption lineage + manuscript-handle + precedence provenance recorded."""
    _patch(monkeypatch, _author_packet(), _qa())
    evidence = [_se(1, {"setting": "the vault", "asserted_facts": [{"assertion": "the gate exists"}]})]
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet_from_evidence(
            s, chapter=ch, evidence=evidence, retrieve=_fixed_retriever([])
        )
        assert row.status == PacketStatus.PROPOSED
        assert row.body["schema_version"] == 1
        assert row.body["scene_seeds"][0].get("seed_id")  # server minted
        assert row.body["lineage"]["source"] == "import_adoption"
        si = row.body["source_inputs"]
        assert si["manuscript_handles"][0]["handle"] == "M1"
        assert si["manuscript_handles"][0]["scene_id"] == str(evidence[0].scene_id)
        assert si["precedence"]["strongest_source"] == "locked_canon"
        assert "outline_chars" not in si  # the outline stamp is dropped on the evidence path
        # exactly one current packet for the chapter
        n = len((await s.execute(select(ChapterPacket).where(ChapterPacket.chapter_id == ch.id))).scalars().all())
        assert n == 1
        # a clean evidence packet has no open questions -> it is approvable
        assert approval_policy.open_question_items(row) == []


async def test_reanchored_conflict_stays_proposed_but_blocks_approval(db_factory, monkeypatch):
    """A manuscript-vs-canon conflict that re-anchors against live canon is folded in as an encoded
    open question. Per Q14 the packet is STILL proposed (conflicts block APPROVAL, not adoption)."""
    _patch(monkeypatch, _author_packet(), _qa())
    canon_id = uuid.uuid4()
    hit = {"id": canon_id, "name": "The Gate", "body": "In canon the gate has never been breached."}
    evidence = [
        _se(
            1,
            {
                "setting": "the vault gate",
                "canon_conflicts": [{"assertion": "the prose shows the gate already breached", "span": [0, 30]}],
            },
        )
    ]
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet_from_evidence(
            s, chapter=ch, evidence=evidence, retrieve=_fixed_retriever([hit])
        )
        assert row.status == PacketStatus.PROPOSED  # conflict-laden is still a proposed contract (Q14)
        items = (row.open_questions or {}).get("items", [])
        encoded = [q for q in items if is_conflict_question(q)]
        assert len(encoded) == 1
        parsed = parse_conflict(encoded[0])
        assert parsed is not None
        assert parsed.canon_id == str(canon_id)
        assert parsed.canon_handle == "C1"  # taken from the handle map the author was shown
        assert parsed.manuscript_handle == "M1"
        assert parsed.scene_id == str(evidence[0].scene_id)
        assert parsed.span == (0, 30)
        # the open question blocks approval (an unresolved editorial decision), never adoption
        assert approval_policy.can_approve(row) is not None


async def test_unanchorable_conflict_stays_proposed_with_a_plain_block_question(db_factory, monkeypatch):
    """A flagged conflict whose canon side cannot be re-anchored is a fail-closed signal: still a proposed
    packet, but a plain approval-blocking open question (never a silently-dropped conflict)."""
    _patch(monkeypatch, _author_packet(), _qa())
    evidence = [
        _se(1, {"canon_conflicts": [{"assertion": "prose asserts a fact with no matching canon", "span": [0, 5]}]})
    ]
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet_from_evidence(
            s,
            chapter=ch,
            evidence=evidence,
            retrieve=_fixed_retriever([]),  # no live canon -> fails closed
        )
        assert row.status == PacketStatus.PROPOSED
        items = (row.open_questions or {}).get("items", [])
        assert not any(is_conflict_question(q) for q in items)  # not encodable (no canon fingerprint)
        assert any("manuscript-vs-canon conflict" in q for q in items)  # but surfaced as a block
        assert approval_policy.can_approve(row) is not None


async def test_thin_author_output_fails_closed_to_blocked(db_factory, monkeypatch):
    """Fail-closed authoring (a thin packet with no scene seeds) yields a BLOCKED packet — Lane A4 maps
    this to adoption `failed` with the blocked packet linked as diagnostic (Q14)."""
    _patch(monkeypatch, {"chapter_job": "x", "scene_seeds": [], "claims": []}, _qa())
    evidence = [_se(1, {"events": [{"summary": "something"}]})]
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet_from_evidence(
            s, chapter=ch, evidence=evidence, retrieve=_fixed_retriever([])
        )
        assert row.status == PacketStatus.BLOCKED
        assert row.confidence == "red"
        assert (row.qa_warnings or {}).get("blocker_kind") == "thin_packet"


async def test_empty_evidence_bundle_fails_closed(db_factory, monkeypatch):
    """No evidence at all -> a blocked packet: there is nothing to reconstruct the chapter from."""
    _patch(monkeypatch, _author_packet(), _qa())
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet_from_evidence(
            s, chapter=ch, evidence=[], retrieve=_fixed_retriever([])
        )
        assert row.status == PacketStatus.BLOCKED
        assert (row.qa_warnings or {}).get("blocker_kind") == "no_evidence"


async def test_malformed_author_fails_closed(db_factory, monkeypatch):
    _patch(monkeypatch, None, _qa())
    evidence = [_se(1, {"events": [{"summary": "something"}]})]
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet_from_evidence(
            s, chapter=ch, evidence=evidence, retrieve=_fixed_retriever([])
        )
        assert row.status == PacketStatus.BLOCKED
        assert (row.qa_warnings or {}).get("blocker_kind") == "unparsable"
