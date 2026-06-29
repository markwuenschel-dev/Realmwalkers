---
id: space_environment
name: Space & Environment
kind: system
status: scaffold
---

# Space & Environment — Dominion Realm

> **Owner field:** World Systems → Space & Environment.
> **Status:** Scaffold / placeholder.
> **Owns:** terrain, portals, zones, boundaries, weather, hazards.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `cosmology.md`, `eriadne.md`.
> **Outputs to:** `motion_positioning.md`, `combat_defense.md`, `base_infrastructure.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place space & environment rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Space & Environment**.

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
z_{\mathrm{environment}} = (\mathrm{localHazards},\ \mathrm{terrainInstability},\ \mathrm{weatherState},\ \mathrm{boundaryCondition},\ \mathrm{spatialDistortion},\ \mathrm{fieldPressure},\ \mathrm{probabilityGradient},\ \mathrm{exposureTime})
$$

Local Fortune fields may appear as $\mathcal{L}(x,t)$ — spatial probability pressure distinct from a character's passive Luck.

### Baseline Drift

Without Luck, environment evolves according to geography, weather physics, ward stability, and hazard mechanics.

### Uncertainty / Diffusion

Uncertainty enters through marginal terrain stability, hazard timing, weather edge cases, and spatial distortion branches.

### Favorability Function

$$
U_{\mathrm{environment}}(z)
$$

Favorable outcomes mean safe passage, delayed hazards, or paths through distortion that remain physically reachable.

### Luck Interaction

Fortune biases hazard timing, weather edge cases, whether rockfall begins now or later, and whether spatial distortion opens a safer reachable path. Misfortune does the reverse. Volatility marks Volatility zones with wider outcome spread.

$$
u_{L,\mathrm{environment}} = \lambda_L R_{\mathrm{environment}}(z,t)\,\nabla U_{\mathrm{environment}}(z,t)
$$

### Reachability Constraints

Luck cannot ignore physical geography, bypass closed boundaries without a spatial/power mechanism, make unreachable areas reachable by chance alone, or override deterministic environmental forces.

### Result Classifier

Examples: safe passage, near miss, hazard trigger, delayed hazard, exposed path, blocked path, local Fortune field, local Misfortune field, Volatility zone.

### Notes

Blessed, cursed, and probability-distorted zones use $u_{\mathrm{field},X}$ per `luck_fortune.md`.

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
