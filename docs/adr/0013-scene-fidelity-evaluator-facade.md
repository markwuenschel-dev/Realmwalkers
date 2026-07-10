# SceneFidelity evaluator facade

One SceneFidelity Evaluator facade runs deterministic preflight, bounded concurrent adapters for active modes, deterministic evidence and dependency processing, and one report merge. Adapters may report only on clauses their mode owns. Adapter failure is recorded as incomplete evaluation; successful results remain valid, and export-required incomplete evaluation holds Production Run completion rather than claiming a prose failure.
