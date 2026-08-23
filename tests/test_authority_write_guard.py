"""Every production route that can reach a human-required authority write, pinned.

The requirement this file exists for: *evidence that no alternate route bypasses the human-authority
gate.* An enumeration done once in a review is evidence about the past. This is a standing control — a
new writer of a protected status fails CI, and the diff makes whoever added it say which kind it is.

WHY A SCAN AND NOT A TYPE. These statuses are plain `Text` columns assigned from an enum, so nothing at
the type level distinguishes "the shared authority transition wrote this" from "a route assigned it
inline". The house precedent is the same shape:
`tests/test_issue259_chapter_packet_writer_guard.py` AST-scans every writer of
`ChapterPacket.open_questions`, and `test_issue223_fork3b_authorization_seam_guard.py` bans a second
authorization seam. This is their sibling for status writes.

THREE WRITE FORMS, ALL SCANNED. Scanning one form is how an enumeration lies to itself: `seed.py`
writes `SceneStatus.APPROVED` BOTH as an assignment (`:222`) and as a constructor kwarg (`:229`), and a
guard that only looked for `.status =` would have reported that file as having one write and missed the
row it creates from scratch. String-literal writes are scanned too, so `status = "approved"` cannot slip
past the enum-shaped patterns.

CLASSIFICATIONS, and what each claims:

* ``GATED``   — a permit is evaluated before the write, under the lock that covers it.
* ``HUMAN``   — this IS the human authority path. The write is the human's act.
* ``DERIVED`` — mirrors a fact that already passed another gate; not an independent authority write.
* ``OPEN``    — a known ungated path, tracked by a ticket. Listed so it is visible and counted, NOT so
                it is forgiven. Moving one out of OPEN is the work; adding one is a regression.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path("src/dominion")

#: The enums whose APPROVED/VERIFIED members mean "a human-required thing is now authorized".
PROTECTED_ENUMS = frozenset({"BeatStatus", "SceneStatus", "PacketStatus", "IssueStatus", "RepairTaskStatus"})
PROTECTED_MEMBERS = frozenset({"APPROVED", "VERIFIED"})
PROTECTED_LITERALS = frozenset({"approved", "verified"})


def _writes_protected(value: ast.AST) -> bool:
    """Does this expression evaluate to a protected status, in ANY shape?

    Walks the whole value subtree rather than matching a line, which is what makes it see a write the
    text scan could not::

        task.status = (
            RepairTaskStatus.WAITING_FOR_HUMAN
            if ... else RepairTaskStatus.VERIFIED     # <- three lines from `.status =`
        )

    That is a live conditional in `production_repair`, and a line-based guard reports the file as having
    one fewer authority write than it has. Bare string literals are caught too, so `status = "approved"`
    cannot dodge the enum-shaped patterns.
    """
    for node in ast.walk(value):
        if isinstance(node, ast.Attribute) and node.attr in PROTECTED_MEMBERS:
            owner = node.value
            if isinstance(owner, ast.Name) and owner.id in PROTECTED_ENUMS:
                return True
        if isinstance(node, ast.Constant) and node.value in PROTECTED_LITERALS:
            return True
    return False


GATED, HUMAN, DERIVED, OPEN = "GATED", "HUMAN", "DERIVED", "OPEN"

#: file -> (number of protected writes, classification, why).
#: A count change or a new file fails. Update this ONLY together with the reason it changed.
EXPECTED: dict[str, tuple[int, str, str]] = {
    "api/routers/chapters.py": (
        2,
        f"{GATED}+{HUMAN}",
        "approve_beats is gated by the chapter contract's open-questions permit under the chapter "
        "workflow lock (#283 C1). create_human_scene's approve_directly writes prose the HUMAN authored "
        "(prose_source='human') — the human is the authority for their own prose.",
    ),
    "api/routers/reviews.py": (
        1,
        OPEN,
        "#283 C2 — editorial scene approval. It HOLDS the chapter lock but consults no packet, no "
        "blocker and no adjudication; `first_approval` above it is an idempotence check, not a gate. "
        "This is the one place a human blesses prose as canonical, and it does not ask whether the "
        "contract that produced the prose is still approved. AWAITING AN OWNER SCOPE RULING.",
    ),
    "api/routers/scenes.py": (
        1,
        OPEN,
        "revert_scene mints a NEW scene at APPROVED from any prior version and never checks that "
        "version's status — reverting to a PENDING_REVIEW draft approves prose no human blessed. Its "
        "docstring defends this ('reverting is itself the human's decision'), which is intent, not a "
        "check. NOT one of #283's C1-C5; found by this scan. AWAITING AN OWNER RULING on whether revert "
        "is an approval act.",
    ),
    "workers/memory/seed.py": (
        2,
        OPEN,
        "#283 C3 — the dominion-seed CLI lands scenes APPROVED with no lock and no evaluator, as an "
        "assignment AND a constructor. An APPROVED scene is a prior-scene input to the staleness hash, "
        "so seeding silently shifts scene-packet staleness downstream. AWAITING AN OWNER SCOPE RULING.",
    ),
    "workers/packet/amendment.py": (
        1,
        GATED,
        "apply_authority_locked — THE shared chapter-authority transition. Gated by the open-questions "
        "predicate via approval_policy.can_approve, before any mutation, under the chapter lock (#277 "
        "clause A). Both approval routes funnel here, which is what closed the amendment bypass.",
    ),
    "workers/scene_packet/beats.py": (
        1,
        DERIVED,
        "derive_beats upserts one beat per ScenePacket it has ALREADY filtered to status == APPROVED. A "
        "mirror of a fact that passed the ScenePacket approval gate, not an independent write.",
    ),
    "workers/production_fidelity.py": (
        1,
        GATED,
        "#285 child A. Reached only for work whose authorization_requirement is ceiling-gated; a "
        "manual-grant hold is NOMINATED instead, and its status is left untouched.",
    ),
    "workers/production_repair.py": (
        4,
        f"{GATED}+{HUMAN}",
        "#285 child B: two auto-verify writes reachable only for ceiling-gated work, plus the two "
        "writes of human_verify_issue — the explicit human VERIFY path, which locks the issue and every "
        "linked task in one transaction.",
    ),
}


def _writes_by_file() -> dict[str, list[str]]:
    """Every assignment or constructor kwarg that sets a `.status` to a protected value.

    AST, not text. Only expressions ASSIGNED TO a `status` target are considered, so a comparison inside
    raw SQL (`AND scenes.status = 'approved'`) is excluded by construction rather than by a keyword
    heuristic that could be fooled by a comment.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a file that will not parse cannot ship
            continue
        for node in ast.walk(tree):
            hit = False
            if isinstance(node, ast.Assign) and _writes_protected(node.value):
                hit = any(isinstance(t, ast.Attribute) and t.attr == "status" for t in node.targets)
            elif isinstance(node, ast.Call):
                hit = any(kw.arg == "status" and _writes_protected(kw.value) for kw in node.keywords)
            if hit:
                found.setdefault(rel, []).append(f"{rel}:{node.lineno}")
    return found


