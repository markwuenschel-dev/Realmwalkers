---
id: combat_defense
name: Combat & Defense
kind: system
status: scaffold
---

# Combat & Defense — Dominion Realm

> **Owner field:** Action Systems → Combat & Defense.
> **Status:** Scaffold / placeholder.
> **Owns:** attacks, penetration, armor, shields, timing, tactics.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `resource_system.md`, `motion_positioning.md`, `embodiment_injury.md`.
> **Outputs to:** `embodiment_injury.md`, `perception_information.md`, `strategy_decision_systems.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place combat & defense rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Combat & Defense**.

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
z_{\mathrm{combat}} = (\mathrm{aimError},\ \mathrm{timingError},\ \mathrm{defenderMotion},\ \mathrm{guardAngle},\ \mathrm{weaponPath},\ \mathrm{penetrationAngle},\ \mathrm{woundDepth},\ \mathrm{organProximity},\ \mathrm{footingStability},\ \mathrm{reactionWindow})
$$

The local possibility state tracks where an attack lands, how deep it goes, and whether marginal timing or geometry resolves favorably.

### Baseline Drift

Without Luck, outcomes evolve according to attacker skill, speed, weapon path, Strength, technique; defender guard, movement, armor, awareness; terrain, visibility, fatigue, and injury.

$$
b_{\mathrm{combat}}(z,t) \text{ pushes toward skill-weighted expected trajectories.}
$$

### Uncertainty / Diffusion

Uncertainty enters through chaotic body motion, marginal timing, partial visibility, unstable footing, weapon deflection, armor glance angles, reaction delay, and battlefield interference.

High uncertainty means Luck has more leverage. Low uncertainty means Luck has less leverage.

### Favorability Function

$$
U_{\mathrm{combat}}(z)
$$

In this subsystem, favorable outcomes depend on **whose perspective is measured** — graze instead of deep wound for the defender; clean opening instead of bind for the attacker. Define $U_{\mathrm{combat}}$ per side before applying Fortune or Misfortune.

### Luck Interaction

Fortune biases trajectories toward favorable reachable combat states for the favored side. Misfortune biases toward harmful reachable states. Volatility increases spread — miraculous saves and absurd catastrophes from the same instability.

A practical local drift expression:

$$
u_{L,\mathrm{combat}} = \lambda_L R_{\mathrm{combat}}(z,t)\,\nabla U_{\mathrm{combat}}(z,t)
$$

### Reachability Constraints

Luck can affect graze vs clean hit, wound path, organ proximity, footing slip, marginal parry angle, timing window, critical severity, stray projectile placement, and whether chaotic melee creates a favorable opening.

Luck cannot negate a clean deterministic strike, make an unblocked lethal blow vanish, replace skill/positioning/armor/speed/awareness, override an overwhelming power gap with no plausible branch, or turn a missed attack into a hit if no physical path exists.

Plain rule: Luck can bias reachable outcomes. It cannot select outcomes with no causal path.

### Result Classifier

$$
\mathrm{Result}_{\mathrm{combat}} = \mathrm{Classify}_{\mathrm{combat}}(z_{\mathrm{final}})
$$

Examples: clean miss, near miss, graze, shallow wound, deep wound, disabling wound, organ-threatening wound, lethal wound, armor deflection, weapon bind, footing failure, timing advantage.

### Notes

Luck should not replace combat skill, training, armor, speed, awareness, or enemy agency. It biases uncertainty around those factors.

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
