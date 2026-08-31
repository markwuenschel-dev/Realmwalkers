"""The named drift patterns reach the drafter, scoped to the scene that is actually being written.

`forbidden_drift.md` documented twenty-four ways this story's prose can fail and **nothing read it** —
not the drafting context, not the RAG index. It shaped no generated sentence. This wires it in.

TWO THINGS THESE TESTS EXIST TO STOP, and neither is "does the string appear":

1. **Unscoped injection.** Handing the drafter all twenty-four entries whole is ~6,350 tokens against a
   measured ~5,440-token draft — it more than doubles input cost on a pipeline where contract
   derivation is already most of the spend. There is a test that fails if DRAFT mode grows back toward
   the full document.

2. **Scoping that does not scope.** The first implementation gated CANON on "any cast member present",
   which is unconditional in practice because Marcus is in every scene. It looked like scoping and was
   not. The tests below assert on *which* patterns are dropped, not merely that something was.
"""

from __future__ import annotations

import pytest

from dominion.workers.context.forbidden_drift import AUDIT, DRAFT, scope_forbidden_drift

# A miniature of the real file's shape: tagged headings, warning signs, a Correction line.
DOC = """# Forbidden Drift

## The Drift Patterns

### 1. Horror-Author Pastiche  ·  `[GENRE] [PROSE]`

**What it is:** Atmosphere without interiority.

**Warning signs:**
- Three sentences of dread with no character reaction

**Correction:** Return to the POV's interiority.

---

### 10. Serra Flattening  ·  `[RELATIONSHIP] [CANON]`

**What it is:** Serra exists only in relation to Marcus, with no independent want driving her scenes.

**Warning signs:**
- Every Serra scene is about Marcus
- Her competence appears only when it rescues him
- She has no goal that would survive his absence
- Her dialogue exists to react rather than to pursue
- Removing Marcus from the chapter would leave her with nothing to do

**Correction:** Give Serra something she wants that has nothing to do with Marcus.

---

### 14. Under-Rendered Combat  ·  `[CHOREOGRAPHY]`

**What it is:** The exchange is summarized rather than staged, and the reader is handed a result.

**Warning signs:**
- A blow lands with no described approach
- Distance between combatants is never established
- The result arrives before the motion that produced it
- Injuries appear without an impact on the page
- You cannot diagram the exchange beat by beat from the prose

**Correction:** Stage every load-bearing beat on the page.

---

### 15. Permanent-Party Assumption  ·  `[CANON] [RELATIONSHIP]`

**What it is:** The six are written as a stable, co-equal, permanent party rather than a fracturing origin cohort.

**Warning signs:**
- The group makes decisions as a unit with no friction
- Nobody's loyalty is contingent on anything
- The roster is treated as settled
- Long-term plans assume everyone is still present for them
- Conflict resolves toward cohesion by default

**Correction:** Write them as an origin cohort, not a final party.

---

### 16. Healer-Trope Death (Brent)  ·  `[CANON] [RELATIONSHIP]`

**What it is:** Caretaker melodrama instead of structural cost.

**Warning signs:**
- The death is framed as sacrifice-for-the-party
- Grief occupies more page-time than consequence
- The group's capability is unchanged afterward
- His last words explain his own theme
- The scene reaches for tears before it reaches for cost

**Correction:** Make the loss structural, not sentimental.

---

### 20. Interpretive Overprocessing  ·  `[PROSE] [VOICE]`

**What it is:** The prose keeps thinking after the meaningful thinking is done.

**Warning signs:**
- Behavior communicates an emotion and the next sentence names it
- A usable conclusion is followed by two alternatives that change nothing
- `not exactly`, `almost`, `seemed` clustering in neighboring paragraphs
- A concrete image immediately translated into its abstract meaning
- The narration answers a question the reader already answered

**Correction:** Stop at the last thought that changes understanding, choice, or behavior.

---

## Quick Diagnostic
"""


def _nums(block: str) -> list[str]:
    return [ln.split(".")[0] for ln in block.split("\n") if ln[:1].isdigit()]


def test_a_solo_quiet_scene_drops_relationship_canon_and_choreography():
    """The headline case. Marcus alone on a hillside cannot violate Brent's death shape."""
    out = scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus"], signals="arrival, observation")
    kept = _nums(out)
    assert "1" in kept, "an always-on GENRE/PROSE pattern was dropped"
    assert "20" in kept, "an always-on PROSE/VOICE pattern was dropped"
    assert "10" not in kept, "Serra Flattening loaded with no Serra on the page"
    assert "14" not in kept, "combat choreography loaded into a scene with no physical action"
    assert "16" not in kept, "Brent's death pattern loaded with no Brent on the page"


