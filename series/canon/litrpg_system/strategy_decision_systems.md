---
id: strategy_decision_systems
name: Strategy & Decision Systems
kind: system
status: scaffold
---

# Strategy & Decision Systems — Dominion Realm

> **Owner field:** Action Systems / Social & Strategic → Strategy & Decision Systems.
> **Status:** Scaffold / placeholder.
> **Owns:** AI, tactics, risk, counterplay, planning.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `perception_information.md`, `combat_defense.md`, `social_faction_systems.md`.
> **Outputs to:** `combat_defense.md`, `economy_logistics.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place strategy & decision systems rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Strategy & Decision Systems**.

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
z_{\mathrm{strategy}} = (\mathrm{agentBeliefs},\ \mathrm{availableActions},\ \mathrm{perceivedPayoffs},\ \mathrm{hiddenInformation},\ \mathrm{riskTolerance},\ \mathrm{timing},\ \mathrm{coordination},\ \mathrm{predictionError})
$$

### Baseline Drift

Without Luck, strategic outcomes follow beliefs, planning, scouting, adaptation, and opponent agency.

### Uncertainty / Diffusion

Uncertainty enters through hidden information, timing windows, coordination failure, and prediction error at branch points.

### Favorability Function

$$
U_{\mathrm{strategy}}(z)
$$

Favorable outcomes mean favorable branch resolution, timing advantage, or hidden information emerging helpfully — for the side measured.

### Luck Interaction

Fortune biases uncertain timing windows, favorable plan branches, and alignment of independent uncertainties. Misfortune biases punishment of risky maneuvers and coordination failure. Volatility widens strategic tail outcomes.

Game-theory note: Luck affects uncertainty and payoff distributions. It should not delete strategic interaction.

### Reachability Constraints

Luck cannot override intelligent agency, force irrational opponent choices without another mechanism, make bad strategy consistently good, or replace scouting/planning/adaptation.

### Result Classifier

Examples: favorable branch, unfavorable branch, opponent misread, player misread, timing advantage, coordination failure, exposed plan, hidden opening.

### Notes

Luck biases branches under uncertainty; it does not replace strategy.

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
