"""Wiring smoke: the whole package imports without a database or API key."""

from __future__ import annotations


def test_app_and_worker_import() -> None:
    import dominion.api.main as api_main
    import dominion.workers.pipeline  # noqa: F401
    import dominion.workers.worker  # noqa: F401

    assert api_main.app is not None


def test_models_metadata_has_core_tables() -> None:
    from dominion.shared.models import Base

    tables = set(Base.metadata.tables)
    assert {"scenes", "beats", "jobs", "approvals", "critiques", "character_state"} <= tables
