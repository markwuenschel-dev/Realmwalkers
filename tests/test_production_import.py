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


def test_facade_carries_no_dead_passthrough_shims():
    """PROD-FACADE: the facade must not carry zero-caller pass-through shims. The deleted ones stay
    deleted; the live wrappers (real callers: derive_contract_classification internally, the rest via
    tests) stay. This pins the deletion so a future 'complete the re-export surface' refactor can't
    silently resurrect the ceremony the deletion test rejected."""
    import dominion.workers.production as production

    dead = ("_int_or_none", "_roster_name_tokens", "_contract_item")
    for name in dead:
        assert not hasattr(production, name), f"dead facade shim {name!r} reappeared"

    live = (
        "derive_contract_classification",
        "run_chapter_draft_qa",
        "chain_scene_entry_states",
        "derive_chapter_sequence",
        "evaluate_chapter_sequence",
    )
    for name in live:
        assert hasattr(production, name), f"live facade wrapper {name!r} was removed"
