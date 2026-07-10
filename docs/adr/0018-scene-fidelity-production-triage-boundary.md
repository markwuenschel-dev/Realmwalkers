# SceneFidelity production triage boundary

Before a Production Run, SceneFidelity creates only a report, Fidelity Critiques, and Repair Previews; these are scene-level review and proposal artifacts, not repair work. Production Run triage materializes only current unresolved export-required Critiques into run-owned Issues and HUMAN_REQUIRED RepairTasks, keyed idempotently by `(production_run_id, fidelity_critique_id)`. Prose or packet changes stale prior reports, critiques, previews, and derived production work so stale findings cannot become later export holds.
