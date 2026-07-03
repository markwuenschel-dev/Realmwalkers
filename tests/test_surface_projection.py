"""Unit tests for canonical-entity surface projection (hidden-identity handling), pure — no DB, no LLM.

Covers the shared `text_match` primitives and the drafter-facing chokepoint `beats._beat_text`, which
together guarantee a forbidden canonical name (e.g. "Roth") is never carried into reader-facing drafter
scaffolding — while internal author fields may keep it.
"""

from __future__ import annotations

from dominion.shared.text_match import (
    binding_replacements,
    project_drafter_fields,
    project_text,
)
from dominion.workers.scene_packet.beats import _beat_text

_BINDINGS = [
    {"canonical_name": "Roth", "surface_label": "the suited Astria figure", "forbidden_surface_terms": ["Roth"]},
    {"canonical_name": "Mara Valeria", "surface_label": "the missing assassin", "forbidden_surface_terms": ["Mara"]},
]


def test_binding_replacements_longest_term_first():
    reps = binding_replacements(_BINDINGS)
    # "Mara Valeria" (12 chars) must be ordered before "Mara" (4) so the multi-word alias wins.
    terms = [t for t, _ in reps]
    assert terms.index("Mara Valeria") < terms.index("Mara")
    assert ("Roth", "the suited Astria figure") in reps


def test_project_text_whole_word_case_insensitive():
    reps = binding_replacements(_BINDINGS)
    out = project_text("ROTH signals; Roth's plan; author-Roth-note aside, Mara Valeria waits.", reps)
    assert "Roth" not in out and "ROTH" not in out
    assert "the suited Astria figure signals" in out
    assert "the missing assassin waits" in out
    # whole-word only: the substring inside "author-Roth-note" hyphenated token — \b still matches around
    # the hyphen, so it is replaced; but an unrelated word like "Rothschild" must NOT be touched.
    assert project_text("Rothschild bank", reps) == "Rothschild bank"


def test_project_drafter_fields_noop_returns_same_object():
    seed = {"scene_job": "Marcus enters"}
    out, changed = project_drafter_fields(seed, binding_replacements(_BINDINGS))
    assert changed is False and out is seed


def test_beat_text_scrubs_forbidden_name():
    body = {
        "scene_job": "Roth hijacks the scrim",
        "required_beats": ["Introduce Roth without naming him", "Duel begins"],
        "exit_state": "Roth vanishes into the crowd",
        "entity_bindings": _BINDINGS,
    }
    text = _beat_text(body)
    assert text is not None
    assert "Roth" not in text
    assert "the suited Astria figure hijacks the scrim" in text
    assert "the suited Astria figure vanishes into the crowd" in text
