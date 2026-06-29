---
id: progression_identity
name: Progression & Identity
kind: system
status: scaffold
---

# Progression & Identity — Dominion Realm

> **Owner field:** Growth Systems → Progression & Identity.
> **Status:** Scaffold / placeholder.
> **Owns:** levels, classes, skills, soul, species, mastery.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `core_rules.md`, `classes.md`, `mechanics.md`, `resource_system.md`.
> **Outputs to:** `resource_system.md`, `interface_abstraction.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place progression & identity rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Progression & Identity**.

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
z_{\mathrm{progression}} = (\mathrm{skillUseContext},\ \mathrm{thresholdProximity},\ \mathrm{trainingQuality},\ \mathrm{breakthroughRisk},\ \mathrm{identityCoherence},\ \mathrm{classResonance},\ \mathrm{soulPressure})
$$

### Baseline Drift

Without Luck, progression follows training, use, class constraints, soul pressure, and system requirements.

### Uncertainty / Diffusion

Uncertainty enters through marginal breakthrough moments, threshold proximity, and unstable advancement branches.

### Favorability Function

$$
U_{\mathrm{progression}}(z)
$$

Favorable outcomes mean clean breakthrough, rare opportunity noticed, or safer failure mode during advancement — not free mastery.

### Luck Interaction

Fortune may bias whether a marginal training opportunity appears, whether breakthrough happens cleanly vs painfully, or whether a threshold moment occurs under favorable circumstances. Misfortune does the reverse. Volatility widens advancement tail risk.

### Reachability Constraints

Luck cannot replace training, create mastery from nothing, grant incompatible classes, bypass class/soul/system requirements, or replace Conviction, Mystery, skill affinity, or actual use.

**Luck is not a normal progression stat** unless explicitly locked later. LCK on the Interface is a passive coupling projection, not a substitute for progression rules.

### Result Classifier

Examples: clean progression, delayed progression, unstable breakthrough, missed opportunity, rare opportunity noticed, backlash during advancement, threshold resonance.

### Notes

Luck biases marginal progression branches; it does not replace earned advancement.

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
