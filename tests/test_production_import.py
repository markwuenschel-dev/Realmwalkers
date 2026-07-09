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
