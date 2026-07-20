"""Autonomy/settings control plane + the rule-learning distill path, over the inbound HTTP harness.

These surfaces had ZERO coverage (re-verified 2026-07-20): the Settings control plane
(`api/routers/settings.py` — autonomy switches, per-agent policy, preset CRUD) and the Tier-3
rule-learning path (`api/routers/learning.py` + `workers/learning/distill.py`). Everything here drives
the real FastAPI app through `httpx.ASGITransport` via the `app_client` fixture (tests/conftest.py),
so routing, `Depends` injection, request-body validation (422s), and response-model serialization all
run on the wire — plus a couple of write+read smokes for beats/markup/threads.

No LLM/provider is ever contacted: the one worker that calls a model (`distill.propose_rules`) has its
`llm.complete` monkeypatched to a canned JSON array. Requires a reachable Postgres (see `db_factory`):
locally these skip when Postgres is down; in CI `DOMINION_REQUIRE_DB=1` makes an unreachable DB fail.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from dominion.shared.enums import BeatStatus, SceneStatus
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    EditPair,
    PovProfile,
    RuleProposal,
    Scene,
)
from dominion.workers import llm
from dominion.workers.budget import Usage

# --- seed helpers (mirror test_learning.py's tiny builders) ---------------------------------------


async def _book(s, title: str = "Dominion Realm") -> Book:
    book = Book(title=title)
    s.add(book)
    await s.flush()
    return book


async def _chapter(s, book: Book, no: int = 1, pov: str = "Marcus") -> Chapter:
    ch = Chapter(book_id=book.id, chapter_no=no, pov=pov)
    s.add(ch)
    await s.flush()
    return ch


async def _scene(s, ch: Chapter, scene_no: int = 1, *, status: str = SceneStatus.APPROVED) -> Scene:
    sc = Scene(chapter_id=ch.id, scene_no=scene_no, version=1, status=status, prose="Prose.", prose_source="agent")
    s.add(sc)
    await s.flush()
    return sc


async def _edit_pair(s, sc: Scene, *, pov: str, agent_text: str, human_text: str) -> EditPair:
    pair = EditPair(scene_id=sc.id, version=sc.version, pov=pov, agent_text=agent_text, human_text=human_text)
    s.add(pair)
    await s.flush()
    return pair


def _stub_llm_complete(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    """Replace the ONE model call in the distill path (distill.propose_rules -> llm.complete) with a
    canned (raw, usage) tuple. distill references `llm.complete` as a module attribute, so patching the
    module object here reaches it at call time — the same seam every other suite uses (test_drafter,
    test_agent_controls, ...)."""

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        return response, Usage(input_tokens=5, output_tokens=5)

    monkeypatch.setattr(llm, "complete", fake_complete)


# A JSON array shaped exactly like distill's prompt asks for: [{"kind","rule","why"}]. distill._coerce
# maps "rule"->rule_text, "why"->rationale; an unknown/blank kind normalizes to "voice".
_DISTILL_TWO_RULES = json.dumps(
    [
        {"kind": "voice", "rule": "Trim filter verbs like saw and felt", "why": "recurring across edits"},
        {"kind": "dialogue", "rule": "Keep dialogue tags to said and asked", "why": "consistent pattern"},
    ]
)
_DISTILL_ONE_RULE = json.dumps(
    [{"kind": "voice", "rule": "Cut throat-clearing openings", "why": "author repeatedly removed them"}]
)


# =================================================================================================
# Autonomy control plane — PUT /settings/autonomy (settings.py:157; D16 guard :160-165)
# =================================================================================================


async def test_set_autonomy_rejects_human_required_ceiling_422(app_client: httpx.AsyncClient) -> None:
    """The D16 guard: `human_required` is a manual-grant Authorization Requirement, never an
    auto-approval ceiling — the PUT must reject it with 422 (settings.py:160-165), not persist it."""
    resp = await app_client.put("/settings/autonomy", json={"authority_ceiling": "human_required"})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "human_required" in detail and "ceiling" in detail, detail


async def test_set_autonomy_valid_write_round_trips_via_get(app_client: httpx.AsyncClient) -> None:
    """A valid PUT persists every switch as KV rows and a SEPARATE GET reads them back: proof the write
    committed and load_config re-reads it. authority_ceiling='scene_local' is inside AUTO_APPROVAL_CEILINGS
    so it survives _normalize_ceiling untouched."""
    put = await app_client.put(
        "/settings/autonomy",
        json={"autonomy_enabled": False, "interval_s": 300, "authority_ceiling": "scene_local", "max_attempts": 5},
    )
    assert put.status_code == 200, put.text

    got = await app_client.get("/settings/autonomy")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["autonomy_enabled"] is False, body
    assert body["interval_s"] == 300, body
    assert body["authority_ceiling"] == "scene_local", body
    assert body["max_attempts"] == 5, body


# =================================================================================================
# Per-agent policy — PUT /settings/agents/{setting}/policy (settings.py:109)
# =================================================================================================


async def test_set_agent_policy_happy_path(app_client: httpx.AsyncClient) -> None:
    """A valid role ('review_model') + quality_level='quality' round-trips: the returned Agent Ops panel
    shows that agent at quality_level='quality', and the active preset flips to 'custom' (any manual
    policy edit un-selects a named preset — agent_ops.apply_agent_policy)."""
    resp = await app_client.put("/settings/agents/review_model/policy", json={"quality_level": "quality"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_preset"] == "custom", body
    row = next((a for a in body["agents"] if a["setting"] == "review_model"), None)
    assert row is not None, body
    assert row["policy"]["quality_level"] == "quality", row["policy"]


async def test_set_agent_policy_unknown_setting_422(app_client: httpx.AsyncClient) -> None:
    """An unknown agent setting is rejected with 422 by the router's ROLE_KEYS guard (settings.py:112),
    before agent_ops runs — the body itself validates fine (all AgentPolicyUpdateIn fields optional)."""
    resp = await app_client.put("/settings/agents/not_a_real_agent/policy", json={})
    assert resp.status_code == 422, resp.text
    assert "unknown agent setting" in resp.json()["detail"], resp.text


# =================================================================================================
# Preset CRUD — POST /settings/presets/custom, PUT/DELETE /settings/presets/{id} (settings.py:73/82/91)
# =================================================================================================


async def test_preset_create_apply_delete_round_trip(app_client: httpx.AsyncClient) -> None:
    """Full custom-preset lifecycle over HTTP: save the current config as a named preset (it becomes the
    active preset and appears with is_custom=True), re-apply it by id, then delete it — after which it is
    gone from the list and the active preset falls back to 'custom' (agent_ops.delete_custom_preset)."""
    created = await app_client.post(
        "/settings/presets/custom", json={"label": "Swarm Test Preset", "description": "created by the test"}
    )
    assert created.status_code == 200, created.text
    body = created.json()
    custom = [p for p in body["presets"] if p["is_custom"]]
    assert len(custom) == 1, body["presets"]
    preset_id = custom[0]["id"]
    assert preset_id.startswith("user:"), custom
    assert body["active_preset"] == preset_id, body

    applied = await app_client.put(f"/settings/presets/{preset_id}")
    assert applied.status_code == 200, applied.text
    assert applied.json()["active_preset"] == preset_id, applied.text

    deleted = await app_client.delete(f"/settings/presets/{preset_id}")
    assert deleted.status_code == 200, deleted.text
    after = deleted.json()
    assert not any(p["id"] == preset_id for p in after["presets"]), after["presets"]
    assert after["active_preset"] == "custom", after

    # Deleting a built-in / non-user preset id is a 422 (only user: presets are deletable).
    bad = await app_client.delete("/settings/presets/fast_drafting")
    assert bad.status_code == 422, bad.text


# =================================================================================================
# Rule learning — POST /books/{id}/distill (learning.py:28) + decision (learning.py:109)
# =================================================================================================


async def test_distill_creates_rule_proposals_and_dedupes(
    app_client: httpx.AsyncClient, db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed EditPairs, canned two-rule model response: the first distill creates 2 pending RuleProposals;
    a second identical distill creates 0 (deduped against existing non-rejected proposals for the
    (book, pov)), and the DB still holds exactly 2 rows total."""
    _stub_llm_complete(monkeypatch, _DISTILL_TWO_RULES)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus
        sc = await _scene(s, ch)
        await _edit_pair(s, sc, pov="Marcus", agent_text="He saw the door.", human_text="The door stood open.")
        await s.commit()
        book_id = book.id

    first = await app_client.post(f"/books/{book_id}/distill?pov=Marcus")
    assert first.status_code == 200, first.text
    created = first.json()
    assert len(created) == 2, created
    assert {r["kind"] for r in created} == {"voice", "dialogue"}, created
    assert all(r["status"] == "pending" for r in created), created

    second = await app_client.post(f"/books/{book_id}/distill?pov=Marcus")
    assert second.status_code == 200, second.text
    assert second.json() == [], second.text  # both rules already pending -> deduped, nothing new

    async with db_factory() as s:
        total = await s.scalar(select(func.count()).select_from(RuleProposal).where(RuleProposal.book_id == book_id))
    assert total == 2, total  # dedupe prevented duplicates piling up