def test_no_unpinned_route_can_reach_a_protected_authority_write():
    """The control. A new writer of an APPROVED/VERIFIED status fails here until it is classified.

    This is the mechanical form of "enumerate and test every production route that can reach the
    protected authority write" — done once it is a review note; done here it is a gate.
    """
    found = _writes_by_file()

    unexpected = sorted(set(found) - set(EXPECTED))
    assert not unexpected, (
        "a NEW route can reach a human-required authority write and is not classified:\n"
        + "\n".join(line for f in unexpected for line in found[f])
        + "\n\nAdd it to EXPECTED with its classification, or route it through an existing gate."
    )

    vanished = sorted(set(EXPECTED) - set(found))
    assert not vanished, (
        f"pinned authority writers no longer exist: {vanished}. If they were removed or moved, update "
        "EXPECTED — a stale pin is a guard that has stopped guarding."
    )

    for rel, (count, _kind, _why) in sorted(EXPECTED.items()):
        actual = len(found[rel])
        assert actual == count, (
            f"{rel} has {actual} protected authority write(s), expected {count}:\n"
            + "\n".join(found[rel])
            + "\n\nA count change means a route was added, removed, or duplicated. Update EXPECTED "
            "together with the reason."
        )


def test_the_scan_catches_every_write_form():
    """A guard that scans one form lies to itself. Proven against real files, not fixtures."""
    found = _writes_by_file()

    # ASSIGNMENT and CONSTRUCTOR, in one real file: seed.py writes SceneStatus.APPROVED both ways, and a
    # `.status =` scan alone reports one write there and misses the row it creates from scratch.
    assert len(found["workers/memory/seed.py"]) == 2

    # CONDITIONAL, three lines from its target — the write a line-based scan cannot see:
    #     task.status = (WAITING_FOR_HUMAN if ... else RepairTaskStatus.VERIFIED)
    assert len(found["workers/production_repair.py"]) == 4, (
        "the conditional VERIFIED write in the ACCEPT branch must be counted; a text scan finds only 3"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'scene.status = "approved"',
        "issue.status = 'verified'",
        "beat.status = BeatStatus.APPROVED",
        "issue.status = IssueStatus.VERIFIED.value",
        "task.status = (RepairTaskStatus.WAITING_FOR_HUMAN if x else RepairTaskStatus.VERIFIED)",
        "Scene(chapter_id=c, status=SceneStatus.APPROVED)",
    ],
)
def test_every_write_shape_is_recognised(snippet):
    """Including the two that dodge a naive pattern: `.value` on the member, and a conditional."""
    tree = ast.parse(snippet)
    values = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "status" for t in n.targets)
    ] + [kw.value for n in ast.walk(tree) if isinstance(n, ast.Call) for kw in n.keywords if kw.arg == "status"]
    assert values, f"the snippet did not parse into a status write: {snippet}"
    assert any(_writes_protected(v) for v in values), f"an authority write slipped the scan: {snippet}"


