"""Style audit over author-pasted prose — the first reader of the style docs on the JUDGING side.

No DB and no model: `load_style_document` and the LLM call are both patched, so every test here is
about the audit's own decisions — which standards it assembles, how it scopes the drift patterns from
the passage's own evidence, and above all what it REFUSES to pass through. The evidence guard is the
reason this module can be trusted at all: a rule citation the author cannot find in their own text
makes the audit unfalsifiable, so a fabricated quote must be dropped and counted, never rendered.
"""

from __future__ import annotations

import pytest

from dominion.shared.enums import Severity
from dominion.workers.context.forbidden_drift import AUDIT, cast_present_in, scope_forbidden_drift
from dominion.workers.reviewers import style_audit

PROSE = (
    "Marcus set the cup down without drinking. Serra watched him do it and said nothing, "
    "which was its own kind of answer. He'd been on the other side of that exact decision before."
)

DRIFT_DOC = """# Forbidden Drift

## The Drift Patterns

### 1. Horror-Author Pastiche  ·  `[GENRE] [PROSE]`

**What it is:** Atmosphere without interiority.

**Warning signs:**
- Three or more sentences of dread with no character reaction

**Correction:** Return to interiority.

---

### 10. Serra Flattening  ·  `[RELATIONSHIP] [CANON]`

**What it is:** Serra becomes agreeable furniture.

**Warning signs:**
- She agrees with Marcus without friction

**Correction:** Restore her separate agenda.

---

### 14. Under-Rendered Combat  ·  `[CHOREOGRAPHY]`

**What it is:** The exchange is summarized rather than staged.

**Warning signs:**
- A fight resolves in one sentence

**Correction:** Stage the exchange beat by beat.
"""


def _patch_standards(monkeypatch, *, docs: dict[str, str]):
    """Patch `load_style_document` to serve `docs` keyed by the tail of the configured path."""

    async def fake_load(_session, path: str) -> str | None:
        for key, content in docs.items():
            if path.endswith(f"{key}.md"):
                return content
        return None

    monkeypatch.setattr(style_audit, "load_style_document", fake_load)


def _patch_model(monkeypatch, raw: str):
    async def fake_complete(**_kwargs):
        return raw, {}

    monkeypatch.setattr(style_audit, "complete_with_rate_limit_fallback", fake_complete)


# --- the evidence guard ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fabricated_quote_is_dropped_and_counted(monkeypatch):
    """A finding whose quote is not in the passage never reaches the author, and the drop is visible.

    Silently discarding it would be almost as bad as rendering it: the author would see a short list
    and have no way to know the run was inventing evidence."""
    _patch_standards(monkeypatch, docs={"prose_contract": "1. Interiority is the engine."})
    _patch_model(
        monkeypatch,
        """[
          {"rule": "contract 1", "rule_source": "prose_contract", "severity": "warn",
           "quote": "He drew his sword and the room went silent.",
           "new_text": "x", "why": "Named emotion."},
          {"rule": "contract 2", "rule_source": "prose_contract", "severity": "warn",
           "quote": "Marcus set the cup down without drinking.",
           "new_text": "Marcus set the cup down.", "why": "Detail does not earn its place."}
        ]""",
    )
    result = await style_audit.audit_prose(None, PROSE)

    assert result.fabricated_dropped == 1
    assert [s.rule for s in result.findings] == ["contract 2"]


@pytest.mark.asyncio
async def test_typographic_variation_is_not_treated_as_fabrication(monkeypatch):
    """Curly quotes and collapsed whitespace are how a model routinely quotes prose back. Rejecting
    those would suppress real findings, which is the worse failure — `quote_is_supported` folds them."""
    _patch_standards(monkeypatch, docs={"prose_contract": "rules"})
    _patch_model(
        monkeypatch,
        '[{"rule": "R2", "rule_source": "prose_clarity_rules", "severity": "warn", '
        '"quote": "He’d been on the   other side of that exact decision before.", '
        '"why": "Refers to an event never staged."}]',
    )
    result = await style_audit.audit_prose(None, PROSE)

    assert result.fabricated_dropped == 0
    assert len(result.findings) == 1


