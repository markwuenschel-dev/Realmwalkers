"""The effective POV of a scene.

POV is owned per chapter (Chapter.pov), but a scene may carry an OPTIONAL per-scene override on its
Beat (Beat.pov) that the author sets after beats are proposed. Everything that resolves POV for a
scene — the voice profile (PovProfile), the rolling POV summary, dialogue scoping, and the
scene-packet author — must honour the override so an overridden scene drafts in that POV's actual
voice and memory, not just under a different label. The rule lives in one place so it can't drift.

This is a leaf module (imports only the ORM models), so both the `context` and `scene_packet`
worker packages can import it without forming an import cycle.
"""

from __future__ import annotations

from dominion.shared.models import Beat, Chapter


def effective_pov(beat: Beat | None, chapter: Chapter) -> str:
    """A scene's POV = the beat's per-scene override when set, else the chapter POV.

    A blank/whitespace override — or no beat at all — inherits ``chapter.pov``, so storing ``""`` on a
    beat cleanly clears the override back to the chapter POV.
    """
    override = ((beat.pov if beat is not None else None) or "").strip()
    return override or chapter.pov
