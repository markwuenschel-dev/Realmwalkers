---
id: power_expression
name: Power Expression
kind: system
status: scaffold
---

# Power Expression — Dominion Realm

> **Owner field:** Action Systems → Power Expression.
> **Status:** Scaffold / placeholder.
> **Owns:** spells, domains, rituals, class abilities, resonance.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `classes.md`, `mechanics.md`, `resource_system.md`.
> **Outputs to:** `combat_defense.md`, `perception_information.md`, `crafting_materials.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place power expression rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Power Expression**.

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
z_{\mathrm{power}} = (\mathrm{spellformCoherence},\ \mathrm{manaFlow},\ \mathrm{domainResonance},\ \mathrm{controlStability},\ \mathrm{phaseAlignment},\ \mathrm{backlashRisk},\ \mathrm{targetCoupling},\ \mathrm{environmentalInterference})
$$

The local possibility state tracks whether a cast stabilizes, misfires, or collapses into backlash.

### Baseline Drift

Without Luck, outcomes follow caster skill, mana availability, domain compatibility, control, and environmental coupling.

### Uncertainty / Diffusion

Uncertainty enters through marginal coherence, resonance instability, phase misalignment, and interference at cast thresholds.

### Favorability Function

$$
U_{\mathrm{power}}(z)
$$

Favorable outcomes mean clean cast, stable resonance, controlled collapse, or phase-aligned success — relative to the spell's allowed mechanism.

### Luck Interaction

Fortune biases miscast branch selection toward safer reachable outcomes, stabilizes resonance, and reduces backlash severity where uncertainty remains. Misfortune does the reverse. Volatility increases spread of miscast and surge outcomes.

For Fate, Mystery, prophecy, or active Luck manipulation, the amplitude layer in `luck_fortune.md` may apply — phase alignment and interference before probability-flow reduction. Do not duplicate that math here.

Active Luck powers incur entropy/control cost per `luck_fortune.md`.

$$
u_{L,\mathrm{power}} = \lambda_L R_{\mathrm{power}}(z,t)\,\nabla U_{\mathrm{power}}(z,t)
$$

### Reachability Constraints

Luck can affect miscast branch selection, backlash severity, ritual timing, and curse/fortune interactions within the spell's mechanism.

Luck cannot replace mana cost, replace skill/control, ignore domain incompatibility, make a spell do something outside its allowed mechanism, or force impossible target coupling.

### Result Classifier

$$
\mathrm{Result}_{\mathrm{power}} = \mathrm{Classify}_{\mathrm{power}}(z_{\mathrm{final}})
$$

Examples: clean cast, unstable cast, partial misfire, backlash, resonance surge, phase-aligned success, controlled collapse, catastrophic collapse.

### Notes

Active Luck is controlled drift under cost and reachability constraints — not free outcome selection.

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