@pytest.mark.asyncio
async def test_two_rules_on_the_same_span_collapse_to_one(monkeypatch):
    """`tokenize` drops overlapping markers silently (prose.ts:559), so a second finding on the same
    span would vanish from the page while still being counted. Collapse it here instead."""
    _patch_standards(monkeypatch, docs={"prose_contract": "rules"})
    quote = "Serra watched him do it and said nothing"
    _patch_model(
        monkeypatch,
        f'[{{"rule": "R6", "rule_source": "prose_clarity_rules", "severity": "warn", '
        f'"quote": "{quote}", "why": "First."}},'
        f'{{"rule": "contract 2", "rule_source": "prose_contract", "severity": "info", '
        f'"quote": "{quote}", "why": "Second."}}]',
    )
    result = await style_audit.audit_prose(None, PROSE)

    assert [s.rule for s in result.findings] == ["R6"]


# --- suggestion shape -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_absent_new_text_and_empty_new_text_stay_distinct(monkeypatch):
    """None = diagnosis only; "" = the fix is a deletion. Collapsing them into one falsy check would
    turn every diagnosis into a proposal to delete the sentence."""
    _patch_standards(monkeypatch, docs={"prose_contract": "rules"})
    _patch_model(
        monkeypatch,
        '[{"rule": "a", "rule_source": "prose_contract", "severity": "info", '
        '"quote": "Marcus set the cup down without drinking.", "why": "Diagnosis only."},'
        '{"rule": "b", "rule_source": "prose_contract", "severity": "info", '
        '"quote": "which was its own kind of answer", "new_text": "", "why": "Cut it."}]',
    )
    result = await style_audit.audit_prose(None, PROSE)

    assert result.findings[0].new_text is None
    assert result.findings[1].new_text == ""


@pytest.mark.asyncio
async def test_no_op_replacement_keeps_the_diagnosis_and_drops_the_replacement(monkeypatch):
    """A replacement identical to the quote renders as a change that changes nothing. The fault it
    names may still be real, so the finding survives without the useless edit."""
    _patch_standards(monkeypatch, docs={"prose_contract": "rules"})
    quote = "Marcus set the cup down without drinking."
    _patch_model(
        monkeypatch,
        f'[{{"rule": "a", "rule_source": "prose_contract", "severity": "warn", '
        f'"quote": "{quote}", "new_text": "{quote}", "why": "Still a real fault."}}]',
    )
    result = await style_audit.audit_prose(None, PROSE)

    assert len(result.findings) == 1
    assert result.findings[0].new_text is None


@pytest.mark.asyncio
async def test_unknown_rule_source_is_labelled_not_trusted(monkeypatch):
    _patch_standards(monkeypatch, docs={"prose_contract": "rules"})
    _patch_model(
        monkeypatch,
        '[{"rule": "?", "rule_source": "strunk_and_white", "severity": "warn", '
        '"quote": "Marcus set the cup down without drinking.", "why": "Invented standard."}]',
    )
    result = await style_audit.audit_prose(None, PROSE)

    assert result.findings[0].rule_source == "unattributed"


@pytest.mark.asyncio
async def test_block_severity_is_reachable_for_the_one_test(monkeypatch):
    """Unlike the advisory reviewers, the audit can return BLOCK — `prose_clarity_rules.md` calls The
    One Test a hard gate. It gates nothing in the pipeline; it ranks for the author's eye."""
    _patch_standards(monkeypatch, docs={"prose_clarity_rules": "The One Test"})
    _patch_model(
        monkeypatch,
        '[{"rule": "The One Test", "rule_source": "prose_clarity_rules", "severity": "block", '
        '"quote": "Marcus set the cup down without drinking.", "why": "Beat unreconstructable."}]',
    )
    result = await style_audit.audit_prose(None, PROSE)

    assert result.findings[0].severity is Severity.BLOCK


