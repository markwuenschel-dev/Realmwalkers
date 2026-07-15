---
id: strategy_decision_systems
name: Strategy & Decision Systems
kind: system
status: scaffold
last_updated: 2026-07-14
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

## Tactical Graph / Team Defense Interface

### Section Contract

This section provides strategy-facing inputs to `combat_defense.md` and receives tactical openings from combat exchanges.

This section owns:

* tactics and planning,
* risk assessment,
* counterplay,
* team-defense planning and graph coverage,
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
Strategy plans protection, formation, target priority, and team-defense graph coverage.
Combat resolves whether a specific local interposition, suppression effect, or protection line changes the exchange.
Strategy then values the result and chooses the next action.
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
\bigl(
G_{\mathrm{tac},t},
O,
\Delta x,
\Delta \tau,
F_{\mathrm{pressure}},
K,
J,
\mathcal I_{t+1},
\mathrm{objectiveState}
\bigr)
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
  routedFrom: combat_defense.md
  routedTo: strategy_decision_systems.md
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

Strategic choice under uncertainty uses expected payoff rather than guaranteed success.

For candidate action $a_i$:

$$
\begin{aligned}
U(a_i)
&=
\mathbb E
\left[
\mathrm{Payoff}
\bigl(
 a_i,
s,
G_{\mathrm{tac}},
\mathcal I
\bigr)
\right]
-
C(a_i)
-
\lambda_R
\mathrm{Risk}(a_i)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $a_i$ | available action |
| $s$ | current strategic state |
| $G_{\mathrm{tac}}$ | tactical graph |
| $\mathcal I$ | information/belief state supplied by Perception |
| $C(a_i)$ | expected resource, position, opportunity, or objective cost |
| $\mathrm{Risk}(a_i)$ | downside distribution or tail exposure |
| $\lambda_R$ | actor/team risk weighting |

All terms must be projected into one declared decision-value scale before subtraction. This is an author/simulator reduction, not a universal moral utility function.

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
  localGraphSlice
  communicationState
  trustOrCoordinationState
  routedFrom: strategy_decision_systems.md
  routedTo: combat_defense.md
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
  openingCreated
  openingClosed
  positionChange
  tempoChange
  protectedTargetExposed
  interpositionResult
  protectionEdgeChange
  formationPressure
  formationBreak
  lineOpened
  lineClosed
  resourcePressureSignal
  injuryRiskSignal
  enemyCommitment
  enemyRecoveryDebt
  objectiveImpactCandidate
  objectiveAccessChanged
  extractionRouteChanged
  routedFrom: combat_defense.md
  routedTo: strategy_decision_systems.md
```

Strategy resolves:

$$
\begin{aligned}
\mathrm{Decision}_{t+1}
&=
\Phi_{\mathrm{decision}}
\bigl(
G_{\mathrm{tac},t+1},
\mathcal I_{t+1},
\mathrm{objectives},
\mathrm{riskTolerance},
\mathrm{availableActions}
\bigr)
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

This subsystem uses the canonical model from `luck_fortune.md` and defines only strategy-local uncertainty.

### Local Possibility State

$$
\begin{aligned}
z_{\mathrm{strategy}}
&=
\bigl(
\mathrm{agentBeliefs},
\mathrm{availableActions},
\mathrm{perceivedPayoffs},
\mathrm{hiddenInformation},
\mathrm{riskTolerance},
\mathrm{timingResidual},
\mathrm{coordinationResidual},
\mathrm{predictionError}
\bigr)
\end{aligned}
$$

### Baseline Drift

Without Luck, strategic outcomes follow beliefs, planning, scouting, coordination, opponent agency, adaptation, and the actual tactical graph.

### Uncertainty / Diffusion

Uncertainty enters through hidden information, timing windows, prediction error, communication loss, coordination failure, and interacting intelligent choices at branch points.

### Favorability Function

Favorability is side- and objective-specific:

$$
\begin{aligned}
U_{\mathrm{strategy}}^{(s)}(z,t)
&=
\mathrm{StrategicFavorabilityForSide}
\bigl(
 s,z,t
\bigr)
\end{aligned}
$$

### Luck Interaction

Fortune and Misfortune bias drift among reachable strategic branches. Volatility widens payoff, coordination, and prediction tails. Luck does not delete strategic interaction or choose another intelligent actor's decision.

### Reachability Constraints

Luck cannot override intelligent agency, force irrational choices without another mechanism, make a dominated plan consistently good, replace scouting/planning/adaptation, or invent communication, units, routes, objectives, or information.

### Result Classifier

$$
\begin{aligned}
\mathrm{Result}_{\mathrm{strategy}}
&=
\mathrm{Classify}_{\mathrm{strategy}}
\bigl(
 z_{\mathrm{strategy,final}}
\bigr)
\end{aligned}
$$

Examples: favorable branch, unfavorable branch, opponent misread, player misread, timing advantage, coordination failure, exposed plan, or hidden opening.

### Forbidden Simplifications

Do not use Luck as mind control, a substitute for planning, a guarantee that enemies cooperate with the plan, or a reason to resolve local contact, movement, perception, injury, or resource accounting here.

### Owner Handoff

```text
StrategyLuckAdapterInput:
  measuredSide
  localPossibilityState
  baselineReachableStrategicSet
  unresolvedStrategyCoordinates
  objectiveAndFavorabilityPerspective
  intelligentAgencyConstraints
  reachabilityGate
  classifier
  routedFrom: strategy_decision_systems.md
  routedThrough: luck_fortune.md
```

Plain rule:

```text
Luck biases uncertain branch resolution.
Strategy still owns the plan, the opponent still owns their choices, and Combat still resolves the local clash.
```

---

## Agent Boundaries

Agents may:

- Add scoped rules that match this owner field.
- Add examples and placeholders.
- Add cross-references to owner files.

Agents must not:

- Move resource formulas here unless this file becomes the explicit owner.
- Reintroduce class rarity bonus attribute-point cadence.
- Conflate Skill Affinity with Domain; Skill Affinity is progression aptitude, while Domain is power expression/source category.
- Treat interface readouts as the underlying reality.
