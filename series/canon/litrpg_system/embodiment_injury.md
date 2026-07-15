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

## Tactical Graph / Team Defense Interface

### Section Contract

This section provides strategy-facing inputs to `combat_defense.md` and receives tactical openings from combat exchanges.

This section owns:

* tactics,
* risk assessment,
* counterplay,
* team defense,
* planning,
* target priority,
* formation logic,
* tactical graph evaluation,
* opponent adaptation.

This section does **not** own:

* contact quality,
* penetration math,
* armor / shield / barrier mechanics,
* HP damage calculation,
* detailed injury progression,
* resource formulas.

Plain rule:

```text
Combat & Defense resolves the local exchange.
Strategy & Decision Systems decides what the exchange means tactically.
```

---

### Tactical Graph

Represent a local fight as a tactical graph.

$$
\begin{aligned}
G_{\mathrm{tac}}
&=
(
N,
E,
W,
Z
)
\end{aligned}
$$

Where:

| Symbol | Meaning                                                              |
| ------ | -------------------------------------------------------------------- |
| $N$    | actors, hazards, objectives, protected targets                       |
| $E$    | edges: threat lines, protection lines, movement lines, line of sight |
| $W$    | weights: danger, cost, payoff, timing, reliability                   |
| $Z$    | zones: terrain, cover, choke, aura, field, control region            |

Combat exchanges update the graph:

$$
\begin{aligned}
G_{\mathrm{tac},t+1}
&=
\Phi_{\mathrm{tacticalGraph}}
(
G_{\mathrm{tac},t},
O,
\Delta x,
\Delta \tau,
K,
J,
I
)
\end{aligned}
$$

Plain rule:

```text
An exchange matters tactically if it changes lines, timing, risk, protection, or available choices.
```

---

### Tactical Opening Intake

Combat & Defense may send:

```text
TacticalOpeningHandoff:
  openingCreated
  openingClosed
  exposedTarget
  brokenGuard
  forcedMovement
  lostTempo
  gainedTempo
  lineOpened
  lineClosed
  formationImpact
  objectiveImpact
  routedFrom
```

This file classifies the opening:

```text
none
minor
useful
serious
decisive
catastrophic
```

Mathematical form:

$$
\begin{aligned}
O_{\mathrm{value}}
&=
\Phi_{\mathrm{opening}}
(
O,
\Delta x,
\Delta \tau,
\mathrm{targetValue},
\mathrm{threatAccess},
\mathrm{allyCoverage},
\mathrm{enemyRecovery},
\mathrm{objectiveState}
)
\end{aligned}
$$

---

### Team Defense

Team defense is not just multiple individual defenses. It is graph coverage.

Use:

$$
\begin{aligned}
D_{\mathrm{team}}
&=
\Phi_{\mathrm{teamDefense}}
(
\mathrm{coverageLines},
\mathrm{interposition},
\mathrm{threatSuppression},
\mathrm{allyReach},
\mathrm{communication},
\mathrm{timing},
\mathrm{trust}
)
\end{aligned}
$$

Team defense may provide Combat & Defense with:

```text
ally interposition
covering fire
threat suppression
forced miss pressure
shield wall support
evacuation route
body block
counterattack threat
protected target coverage
```

Plain rule:

```text
A team defense works when the enemy's best branch becomes worse before contact resolves.
```

---

### Counterplay and Payoff

Strategic choice under uncertainty uses expected payoff, not guaranteed success.

$$
\begin{aligned}
U(a_i)
&=
\mathbb{E}
[
\mathrm{Payoff}
(
a_i,
s,
G_{\mathrm{tac}},
I
)
]
-

## \mathrm{Cost}(a_i)

\mathrm{Risk}(a_i)
\end{aligned}
$$

Where:

| Symbol             | Meaning           |
| ------------------ | ----------------- |
| $a_i$              | available action  |
| $s$                | current state     |
| $G_{\mathrm{tac}}$ | tactical graph    |
| $I$                | information state |

Plain rule:

```text
Good tactics improve branch quality. They do not guarantee branch selection.
```

---

### Strategy Handoff to Combat

This file may send:

```text
StrategyCombatOutput:
  targetPriority
  protectionPriority
  teamDefenseLines
  suppressionPressure
  plannedInterposition
  baitPlan
  retreatRoute
  formationState
  objectivePressure
```

Combat & Defense consumes this for:

```text
reachable branches
defense modes
counterpressure
forced misses
ally interposition
tactical opening creation
```

---

### Combat Handoff to Strategy

Combat & Defense may send:

```text
CombatStrategyHandoff:
  tacticalOpening
  positionChange
  tempoChange
  protectedTargetExposed
  formationBreak
  resourcePressureSignal
  injuryRiskSignal
  enemyCommitment
  enemyRecoveryDebt
```

Strategy resolves:

$$
\begin{aligned}
\mathrm{Decision}*{t+1}
&=
\Phi*{\mathrm{decision}}
(
G_{\mathrm{tac},t+1},
I_{t+1},
\mathrm{objectives},
\mathrm{riskTolerance},
\mathrm{availableActions}
)
\end{aligned}
$$

Plain rule:

```text
Combat creates the opening. Strategy decides whether anyone can use it.
```


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
