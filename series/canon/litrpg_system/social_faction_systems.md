---
id: social_faction_systems
name: Social & Faction Systems
kind: system
status: scaffold
---

# Social & Faction Systems — Dominion Realm

> **Owner field:** Social & Strategic Systems → Social & Faction Systems.
> **Status:** Scaffold / placeholder.
> **Owns:** reputation, diplomacy, law, alliances, institutions.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `economy_logistics.md`, `cosmology.md`.
> **Outputs to:** `strategy_decision_systems.md`, `progression_identity.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place social & faction systems rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Social & Faction Systems**.

---

## Placeholder Rules

Use these until the subsystem is expanded:

- Prefer narrative adjudication when exact mechanics are missing.
- State whether a rule is canon, provisional, or author-facing only.
- Keep examples clearly marked as examples.
- Do not invent hidden formulas if the story only needs a qualitative boundary.

---

## Open Questions

- What are the minimum Book-1 rules needed for this subsystem?
- Which rules need formulas, and which should stay narrative?
- Which existing scenes already imply constraints?
- Which other subsystem receives the output?

---

## Luck/Fortune Adapter

This subsystem uses the canonical Luck/Fortune model from `luck_fortune.md`.

### Local Possibility State

$$
z_{\mathrm{social}} = (\mathrm{trust},\ \mathrm{suspicion},\ \mathrm{rumorSpread},\ \mathrm{witnessPresence},\ \mathrm{timing},\ \mathrm{socialRisk},\ \mathrm{factionIncentive},\ \mathrm{evidenceAccess},\ \mathrm{emotionalVolatility})
$$

### Baseline Drift

Without Luck, social outcomes follow charisma, reputation, leverage, trust, institutions, and incentives.

### Uncertainty / Diffusion

Uncertainty enters through witness timing, rumor propagation, emotional volatility, and whether evidence is found or missed.

### Favorability Function

$$
U_{\mathrm{social}}(z)
$$

Favorable outcomes mean favorable timing, belief traction, negotiation openings, or rumor dying — relative to the actor measured.

### Luck Interaction

Fortune may bias whether the right person overhears, whether a witness appears, whether an accusation gains traction, or whether negotiation opens with favorable timing. Misfortune does the reverse. Volatility widens social tail events.

$$
u_{L,\mathrm{social}} = \lambda_L R_{\mathrm{social}}(z,t)\,\nabla U_{\mathrm{social}}(z,t)
$$

### Reachability Constraints

Luck cannot mind-control people, replace charisma/reputation/leverage/trust, erase public evidence, or make institutions behave against core incentives without causal pressure.

### Result Classifier

Examples: ignored, suspected, challenged, believed, protected, exposed, rumor amplified, rumor dies, negotiation opening, faction backlash.

### Notes

Luck biases social timing and evidence emergence; it does not replace social intelligence.

---

## Agent Boundaries

Agents may:

- Add scoped rules that match this owner field.
- Add examples and placeholders.
- Add cross-references to owner files.

Agents must not:

- Move resource formulas here unless this file becomes the explicit owner.
- Reintroduce class rarity bonus attribute-point cadence.
- Use Affinity to mean Domain.
- Treat interface readouts as the underlying reality.
