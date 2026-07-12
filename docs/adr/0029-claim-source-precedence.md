# Claim Source Precedence

## Decision

`ClaimSource` becomes an **enforced precedence policy**, not merely a per-claim label. The total order is:

```
LOCKED_CANON  >  DERIVED_FROM_MANUSCRIPT  >  DERIVED_FROM_OUTLINE  >  PLAUSIBLE_INFERENCE  >  UNRESOLVED
```

`FORBIDDEN` is **not** a rank — it is an independent prohibition that constrains the surface contract regardless of precedence.

`DERIVED_FROM_MANUSCRIPT` is a new source: a claim that is *traceable in an imported prose snapshot* (via an `M#` handle resolving to an immutable `(scene_id, version, prose_hash)` and evidence span). It may govern the adopted chapter, but it **never** enters canon retrieval and **never** overrides locked canon automatically. Imported prose is evidence, not new global canon.

## Context

Import Adoption (ADR 0028) feeds the ChapterPacket Author a bundle of `M#` manuscript-evidence claims alongside the existing canon/outline/inference claims. `ClaimSource` previously only labelled a claim's strength; nothing decided what happens when two claims of different strength assert conflicting facts. Without an explicit rule, packet authoring would silently pick a side — promoting prose to canon, or discarding an established prose fact — exactly the failure the adoption design forbids. The rule is durable and surprising (the actual prose readers will see ranks *below* locked canon but *above* an outline guess), so future packet work would otherwise rediscover it incorrectly.

## Enforcement

Conflicts are resolved by the precedence order; a conflict the order cannot break becomes a structured open question that **blocks ChapterPacket approval** — not merely lowers confidence.

- **Manuscript × locked canon** → create a `manuscript_canon_conflict` open question carrying both source references, the `M#` spans, and the conflicting assertions. Confidence is set to at least `yellow`. Approval is blocked by the unresolved question itself.
  - *Resolved: manuscript wins* → retain the resolution + rationale in the packet's adjudication history; permit a chapter-local `DERIVED_FROM_MANUSCRIPT` claim. It is **not** promoted to canon.
  - *Resolved: canon wins* → the packet's constraints require the later revision to correct the imported prose.
- **Manuscript × outline** → manuscript wins for reconstruction of this chapter; record the source resolution. No open question unless locked canon also conflicts.
- **Manuscript × manuscript** (same-strength evidence disagreeing) → an open question; same-strength evidence must not silently choose a winner.

`FORBIDDEN` continues to drive surface-term prohibition independently (`packet/surface_policy.py`); it is orthogonal to the precedence order above.

## Alternatives considered

- **Rank `DERIVED_FROM_MANUSCRIPT` at the bottom (above only `UNRESOLVED`)** — rejected: it treats the actual prose as weaker than an outline guess and generates needless open questions.
- **Treat manuscript as co-authoritative with canon for scene-local facts** — rejected: it blurs the "prose is evidence, not canon" seam and risks silently promoting prose to canon-strength.
- **Keep `ClaimSource` a label with no enforced order** — rejected: it leaves every conflict to be silently adjudicated by whichever agent happens to author the claim.

## Consequences

Manuscript evidence can reconstruct an imported chapter's contract without contaminating canon retrieval. Every manuscript-vs-canon disagreement surfaces as a human-adjudicated open question with full provenance, and the adjudication is retained. Promotion of a manuscript fact to canon remains a separate, later, explicit human act — out of scope here.
