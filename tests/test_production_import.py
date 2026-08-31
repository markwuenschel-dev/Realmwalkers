"""C5a guard: production.py's production_sequence/production_repair imports are hoisted to module scope,
so the module graph must stay acyclic. A fresh interpreter importing the module is the real check — an
in-process import can be masked by modules already loaded by the suite. No DB.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_production_module_imports_without_cycle():
    src = Path(__file__).resolve().parent.parent / "src"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(src), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run(
        [sys.executable, "-c", "import dominion.workers.production"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"importing dominion.workers.production failed:\n{result.stderr}"


def test_production_fidelity_module_imports_without_cycle():
    """REPAIR-GOD: the extracted SceneFidelity production lane imports cleanly in a fresh interpreter
    (it pulls in production_repair for the public queue seam — that direction is fine)."""
    src = Path(__file__).resolve().parent.parent / "src"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(src), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run(
        [sys.executable, "-c", "import dominion.workers.production_fidelity"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"importing dominion.workers.production_fidelity failed:\n{result.stderr}"


def test_production_repair_does_not_import_production_fidelity():
    """One-way seam (REPAIR-GOD): production_fidelity imports production_repair, NEVER the reverse. A
    fresh interpreter importing only production_repair must not transitively load production_fidelity —
    otherwise the two lanes form a cycle."""
    src = Path(__file__).resolve().parent.parent / "src"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(src), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, dominion.workers.production_repair; "
            "assert 'dominion.workers.production_fidelity' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"production_repair transitively imported production_fidelity (import graph is not one-way):\n{result.stderr}"
    )


def test_production_re_exports_no_private_names():
    """PROD-FACADE: an underscore name must not be reachable through this module.

    Three private helpers used to be re-exported here, and the reason was never accidental:
    pipeline.py called `prod._block_production_on_timeline_failure` across a module boundary,
    so the private-name convention was being defeated by design. Nothing enforced it -- ruff
    selects only E/F/I/UP/B, and the check this test replaces was a hardcoded five-name
    allow-list that could not see a new one. This is the rule, not a list."""
    import dominion.workers.production as production

    leaked = [n for n in dir(production) if n.startswith("_") and not n.startswith("__")]
    assert not leaked, f"private name(s) reachable through the production module: {leaked}"


def test_deleted_passthrough_shims_stay_deleted():
    """PROD-FACADE: the module re-exports what its callers reach through it, and nothing else.

    Every name below was reachable through this module and is not any more. Most had no caller
    outside it: some had none at all, some only this module itself, some only tests importing them
    from here. One did have a production caller -- pipeline.py reached a private helper through
    this module, which is precisely why it should not have been re-exported. They now live where
    they are defined.
    Resurrecting one re-creates the indirection without re-creating a reason for it."""
    import dominion.workers.production as production

    deleted = (
        # removed earlier, kept pinned
        "_int_or_none",
        "_roster_name_tokens",
        "_contract_item",
        # no caller anywhere
        "triage_scene_fidelity_for_production",
        # called only by this module; callers now name production_sequence
        "derive_contract_classification",
        "ensure_chapter_sequence",
        "ensure_draft_run_timeline",
        "_scene_packet_map",
        "_latest_scene_map",
        # called by this module and by tests
        "assemble_run",
        # reached only from tests, which now import production_sequence
        "latest_draft_timeline",
        "derive_chapter_sequence",
        "chain_scene_entry_states",
        "run_chapter_draft_qa",
        "evaluate_chapter_sequence",
        # re-exported so pipeline.py could reach a private helper; it names the owner now
        "_block_production_on_timeline_failure",
    )
    for name in deleted:
        assert not hasattr(production, name), f"deleted facade shim {name!r} reappeared"