async def test_accept_rule_proposal_appends_to_pov_voice_spec(
    app_client: httpx.AsyncClient, db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepting a proposal (POST /rule-proposals/{id}/decision, status=accepted) flips its status AND
    appends the rule as a bullet line to that POV's PovProfile.voice_spec (find-or-create), which the
    drafter reads fresh on the next scene (learning.py:92-106)."""
    _stub_llm_complete(monkeypatch, _DISTILL_ONE_RULE)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus, no PovProfile yet
        sc = await _scene(s, ch)
        await _edit_pair(s, sc, pov="Marcus", agent_text="Well, he cleared his throat.", human_text="He spoke.")
        await s.commit()
        book_id = book.id

    distilled = await app_client.post(f"/books/{book_id}/distill?pov=Marcus")
    assert distilled.status_code == 200, distilled.text
    proposals = distilled.json()
    assert len(proposals) == 1, proposals
    proposal_id = proposals[0]["id"]

    decided = await app_client.post(f"/rule-proposals/{proposal_id}/decision", json={"status": "accepted"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "accepted", decided.text

    # The accepted rule is now a bullet line on the (find-or-created) POV voice spec.
    async with db_factory() as s:
        profile = (
            await s.execute(select(PovProfile).where(PovProfile.book_id == book_id, PovProfile.character == "Marcus"))
        ).scalar_one()
    assert profile.voice_spec is not None, "accepting a rule must create the POV profile"
    assert profile.voice_spec.startswith("- "), profile.voice_spec
    assert "Cut throat-clearing openings" in profile.voice_spec, profile.voice_spec


# =================================================================================================
# Write+read smokes — beats.py, markup.py, threads.py
# =================================================================================================


async def test_beats_update_round_trip(app_client: httpx.AsyncClient, db_factory) -> None:
    """PUT /beats/{id} edits only the supplied field and persists it. Seed a proposed beat, edit its
    beat_text over HTTP (BeatUpdateIn), assert the 200 echo carries the new text, then read it back
    through a separate session to prove the handler's commit landed."""
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        beat = Beat(chapter_id=ch.id, scene_no=1, tags=[], status=BeatStatus.PROPOSED, beat_text="original beat")
        s.add(beat)
        await s.flush()
        await s.commit()
        beat_id = beat.id

    resp = await app_client.put(f"/beats/{beat_id}", json={"beat_text": "edited beat text"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["beat_text"] == "edited beat text", resp.text

    async with db_factory() as s:
        reread = await s.get(Beat, beat_id)
    assert reread is not None and reread.beat_text == "edited beat text"


async def test_markup_annotation_create_and_list(app_client: httpx.AsyncClient, db_factory) -> None:
    """POST /scenes/{id}/annotations creates a margin note; a SEPARATE GET /scenes/{id}/annotations
    lists it back — a full create->persist->read round-trip through the markup router."""
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch)
        await s.commit()
        scene_id = sc.id

    created = await app_client.post(
        f"/scenes/{scene_id}/annotations",
        json={"note": "tighten this beat", "quote": "The door stood open.", "author": "Mark"},
    )
    assert created.status_code == 200, created.text
    ann = created.json()
    assert ann["note"] == "tighten this beat" and ann["scene_id"] == str(scene_id), ann

    listed = await app_client.get(f"/scenes/{scene_id}/annotations")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert any(r["id"] == ann["id"] and r["note"] == "tighten this beat" for r in rows), rows


async def test_threads_create_and_list(app_client: httpx.AsyncClient, db_factory) -> None:
    """POST /books/{id}/threads creates a curated arc; a SEPARATE GET /books/{id}/threads lists it back
    with an (empty) beats array — a full create->persist->read round-trip through the threads router."""
    async with db_factory() as s:
        book = await _book(s)
        await s.commit()
        book_id = book.id

    created = await app_client.post(
        f"/books/{book_id}/threads",
        json={"name": "Soren and Lyra", "kind": "relationship", "state": "rising", "note": "slow burn"},
    )
    assert created.status_code == 200, created.text
    thread = created.json()
    assert thread["name"] == "Soren and Lyra", thread
    assert thread["beats"] == [], thread

    listed = await app_client.get(f"/books/{book_id}/threads")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    match = next((t for t in rows if t["id"] == thread["id"]), None)
    assert match is not None and match["name"] == "Soren and Lyra", rows
