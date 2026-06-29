---
id: perception_information
name: Perception & Information
kind: system
status: scaffold
---

# Perception & Information — Dominion Realm

> **Owner field:** Action Systems → Perception & Information.
> **Status:** Scaffold / placeholder.
> **Owns:** senses, Insight, stealth, illusion, salience, inference.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `core_rules.md`, `resource_system.md`, `power_expression.md`.
> **Outputs to:** `strategy_decision_systems.md`, `combat_defense.md`, `interface_abstraction.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place perception & information rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Perception & Information**.

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
z_{\mathrm{perception}} = (\mathrm{signalStrength},\ \mathrm{noise},\ \mathrm{attentionDirection},\ \mathrm{salience},\ \mathrm{evidenceVisibility},\ \mathrm{falsePositiveRisk},\ \mathrm{falseNegativeRisk},\ \mathrm{timing},\ \mathrm{occlusion})
$$

The local possibility state tracks whether evidence emerges, is missed, or is misread when the read is marginal.

### Baseline Drift

Without Luck, perception follows senses, training, Insight level, attention, and environmental signal quality.

### Uncertainty / Diffusion

Uncertainty enters through noise, occlusion, timing, salience competition, and marginal Insight resolution.

### Favorability Function

$$
U_{\mathrm{perception}}(z)
$$

Favorable outcomes mean the right clue becomes visible, salient, or cleanly parsed — for the observer whose perspective is measured.

### Luck Interaction

Fortune biases which evidence $E$ appears or survives damage, whether a scout looks in a useful direction, and whether Insight receives a cleaner read when the result is marginal. Misfortune biases toward false leads and corrupted reads. Volatility increases spread between clean clue and misleading signal.

Bayesian note: Luck can affect which evidence $E$ appears, not the truth of hypothesis $H$ itself.

$$
P(H \mid E) \text{ still requires valid inference; Luck does not grant } E \text{ without an evidence path.}
$$

### Reachability Constraints

Luck can affect clue visibility, sound timing, hidden detail survival, and marginal Insight clarity.

Luck cannot grant knowledge with no evidence path, replace Insight/senses/training/inference, reveal impossible information, or bypass active concealment unless uncertainty remains in the concealment system.

### Result Classifier

$$
\mathrm{Result}_{\mathrm{perception}} = \mathrm{Classify}_{\mathrm{perception}}(z_{\mathrm{final}})
$$

Examples: unnoticed, vaguely noticed, suspected, confirmed, false lead, clean clue, corrupted read, partial Insight, misleading signal.

### Notes

Luck biases evidence emergence; it does not replace perception skill or Insight.

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
