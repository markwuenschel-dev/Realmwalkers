---
id: perception_information
name: Perception & Information
kind: system
status: scaffold
last_updated: 2026-07-14
---

# Perception & Information — Dominion Realm

> **Owner field:** Action Systems → Perception & Information.
> **Status:** Scaffold / placeholder.
> **Owns:** senses, Insight, stealth, illusion, salience, inference.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `core_rules.md`, `resource_system.md`, `power_expression.md`, `combat_defense.md`.
> **Outputs to:** `strategy_decision_systems.md`, `combat_defense.md`, `interface_abstraction.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place perception & information rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Working Rules

## Combat Perception Interface

### Section Contract

This section provides perception-facing inputs to `combat_defense.md` and receives perception consequences from combat exchanges.

This section owns:

* senses,
* attention,
* Insight,
* stealth,
* illusion,
* salience,
* inference,
* threat recognition,
* misreads,
* partial or corrupted information.

This section does **not** own:

* attack resolution,
* contact quality,
* penetration,
* HP damage,
* armor / shield / barrier behavior,
* final tactical decision-making.

Plain rule:

```text
Perception & Information decides what the actor can notice, parse, infer, or misread.
Combat & Defense decides what that information allows in the exchange.
```

---

### Combat Information State

Use:

$$
\begin{aligned}
\mathcal I_t
&=
(
S,
N,
A_{\mathrm{attn}},
\Sigma,
E_{\mathrm{vis}},
P_{\mathrm{threat}},
P_{\mathrm{false}},
O,
t_p
)
\end{aligned}
$$

Where:

| Symbol                | Meaning                              |
| --------------------- | ------------------------------------ |
| $S$                   | signal strength                      |
| $N$                   | noise / interference                 |
| $A_{\mathrm{attn}}$   | attention direction                  |
| $\Sigma$              | salience                             |
| $E_{\mathrm{vis}}$    | visible evidence                     |
| $P_{\mathrm{threat}}$ | inferred threat probability          |
| $P_{\mathrm{false}}$  | false-positive / false-negative risk |
| $O$                   | occlusion                            |
| $t_p$                 | perception time                      |

Simplified author-facing form:

```text
CombatInfoState:
  signalStrength
  noise
  attention
  salience
  visibleEvidence
  threatModel
  falseReadRisk
  occlusion
  perceptionTime
```

---

### Threat Recognition

Threat recognition estimates whether the observer identifies an incoming action in time to respond.

$$
\begin{aligned}
P_{\mathrm{recognize}}
&=
\Phi_{\mathrm{recognition}}
(
S,
N,
A_{\mathrm{attn}},
\Sigma,
E_{\mathrm{vis}},
O,
\mathrm{training},
\mathrm{Insight},
\mathrm{priorBelief}
)
\end{aligned}
$$

Perception time:

$$
\begin{aligned}
t_p
&=
f_{\mathrm{perception}}
(
S,
N,
A_{\mathrm{attn}},
\Sigma,
O,
\mathrm{familiarity},
\mathrm{stress}
)
\end{aligned}
$$

Combat & Defense consumes $t_p$ inside reaction margin.


The probability expression is an author/simulator model. The combat-facing result is a classified observation state, not a guaranteed truth:

$$
\begin{aligned}
\mathcal R_{\mathrm{threat}}
&=
\mathrm{ClassifyRecognition}
\bigl(
P_{\mathrm{recognize}},
t_p,
\mathrm{threatConfidence},
\mathrm{perceivedEvidence}
\bigr)
\end{aligned}
$$

Possible outputs include `unrecognized`, `suspected`, `recognized`, `recognized_late`, and `recognized_with_wrong_model`.

Plain rule:

```text
A character cannot actively defend against a threat they have not perceived, predicted, or already covered.
```

---

### Feints and Misreads

A feint changes perceived threat, not physical truth.

$$
\begin{aligned}
P_{\mathrm{misread}}
&=
\Phi_{\mathrm{misread}}
(
\mathrm{feintQuality},
S,
N,
A_{\mathrm{attn}},
\Sigma,
\mathrm{defenderTraining},
\mathrm{priorBelief},
\mathrm{pressure}
)
\end{aligned}
$$

Possible outputs:

```text
clean read
partial read
late read
wrong line
wrong timing
wrong intent
false opening
ignored real threat
overreaction
```

Plain rule:

```text
Feints work by shaping the defender's model of the exchange.
They do not rewrite the attack after the fact.
```

---

### Insight Combat Boundary

Insight may provide partial combat information when the evidence path exists and the skill can parse it.

Combat-facing Insight may reveal:

```text
threat direction
damage type hint
class or level fragment
status condition
weak point clue
barrier instability
injury signal
resource strain
intent ambiguity
```

Insight must not automatically reveal:

```text
true names
perfect future outcomes
hidden facts with no evidence path
unreachable tactical solutions
the full underlying reality behind the interface
```

Insight cost remains owned by `resource_system.md`.

Plain rule:

```text
Insight improves the read. It does not replace perception, inference, timing, or evidence.
```

---

### Perception Handoff to Combat

This file may send:

```text
PerceptionCombatOutput:
  perceptionTime
  threatRecognized
  recognitionClass
  threatConfidence
  perceivedAttackPath
  perceivedIntent
  perceivedDamageType
  perceivedTiming
  salienceState
  misreadRisk
  insightRead
  availableEvidence
  uncertainty
  routedFrom: perception_information.md
  routedTo: combat_defense.md
