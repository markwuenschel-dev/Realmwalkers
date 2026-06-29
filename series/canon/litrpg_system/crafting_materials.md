---
id: crafting_materials
name: Crafting & Materials
kind: system
status: scaffold
---

# Crafting & Materials — Dominion Realm

> **Owner field:** Creation & Infrastructure → Crafting & Materials.
> **Status:** Scaffold / placeholder.
> **Owns:** gear, alchemy, enchanting, repair, construction materials.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `mechanics.md`, `power_expression.md`.
> **Outputs to:** `economy_logistics.md`, `combat_defense.md`, `base_infrastructure.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place crafting & materials rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Crafting & Materials**.

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
z_{\mathrm{craft}} = (\mathrm{purity},\ \mathrm{stability},\ \mathrm{resonance},\ \mathrm{defectDensity},\ \mathrm{catalystAlignment},\ \mathrm{temperature},\ \mathrm{pressure},\ \mathrm{thermalStress},\ \mathrm{grainStructure},\ \mathrm{enchantmentCoherence})
$$

The local possibility state tracks material and process quality as a continuous trajectory toward success or failure basins.

### Baseline Drift

Without Luck, outcomes tend toward material quality, crafter skill, tool quality, process control, recipe compatibility, domain resonance, and thermal/mechanical stress.

$$
b_{\mathrm{craft}}(z,t) \text{ reflects skill- and material-weighted expected process evolution.}
$$

### Uncertainty / Diffusion

Uncertainty enters through impurities, hidden defects, temperature fluctuations, resonance instability, catalyst variance, timing sensitivity, material memory, and microfractures.

High uncertainty means Luck has more leverage near stabilization thresholds.

### Favorability Function

$$
U_{\mathrm{craft}}(z)
$$

In this subsystem, favorable outcomes mean stability, quality, safety, or rare resonance — depending on what the crafter is optimizing for.

### Luck Interaction

Fortune biases toward cleaner stabilization, lower defect propagation, and favorable failure-basin avoidance. Misfortune biases toward flaw propagation and catastrophic failure basins. Volatility widens quality spread.

Preferred computational model:

$$
dz_t = [b_{\mathrm{craft}} + u_{L,\mathrm{craft}}]\,dt + \sigma_{\mathrm{craft}}\,dW_t
$$

Then classify final quality, defect density, resonance stability, and failure risk.

### Reachability Constraints

Luck can affect impurity distribution, defect formation, rare stable alignment, side-effect severity, enchantment stability, failure basin selection, and whether a reaction stabilizes near threshold.

Luck cannot make incompatible materials compatible by itself, replace skill/recipe knowledge/tools/material quality, create rare materials from nothing, or erase deterministic failure from gross mismatch.

### Result Classifier

$$
\mathrm{Result}_{\mathrm{craft}} = \mathrm{Classify}_{\mathrm{craft}}(z_{\mathrm{final}})
$$

Examples: failed, flawed, usable, stable, excellent, rare variant, unstable success, hidden defect, catastrophic failure.

### Notes

Skill and materials set baseline drift and the quality envelope. Luck improves uncertain tails within that envelope.

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
