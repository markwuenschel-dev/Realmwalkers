---
id: economy_logistics
name: Economy & Logistics
kind: system
status: scaffold
---

# Economy & Logistics — Dominion Realm

> **Owner field:** Creation & Infrastructure / Social & Strategic → Economy & Logistics.
> **Status:** Scaffold / placeholder.
> **Owns:** costs, trade, transport, scarcity, production chains.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `crafting_materials.md`, `base_infrastructure.md`, `space_environment.md`.
> **Outputs to:** `social_faction_systems.md`, `strategy_decision_systems.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place economy & logistics rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

Placeholder. Add only rules that belong to **Economy & Logistics**.

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
z_{\mathrm{logistics}} = (\mathrm{stockpileLevel},\ \mathrm{routeRisk},\ \mathrm{transportDelay},\ \mathrm{spoilage},\ \mathrm{laborAvailability},\ \mathrm{marketPrice},\ \mathrm{demandShock},\ \mathrm{supplyFailure},\ \mathrm{sabotageRisk},\ \mathrm{weatherDisruption})
$$

The local possibility state tracks large-scale stochastic supply and delivery uncertainty.

### Baseline Drift

Without Luck, logistics follow planning, capacity, reserves, redundancy, routes, and institutional constraints.

### Uncertainty / Diffusion

Uncertainty enters through weather, equipment failure, labor variance, market shocks, and alignment of small failures.

### Favorability Function

$$
U_{\mathrm{logistics}}(z)
$$

Favorable outcomes mean low delay, low spoilage, low cost, preserved secrecy, or surplus when uncertainty resolves well.

### Luck Interaction

Fortune biases caravan timing, spoilage rate, price movement under uncertainty, labor availability, and whether route disruption stays minor. Misfortune biases toward cascading failure. Volatility widens tail risk on deliveries and shortages.

$$
u_{L,\mathrm{logistics}} = \lambda_L R_{\mathrm{logistics}}(z,t)\,\nabla U_{\mathrm{logistics}}(z,t)
$$

### Reachability Constraints

Luck can affect timing, spoilage, hidden bottleneck surfacing, and whether multiple small failures align.

Luck cannot create goods from nothing, ignore scarcity, replace planning/reserves/redundancy/capacity, remove deterministic shortages, or make bad logistics sustainable forever.

### Result Classifier

$$
\mathrm{Result}_{\mathrm{logistics}} = \mathrm{Classify}_{\mathrm{logistics}}(z_{\mathrm{final}})
$$

Examples: on-time delivery, minor delay, major delay, spoilage, shortage, price spike, equipment failure, route closure, lucky surplus, cascading failure.

### Notes

Operations-research note: Luck biases uncertain disruptions and tail risk. It does not replace supply-chain design.

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
