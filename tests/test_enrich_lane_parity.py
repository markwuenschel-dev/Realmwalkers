"""Fitness check: the Enrich panel's hardcoded lane list must match the backend's canonical lanes.

The backend single-sources the enrichment lanes from `workers/router.DRAFT_PASSES` in `passes_for`
order, and `GET /enrich/lanes` serves exactly that list (`api/routers/enrich.py:_ALL_LANES`). The
frontend `EnrichScreen.tsx` hardcodes a parallel `LANES` array (id/label/hint) with only a "keep
aligned" comment and does not consume the endpoint. So adding a pass to `router._LANE_PASSES` grows
the backend lanes while the panel silently keeps offering the old set.

This test pins that hand-maintained mirror: it reads the frontend `LANES` ids and asserts they equal
the backend canonical lanes, in the same order (the panel promises the server's fixed chain order).
It reads the .tsx as text — no node/Playwright needed — so it runs in the ordinary backend suite.
When the two drift it fails and names the offending lane; the fix is to update `LANES` (or make the
panel consume `GET /enrich/lanes`).
"""

from __future__ import annotations

import re
from pathlib import Path

from dominion.workers.router import DRAFT_PASSES, passes_for

_ENRICH_SCREEN = Path(__file__).resolve().parents[1] / "frontend" / "src" / "desk" / "screens" / "EnrichScreen.tsx"


def _frontend_lane_ids(source: str) -> list[str]:
    """Extract the `id` literals from the `const LANES: ... = [ ... ];` array, in file order."""
    m = re.search(r"const LANES\b[^=]*=\s*\[(.*?)\];", source, re.DOTALL)
    assert m, "could not locate the `const LANES = [...]` array in EnrichScreen.tsx"
    return re.findall(r"""id:\s*["']([^"']+)["']""", m.group(1))


def test_enrich_panel_lanes_match_backend_canonical_order():
    assert _ENRICH_SCREEN.is_file(), f"frontend Enrich screen not found at {_ENRICH_SCREEN}"
    backend = [p.name for p in passes_for(list(DRAFT_PASSES))]
    frontend = _frontend_lane_ids(_ENRICH_SCREEN.read_text(encoding="utf-8"))
    assert frontend == backend, (
        f"Enrich panel LANES {frontend} drifted from backend canonical lanes {backend}. "
        "Update frontend/src/desk/screens/EnrichScreen.tsx `LANES` (or have the panel consume "
        "GET /enrich/lanes)."
    )


def test_lane_id_extractor_detects_drift():
    """Red-capability proof: the extractor + comparison catch a mismatched frontend list."""
    drifted = 'const LANES: X[] = [ { id: "combat" }, { id: "sensory" } ];'  # missing "dialogue"
    backend = [p.name for p in passes_for(list(DRAFT_PASSES))]
    assert _frontend_lane_ids(drifted) != backend
