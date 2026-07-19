"""ARTIFACT-TYPE: Artifact.artifact_type has one authoritative vocabulary (enums.ArtifactType). Every
`artifact_type=` write and `artifact_type==` filter literal in the tree must be a registered member, so a
new value or a drifted filter fails here; and the (formerly triplicated) scene_fidelity_report constant
now resolves to the enum in one place."""

from __future__ import annotations

import re
from pathlib import Path

from dominion.shared.enums import ArtifactType

_SRC = Path(__file__).resolve().parent.parent / "src" / "dominion"
_LITERAL = re.compile(r'artifact_type\s*==?\s*"([a-z_]+)"')  # matches both `= "x"` writes and `== "x"` filters


def test_every_artifact_type_literal_is_registered():
    valid = {t.value for t in ArtifactType}
    offenders: dict[str, set[str]] = {}
    for py in _SRC.rglob("*.py"):
        for value in _LITERAL.findall(py.read_text(encoding="utf-8")):
            if value not in valid:
                offenders.setdefault(str(py.relative_to(_SRC)), set()).add(value)
    assert not offenders, f"artifact_type literals missing from ArtifactType: {offenders}"


def test_fidelity_report_constant_consolidated_to_enum():
    from dominion.api.routers.scenes import _FIDELITY_REPORT_TYPE as scenes_const
    from dominion.workers.production_repair import _FIDELITY_REPORT_TYPE as repair_const
    from dominion.workers.scene_fidelity.evaluator import REPORT_ARTIFACT_TYPE

    assert REPORT_ARTIFACT_TYPE == scenes_const == repair_const == ArtifactType.SCENE_FIDELITY_REPORT.value