def test_a_sql_comparison_is_not_a_write():
    """`AND scenes.status = 'approved'` inside raw SQL is character-identical to a Python assignment.
    The AST excludes it by construction — it is a string, not an assignment TO a status target — which
    is why this guard no longer needs a SQL keyword heuristic that a comment could fool."""
    found = _writes_by_file()
    for reader in ("shared/migrations.py", "workers/boot_reconciliation.py"):
        assert reader not in found, f"{reader} contains SQL comparisons, not authority writes"


def test_every_open_path_names_a_ticket_and_a_reason():
    """An OPEN classification is a tracked debt, not an excuse. Each must say which ticket owns it and
    what specifically is missing — 'known issue' with no reason is how a bypass becomes permanent."""
    for rel, (_count, kind, why) in sorted(EXPECTED.items()):
        if OPEN not in kind:
            continue
        assert len(why) > 80, f"{rel}: an OPEN path needs a real explanation, not a label"
        assert "AWAITING AN OWNER" in why, f"{rel}: an OPEN path must record that it is awaiting a ruling"


def test_the_gated_and_human_paths_outnumber_the_open_ones():
    """A weak but honest health check: most routes to a protected write should already be governed.

    Recorded as a number so the direction of travel is visible in the diff — this is the count that has
    to reach zero OPEN for outcome 6 to be genuinely closed.
    """
    open_files = [rel for rel, (_c, kind, _w) in EXPECTED.items() if OPEN in kind]
    governed = [rel for rel, (_c, kind, _w) in EXPECTED.items() if OPEN not in kind]
    assert len(governed) > len(open_files), (
        f"more ungoverned routes than governed ones: OPEN={open_files} governed={governed}"
    )
    assert len(open_files) == 3, (
        f"the OPEN set changed: {open_files}. Three FILES remain (seed.py holds two of the writes): "
        "#283 C2 editorial approval, #283 C3 the seed CLI, and scene revert — which is NOT one of "
        "#283's C1-C5 and was found by this scan. Update this number ONLY when one is genuinely closed "
        "or a new one is genuinely found."
    )
