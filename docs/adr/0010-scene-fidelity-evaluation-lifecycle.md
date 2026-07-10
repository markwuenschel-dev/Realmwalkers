# SceneFidelity evaluation lifecycle

SceneFidelity performs deterministic validation before drafting and evaluates only the final author-visible DraftAttempt after drafting. A report is current only when its prose hash, packet fingerprint, packet identifier, and evaluated artifact match the current scene. Missing or stale export-required evaluation holds Production Run completion as operational incompleteness, not as a prose-quality failure.
