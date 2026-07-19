"""Pin the ch1_bad_run golden fixtures to the CURRENT Pydantic output schemas.

The ch1_bad_run goldens (tests/fixtures/ch1_bad_run/, captured from failing run
51d635ec) are the recorded API-response shapes the pipeline regression suite asserts
against. Those assertions read raw dict keys, so a schema that drifts away from the
captured production shape would leave the whole suite GREEN against a stale contract.

These tests close that gap: each golden is re-validated through the live output schema
it was produced by (`model_validate`). If a fixture stops validating cleanly, the
schema has drifted from the shape the pipeline actually emits (or the fixture is stale)
— either way we want a LOUD failure here, not silent green elsewhere.

Schema map (see src/dominion/shared/schemas.py):
  * production_run_detail.json -> ProductionRunDetailOut
  * scene_packets.json (each entry) -> ScenePacketOut
  * chapter_packet.json -> PacketOut

PacketOut (not a nonexistent "ChapterPacketOut") is the chapter-packet output schema:
it is the response_model the packets router returns for a chapter knowledge packet, and
its 18 fields cover every key in chapter_packet.json. It is defined in
dominion.shared.schemas and merely re-exported by dominion.api.routers.packets; importing
it from schemas keeps this test free of the router's DB/FastAPI wiring.

Pure JSON + Pydantic validation: no database, no network.
"""

from __future__ import annotations

import ch1_bad_run_fixtures as fx
import pytest

from dominion.shared.schemas import PacketOut, ProductionRunDetailOut, ScenePacketOut


def test_production_run_detail_matches_current_schema():
    """production_run_detail.json must still validate through ProductionRunDetailOut."""
    ProductionRunDetailOut.model_validate(fx.production_run_detail())


@pytest.mark.parametrize("scene_no", [1, 2, 3, 4])
def test_scene_packet_matches_current_schema(scene_no):
    """Each approved scene packet must still validate through ScenePacketOut."""
    packet = next(p for p in fx.scene_packets() if int(p["scene_no"]) == scene_no)
    ScenePacketOut.model_validate(packet)


def test_chapter_packet_matches_current_schema():
    """chapter_packet.json must still validate through PacketOut (the chapter-packet
    output schema — there is no ChapterPacketOut)."""
    PacketOut.model_validate(fx.chapter_packet())
