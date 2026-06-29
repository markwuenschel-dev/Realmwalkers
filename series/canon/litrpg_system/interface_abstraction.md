---
id: interface_abstraction
name: Interface & Abstraction
kind: system
status: scaffold
---

# Interface & Abstraction — Dominion Realm

> **Owner field:** Interface & Abstraction → Interface & Abstraction.
> **Status:** Scaffold / placeholder.
> **Owns:** stat display, hidden values, diagnostics, system compression.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `core_rules.md`, `perception_information.md`, `resource_system.md`.
> **Outputs to:** `core_rules.md`, `perception_information.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place interface & abstraction rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Interface & Abstraction**.

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
z_{\mathrm{interface}} = (\mathrm{displayResolution},\ \mathrm{diagnosticAccess},\ \mathrm{hiddenVariableExposure},\ \mathrm{probabilityReadClarity},\ \mathrm{corruption},\ \mathrm{userComprehension})
$$

The local possibility state tracks what the Interface shows, hides, rounds, misreads, or compresses about probability and Fortune effects.

### LCK as Interface Projection

**LCK** on the Interface is a scalar projection of ambient/passive Fortune coupling — for display and class-resonance tuning. It is **not** the canonical Luck model and does **not** feed HP/Mana/Stamina/Reserve maximums. Canonical definition → `luck_fortune.md`.

Do not present LCK as `+2% crit chance` unless using an intentionally simplified Interface projection clearly labeled as such.

### Baseline Drift

Without Luck-specific Interface features, readouts compress continuous reality into categories, bars, and messages.

### Uncertainty / Diffusion

Uncertainty enters through diagnostic limits, corruption, user comprehension, and whether probability deviations are detectable at all.

### Favorability Function

$$
U_{\mathrm{interface}}(z)
$$

Favorable Interface states mean clearer diagnostics, visible cost warnings, and accurate classification of Fortune/Misfortune pressure.

### Luck Interaction

Fortune/Misfortune/Volatility may affect whether probability deviations are detected, whether outcomes are described as direct results or vague anomalies, whether active Luck cost is visible, and whether distortion is misclassified as "chance."

Possible Interface messages:

```text
Probability deviation detected.
Outcome shifted within plausible range.
Local Fortune pressure unstable.
Entropy debt acquired.
Reachability constraint prevented outcome selection.
```

### Reachability Constraints

The Interface may hide Luck entirely, partially expose it, or describe it indirectly. It must not expose exact canonical formulas unless the Interface has a reason to reveal them.

### Result Classifier

Examples: hidden, vague anomaly, partial read, clean Fortune/Misfortune label, entropy-debt warning, reachability-block message, corrupted read.

### Notes

Interface display ≠ underlying reality. The UI compresses the probability-flow model; it does not replace it.

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
