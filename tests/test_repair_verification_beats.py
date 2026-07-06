"""Contract for the required_beats_preserved verification flag (audit candidate D5, 2026-07-06).

RepairVerification.required_beats_preserved is an advisory flag shown to the human approver. The
single-scene verify path derived it from real evidence (the revised scene stayed bound to its scene
packet, so its required beats weren't dropped), but the chapter-scoped path reported a bare
``bool(task.instructions)`` proxy that was effectively always True — telling the operator "beats
preserved" even when a revised scene had been re-bound to a different packet.

Both paths now share one definition via ``_required_beats_preserved``. This pins that contract: the
packet binding must actually be preserved, not just an instruction present.
"""

from __future__ import annotations

from dominion.workers.production_repair import _required_beats_preserved


def test_not_preserved_when_scene_rebound_to_different_packet():
    # The regression the chapter-scoped fix closes: re-binding a scene to a different packet drops
    # that scene's required beats, so the flag is False even though the repair carried an instruction
    # (the old bool(task.instructions) proxy wrongly reported True here).
    assert _required_beats_preserved(packet_binding_preserved=False, instruction_present=True) is False


def test_preserved_when_binding_intact_and_instructed():
    assert _required_beats_preserved(packet_binding_preserved=True, instruction_present=True) is True


def test_not_preserved_without_an_instruction():
    assert _required_beats_preserved(packet_binding_preserved=True, instruction_present=False) is False
