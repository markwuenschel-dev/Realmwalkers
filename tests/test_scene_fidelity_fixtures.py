"""SceneFidelity fixture corpus loader + the merge gate every other lane must satisfy.

This is Lane 8 (Phase A): it runs at T0, before any SceneFidelity code exists, and stays active
through every merge. The fixture files under ``tests/fixtures/scene_fidelity/`` are the shared,
versioned corpus (ADR 0015): ``hard`` fixtures must pass exactly, ``delta_reviewed`` fixtures require a
written false-positive / false-negative delta review before a prompt/model/policy change ships, and
``exploratory`` fixtures are non-blocking. Later lanes import ``load_fixture`` / ``load_fixture_manifest``
from this module to drive their own contract, evaluator, policy, and end-to-end tests against one corpus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "scene_fidelity"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"

KNOWN_CLASSES = {"hard", "delta_reviewed", "exploratory"}

# Where a fixture file lives, by class. The file for id X of class C is <CLASS_DIR[C]>/X.json.
CLASS_DIR = {"hard": "hard", "delta_reviewed": "delta", "exploratory": "exploratory"}

# Every fixture file carries these keys; `prose` is present only on evaluator/policy fixtures.
_REQUIRED_FIXTURE_KEYS = {"id", "class", "title", "purpose", "packet", "expect"}


def load_fixture_manifest() -> dict[str, Any]:
    """Load and structurally validate the corpus manifest."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("fixture manifest is not a JSON object")
    if manifest.get("schema_version") != 1:
        raise ValueError(f"unsupported fixture manifest schema_version: {manifest.get('schema_version')!r}")
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("fixture manifest has no fixtures")
    for entry in fixtures:
        if not isinstance(entry, dict) or "id" not in entry or "class" not in entry:
            raise ValueError(f"malformed manifest entry: {entry!r}")
    return manifest


def fixture_path(fixture_id: str, fixture_class: str) -> Path:
    if fixture_class not in CLASS_DIR:
        raise ValueError(f"unknown fixture class {fixture_class!r} for {fixture_id!r}")
    return FIXTURE_ROOT / CLASS_DIR[fixture_class] / f"{fixture_id}.json"


def load_fixture(fixture_id: str) -> dict[str, Any]:
    """Load one fixture by id (resolving its class from the manifest) and validate its schema."""
    manifest = load_fixture_manifest()
    entry = next((e for e in manifest["fixtures"] if e["id"] == fixture_id), None)
    if entry is None:
        raise KeyError(f"no manifest entry for fixture id {fixture_id!r}")
    return _load_and_validate(entry["id"], entry["class"])


def iter_fixtures(fixture_class: str | None = None) -> list[dict[str, Any]]:
    """Load every fixture (optionally filtered to one class), each schema-validated."""
    manifest = load_fixture_manifest()
    out: list[dict[str, Any]] = []
    for entry in manifest["fixtures"]:
        if fixture_class is not None and entry["class"] != fixture_class:
            continue
        out.append(_load_and_validate(entry["id"], entry["class"]))
    return out


def _load_and_validate(fixture_id: str, fixture_class: str) -> dict[str, Any]:
    path = fixture_path(fixture_id, fixture_class)
    if not path.exists():
        raise FileNotFoundError(f"fixture {fixture_id!r} ({fixture_class}) missing at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = _REQUIRED_FIXTURE_KEYS - data.keys()
    if missing:
        raise ValueError(f"fixture {fixture_id!r} missing keys: {sorted(missing)}")
    if data["id"] != fixture_id:
        raise ValueError(f"fixture {fixture_id!r} declares mismatched id {data['id']!r}")
    if data["class"] != fixture_class:
        raise ValueError(f"fixture {fixture_id!r} declares class {data['class']!r}, manifest says {fixture_class!r}")
    if data["class"] not in KNOWN_CLASSES:
        raise ValueError(f"fixture {fixture_id!r} has unknown class {data['class']!r}")
    if not isinstance(data["packet"], dict):
        raise ValueError(f"fixture {fixture_id!r} packet is not an object")
    if not isinstance(data["expect"], dict) or not data["expect"]:
        raise ValueError(f"fixture {fixture_id!r} has an empty expect block")
    _validate_packet_shape(fixture_id, data["packet"])
    return data


def _validate_packet_shape(fixture_id: str, packet: dict[str, Any]) -> None:
    """A packet is either legacy/inert (no fidelity fields) or a forward-only active contract that
    declares fidelity_contract_version:1 and a fidelity_requirements list (ADR 0025)."""
    version = packet.get("fidelity_contract_version")
    requirements = packet.get("fidelity_requirements")
    if version is None and requirements is None:
        return  # legacy / inert packet — correct by construction
    if version != 1:
        raise ValueError(f"fixture {fixture_id!r} active packet must declare fidelity_contract_version:1")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError(f"fixture {fixture_id!r} active packet has no fidelity_requirements")
    for req in requirements:
        if not isinstance(req, dict):
            raise ValueError(f"fixture {fixture_id!r} has a non-object requirement")
        for key in ("requirement_id", "mode", "post_draft_policy", "clauses"):
            if key not in req:
                raise ValueError(f"fixture {fixture_id!r} requirement missing {key!r}")


# --------------------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------------------


def test_fixture_manifest_has_unique_ids_and_known_classes() -> None:
    manifest = load_fixture_manifest()
    classes = {item["class"] for item in manifest["fixtures"]}
    assert classes <= KNOWN_CLASSES
    ids = [item["id"] for item in manifest["fixtures"]]
    assert len(set(ids)) == len(ids)


def test_every_manifest_entry_has_a_loadable_fixture() -> None:
    manifest = load_fixture_manifest()
    for entry in manifest["fixtures"]:
        fixture = load_fixture(entry["id"])
        assert fixture["id"] == entry["id"]
        assert fixture["class"] == entry["class"]
        assert fixture["title"].strip()
        assert fixture["purpose"].strip()


def test_corpus_covers_the_required_hard_contracts() -> None:
    """The hard tier must pin every invariant Lane 8 Phase A owns (malformed active requirements, stale
    reports, invalid anchors, dependency cycles, no override inheritance, forward-only legacy packets),
    plus the flagship agency-loss case."""
    hard_ids = {f["id"] for f in iter_fixtures("hard")}
    required = {
        "serra_agency_loss",
        "stale_report_is_operational_hold",
        "malformed_active_requirement_blocks_approval",
        "invalid_anchor_is_report_only",
        "dependency_cycle_rejected",
        "override_does_not_inherit",
        "legacy_packet_is_inert",
    }
    assert required <= hard_ids, f"missing required hard fixtures: {sorted(required - hard_ids)}"


def test_all_five_modes_appear_in_the_corpus() -> None:
    """Closed mode registry coverage (ADR 0011): every typed mode must be exercised by at least one
    active fixture so no adapter ships without a fixture."""
    expected = {"relationship_turn", "intimacy_blocking", "combat_blocking", "spatial_affordance", "reader_movie"}
    modes: set[str] = set()
    for fixture in iter_fixtures():
        for req in fixture["packet"].get("fidelity_requirements", []) or []:
            modes.add(req["mode"])
    assert modes == expected, f"corpus is missing modes: {sorted(expected - modes)}"
