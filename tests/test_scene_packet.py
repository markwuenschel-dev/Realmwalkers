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
from dominion.shared.enums import LengthStatus, ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    ChapterPacket,
    DraftAttempt,
    Job,
    ScenePacket,
)
from dominion.workers import background_work as bw
from dominion.workers.budget import TokenBudget
from dominion.workers.context import ScenePacketRequiredError, assemble_context
from dominion.workers.length import guard as guard_mod
from dominion.workers.length import planner as planner_mod
from dominion.workers.scene_packet import author as sp_author
from dominion.workers.scene_packet import author_sections as sp_sections
from dominion.workers.scene_packet import derive as sp_derive
from dominion.workers.scene_packet import hash as sp_hash
from dominion.workers.scene_packet import qa as sp_qa

# --- length planner (pure) ------------------------------------------------------------------------

def _seed(seed_id: str, **kw: Any) -> dict[str, Any]:
    return {"seed_id": seed_id, "scene_no": kw.pop("scene_no", 1), **kw}


def test_planner_targets_sum_close_to_chapter_target():
    seeds = [_seed("11111111-1111-1111-1111-111111111111", scene_type="bridge", scene_no=1),
             _seed("22222222-2222-2222-2222-222222222222", scene_type="combat", scene_no=2),
             _seed("33333333-3333-3333-3333-333333333333", scene_type="dialogue", scene_no=3)]
    budgets = planner_mod.plan_word_budgets(
        chapter_target_words=9000, chapter_max_words=None, scene_seeds=seeds
    )
    assert abs(sum(b["target"] for b in budgets.values()) - 9000) <= 3  # rounding only


def test_planner_combat_outweighs_bridge_and_emits_hard_max_and_priorities():
    bridge = _seed("11111111-1111-1111-1111-111111111111", scene_type="bridge")
    combat = _seed("22222222-2222-2222-2222-222222222222", scene_type="combat")
    budgets = planner_mod.plan_word_budgets(
        chapter_target_words=6000, chapter_max_words=None, scene_seeds=[bridge, combat]
    )
    b = budgets[bridge["seed_id"]]
    c = budgets[combat["seed_id"]]
    assert c["target"] > b["target"]                       # combat earns more page-space
    assert b["hard_max"] > 0 and b["compression_priority"] # full budget emitted


def test_planner_manual_budget_overrides_allocation():
    manual = _seed("11111111-1111-1111-1111-111111111111", word_budget={"target": 2222})
    auto = _seed("22222222-2222-2222-2222-222222222222", scene_type="dialogue")
    budgets = planner_mod.plan_word_budgets(
        chapter_target_words=8000, chapter_max_words=None, scene_seeds=[manual, auto]
    )
    assert budgets[manual["seed_id"]]["target"] == 2222


def test_planner_must_not_spend_from_forbidden():
    seed = _seed("11111111-1111-1111-1111-111111111111", forbidden_reveals=["the twist"])
    budgets = planner_mod.plan_word_budgets(
        chapter_target_words=2000, chapter_max_words=None, scene_seeds=[seed]
    )
    mns = budgets[seed["seed_id"]]["must_not_spend_words_on"]
    assert any("the twist" in m for m in mns)


# --- length guard ---------------------------------------------------------------------------------

_BUDGET = {"min": 700, "target": 1000, "max": 1350, "hard_max": 1600}


async def _noop(prose, **kw):
    return prose


def test_guard_counts_words():
    assert guard_mod.count_words("one two three") == 3
    assert guard_mod.count_words("") == 0


async def test_guard_within_budget_passes():
    r = await guard_mod.apply_length_guard(
        "word " * 1000, word_budget=_BUDGET, scene_contract={},
        budget=TokenBudget(max_tokens=100000), compress=_noop, expand=_noop,
    )
    assert r.length_status == LengthStatus.WITHIN_BUDGET and not r.stages


async def test_guard_over_hard_max_compresses():
    async def shrink(prose, **kw):
        return "word " * 900
    r = await guard_mod.apply_length_guard(
        "word " * 5000, word_budget=_BUDGET, scene_contract={},
        budget=TokenBudget(max_tokens=100000), compress=shrink, expand=_noop,
    )
    assert r.word_count == 900
    assert [s.stage for s in r.stages] == ["length_compression"]
    assert not r.quarantine


async def test_guard_still_over_hard_max_quarantines():
    async def still_big(prose, **kw):
        return "word " * 5000
    r = await guard_mod.apply_length_guard(
        "word " * 6000, word_budget=_BUDGET, scene_contract={},
        budget=TokenBudget(max_tokens=100000), compress=still_big, expand=_noop,
    )
    assert r.length_status == LengthStatus.OVER_HARD_MAX_QUARANTINED and r.quarantine


async def test_guard_under_min_no_expand_unless_configured(monkeypatch):
    # ~80% of min is under but not skeletal, and auto-expand is off -> INFO, no rewrite.
    monkeypatch.setattr(settings, "length_auto_expand_under_min", False)
    r = await guard_mod.apply_length_guard(
        "word " * 600, word_budget=_BUDGET, scene_contract={},
        budget=TokenBudget(max_tokens=100000), compress=_noop, expand=_noop,
    )
    assert r.length_status == LengthStatus.UNDER_MIN and not r.stages


# --- source hash (staleness) ----------------------------------------------------------------------

def test_source_hash_is_stable_and_input_sensitive():
    base = dict(chapter_packet_id="cp", chapter_packet_body={"a": 1}, scene_seed={"s": 1},
                chapter_word_budget={"target": 1000})
    assert sp_hash.source_hash(**base) == sp_hash.source_hash(**base)
    changed = {**base, "scene_seed": {"s": 2}}
    assert sp_hash.source_hash(**changed) != sp_hash.source_hash(**base)


