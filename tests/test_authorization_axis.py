"""ADR-0031 D16 / A1c — the Authorization Requirement axis.

The defect this replaces: `apply_repair_task` gated on
`human_approved or task.human_approved_at is not None or autonomous`, three booleans in which "the caller
is automated" authorized itself, and `RepairTask.requires_human_approval` was a stored mutable column
standing in for the requirement. These pin four things:

1. **The decision** — `authorize_repair`'s truth table, including that `autonomous` is not an input.
2. **Orthogonality** — manual-grant work exists at LOW blast radius and no automated caller executes it.
   Without this the axis would just be `authority_level` under a new name.
3. **Python/SQL agreement** — `requires_explicit_authorization` (the wire projection) and
   `requires_explicit_authorization_clause` (the drain/sweeper filter) are one rule in two forms.
4. **Parity** — the claimable set is exactly the set the retired boolean selected: no widening.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from dominion.shared.authorization import (
    DEFAULT_AUTHORIZATION_CEILING,
    GRANT_CEILING,
    GRANT_HUMAN,
    GRANT_PRIOR_HUMAN,
    REFUSAL_ABOVE_CEILING,
    REFUSAL_MANUAL_GRANT_REQUIRED,
    REFUSAL_UNKNOWN_REQUIREMENT,
    authorize_repair,
    requirement_for_authority,
    requires_explicit_authorization,
    requires_explicit_authorization_clause,
)
from dominion.shared.enums import (
    AuthorizationRequirement,
    IssueStatus,
    ProductionRunStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
)
from dominion.shared.models import Book, Chapter, Issue, ProductionRun, RepairTask
from dominion.workers import production

CEILING_GATED = AuthorizationRequirement.CEILING_GATED.value
MANUAL_GRANT = AuthorizationRequirement.MANUAL_GRANT.value

#: The exact set the retired `requires_human_approval = false` boolean selected, spelled out so a change
#: to the ceiling constant has to change this list too (a silent widening cannot pass).
_DRAIN_CLAIMABLE = {"span_only", "scene_local", "scene_structural"}


# --- the decision ---------------------------------------------------------------------------------


def _decide(**kw):
    base = dict(
        authorization_requirement=CEILING_GATED,
        authority_level=RepairAuthorityLevel.SCENE_LOCAL.value,
        ceiling=DEFAULT_AUTHORIZATION_CEILING,
        human_approved=False,
        prior_human_grant=False,
    )
    return authorize_repair(**{**base, **kw})


def test_human_grant_authorizes_every_requirement():
    for requirement in (CEILING_GATED, MANUAL_GRANT):
        for level in RepairAuthorityLevel:
            d = _decide(authorization_requirement=requirement, authority_level=level.value, human_approved=True)
            assert d.authorized and d.grant == GRANT_HUMAN, (requirement, level)


def test_prior_human_grant_authorizes_every_requirement():
    # One human approval covers the task's whole repair loop, not a single attempt — a verify verdict of
    # NEEDS_ANOTHER_REPAIR re-queues the task WITH its stamp, and that stamp must still authorize it.
    for requirement in (CEILING_GATED, MANUAL_GRANT):
        d = _decide(authorization_requirement=requirement, prior_human_grant=True)
        assert d.authorized and d.grant == GRANT_PRIOR_HUMAN, requirement


def test_manual_grant_is_refused_at_every_ceiling_including_its_own_level():
    # The B-3 conflation: `human_required` used to be a rung a raised ceiling could negate. It cannot.
    for ceiling in (*[level.value for level in RepairAuthorityLevel], "garbage", ""):
        for level in RepairAuthorityLevel:
            d = _decide(authorization_requirement=MANUAL_GRANT, authority_level=level.value, ceiling=ceiling)
            assert not d.authorized and d.refusal == REFUSAL_MANUAL_GRANT_REQUIRED, (ceiling, level)


def test_ceiling_gated_authorized_only_within_the_declared_ceiling():
    d = _decide(
        authority_level=RepairAuthorityLevel.SCENE_STRUCTURAL.value,
        ceiling=RepairAuthorityLevel.CHAPTER_STRUCTURAL.value,
    )
    assert d.authorized and d.grant == GRANT_CEILING
    d = _decide(
        authority_level=RepairAuthorityLevel.CHAPTER_STRUCTURAL.value,
        ceiling=RepairAuthorityLevel.SCENE_STRUCTURAL.value,
    )
    assert not d.authorized and d.refusal == REFUSAL_ABOVE_CEILING


def test_unknown_ceiling_and_unknown_requirement_fail_closed():
    assert not _decide(ceiling="garbage").authorized
    # `human_required` is not a valid ceiling — declaring it authorizes NOTHING, it does not raise the bar.
    assert not _decide(ceiling=RepairAuthorityLevel.HUMAN_REQUIRED.value).authorized
    unknown = _decide(authorization_requirement="something_new")
    assert not unknown.authorized and unknown.refusal == REFUSAL_UNKNOWN_REQUIREMENT


def test_being_autonomous_is_not_an_input_to_the_decision():
    # The retired gate read `... or autonomous`. `authorize_repair` has no such parameter at all — the
    # only way an automated caller authorizes work is by DECLARING a ceiling that covers it.
    assert "autonomous" not in authorize_repair.__annotations__
    assert "autonomous" not in authorize_repair.__code__.co_varnames


def test_mint_default_derives_requirement_from_blast_radius():
    for level in RepairAuthorityLevel:
        expected = MANUAL_GRANT if level is RepairAuthorityLevel.HUMAN_REQUIRED else CEILING_GATED
        assert requirement_for_authority(level) == expected, level
        assert requirement_for_authority(level.value) == expected, level


# --- Python/SQL agreement + parity with the retired boolean ---------------------------------------


def test_projection_matches_the_retired_boolean_rule():
    # The old mint rule was `authority_level in {CROSS_SCENE, CHAPTER_STRUCTURAL, HUMAN_REQUIRED}`.
    for level in RepairAuthorityLevel:
        derived = requires_explicit_authorization(level.value, requirement_for_authority(level))
        assert derived == (level.value not in _DRAIN_CLAIMABLE), level


def test_manual_grant_always_requires_explicit_authorization_at_any_blast_radius():
    for level in RepairAuthorityLevel:
        assert requires_explicit_authorization(level.value, MANUAL_GRANT), level


async def test_sql_clause_agrees_with_python_projection_over_every_pair(db_factory):
    """The drain and the sweeper filter in SQL; the wire projection and the gate read Python. One rule,
    two forms — this enumerates every (authority_level, requirement) pair against real rows."""
    async with db_factory() as s:
        book = Book(title="Realmwalkers")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Mara")
        s.add(ch)
        await s.flush()
        run = ProductionRun(book_id=book.id, chapter_id=ch.id, status=ProductionRunStatus.RUNNING)
        s.add(run)
        await s.flush()
        expected: dict[uuid.UUID, bool] = {}
        for level in RepairAuthorityLevel:
            for requirement in (CEILING_GATED, MANUAL_GRANT):
                task = RepairTask(
                    production_run_id=run.id,
                    chapter_id=ch.id,
                    repair_kind="continuity",
                    authority_level=level.value,
                    authorization_requirement=requirement,
                    status=RepairTaskStatus.QUEUED,
                    issue_ids=[],
                    instructions="x",
                )
                s.add(task)
                await s.flush()
                assert task.requires_human_approval == requires_explicit_authorization(level.value, requirement)
                expected[task.id] = task.requires_human_approval
        await s.flush()
        selected = set(
            (await s.execute(select(RepairTask.id).where(requires_explicit_authorization_clause()))).scalars().all()
        )
        assert selected == {tid for tid, needs in expected.items() if needs}


# --- orthogonality, end to end through the real apply seam -----------------------------------------


async def _seed_task(s, *, authority: RepairAuthorityLevel, requirement: str) -> tuple[ProductionRun, RepairTask]:
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Mara", outline="o")
    s.add(ch)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=ch.id, status=ProductionRunStatus.RUNNING)
    s.add(run)
    await s.flush()
    issue = Issue(
        production_run_id=run.id,
        chapter_id=ch.id,
        artifact_type="chapter_draft_qa",
        artifact_id=uuid.uuid4(),
        validator="scene_scope",
        issue_kind="scene_scope_bleed",
        severity="repair",
        claim="c",
        recommended_action="a",
        status=IssueStatus.REPAIR_QUEUED,
        payload_json={},
    )
    s.add(issue)
    await s.flush()
    task = RepairTask(
        production_run_id=run.id,
        chapter_id=ch.id,
        repair_kind="continuity",
        authority_level=authority.value,
        authorization_requirement=requirement,
        status=RepairTaskStatus.QUEUED,
        issue_ids=[str(issue.id)],
        instructions="Fix it.",
    )
    s.add(task)
    await s.flush()
    return run, task


async def test_low_blast_radius_manual_grant_is_refused_to_every_automated_ceiling(db_factory):
    """THE orthogonality proof. A `scene_local` repair — the smallest blast radius there is, and one the
    unattended drain would normally claim without a click — carries MANUAL_GRANT. No declared ceiling
    executes it. If authorization were still authority_level under a new name, this would apply."""
    async with db_factory() as s:
        _run, task = await _seed_task(s, authority=RepairAuthorityLevel.SCENE_LOCAL, requirement=MANUAL_GRANT)
        task_id = task.id
        for ceiling in (RepairAuthorityLevel.SPAN_ONLY.value, RepairAuthorityLevel.CHAPTER_STRUCTURAL.value):
            out = await production.apply_repair_task(s, task_id, autonomous=True, authorization_ceiling=ceiling)
            assert out.status == RepairTaskStatus.WAITING_FOR_HUMAN, ceiling
            assert out.human_approved_at is None  # refusal never stamps a human grant


async def test_low_blast_radius_manual_grant_is_invisible_to_the_drain_query(db_factory):
    """Defence in depth for the same case: the drain's claim query must not even select it."""
    async with db_factory() as s:
        _run, task = await _seed_task(s, authority=RepairAuthorityLevel.SCENE_LOCAL, requirement=MANUAL_GRANT)
        claimable = (
            (
                await s.execute(
                    select(RepairTask.id).where(
                        RepairTask.status == RepairTaskStatus.QUEUED,
                        ~requires_explicit_authorization_clause(),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert task.id not in claimable
        assert task.requires_human_approval is True  # and the Desk shows it as needing a click


async def test_ceiling_gated_scene_local_is_claimable_by_default_ceiling(db_factory):
    """Parity guard: the same blast radius WITHOUT the manual-grant requirement stays drain-claimable."""
    async with db_factory() as s:
        _run, task = await _seed_task(s, authority=RepairAuthorityLevel.SCENE_LOCAL, requirement=CEILING_GATED)
        claimable = (
            (
                await s.execute(
                    select(RepairTask.id).where(
                        RepairTask.status == RepairTaskStatus.QUEUED,
                        ~requires_explicit_authorization_clause(),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert task.id in claimable
        assert task.requires_human_approval is False


async def test_stored_requires_human_approval_column_is_gone(db_factory):
    """`requires_human_approval` stopped being a mutable field: it is a read-only derived property, and
    the physical column is dropped by the A1c migration (guarded by its fail-closed preflight). Asserted
    against `information_schema`, not the ORM's opinion of itself."""
    async with db_factory() as s:
        _run, task = await _seed_task(s, authority=RepairAuthorityLevel.SCENE_LOCAL, requirement=CEILING_GATED)
        with pytest.raises(AttributeError):
            task.requires_human_approval = True  # type: ignore[misc]
        assert "requires_human_approval" not in RepairTask.__table__.columns
        physical = set(
            (
                await s.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'repair_tasks'")
                )
            )
            .scalars()
            .all()
        )
        assert "requires_human_approval" not in physical
        assert "authorization_requirement" in physical
