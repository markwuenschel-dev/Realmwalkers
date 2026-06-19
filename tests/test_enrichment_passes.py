"""Enrichment-pass stubs must fail SOFT (PassError), so the pipeline lands the spine + a flag rather
than hard-failing the job (DESIGN §4 / OPEN-10). Pure unit test — no DB, no pipeline."""
from __future__ import annotations

import pytest

from dominion.workers.specialists.base import PassError
from dominion.workers.specialists.combat import combat_pass
from dominion.workers.specialists.dialogue import dialogue_pass
from dominion.workers.specialists.sensory import sensory_pass


@pytest.mark.parametrize("enrichment_pass", [combat_pass, dialogue_pass, sensory_pass])
async def test_unimplemented_enrichment_pass_raises_passerror_not_notimplemented(enrichment_pass):
    # The stubs raise before touching ctx, so a dummy ctx is fine. PassError is caught by the
    # pipeline (partial spine + advisory flag); NotImplementedError is NOT and would fail the job.
    with pytest.raises(PassError) as excinfo:
        await enrichment_pass.run("Some drafted prose.", None)
    assert not isinstance(excinfo.value, NotImplementedError)