def test_source_hash_folds_in_pov_override_without_disturbing_unoverridden():
    # A per-scene POV override re-opens an already-approved packet (changes the hash), but a scene with
    # NO override must keep the exact hash it had before scene_pov existed — so the upgrade doesn't
    # mass-invalidate every packet.
    base = dict(chapter_packet_id="cp", chapter_packet_body={"a": 1}, scene_seed={"s": 1},
                chapter_word_budget={"target": 1000})
    no_override = sp_hash.source_hash(**base)
    assert sp_hash.source_hash(**base, scene_pov=None) == no_override   # absent override == legacy hash
    assert sp_hash.source_hash(**base, scene_pov="") == no_override     # blank override == legacy hash
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


async def _approved_chapter_packet(s, book, ch, seeds: list[dict[str, Any]]) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id, chapter_id=ch.id, status="approved", confidence="green",
        body={"scene_seeds": seeds, "characters_present": ["Marcus", "Serra"],
              "characters_absent": ["Eriadne"], "canon_locks": ["the Realm is real"]},
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    return cp


def _scene_body(word_budget: dict[str, Any] | None = None) -> dict[str, Any]:
    mole = "Serra is the mole"
    return {
        "scene_no": 1, "scene_job": "Marcus intercepts.", "scene_type": "combat",
        "word_budget": word_budget or {"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400},
        "known_before_scene": {"reader": ["the route"], "pov": ["the route"], "omniscient_author": [mole]},
        "learned_during_scene": {
            "reader_must_learn": ["the cohort is converging"],
            "reader_may_learn": [], "reader_may_infer_only": [],
        },
        "must_remain_hidden": {"reader": [mole], "pov": [], "all_surface_prose": []},
        "pov_permissions": {
            "may_notice": [], "may_infer": [], "must_not_know": [mole], "may_be_wrong_about": [],
        },
        "intentional_mysteries": [
            {"mystery": "who tipped the cohort", "desired_reader_effect": "unease", "do_not_explain": True},
        ],
        "reviewer_false_positive_traps": ["the missing tip source is intentional"],
        "required_beats": ["land the hit"], "forbidden_beats": ["Marcus uses his Aspect"],
        "exit_state": "both wounded", "phrases_to_avoid_echoing": ["reader must learn"],
        "reviewer_instructions": {"combat": ["track stamina"], "continuity": []},
    }


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


# --- fake Anthropic client: exercises the REAL author/QA + llm.complete (incl. telemetry capture,
# truncation detection, and the fallback-model escalation) without a network call -------------------

class _FakeBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeUsage:
    def __init__(self) -> None:
        self.input_tokens = 2000
        self.output_tokens = 500
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 1500


class _FakeResp:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage()
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responder) -> None:
        self._responder = responder

    async def create(self, *, model, max_tokens, system, messages):
        sys_text = system[0]["text"] if isinstance(system[0], dict) else system[0].text
        # The user content is a string (no cached prefix) or a [prefix, trailing] block list. The
        # sectioned author now carries its per-section directive in the trailing (uncached) user block,
        # NOT system, so the responder needs the user text to route a fake slice per section.
        content = messages[0]["content"]
        if isinstance(content, str):
            user_text = content
        else:
            user_text = "\n".join(b["text"] if isinstance(b, dict) else b.text for b in content)
        return self._responder(
            model=model, system_text=sys_text, user_text=user_text, max_tokens=max_tokens
        )


class _FakeClient:
    def __init__(self, responder) -> None:
        self.messages = _FakeMessages(responder)


def _patch_llm_client(monkeypatch, responder) -> None:
    from dominion.workers import llm
    monkeypatch.setattr(llm, "_client", lambda: _FakeClient(responder))


def _qa_ok() -> str:
    import json
    return json.dumps({"verdict": "approve", "residual_risks": [], "issues": []})


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
        assert row.source_hash                                   # staleness anchor recorded
        assert row.body["word_budget"]["target"] == 1500         # planner budget folded in
        assert "known_before_scene" in row.body