def test_the_pov_alone_does_not_unlock_canon():
    """The bug the first implementation shipped.

    CANON gated on "any cast member present" reads as scoping and is unconditional in practice,
    because the POV is cast and the POV is in every scene. Marcus alone must NOT unlock canon.
    """
    out = scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus"], signals="")
    # Pattern 15 is the one that isolates this: CANON + RELATIONSHIP, and NO character in its title.
    # Asserting on 16 instead would pass even with the bug present, because 16 is dropped by the title
    # gate (no Brent on the page) regardless of whether the family gate works — which is precisely how
    # an earlier version of this test passed against a broken build.
    assert "15" not in _nums(out), "the POV's own presence unlocked CANON — the scoping is decorative"
    assert "16" not in _nums(out)


def test_a_second_character_unlocks_relationship_and_canon():
    out = scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus", "Serra"], signals="conversation")
    kept = _nums(out)
    assert "10" in kept, "Serra on the page did not load Serra Flattening"
    assert "16" not in kept, "Brent's pattern loaded for a scene Brent is not in"


def test_physical_signals_unlock_choreography():
    quiet = _nums(scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus"], signals="a quiet meal"))
    fight = _nums(scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus"], signals="he parried the blade"))
    assert "14" not in quiet
    assert "14" in fight, "combat prose did not load the choreography pattern"


def test_draft_mode_carries_corrections_not_warning_signs():
    """The design decision, pinned.

    A warning sign teaches a model what the failure looks like; only the correction tells it what to
    write instead. If DRAFT mode ever starts shipping warning signs, the injection doubles in size and
    gets worse at its job simultaneously.
    """
    out = scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus"], signals="", mode=DRAFT)
    assert "Return to the POV's interiority." in out, "the correction is missing"
    assert "Warning signs" not in out, "DRAFT mode shipped warning signs"
    assert "**What it is:**" not in out, "DRAFT mode shipped full entry bodies"


def test_audit_mode_carries_the_full_entry():
    out = scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus"], signals="", mode=AUDIT)
    assert "**Warning signs:**" in out, "AUDIT mode dropped the warning signs a reviewer needs"
    assert "**What it is:**" in out


def test_draft_mode_stays_far_smaller_than_audit_mode():
    """The cost guard. Measured drafting input is ~5,440 tokens/call; the full document is ~6,350.

    This does not assert an absolute budget — the file is meant to grow. It asserts the *ratio*, which
    is what actually protects the draft prompt as patterns are added.
    """
    kw = dict(pov="Marcus", present=["Marcus", "Brent"], signals="combat")
    draft = scope_forbidden_drift(DOC, mode=DRAFT, **kw)
    audit = scope_forbidden_drift(DOC, mode=AUDIT, **kw)
    assert len(draft) < len(audit) / 2, (
        f"DRAFT mode is no longer materially cheaper than AUDIT ({len(draft)} vs {len(audit)}); "
        "the injection is drifting back toward the whole document"
    )


def test_the_recurrence_rule_travels_with_the_patterns():
    """Every individual construction is legal once. Shipping the list without that sentence turns a
    craft guide into a list of banned words, which is the failure mode the file itself warns about."""
    out = scope_forbidden_drift(DOC, pov="Marcus", present=["Marcus"], signals="")
    assert "RECURRENCE" in out


# The 'absence must never fail a draft' contract for drift patterns is no longer this module's to
# hold: resolution moved to style_source.load_style_document, and
# tests/test_style_documents.py::test_neither_source_returns_none_and_does_not_raise covers the case
# where the patterns are in neither Postgres nor on disk.


def test_the_real_file_parses_and_every_pattern_is_reachable():
    """Guards the coupling between the document and this parser.

    The loader keys on `### N. Title · \\`[TAGS]\\``. An edit that drops a tag makes that pattern
    silently unreachable — no error, just guidance that stops being applied. This is the test that
    notices.
    """
    from pathlib import Path

    doc = Path("series/style/forbidden_drift.md")
    if not doc.exists():  # creative content is local-only; CI has no series/
        pytest.skip("series/ is gitignored and absent in this environment")

    text = doc.read_text(encoding="utf-8")
    headings = [ln for ln in text.split("\n") if ln.startswith("### ") and ln[4:5].isdigit()]
    everything = scope_forbidden_drift(
        text, pov="Marcus", present=["Marcus", "Serra", "Brent", "Mathias", "Mara", "Sebastian"], signals="combat"
    )
    assert len(_nums(everything)) == len(headings), (
        f"{len(headings)} patterns in the file but {len(_nums(everything))} reachable — "
        "one is missing its family tag or its Correction line"
    )
