# SceneFidelity clause dependencies

SceneFidelity clauses may declare acyclic dependencies by stable `clause_id`. Validation rejects missing targets, self-references, duplicate IDs, and cycles. A failed prerequisite produces diagnostic context for its dependent clause but does not automatically fail it; the drafter receives prerequisites before their dependent payoffs.