async def test_derive_blocks_on_thin_body(db_factory, monkeypatch):
    async def thin(**kw):
        return {"scene_no": 1}  # missing required contract sections

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", False)  # exercise the monolithic path
    monkeypatch.setattr(sp_author, "author_scene_packet", thin)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        sid = str(uuid.uuid4())
        cp = await _approved_chapter_packet(s, book, ch, [_seed(sid)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        assert counts["blocked"] == 1
        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.BLOCKED


async def test_derive_gives_each_scene_its_own_budget(db_factory, monkeypatch):
    """Regression: deriving a multi-scene chapter must NOT share one per-scene token budget across all
    scenes. The bug exhausted a single shared budget after the first scene (its QA call tipped it over),
    then every later author call started already-over-budget and failed closed — surfacing as "QA
    returned no usable verdict" on scene 1 and "author returned an incomplete body" on scenes 2+."""
    from dominion.workers.budget import Usage

    async def charging_author(*, budget, **kw):
        budget.charge(Usage(input_tokens=0, output_tokens=20_000))
        return _scene_body()

    async def charging_qa(_b, *, budget, **kw):
        budget.charge(Usage(input_tokens=0, output_tokens=10_000))
        return {"verdict": ScenePacketVerdict.APPROVE, "residual_risks": [], "issues": []}

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", False)  # per-scene budget is derive-level
    monkeypatch.setattr(sp_author, "author_scene_packet", charging_author)
    monkeypatch.setattr(sp_qa, "qa_scene_packet", charging_qa)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        seeds = [_seed(str(uuid.uuid4()), scene_no=i) for i in range(1, 4)]  # three scenes
        cp = await _approved_chapter_packet(s, book, ch, seeds)
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        # 30k spent per scene < the 40k per-scene budget -> all three proposed, none starved.
        # (Summed across 3 scenes that's 90k, well over a single shared 40k — the bug this guards.)
        assert counts["created"] == 3
        assert counts["blocked"] == 0


async def test_derive_persists_per_call_telemetry(db_factory, monkeypatch):
    """The derive captures one llm_calls row per Author/QA call, tagged with stage + scene + cache."""
    import json

    from dominion.shared.models import LlmCall

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", False)  # monolithic: exactly 1 author call
    body = _scene_body()

    def responder(*, model, system_text, user_text, max_tokens):
        if "QA agent" in system_text:
            return _FakeResp(_qa_ok())
        return _FakeResp(json.dumps(body))

    _patch_llm_client(monkeypatch, responder)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()), scene_no=1)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        assert counts["created"] == 1 and counts["blocked"] == 0

        calls = (await s.execute(select(LlmCall).where(LlmCall.chapter_id == ch.id))).scalars().all()
        assert sorted(c.stage for c in calls) == [
            "scene_packet_author", "scene_packet_author_prefix_prime",
            "scene_packet_qa", "scene_packet_qa_prefix_prime",
        ]
        assert all(c.scene_no is None for c in calls if c.stage.endswith("prefix_prime"))
        author = next(c for c in calls if c.stage == "scene_packet_author")
        assert author.scene_no == 1
        assert author.model == settings.scene_packet_author_model
        assert author.cache_read_tokens == 1500 and not author.truncated


async def test_derive_blocks_with_specific_truncation_reason(db_factory, monkeypatch):
    """A truncated author body names the real cause (not a generic 'incomplete body'), and both the
    primary + escalated attempts are recorded as truncated telemetry."""
    from dominion.shared.models import LlmCall

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", False)  # monolithic: primary+fallback = 2 calls

    def responder(*, model, system_text, user_text, max_tokens):
        if "Acknowledge cache prime" in user_text:
            return _FakeResp("{}")
        # Author always cut off mid-object; QA never reached (body never valid).
        return _FakeResp('{"scene_no": 1, "known_before_scene":', stop_reason="max_tokens")

    _patch_llm_client(monkeypatch, responder)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        assert counts["blocked"] == 1

        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.BLOCKED
        reason = (row.qa_warnings or {}).get("blocked_reason") or row.body.get("blocked_reason")
        assert reason and "truncated" in reason

        calls = (await s.execute(select(LlmCall))).scalars().all()
        author_calls = [c for c in calls if c.stage == "scene_packet_author"]
        assert len(author_calls) == 2 and all(c.truncated for c in author_calls)
        assert {c.model for c in author_calls} == {
            settings.scene_packet_author_model, settings.scene_packet_author_fallback_model
        }
        assert sorted(c.stage for c in calls if c.stage.endswith("prefix_prime")) == [
            "scene_packet_author_prefix_prime", "scene_packet_qa_prefix_prime",
        ]


async def test_author_escalates_to_fallback_model_on_bad_primary(db_factory, monkeypatch):
    """A primary model that can't emit valid JSON is rescued by the one-shot fallback escalation."""
    import json

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", False)  # monolithic single-body escalation
    body = _scene_body()

    def responder(*, model, system_text, user_text, max_tokens):
        if "QA agent" in system_text:
            return _FakeResp(_qa_ok())
        if model == settings.scene_packet_author_model:
            return _FakeResp("sorry, here is the packet: (not actually json)")  # primary fails
        return _FakeResp(json.dumps(body))  # fallback model succeeds

    _patch_llm_client(monkeypatch, responder)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()), scene_no=1)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        assert counts["created"] == 1 and counts["blocked"] == 0
        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.PROPOSED
        assert "known_before_scene" in row.body


# --- sectioned author (concurrent section calls merged server-side) -------------------------------

# A body carrying every key any section owns (the base _scene_body lacks chapter_position/tone_pressure).
def _complete_scene_body() -> dict[str, Any]:
    return {**_scene_body(), "chapter_position": "middle", "tone_pressure": "rising dread"}


def _section_responder(monkeypatch, *, overrides: dict[tuple[str, str], tuple[str, str]] | None = None):
    """Drive the REAL sectioned author + llm.complete with a fake client that returns the correct JSON
    slice per section. `overrides[(section_name, 'primary'|'fallback')] = (raw_text, stop_reason)` forces
    a specific (bad) response for one section on one model, to exercise escalation / fail-closed."""
    import json

    overrides = overrides or {}
    complete = _complete_scene_body()
    monkeypatch.setattr(settings, "scene_packet_author_sectioned", True)

    def responder(*, model, system_text, user_text, max_tokens):
        if "QA agent" in system_text:
            return _FakeResp(_qa_ok())
        # The section marker now rides in the trailing user block (kept out of system so every section
        # shares one cached prefix), so route on user_text.
        if "Acknowledge cache prime" in user_text:
            return _FakeResp("{}")
        for sec in sp_sections._SECTIONS:
            if f"[section:{sec.name}]" in user_text:
                tier = "primary" if model == settings.scene_packet_author_model else "fallback"
                if (sec.name, tier) in overrides:
                    raw, stop = overrides[(sec.name, tier)]
                    return _FakeResp(raw, stop_reason=stop)
                return _FakeResp(json.dumps({k: complete[k] for k in sec.keys}))
        raise AssertionError(f"unrecognized section user prompt: {user_text[:120]}")

    _patch_llm_client(monkeypatch, responder)


