# SceneFidelity forward-only rollout

SceneFidelity is inert unless an author-approved ScenePacket has active requirements and `fidelity_contract_version: 1`. Existing packets, scenes, drafts, and exports are not backfilled, re-evaluated, or held. New Critique provenance fields are nullable, Issue statuses are additive, and strict payload validation applies only to `reviewer="scene_fidelity"`. Editing an older packet opts future DraftAttempts into the new contract without judging drafts written against its prior version.
