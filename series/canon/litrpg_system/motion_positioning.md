---
id: motion_positioning
name: Motion & Positioning
kind: system
status: scaffold
last_updated: 2026-07-14
---

# Motion & Positioning — Dominion Realm

> **Owner field:** Action Systems → Motion & Positioning.
> **Status:** Scaffold / placeholder.
> **Owns:** movement, balance, momentum, terrain traversal.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `resource_system.md`, `space_environment.md`, `embodiment_injury.md`, `combat_defense.md`.
> **Outputs to:** `combat_defense.md`, `strategy_decision_systems.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place motion & positioning rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

## Combat Motion / Positioning Interface

### Section Contract

This section provides motion-facing inputs to `combat_defense.md` and receives position consequences back from combat exchanges.

This section owns:

* movement,
* balance,
* footing,
* momentum,
* traction,
* stance,
* terrain traversal,
* local displacement,
* fall / stumble / collision trajectories.

This section does **not** own:

* attack damage,
* penetration,
* HP damage,
* armor / shield / barrier failure,
* tactical decision-making beyond motion feasibility.

Plain rule:

```text
Motion & Positioning decides what movement is physically possible.
Combat & Defense decides what that movement means inside an exchange.
```

---

### Local Motion State

Use:

$$
\begin{aligned}
M_t
&=
\bigl(
q,
\dot q,
\ddot q,
\beta_{\mathrm{bal}},
\mu,
\phi_{\mathrm{foot}},
p_{\mathrm{foot}},
\theta_{\mathrm{body}},
r_{\mathrm{reach}},
E_{\mathrm{terrain}}
\bigr)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $q$ | position |
| $\dot q$ | velocity |
| $\ddot q$ | acceleration |
| $\beta_{\mathrm{bal}}$ | balance state |
| $\mu$ | traction / friction |
| $\phi_{\mathrm{foot}}$ | footing stability |
| $p_{\mathrm{foot}}$ | foot placement |
| $\theta_{\mathrm{body}}$ | body angle / facing / lean |
| $r_{\mathrm{reach}}$ | reach envelope |
| $E_{\mathrm{terrain}}$ | terrain state |

The balance symbol is deliberately not $b$, which is reserved for baseline drift inside local Luck adapters.

Simplified author-facing form:

```text
MotionState:
  position
  velocity
  acceleration
  balance
  traction
  footing
  facing
  reach
  terrain
```

---

### Motion Feasibility

Before Combat & Defense can use avoidance, interception, retreat, advance, pivot, or another movement-dependent branch, this file determines whether the motion exists and how long it takes.

For candidate motion or defense mode $d$:

$$
\begin{aligned}
\mathcal F_{\mathrm{motion}}(d)
&=
\Phi_{\mathrm{motion}}
\bigl(
M_t,
d,
E_{\mathrm{terrain}},
R_{\mathrm{stamina}},
\mathrm{injuryState}
\bigr)
\end{aligned}
$$

```text
MotionFeasibilityResult[d]:
  status: possible | possible_with_cost | partial | unstable | late | impossible
  executionTime t_m(d)
  path
  displacementCandidate
  facingCandidate
  balanceAfter
  footingAfter
  staminaPressure
  recoveryBurden
  collisionOrFallRisk
```

Combat consumes $t_m(d)$ and the status for its mode-specific reaction margin. Combat must not infer a dodge, pivot, interposition path, or recovery branch that Motion marked `impossible`.

Plain rule:

```text
A dodge is not available just because the character wants it.
Space, footing, momentum, injury, timing, and Stamina decide whether it exists.
```

---

### Momentum and Derivative Pressure

Momentum-sensitive movement is projected along the exchange-relevant direction $\hat n$ rather than treated as a flat bonus.

$$
\begin{aligned}
\Pi_{\mathrm{momentum}}(\hat n)
&=
\lambda_v
\left\langle
\dot q,
\hat n
\right\rangle
+
\lambda_a
\left\langle
\ddot q,
\hat n
\right\rangle
\end{aligned}
$$

The calibration weights $\lambda_v$ and $\lambda_a$ map local kinematics into an author-facing effect-equivalent pressure. They do not imply universal physical units.

Combat-facing motion state:

$$
\begin{aligned}
M_{\mathrm{combatMotion}}
&=
\Phi_{\mathrm{combatMotion}}
\bigl(
\Pi_{\mathrm{momentum}},
\beta_{\mathrm{bal}},
\mu,
\phi_{\mathrm{foot}},
\theta_{\mathrm{body}},
r_{\mathrm{reach}},
E_{\mathrm{terrain}}
\bigr)
\end{aligned}
$$

This may feed:

```text
impact power
stagger risk
knockback
forced step
fall risk
recovery debt
dodge quality
interception quality
```

Plain rule:

```text
Momentum is not a flat bonus. It matters because the body is already moving and must pay to redirect.
```

---

### Position Change Intake

Combat & Defense may send a position-change output.

```text
CombatPositionHandoff:
  positionChange
  forceDirection
  contactClass
  effectiveImpact
  balanceDisruption
  stanceDisruption
  terrainContact
  collisionCandidate
  fallCandidate
  recoveryDebt
  routedFrom: combat_defense.md
  routedTo: motion_positioning.md
