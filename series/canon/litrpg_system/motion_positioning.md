---
id: motion_positioning
name: Motion & Positioning
kind: system
status: scaffold
---

# Motion & Positioning — Dominion Realm

> **Owner field:** Action Systems → Motion & Positioning.
> **Status:** Scaffold / placeholder.
> **Owns:** movement, balance, momentum, terrain traversal.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `resource_system.md`, `space_environment.md`.
> **Outputs to:** `combat_defense.md`, `strategy_decision_systems.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place motion & positioning rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Motion & Positioning**.

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
z_{\mathrm{motion}} = (\mathrm{balance},\ \mathrm{traction},\ \mathrm{momentum},\ \mathrm{jointLoad},\ \mathrm{obstacleClearance},\ \mathrm{pathAngle},\ \mathrm{reactionTiming},\ \mathrm{fallTrajectory})
$$

### Baseline Drift

Without Luck, movement follows agility, training, terrain awareness, momentum, and physical constraints.

### Uncertainty / Diffusion

Uncertainty enters through marginal footing, traction loss, obstacle clearance, and near-fall recovery branches.

### Favorability Function

$$
U_{\mathrm{motion}}(z)
$$

Favorable outcomes mean recovered stumble, narrow dodge, controlled landing, or momentum carrying away from danger.

### Luck Interaction

Fortune biases foot placement, slip severity, landing angle, collision margin, and trajectory through chaotic terrain. Misfortune does the reverse. Volatility widens fall and collision tails.

$$
u_{L,\mathrm{motion}} = \lambda_L R_{\mathrm{motion}}(z,t)\,\nabla U_{\mathrm{motion}}(z,t)
$$

### Reachability Constraints

Luck cannot create traction where none exists, ignore momentum, reverse committed movement without force or skill, or replace agility/training/terrain awareness.

### Result Classifier

Examples: clean step, stumble, recovered stumble, slip, fall, collision, narrow dodge, bad landing, controlled landing.

### Notes

Luck biases movement uncertainty; it does not replace physical skill.

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