async def test_sectioned_author_merges_sections_into_one_packet(db_factory, monkeypatch):
    """Default path: the author fans into one call PER SECTION (concurrently), and the slices merge into
    a single valid packet — same body shape the monolithic author produced."""
    from dominion.shared.models import LlmCall

    _section_responder(monkeypatch)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()), scene_no=1)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        assert counts["created"] == 1 and counts["blocked"] == 0

        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.PROPOSED
        # every section's keys made it into the merged body, plus the server-stamped budget
        assert {"known_before_scene", "learned_during_scene", "must_remain_hidden", "pov_permissions",
                "intentional_mysteries", "reviewer_false_positive_traps", "reviewer_instructions",
                "phrases_to_avoid_echoing", "tone_pressure"} <= set(row.body)
        assert row.body["word_budget"]["target"] == 1500

        # one llm_calls row per section (all under the unchanged scene_packet_author stage) + one QA
        authors = [c for c in (await s.execute(select(LlmCall))).scalars() if c.stage == "scene_packet_author"]
        assert len(authors) == len(sp_sections._SECTIONS)
        assert all(c.scene_no == 1 for c in authors)


async def test_sectioned_author_uses_chapter_and_scene_cache_breakpoints(monkeypatch):
    """The sectioned author must send two cache breakpoints: a chapter-shared prefix that can be
    reused across scenes, then a scene-local prefix reused across sections. The section directive stays
    uncached so section names cannot poison either cache key."""
    import json

    from dominion.workers import llm

    CHAPTER_PREFIX = 14_000
    SCENE_PREFIX = 6_000
    chapter_writes: list[str] = []
    scene_writes: list[str] = []
    seen_chapter: set[str] = set()
    seen_scene: set[str] = set()
    complete = _complete_scene_body()

    class _Usage:
        def __init__(self, *, creation: int, read: int) -> None:
            self.input_tokens = 40
            self.output_tokens = 200
            self.cache_creation_input_tokens = creation
            self.cache_read_input_tokens = read

    class _Resp:
        def __init__(self, text: str, usage: _Usage) -> None:
            self.content = [_FakeBlock(text)]
            self.usage = usage
            self.stop_reason = "end_turn"

    class _Messages:
        async def create(self, *, model, max_tokens, system, messages):
            sys_text = system[0]["text"]
            blocks = messages[0]["content"]
            assert isinstance(blocks, list) and len(blocks) == 3
            chapter_text, scene_text, trailing = blocks[0]["text"], blocks[1]["text"], blocks[2]["text"]
            chapter_key = sys_text + "\x00" + chapter_text
            scene_key = chapter_key + "\x00" + scene_text
            creation = read = 0
            if chapter_key in seen_chapter:
                read += CHAPTER_PREFIX
            else:
                seen_chapter.add(chapter_key)
                chapter_writes.append(chapter_key)
                creation += CHAPTER_PREFIX
            if scene_key in seen_scene:
                read += SCENE_PREFIX
            else:
                seen_scene.add(scene_key)
                scene_writes.append(scene_key)
                creation += SCENE_PREFIX
            for sec in sp_sections._SECTIONS:
                if f"[section:{sec.name}]" in trailing:
                    return _Resp(json.dumps({k: complete[k] for k in sec.keys}), _Usage(creation=creation, read=read))
            raise AssertionError(f"no section marker in trailing user block: {trailing[:80]}")

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", True)
    monkeypatch.setattr(llm, "_client", lambda: _Client())

    budget = TokenBudget(max_tokens=settings.scene_token_budget)
    body = await sp_sections.author_scene_packet_sectioned(
        pov="Marcus",
        chapter_packet_body={"chapter_job": "x" * 400},
        scene_seed=_seed(str(uuid.uuid4()), scene_no=1),
        word_budget={"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400},
        budget=budget,
    )

    assert len(chapter_writes) == 1
    assert len(scene_writes) == 1
    assert "known_before_scene" in body and body["word_budget"]["target"] == 1500
    assert budget.used < settings.scene_token_budget
    assert budget.used < (CHAPTER_PREFIX + SCENE_PREFIX) * 2


async def test_sectioned_author_raw_context_window_failure_preflights_before_llm(monkeypatch):
    """ScenePacket raw context-window failures must happen before the API call and must not rely on
    weighted/cache-aware TokenBudget accounting."""
    from dominion.workers import llm
    from dominion.workers.llm import ContextWindowExceeded

    monkeypatch.setattr(settings, "scene_packet_context_window_budget", 10)

    class _Messages:
        async def create(self, **kwargs):
            raise AssertionError("LLM client should not be called after context preflight fails")

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(llm, "_client", lambda: _Client())

    with pytest.raises(ContextWindowExceeded, match="ScenePacket context window exceeded"):
        await sp_sections.author_scene_packet_sectioned(
            pov="Marcus",
            chapter_packet_body={"chapter_job": "x" * 1000},
            scene_seed=_seed(str(uuid.uuid4()), scene_no=1),
            word_budget={"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400},
            budget=TokenBudget(max_tokens=60_000),
        )


async def test_author_prime_and_real_request_match_through_shared_prefix(monkeypatch):
    """Author prime and real section calls must be identical through the chapter_shared_prefix
    breakpoint; otherwise the real call writes the cache again instead of reading it."""
    import json

    from dominion.workers import llm

    monkeypatch.setattr(settings, "scene_packet_context_window_budget", 500_000)
    complete = _complete_scene_body()
    seen: dict[str, object] = {}

    class _Messages:
        async def create(self, *, model, max_tokens, system, messages):
            blocks = messages[0]["content"]
            assert isinstance(blocks, list)
            trailing = blocks[-1]["text"]
            shape = {
                "system": system[0]["text"],
                "cached": [b["text"] for b in blocks[:-1]],
                "trailing": trailing,
            }
            if "Acknowledge cache prime" in trailing:
                seen["prime"] = shape
                return _FakeResp("{}")
            seen.setdefault("real", shape)
            for sec in sp_sections._SECTIONS:
                if f"[section:{sec.name}]" in trailing:
                    return _FakeResp(json.dumps({k: complete[k] for k in sec.keys}))
            raise AssertionError(f"no section marker in trailing user block: {trailing[:80]}")

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(llm, "_client", lambda: _Client())
    chapter_body = {"chapter_job": "hold the line", "scene_seeds": []}
    pov_summary = "Marcus knows the bridge is watched."
    omniscient = "The reader knows the ambush is staged."

    await sp_sections.prime_author_shared_prefix(
        chapter_packet_body=chapter_body, pov_summary=pov_summary, omniscient_summary=omniscient,
        budget=TokenBudget(max_tokens=100_000),
    )
    await sp_sections.author_scene_packet_sectioned(
        pov="Marcus", chapter_packet_body=chapter_body,
        scene_seed=_seed(str(uuid.uuid4()), scene_no=1),
        word_budget={"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400},
        pov_summary=pov_summary, omniscient_summary=omniscient,
        budget=TokenBudget(max_tokens=60_000),
    )

    prime = seen["prime"]
    real = seen["real"]
    assert prime["system"] == real["system"]
    assert len(prime["cached"]) == 1
    assert len(real["cached"]) == 2
    assert prime["cached"][0] == real["cached"][0]
    assert "THIS SCENE'S SEED" not in prime["cached"][0]
    assert "THIS SCENE'S SEED" in real["cached"][1]
    assert "Acknowledge cache prime" not in real["cached"][0]


async def test_qa_prime_and_real_request_match_through_shared_prefix(monkeypatch):
    """QA prime and real QA calls use a different system prompt from Author, but within QA they must
    match through the chapter_shared_prefix breakpoint."""
    from dominion.workers import llm

    monkeypatch.setattr(settings, "scene_packet_context_window_budget", 500_000)
    seen: dict[str, object] = {}

    class _Messages:
        async def create(self, *, model, max_tokens, system, messages):
            blocks = messages[0]["content"]
            assert isinstance(blocks, list)
            trailing = blocks[-1]["text"]
            shape = {
                "system": system[0]["text"],
                "cached": [b["text"] for b in blocks[:-1]],
                "trailing": trailing,
            }
            if "Acknowledge cache prime" in trailing:
                seen["prime"] = shape
                return _FakeResp("{}")
            seen["real"] = shape
            return _FakeResp(_qa_ok())

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(llm, "_client", lambda: _Client())
    chapter_body = {"chapter_job": "hold the line", "scene_seeds": []}

    await sp_qa.prime_qa_shared_prefix(chapter_body, budget=TokenBudget(max_tokens=100_000))
    await sp_qa.qa_scene_packet(
        _complete_scene_body(), chapter_packet_body=chapter_body, budget=TokenBudget(max_tokens=60_000)
    )

    prime = seen["prime"]
    real = seen["real"]
    assert prime["system"] == real["system"]
    assert len(prime["cached"]) == len(real["cached"]) == 1
    assert prime["cached"][0] == real["cached"][0]
    assert "Acknowledge cache prime" not in real["cached"][0]
    assert "SCENE PACKET" in real["trailing"]


async def test_derive_primes_shared_prefix_before_scene_work_for_reported_67k_case(db_factory, monkeypatch):
    """Regression for Scene 1 failing with `67040 > 60000`: the 66.5k chapter-shared cache write
    is charged to explicit prefix-prime calls, while both Scene 1 and Scene 2 read that prefix under
    their own 60k scene-local budgets."""
    import json

    from dominion.shared.models import LlmCall
    from dominion.workers import llm

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", True)
    monkeypatch.setattr(settings, "scene_token_budget", 60_000)
    monkeypatch.setattr(settings, "scene_packet_prefix_prime_token_budget", 100_000)
    monkeypatch.setattr(settings, "scene_packet_context_window_budget", 500_000)

    CHAPTER_PREFIX = 66_500
    SCENE_PREFIX = 500
    DIRECTIVE_INPUT = 40
    OUTPUT = 500
    complete = _complete_scene_body()
    seen: set[str] = set()
    events: list[str] = []

    class _Usage:
        def __init__(self, *, creation: int, read: int) -> None:
            self.input_tokens = DIRECTIVE_INPUT
            self.output_tokens = OUTPUT
            self.cache_creation_input_tokens = creation
            self.cache_read_input_tokens = read

    class _Resp:
        def __init__(self, text: str, usage: _Usage) -> None:
            self.content = [_FakeBlock(text)]
            self.usage = usage
            self.stop_reason = "end_turn"

    class _Messages:
        async def create(self, *, model, max_tokens, system, messages):
            sys_text = system[0]["text"]
            is_qa = "QA agent" in sys_text
            blocks = messages[0]["content"]
            assert isinstance(blocks, list)
            trailing = blocks[-1]["text"]
            creation = read = 0
            prefix_key = sys_text
            for i, block in enumerate(blocks[:-1]):
                prefix_key += "\x00" + block["text"]
                weight = CHAPTER_PREFIX if i == 0 else SCENE_PREFIX
                if prefix_key in seen:
                    read += weight
                else:
                    seen.add(prefix_key)
                    creation += weight

            if "Acknowledge cache prime" in trailing:
                events.append("qa_prime" if is_qa else "author_prime")
                return _Resp("{}", _Usage(creation=creation, read=read))

            if is_qa:
                assert events[:2] == ["author_prime", "qa_prime"]
                events.append("qa_scene")
                return _Resp(_qa_ok(), _Usage(creation=creation, read=read))

            assert events[:2] == ["author_prime", "qa_prime"]
            events.append("author_scene")
            for sec in sp_sections._SECTIONS:
                if f"[section:{sec.name}]" in trailing:
                    return _Resp(json.dumps({k: complete[k] for k in sec.keys}), _Usage(creation=creation, read=read))
            raise AssertionError(f"no section marker in trailing user block: {trailing[:80]}")

    class _Client:
        def __init__(self) -> None:
            self.messages = _Messages()

    fake = _Client()
    monkeypatch.setattr(llm, "_client", lambda: fake)

    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        seeds = [_seed(str(uuid.uuid4()), scene_no=1), _seed(str(uuid.uuid4()), scene_no=2)]
        cp = await _approved_chapter_packet(s, book, ch, seeds)
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()

        assert counts["created"] == 2
        assert counts["blocked"] == 0
        assert counts["context_budget_report"]["context_window_budget"] == 500_000
        assert events[:2] == ["author_prime", "qa_prime"]

        calls = (await s.execute(select(LlmCall).where(LlmCall.chapter_id == ch.id))).scalars().all()
        prime_calls = [c for c in calls if c.stage.endswith("prefix_prime")]
        assert sorted(c.stage for c in prime_calls) == [
            "scene_packet_author_prefix_prime", "scene_packet_qa_prefix_prime",
        ]
        assert all(c.scene_no is None for c in prime_calls)
        assert all(c.cache_creation_tokens == CHAPTER_PREFIX for c in prime_calls)

        scene_author_calls = [c for c in calls if c.stage == "scene_packet_author"]
        assert scene_author_calls
        assert all(c.scene_no in {1, 2} for c in scene_author_calls)
        assert all(c.cache_creation_tokens < CHAPTER_PREFIX for c in scene_author_calls)
        assert any(c.scene_no == 1 and c.cache_read_tokens >= CHAPTER_PREFIX for c in scene_author_calls)
        assert any(c.scene_no == 2 and c.cache_read_tokens >= CHAPTER_PREFIX for c in scene_author_calls)


async def test_sectioned_author_escalates_only_the_failed_section(db_factory, monkeypatch):
    """Point 4: a section that fails on the primary reruns on the fallback model ALONE — the other
    sections are not re-run. So total author calls = sections + 1."""
    from dominion.shared.models import LlmCall

    _section_responder(monkeypatch, overrides={("knowledge", "primary"): ("not json at all", "end_turn")})
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()), scene_no=1)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        assert counts["created"] == 1 and counts["blocked"] == 0

        authors = [c for c in (await s.execute(select(LlmCall))).scalars() if c.stage == "scene_packet_author"]
        assert len(authors) == len(sp_sections._SECTIONS) + 1            # exactly one extra (the rerun)
        assert sum(c.model == settings.scene_packet_author_fallback_model for c in authors) == 1
        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.PROPOSED and "known_before_scene" in row.body


async def test_sectioned_author_blocks_when_a_section_is_unrecoverable(db_factory, monkeypatch):
    """A section that fails on BOTH primary and fallback fails the whole packet closed, with a reason
    naming the section (never a partial contract)."""
    _section_responder(monkeypatch, overrides={
        ("knowledge", "primary"): ("garbage", "end_turn"),
        ("knowledge", "fallback"): ("still garbage", "end_turn"),
    })
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()), scene_no=1)])
        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        assert counts["blocked"] == 1

        row = (await s.execute(select(ScenePacket))).scalars().one()
        assert row.status == ScenePacketStatus.BLOCKED
        reason = (row.qa_warnings or {}).get("blocked_reason") or row.body.get("blocked_reason")
        assert reason and "knowledge" in reason


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
        assert beat.target_words == 1500                          # mirrors word_budget.target
        assert beat.tags == ["combat"]
        assert beat.characters_present == ["Marcus", "Serra"]     # chapter cast minus absent
        # hard constraints are NOT copied onto the beat
        assert not hasattr(beat, "must_remain_hidden")
        assert "must_remain_hidden" not in (beat.beat_text or "")