```

Motion & Positioning resolves:

$$
\begin{aligned}
M_{t+1}
&=
\Phi_{\mathrm{position}}
(
M_t,
\Delta x,
I_{\mathrm{eff}},
\mathrm{forceDirection},
E_{\mathrm{terrain}},
r_{\mathrm{recovery}},
\mathrm{injuryState}
)
\end{aligned}
$$

Possible results:

```text
stable step
forced step
stumble
recovered stumble
fall
knockback
collision
bad landing
controlled landing
stance break
terrain disadvantage
```

---

### Combat Output to Motion

This file may return motion state back to Combat & Defense.

```text
MotionCombatOutput:
  updatedPosition
  updatedFacing
  balanceState
  footingState
  tractionState
  reachEnvelope
  combatMotionState M_combatMotion
  executionTimeByCandidateMode t_m(d)
  feasibilityByCandidateMode
  fallRisk
  recoveryBurden
  availableMovementBranches
  routedFrom: motion_positioning.md
  routedTo: combat_defense.md
```

Plain rule:

```text
Motion changes what attacks and defenses are reachable next.
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

This subsystem uses the canonical model from `luck_fortune.md` and defines only motion-local state and constraints.

### Local Possibility State

$$
\begin{aligned}
z_{\mathrm{motion}}
&=
\bigl(
\mathrm{balanceResidual},
\mathrm{tractionResidual},
\mathrm{momentumRedirectionResidual},
\mathrm{jointLoadResidual},
\mathrm{obstacleClearance},
\mathrm{pathAngle},
\mathrm{reactionTiming},
\mathrm{fallTrajectory}
\bigr)
\end{aligned}
$$

### Baseline Drift

Without Luck, movement follows kinematics, agility, training, terrain, traction, current balance, injury, and resource-constrained effort.

### Uncertainty / Diffusion

Uncertainty remains only where more than one motion outcome is physically reachable: marginal footing, traction loss, obstacle clearance, redirection, near-fall recovery, collision angle, or landing trajectory.

### Favorability Function

Favorability is actor- and objective-specific:

$$
\begin{aligned}
U_{\mathrm{motion}}^{(a)}(z,t)
&=
\mathrm{MotionFavorabilityForActor}
\bigl(
 a,z,t
\bigr)
\end{aligned}
$$

### Luck Interaction

Fortune and Misfortune bias drift among reachable motion states. Volatility widens diffusion and tail risk. This file supplies $z_{\mathrm{motion}}$, $U_{\mathrm{motion}}^{(a)}$, its reachability gate, and its classifier to `luck_fortune.md`; it does not restate the canonical probability-flow equations.

### Reachability Constraints

Luck cannot create traction where no supporting mechanism exists, ignore momentum, reverse committed movement without force or skill, invent space, cancel an unavoidable collision, or replace agility, training, terrain awareness, injury, or Stamina constraints.

### Result Classifier

$$
\begin{aligned}
\mathrm{Result}_{\mathrm{motion}}
&=
\mathrm{Classify}_{\mathrm{motion}}
\bigl(
 z_{\mathrm{motion,final}}
\bigr)
\end{aligned}
$$

Examples: clean step, forced step, recovered stumble, slip, fall, collision, narrow dodge, bad landing, controlled landing, or impossible branch.

### Forbidden Simplifications

Do not use Luck as a flat dodge bonus, a replacement for movement skill, a source of missing ground or space, or a way to run perception, combat penetration, injury, strategy, or resource uncertainty inside Motion.

### Owner Handoff

```text
MotionLuckAdapterInput:
  actor
  localPossibilityState
  baselineReachableMotionSet
  unresolvedMotionCoordinates
  favorabilityPerspective
  reachabilityGate
  classifier
  routedFrom: motion_positioning.md
  routedThrough: luck_fortune.md
```

Plain rule:

```text
Luck biases motion uncertainty. Motion still decides what movement is physically possible.
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