```

Combat & Defense consumes these values for:

```text
reaction margin
available defense modes
contact quality
feint success
Luck hook margins
tactical opening recognition
```

---

### Combat Handoff to Perception

Combat & Defense may send perception consequences back.

```text
CombatPerceptionHandoff:
  suddenImpact
  painShock
  visualObstruction
  noiseBurst
  salienceShift
  interfaceWarningCandidate
  threatModelUpdateCandidate
  misreadConsequence
  newVisibleEvidence
  newAudibleEvidence
  concealmentBroken
  disguiseOrFormExposed
  attackPatternRevealed
  barrierOrArmorFailureVisible
  attentionForcedByPhysicalEvent
  routedFrom: combat_defense.md
  routedTo: perception_information.md
```

This file resolves:

$$
\begin{aligned}
\mathcal I_{t+1}
&=
\Phi_{\mathrm{perception}}
(
\mathcal I_t,
\mathrm{shock},
\mathrm{noise},
\mathrm{occlusion},
\mathrm{pain},
\mathrm{salienceShift},
\mathrm{newEvidence}
)
\end{aligned}
$$

Plain rule:

```text
Combat changes what can be noticed next.
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

This subsystem uses the canonical model from `luck_fortune.md` and defines only perception-local uncertainty.

### Local Possibility State

$$
\begin{aligned}
z_{\mathrm{perception}}
&=
\bigl(
\mathrm{signalStrength},
\mathrm{noise},
\mathrm{attentionDirection},
\mathrm{salience},
\mathrm{evidenceVisibility},
\mathrm{falsePositiveRisk},
\mathrm{falseNegativeRisk},
\mathrm{timingResidual},
\mathrm{occlusion}
\bigr)
\end{aligned}
$$

### Baseline Drift

Without Luck, perception follows senses, training, Insight, attention, prior belief, evidence quality, concealment, and environmental signal conditions.

### Uncertainty / Diffusion

Uncertainty enters through noise, occlusion, timing, salience competition, evidence damage, ambiguous cues, and marginal Insight resolution.

### Favorability Function

Favorability is observer-specific:

$$
\begin{aligned}
U_{\mathrm{perception}}^{(o)}(z,t)
&=
\mathrm{PerceptualFavorabilityForObserver}
\bigl(
 o,z,t
\bigr)
\end{aligned}
$$

A favorable state means that relevant evidence becomes visible, salient, preserved, or cleanly parsed for observer $o$. It does not mean the observer's preferred belief becomes true.

### Luck Interaction

Fortune and Misfortune bias drift among reachable evidence/read states. Volatility widens the spread between clean clue, missed clue, false lead, and corrupted read. This file supplies its local adapter contract to `luck_fortune.md` rather than copying the canonical probability-flow equations.

Bayesian boundary:

$$
\begin{aligned}
P(H\mid E)
&\propto
P(E\mid H)P(H)
\end{aligned}
$$

Luck may affect whether reachable evidence $E$ appears, survives, or becomes salient. It does not change the truth of hypothesis $H$ or make invalid inference valid.

### Reachability Constraints

Luck cannot grant knowledge with no evidence path, replace Insight, senses, training, or inference, reveal impossible information, or bypass deterministic concealment. It may matter only where the concealment/evidence system still leaves multiple reachable observation states.

### Result Classifier

$$
\begin{aligned}
\mathrm{Result}_{\mathrm{perception}}
&=
\mathrm{Classify}_{\mathrm{perception}}
\bigl(
 z_{\mathrm{perception,final}}
\bigr)
\end{aligned}
$$

Examples: unnoticed, vaguely noticed, suspected, confirmed, false lead, clean clue, corrupted read, partial Insight, or misleading signal.

### Forbidden Simplifications

Do not use Luck as omniscience, a flat perception bonus, evidence creation without a causal path, mind control, or a reason to resolve combat, motion, injury, resource, or strategic uncertainty in this file.

### Owner Handoff

```text
PerceptionLuckAdapterInput:
  observer
  localPossibilityState
  baselineReachableEvidenceSet
  unresolvedPerceptionCoordinates
  favorabilityPerspective
  evidencePath
  reachabilityGate
  classifier
  routedFrom: perception_information.md
  routedThrough: luck_fortune.md
```

Plain rule:

```text
Luck biases evidence emergence and read quality.
Perception still owns noticing, parsing, inference, and misread.
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
