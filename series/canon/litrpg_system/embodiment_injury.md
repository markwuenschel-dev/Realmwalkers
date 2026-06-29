---
id: embodiment_injury
name: Embodiment & Injury
kind: system
status: scaffold
---

# Embodiment & Injury — Dominion Realm

> **Owner field:** Embodiment & Injury → Embodiment & Injury.
> **Status:** Scaffold / placeholder.
> **Owns:** anatomy, wounds, trauma, disease, poison, organ damage.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `resource_system.md`, `combat_defense.md`.
> **Outputs to:** `resource_system.md`, `perception_information.md`, `power_expression.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place embodiment & injury rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Embodiment & Injury**.

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
z_{\mathrm{injury}} = (\mathrm{woundPath},\ \mathrm{tissueDepth},\ \mathrm{vesselProximity},\ \mathrm{organProximity},\ \mathrm{infectionLoad},\ \mathrm{clotStability},\ \mathrm{shockRisk},\ \mathrm{painResponse},\ \mathrm{scarFormation})
$$

### Baseline Drift

Without Luck, injury evolution follows wound mechanics, treatment, anatomy, and healing magic where applied.

### Uncertainty / Diffusion

Uncertainty enters through marginal vessel proximity, clot stability, infection chance, movement tearing, and recovery tail events.

### Favorability Function

$$
U_{\mathrm{injury}}(z)
$$

Favorable outcomes mean missed critical structures, stable clots, clean recovery, or milder complications — relative to the wound already inflicted.

### Luck Interaction

Fortune may bias whether a blade misses an artery by a small margin, whether clotting holds, whether infection develops, or whether recovery follows a clean vs bad tail. Misfortune does the reverse. Volatility widens complication spread.

$$
u_{L,\mathrm{injury}} = \lambda_L R_{\mathrm{injury}}(z,t)\,\nabla U_{\mathrm{injury}}(z,t)
$$

### Reachability Constraints

Luck cannot undo existing tissue destruction, heal by itself, replace medical treatment or healing magic, prevent inevitable death from a fully deterministic injury, or make anatomy irrelevant.

### Result Classifier

Examples: superficial injury, bleeding wound, deep tissue wound, organ risk, organ damage, infection complication, shock cascade, scar complication, stable recovery, unstable recovery.

### Notes

Luck biases injury complication branches; it does not replace treatment or healing.

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
