"""Deterministic required-beat preservation check for repair verification (audit candidate D5).

A repair should not *drop* a required beat that the pre-repair prose contained. This module answers
exactly that — a before→after regression delta — with no LLM call, reusing the tuned keyword matcher
``scene_scope.beat_matches_prose`` (the same machinery that powers scene-scope-bleed detection).

Pure and DB-free on purpose: callers load the ``ChapterSequence`` and scene prose, then hand plain
strings/lists in here so the preservation logic can be unit-tested in isolation. The schema-facing
``RepairVerification.required_beats_preserved`` field takes only ``result.preserved``; the richer
``BeatsPreservedResult`` drives diagnostics in ``payload_json``.

Semantics (advisory only — no verdict gates on this):
  * checked            — beats were evaluated; ``preserved`` is a real answer.
  * empty_required_beats — the scene(s) had no required beats; vacuously preserved.
  * unavailable        — the sequence/body/prose/beat data was missing, so the check could not run;
                         ``preserved=True`` (absence of evidence is not a preservation failure), but
                         diagnostics say "not checked" rather than "checked clean".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from dominion.workers.scene_scope import beat_matches_prose

if TYPE_CHECKING:
    from dominion.shared.models import ChapterSequence

# Stable separator when concatenating multiple revised scenes into one chapter-region before/after
# text. Relocation of a beat between revised scenes stays "preserved" (it is still somewhere in the
# region); the marker only prevents two scenes' words from fusing across the join boundary.
SCENE_BREAK = "\n\n--- SCENE BREAK ---\n\n"


@dataclass(frozen=True)
class BeatsPreservedResult:
    preserved: bool
    status: Literal["checked", "empty_required_beats", "unavailable"]
    checked_count: int
    present_before_count: int
    dropped_beats: tuple[str, ...]
    reason: str | None = None


def ordered_unique(values: Iterable[str]) -> list[str]:
    """Order-preserving dedupe (stable diagnostics/tests, unlike a raw set)."""
    return list(dict.fromkeys(value for value in values if value))


def beats_preserved(
    before_text: str | None,
    after_text: str | None,
    required_beats: list[str] | None,
) -> BeatsPreservedResult:
    """Did the repair drop a required beat that was present before it?

    ``preserved`` is False only when a required beat matched the before-text and no longer matches the
    after-text. Missing inputs → ``unavailable`` (preserved=True, but flagged not-checked). No required
    beats → ``empty_required_beats`` (vacuously preserved).
    """
    if before_text is None or after_text is None or required_beats is None:
        return BeatsPreservedResult(
            preserved=True,
            status="unavailable",
            checked_count=0,
            present_before_count=0,
            dropped_beats=(),
            reason="Missing before_text, after_text, or required_beats.",
        )

    if not required_beats:
        return BeatsPreservedResult(
            preserved=True,
            status="empty_required_beats",
            checked_count=0,
            present_before_count=0,
            dropped_beats=(),
        )

    present_before = [beat for beat in required_beats if beat_matches_prose(beat, before_text)]
    dropped = [beat for beat in present_before if not beat_matches_prose(beat, after_text)]

    return BeatsPreservedResult(
        preserved=not dropped,
        status="checked",
        checked_count=len(required_beats),
        present_before_count=len(present_before),
        dropped_beats=tuple(dropped),
    )


def _scenes_by_no(sequence: ChapterSequence | None) -> dict[int, dict] | None:
    """Index a chapter sequence body's scene seeds by scene_no, or None if the data is unavailable."""
    body = getattr(sequence, "body", None)
    if not isinstance(body, dict):
        return None
    scenes = body.get("scenes")
    if not isinstance(scenes, list):
        return None
    indexed: dict[int, dict] = {}
    for scene in scenes:
        if isinstance(scene, dict) and scene.get("scene_no") is not None:
            try:
                indexed[int(scene["scene_no"])] = scene
            except (TypeError, ValueError):
                continue
    return indexed


def _beats_of(scene: dict) -> list[str]:
    return [beat for beat in (scene.get("required_beats") or []) if isinstance(beat, str) and beat]


def required_beats_for_scene(sequence: ChapterSequence | None, scene_no: int) -> list[str] | None:
    """The required beats authored for one scene, or None if the sequence/scene data is unavailable.

    Returns ``[]`` (not None) when the scene exists in the sequence but has no required beats — an
    empty contract, distinct from missing data.
    """
    indexed = _scenes_by_no(sequence)
    if indexed is None:
        return None
    scene = indexed.get(scene_no)
    if scene is None:
        return None
    return _beats_of(scene)


def required_beats_for_scenes(
    sequence: ChapterSequence | None,
    scene_numbers: Iterable[int],
) -> dict[int, list[str]] | None:
    """Required beats keyed by scene_no, or None if the sequence itself is unavailable.

    Scenes absent from the sequence body contribute an empty list (the caller ``.get(sn, [])``s over
    them); only a wholly-unavailable sequence returns None.
    """
    indexed = _scenes_by_no(sequence)
    if indexed is None:
        return None
    return {scene_no: _beats_of(indexed[scene_no]) for scene_no in scene_numbers if scene_no in indexed}
