# SceneFidelity lane integration

SceneFidelity implementation uses eight bounded lanes with one runtime facade. Fixtures and integration begin at T0 and remain active through every merge. Contract and migration work merges first, followed by packet contract, parallel drafter/evaluator work, policy/production triage, repair previews, and Desk UI. Policy publishes its report-projection interface early; the integration lane owns the fixture gate and final merge review.