@pytest.mark.asyncio
async def test_malformed_model_output_yields_no_findings_not_an_error(monkeypatch):
    _patch_standards(monkeypatch, docs={"prose_contract": "rules"})
    _patch_model(monkeypatch, "I could not find anything wrong with this passage.")
    result = await style_audit.audit_prose(None, PROSE)

    assert result.findings == []
    assert result.standards_loaded == ["prose_contract"]


# --- standards assembly ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_standards_are_reported_not_hidden(monkeypatch):
    """A run with half its standards missing looks identical in the suggestion list, so the caller is
    told which ones arrived."""
    _patch_standards(monkeypatch, docs={"prose_contract": "rules"})
    _patch_model(monkeypatch, "[]")
    result = await style_audit.audit_prose(None, PROSE)

    assert result.standards_loaded == ["prose_contract"]
    assert set(result.standards_missing) == {"prose_clarity_rules", "voice_guide", "forbidden_drift"}


@pytest.mark.asyncio
async def test_no_standards_at_all_returns_empty_loaded_without_calling_the_model(monkeypatch):
    """Clean prose and un-auditable prose must not look the same. With nothing to judge against the
    audit reports empty `standards_loaded` — the router turns that into a 503 rather than an all-clear."""
    _patch_standards(monkeypatch, docs={})

    async def explode(**_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the model was called with no standards loaded")

    monkeypatch.setattr(style_audit, "complete_with_rate_limit_fallback", explode)
    result = await style_audit.audit_prose(None, PROSE)

    assert result.standards_loaded == []
    assert result.findings == []


@pytest.mark.asyncio
async def test_empty_prose_short_circuits(monkeypatch):
    async def explode(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("audited an empty passage")

    monkeypatch.setattr(style_audit, "load_style_document", explode)
    result = await style_audit.audit_prose(None, "   ")

    assert result.findings == []


# --- drift scoping from the passage's own evidence -------------------------------------------------


def test_cast_present_in_reads_the_roster_off_the_page():
    assert cast_present_in(PROSE) == {"marcus", "serra"}
    assert cast_present_in("The room was empty and cold.") == set()
    # Substring hits do not count — "Brentwood" is not Brent.
    assert cast_present_in("They met at Brentwood station.") == set()


def test_audit_mode_carries_warning_signs_that_draft_mode_omits():
    """The whole reason AUDIT mode exists: a reviewer needs the symptoms, a drafter needs only the
    correction. This is the first thing that has ever asserted the difference."""
    audit = scope_forbidden_drift(DRIFT_DOC, pov="Marcus", present={"marcus", "serra"}, signals="", mode=AUDIT)
    draft = scope_forbidden_drift(DRIFT_DOC, pov="Marcus", present={"marcus", "serra"}, signals="")

    assert "Warning signs" in audit
    assert "Warning signs" not in draft


def test_relationship_patterns_load_only_when_a_second_cast_member_is_present():
    """`Serra Flattening` is `[RELATIONSHIP] [CANON]`. With Marcus alone on the page it is noise; with
    Serra there it is the point."""
    with_serra = scope_forbidden_drift(
        DRIFT_DOC, pov="Marcus", present=cast_present_in(PROSE), signals=PROSE, mode=AUDIT
    )
    alone = scope_forbidden_drift(
        DRIFT_DOC, pov="Marcus", present=cast_present_in("Marcus set the cup down."), signals="", mode=AUDIT
    )

    assert "Serra Flattening" in with_serra
    assert "Serra Flattening" not in alone


def test_choreography_patterns_load_from_physical_vocabulary_in_the_passage():
    """`signals` is the passage itself, so the text decides whether combat diagnostics apply."""
    fight = scope_forbidden_drift(
        DRIFT_DOC, pov="Marcus", present={"marcus"}, signals="He parried the blade and struck back.", mode=AUDIT
    )
    quiet = scope_forbidden_drift(DRIFT_DOC, pov="Marcus", present={"marcus"}, signals=PROSE, mode=AUDIT)

    assert "Under-Rendered Combat" in fight
    assert "Under-Rendered Combat" not in quiet
