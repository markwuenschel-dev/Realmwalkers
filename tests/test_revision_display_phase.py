"""Pure (no-DB) unit tests for the revise seam's server-derived helpers (ADR 0028, Slice 2)."""

from __future__ import annotations

from dominion.shared.enums import RevisionRequestStatus
from dominion.workers.revision import derive_display_phase, prose_hash


def test_prose_hash_is_stable_and_content_addressed():
    assert prose_hash("Marcus arrives.") == prose_hash("Marcus arrives.")
    assert prose_hash("Marcus arrives.") != prose_hash("Marcus leaves.")
    # None and empty hash to the same digest, so a concurrency token always exists.
    assert prose_hash(None) == prose_hash("")


def test_every_status_maps_to_a_human_phase():
    for status in RevisionRequestStatus:
        phase, action = derive_display_phase(status.value)
        assert phase and phase != status.value  # a human phrase, not the raw enum value
        assert action is None or isinstance(action, str)


def test_awaiting_contract_phase_names_the_missing_contract():
    phase, action = derive_display_phase(RevisionRequestStatus.AWAITING_CONTRACT.value)
    assert phase == "Preparing contract"
    assert action is not None and "contract" in action.lower()


def test_unknown_status_degrades_to_itself():
    # Forward-compatible: a status this build doesn't know still renders (as itself, no action) rather
    # than raising in the response serializer.
    phase, action = derive_display_phase("some_future_status")
    assert phase == "some_future_status" and action is None
