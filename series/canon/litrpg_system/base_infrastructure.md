---
id: base_infrastructure
name: Base & Infrastructure
kind: system
status: scaffold
---

# Base & Infrastructure — Dominion Realm

> **Owner field:** Creation & Infrastructure → Base & Infrastructure.
> **Status:** Scaffold / placeholder.
> **Owns:** buildings, supply lines, wards, defenses, population support.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `crafting_materials.md`, `economy_logistics.md`, `space_environment.md`.
> **Outputs to:** `social_faction_systems.md`, `strategy_decision_systems.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place base & infrastructure rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Base & Infrastructure**.

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
z_{\mathrm{base}} = (\mathrm{structuralIntegrity},\ \mathrm{maintenanceState},\ \mathrm{laborCoordination},\ \mathrm{supplyAvailability},\ \mathrm{wardStability},\ \mathrm{hazardExposure},\ \mathrm{failureRisk},\ \mathrm{repairQueue})
$$

### Baseline Drift

Without Luck, infrastructure follows construction quality, maintenance, load, and supply availability.

### Uncertainty / Diffusion

Uncertainty enters through hidden defects, deferred maintenance, hazard timing, and cascade alignment.

### Favorability Function

$$
U_{\mathrm{base}}(z)
$$

Favorable outcomes mean detected flaws, delayed failure, lucky saves before crisis, or repairs finishing in time.

### Luck Interaction

Fortune may bias whether weak components fail now or later, whether maintenance catches a flaw, or whether small failures fail to cascade. Misfortune does the reverse. Volatility widens infrastructure tail risk.

### Reachability Constraints

Luck cannot make poor construction safe forever, replace material quality or maintenance, erase structural load, or prevent deterministic collapse under impossible stress.

### Result Classifier

Examples: stable operation, minor fault, detected flaw, hidden flaw, delayed failure, cascading failure, lucky save, infrastructure breakdown.

### Notes

Luck biases failure timing and cascade branches; it does not replace engineering.

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