async def test_beats_rederive_idempotent_and_prune_keeps_drafted(db_factory):
    from dominion.shared.models import Scene
    from dominion.workers.scene_packet import beats as beats_mod

    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp1 = ScenePacket(book_id=book.id, chapter_id=ch.id, chapter_packet_id=cp.id, scene_no=1,
                          status=ScenePacketStatus.APPROVED, body=_scene_body(), source_hash="h1")
        sp2 = ScenePacket(book_id=book.id, chapter_id=ch.id, chapter_packet_id=cp.id, scene_no=2,
                          status=ScenePacketStatus.APPROVED, body=_scene_body(), source_hash="h2")
        s.add_all([sp1, sp2])
        await s.flush()

        assert await beats_mod.derive_beats(s, chapter_id=ch.id) == 2
        await s.flush()
        first = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        assert len(first) == 2
        ids = {b.scene_packet_id: b.id for b in first}

        # re-derive: same packets -> updated in place, no duplicates
        assert await beats_mod.derive_beats(s, chapter_id=ch.id) == 2
        await s.flush()
        again = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        assert len(again) == 2 and {b.id for b in again} == set(ids.values())

        # scene 2 drafted; un-approve both packets -> scene-1 beat pruned, scene-2 beat kept
        s.add(Scene(chapter_id=ch.id, scene_no=2, status="pending_review", prose="drafted"))
        sp1.status = sp2.status = ScenePacketStatus.PROPOSED
        await s.flush()
        assert await beats_mod.derive_beats(s, chapter_id=ch.id) == 0
        await s.flush()
        remaining = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        assert [b.scene_no for b in remaining] == [2]


