"""Enum parity fitness checks — pin cross-tier vocabularies the code documents as mirrors.

`ScenePacketVerdict` is declared to mirror `PacketVerdict` (enums.py docstring). Nothing enforced that,
so the two could silently drift. This asserts the member sets stay identical, so a divergence is a red
test instead of a runtime surprise across the two packet tiers. Pure in-process check; no DB.
"""

from __future__ import annotations

from dominion.shared.enums import PacketVerdict, ScenePacketVerdict


def test_scene_packet_verdict_mirrors_packet_verdict():
    assert {m.name: m.value for m in ScenePacketVerdict} == {m.name: m.value for m in PacketVerdict}