async def test_blocked_scene_packet_cannot_be_approved(db_factory, monkeypatch):
    async def thin(**kw):
        return {"scene_no": 1}

    monkeypatch.setattr(settings, "scene_packet_author_sectioned", False)
    monkeypatch.setattr(sp_author, "author_scene_packet", thin)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        sp = (await s.execute(select(ScenePacket))).scalars().one()
        with pytest.raises(HTTPException) as exc:
            await sp_router.approve_scene_packet(sp.id, s)
        assert exc.value.status_code == 409


async def _proposed_scene_packet(s, book, ch, cp, *, verdict, warnings) -> ScenePacket:
    sp = ScenePacket(
        book_id=book.id, chapter_id=ch.id, chapter_packet_id=cp.id, scene_seed_id=uuid.uuid4(),
        scene_no=1, status=ScenePacketStatus.PROPOSED, qa_verdict=verdict, qa_warnings=warnings,
        body=_scene_body(), source_hash="h",
    )
    s.add(sp)
    await s.flush()
    return sp


async def test_proposed_packet_with_blocking_qa_cannot_be_approved(db_factory):
    """The gap that produced the silent 409: a packet that is PROPOSED (not BLOCKED) but whose QA gates
    drafting. `_has_blocking_qa` must refuse it for a revise_required verdict AND for a block-severity
    issue even under a non-blocking verdict (approve_warn). Mirrors the frontend's blockingQa()."""
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])

        revise = await _proposed_scene_packet(
            s, book, ch, cp,
            verdict=ScenePacketVerdict.REVISE_REQUIRED,
            warnings={"residual_risks": [], "issues": []},
        )
        with pytest.raises(HTTPException) as exc:
            await sp_router.approve_scene_packet(revise.id, s)
        assert exc.value.status_code == 409

        # approve_warn verdict, but an issue is severity:"block" -> still gated (the trap that read as
        # an enabled Approve button on the old frontend).
        warn_but_blocked = await _proposed_scene_packet(
            s, book, ch, cp,
            verdict=ScenePacketVerdict.APPROVE_WARN,
            warnings={"residual_risks": [], "issues": [{"kind": "leak", "detail": "x", "severity": "block"}]},
        )
        with pytest.raises(HTTPException) as exc:
            await sp_router.approve_scene_packet(warn_but_blocked.id, s)
        assert exc.value.status_code == 409


async def test_proposed_packet_with_approve_warn_and_no_block_issue_approves(db_factory, monkeypatch):
    """The flip side: approve_warn with only info/warn issues is NOT gated — approval proceeds and
    derives the beat (so the gate doesn't over-block legitimate warn-level packets)."""
    _patch_scene_agents(monkeypatch, _scene_body())  # only needed for beat derivation inputs
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp = await _proposed_scene_packet(
            s, book, ch, cp,
            verdict=ScenePacketVerdict.APPROVE_WARN,
            warnings={"residual_risks": ["minor echo risk"], "issues": [{"detail": "soft", "severity": "warn"}]},
        )
        await sp_router.approve_scene_packet(sp.id, s)
        assert (await s.get(ScenePacket, sp.id)).status == ScenePacketStatus.APPROVED


# --- context assembly requires an approved scene packet --------------------------------------------

async def _approved_scene_packet_with_beat(s, book, ch, cp) -> tuple[ScenePacket, Beat]:
    sp = ScenePacket(
        book_id=book.id, chapter_id=ch.id, chapter_packet_id=cp.id, scene_seed_id=uuid.uuid4(),
        scene_no=1, status=ScenePacketStatus.APPROVED, qa_verdict=ScenePacketVerdict.APPROVE,
        body=_scene_body(), source_hash="h",
    )
    s.add(sp)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_packet_id=sp.id, scene_no=1, status="approved",
                characters_present=["Marcus"], beat_text="Marcus intercepts.", target_words=1500)
    s.add(beat)
    await s.flush()
    return sp, beat


async def test_assemble_context_loads_scene_contract_without_run_id(db_factory):
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp, beat = await _approved_scene_packet_with_beat(s, book, ch, cp)
        job = Job(kind="draft", status="queued", token_budget=40000,
                  book_id=book.id, chapter_id=ch.id, beat_id=beat.id, scene_packet_id=sp.id)
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
        sp = ScenePacket(book_id=book.id, chapter_id=ch.id, chapter_packet_id=cp.id, scene_no=1,
                         status=ScenePacketStatus.PROPOSED, body=_scene_body(), source_hash="h")
        s.add(sp)
        await s.flush()
        beat = Beat(chapter_id=ch.id, scene_packet_id=sp.id, scene_no=1, status="approved")
        s.add(beat)
        await s.flush()
        job = Job(kind="draft", status="queued", token_budget=40000,
                  book_id=book.id, chapter_id=ch.id, beat_id=beat.id, scene_packet_id=sp.id)
        s.add(job)
        await s.flush()
        with pytest.raises(ScenePacketRequiredError):
            await assemble_context(s, job)


# --- pipeline integration: provenance + length + scene-packet stamping -----------------------------

async def test_pipeline_records_draft_attempts_and_scene_packet_fields(db_factory, monkeypatch):
    from dominion.workers import pipeline
    from dominion.workers.specialists import drafter as drafter_mod

    async def fake_draft(self, prose, ctx):
        return "word " * 1500  # within the 1500-target budget

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)
    monkeypatch.setattr(pipeline, "reviewers_for", lambda tags: [])
    monkeypatch.setattr(pipeline, "passes_for", lambda tags: [])

    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp, beat = await _approved_scene_packet_with_beat(s, book, ch, cp)
        job = Job(kind="draft", status="queued", token_budget=40000,
                  book_id=book.id, chapter_id=ch.id, beat_id=beat.id, scene_packet_id=sp.id)
        s.add(job)
        await s.commit()

        scene = await pipeline.generate_one_scene(s, job)
        await s.commit()

        assert scene.scene_packet_id == sp.id
        assert scene.word_count and scene.length_status == LengthStatus.WITHIN_BUDGET
        attempts = (await s.execute(
            select(DraftAttempt).where(DraftAttempt.scene_id == scene.id)
        )).scalars().all()
        stages = {a.stage for a in attempts}
        assert "drafter_raw" in stages and "final_rendered" in stages
        assert all(a.scene_packet_id == sp.id for a in attempts)


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


# --- background derive endpoint -------------------------------------------------------------------

async def test_derive_endpoint_requires_approved_chapter_packet(db_factory):
    from fastapi import BackgroundTasks, HTTPException

    async with db_factory() as s:
        _book, ch = await _seed_book_chapter(s)  # no approved chapter packet
        await s.commit()
        with pytest.raises(HTTPException) as exc:
            await sp_router.derive_scene_packets(ch.id, BackgroundTasks(), s)
        assert exc.value.status_code == 409


async def test_derive_router_outputs_context_budget_report(db_factory, monkeypatch):
    """Both the sync derive result and the polled derive-status result expose context_budget_report."""
    report = {"context_window_budget": 123, "chapter_packet": 45, "scenes": []}

    async def fake_derive(session, *, packet):
        return {"created": 1, "updated": 0, "blocked": 0, "stale": 0, "context_budget_report": report}

    monkeypatch.setattr(sp_router.derive_mod, "derive_scene_packets", fake_derive)
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        await s.commit()

        out = await sp_router._derive_sync(ch.id, s)
        assert out.context_budget_report == report

        bw.set_derive_result(str(ch.id), {
            "created": 1, "updated": 0, "blocked": 0, "stale": 0, "context_budget_report": report,
        })
        try:
            status = await sp_router.derive_status(ch.id, s)
            assert status.result is not None
            assert status.result.context_budget_report == report
        finally:
            bw.pop_derive_result(str(ch.id))


async def test_derive_endpoint_schedules_background_run(db_factory):
    from fastapi import BackgroundTasks

    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        await s.commit()
        bg = BackgroundTasks()
        out = await sp_router.derive_scene_packets(ch.id, bg, s)
        try:
            assert out.running and out.phase == "deriving"
            assert bg.tasks  # a background task was scheduled
        finally:
            bw.finish(sp_router._derive_key(ch.id))  # don't leak state across tests


async def test_propose_packet_endpoint_schedules_background_run(db_factory):
    from fastapi import BackgroundTasks

    from dominion.api.routers import packets as packets_router

    async with db_factory() as s:
        _book, ch = await _seed_book_chapter(s)
        await s.commit()
        bg = BackgroundTasks()
        key = str(ch.id)
        out = await packets_router.propose_packet(ch.id, bg, s)
        try:
            assert out.running and out.phase == "authoring"
            assert bg.tasks
            out2 = await packets_router.propose_packet(ch.id, bg, s)
            assert out2.running
        finally:
            bw.finish(key)


# --- knowledge ledger -----------------------------------------------------------------------------

async def test_knowledge_facts_recorded_from_scene_reveals(db_factory):
    from dominion.shared.enums import KnowledgeStatus
    from dominion.shared.models import KnowledgeFact, Scene
    from dominion.workers.memory import knowledge

    body = _scene_body()
    body["learned_during_scene"]["reader_must_learn"] = ["the cohort is converging", "the gate is real"]
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        cp = await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        sp = ScenePacket(book_id=book.id, chapter_id=ch.id, chapter_packet_id=cp.id, scene_no=1,
                         status=ScenePacketStatus.APPROVED, body=body, source_hash="h")
        s.add(sp)
        await s.flush()
        scene = Scene(chapter_id=ch.id, scene_no=1, status="approved", prose="x", scene_packet_id=sp.id)
        s.add(scene)
        await s.flush()

        n = await knowledge.record_scene_reveals(s, scene_id=scene.id)
        await s.commit()
        assert n == 2
        facts = (await s.execute(select(KnowledgeFact).where(KnowledgeFact.book_id == book.id))).scalars().all()
        assert {f.fact for f in facts} == {"the cohort is converging", "the gate is real"}
        assert all(f.status == KnowledgeStatus.REVEALED for f in facts)
        assert all(f.known_by_reader_after_scene_id == scene.id for f in facts)

        # idempotent: re-running does not duplicate
        await knowledge.record_scene_reveals(s, scene_id=scene.id)
        await s.commit()
        again = (await s.execute(select(KnowledgeFact).where(KnowledgeFact.book_id == book.id))).scalars().all()
        assert len(again) == 2
