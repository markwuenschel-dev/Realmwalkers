---
id: combat_defense
name: Combat & Defense
kind: system
status: scaffold
---

# Combat & Defense — Dominion Realm

> **Owner field:** Action Systems → Combat & Defense.
> **Status:** Scaffold / placeholder.
> **Owns:** attacks, penetration, armor, shields, timing, tactics.
> **Does not own:** system-wide routing (`core_rules.md`), unrelated subsystem formulas, or mature rules owned elsewhere.
> **Inputs from:** `resource_system.md`, `motion_positioning.md`, `embodiment_injury.md`.
> **Outputs to:** `embodiment_injury.md`, `perception_information.md`, `strategy_decision_systems.md`.

---

## Canon Locks

- This file exists so agents have a correct destination for future rules.
- Do not place combat & defense rules in `core_rules.md` unless they are routing or terminology rules.
- Do not duplicate mature formulas from owner files; cross-reference them.

---

## Combat Resolution Thesis

Combat is not an attack roll subtracting armor from damage. Combat is the resolution of bodies, weapons, powers, perception, timing, terrain, resources, and uncertainty into a physical outcome.

At the highest level:

$$
\begin{aligned}
\mathrm{Combat}
&=
\mathrm{Geometry}
+\mathrm{Motion}
+\mathrm{Force}
+\mathrm{MaterialResistance}
+\mathrm{PowerExpression}
\\
&\quad
+\mathrm{Information}
+\mathrm{Timing}
+\mathrm{ResourceTolerance}
+\mathrm{InjuryConsequence}
+\mathrm{Uncertainty}
\end{aligned}
$$

A combat exchange answers four linked questions:

1. **Can the attack occupy the target's space?**
2. **If contact occurs, what kind of contact is it?**
3. **If force or effect transfers, what defensive layers modify it?**
4. **If damage gets through, what consequence does the body, resource system, or metaphysical structure suffer?**

This file owns the exchange engine: contact, defense, penetration, shielding, armor, timing, and tactical resolution. It does **not** own the full resource formulas, detailed anatomy, spell taxonomy, or interface display rules. Those belong to their owner files.

Combat resolution should preserve three principles:

### 1. Physical sequence before result

A result must come from a traceable exchange.

```text
Wrong: Serra got through his guard.
Right: Serra drew his guard high, stepped inside the recovery line, turned his block into a screen, and cut past his exposed ribs.
```

The system may classify the outcome as `guard bypass`, `clean contact`, or `deep wound`, but the scene must still be physically intelligible when the exchange is choreography-load-bearing.

### 2. Defense is layered, not singular

A defender may survive because the attack missed, arrived late, struck at a bad angle, hit armor, was absorbed by a shield, lost coherence, failed to penetrate, damaged HP without causing severe injury, or caused injury that the body/resource system could still tolerate.

Defense therefore resolves through layered questions:

$$
\mathrm{Defense}
=

\mathrm{Avoidance}
+
\mathrm{Interception}
+
\mathrm{Deflection}
+
\mathrm{Absorption}
+
\mathrm{Resistance}
+
\mathrm{Recovery}
+
\mathrm{Counterpressure}
$$

A character with strong defense is not simply “hard to damage.” They may be hard to line up, hard to surprise, hard to penetrate, hard to stagger, hard to exhaust, hard to disable, or hard to keep down. These are different defensive strengths.

### 3. HP damage and injury are related, not identical

HP represents immediate survivability and bodily integrity under damage. Injury represents specific functional harm.

Combat & Defense may output both:

$$
\mathrm{HPDamage}
$$

and:

$$
\mathrm{InjuryRisk}
$$

But detailed wound progression, organ damage, trauma, poison, disease, and long-term impairment route to `embodiment_injury.md`.

A clean hit may cause high HP damage without a specific lasting injury. A lower-damage hit may cause a serious functional injury if it reaches the wrong structure. A character can remain alive and combat-capable while still carrying an injury that changes movement, concentration, regeneration, organ function, or future risk.

## Combat Exchange Spine

Use this as the provisional author-facing spine:

$$
\mathrm{Exchange}
=
\Phi_{\mathrm{combat}}(A, D, E, R, I, L)
$$

Where:

- $A$ = attack vector
- $D$ = defense state
- $E$ = environment
- $R$ = resource state
- $I$ = information/perception state
- $L$ = Luck/Fortune coupling

The exchange produces:

$$
\begin{aligned}
\mathrm{CombatResult}
=
(&\mathrm{contactClass},
\mathrm{penetration},
\mathrm{damage},
\mathrm{injuryRisk},
\\
&\mathrm{resourceCost},
\mathrm{positionChange},
\mathrm{tempoChange},
\mathrm{tacticalOpening})
\end{aligned}
$$

Plain rule:

```text
Combat resolves as a causal chain. The math exists to preserve consequence, not to replace staging.
```

---

## Local Combat State

Combat state tracks the minimum variables needed to resolve an exchange without turning the entire story into a physics simulation.

The broad local state is:

$$
\begin{aligned}
x_{\mathrm{combat}}(t)
=
(B, W, G, A_r, S, R, P, E, T, L)
\end{aligned}
$$

Where:

| Symbol | Field                   | Meaning                                                                |
| ------ | ----------------------- | ---------------------------------------------------------------------- |
| $B$    | Body state              | posture, stance, balance, reach, facing, injury, fatigue               |
| $W$    | Weapon / effect state   | weapon path, spell vector, projectile line, active edge, force carrier |
| $G$    | Guard state             | blocks, parries, shield angle, defensive coverage, open lines          |
| $A_r$  | Armor / barrier state   | armor coverage, barrier coherence, shield durability, weak points      |
| $S$    | Skill / technique state | trained pattern, execution quality, mastery, style constraints         |
| $R$    | Resource state          | current HP, Mana, Stamina, Reserve, depletion pressure                 |
| $P$    | Perception state        | awareness, target model, visibility, read accuracy, deception          |
| $E$    | Environment state       | terrain, footing, cover, weather, light, crowding, hazards             |
| $T$    | Timing state            | initiative, reaction window, commitment, recovery, tempo               |
| $L$    | Luck/Fortune state      | reachable uncertainty bias, Fortune/Misfortune/Volatility              |

This local state does not replace subsystem owners. It references them.

* Detailed resources route to `resource_system.md`.
* Detailed anatomy and injury route to `embodiment_injury.md`.
* Detailed motion and terrain traversal route to `motion_positioning.md`.
* Detailed perception, stealth, salience, Insight, and illusion route to `perception_information.md`.
* Detailed power-domain expression routes to `power_expression.md`.

### Actor State

For a combatant $i$:

$$
\begin{aligned}
C_i(t)
=

(
q_i,
\dot{q}_i,
g_i,
r_i,
s_i,
p_i,
\tau_i
)
\end{aligned}
$$

Where:

| Term        | Meaning                                                  |
| ----------- | -------------------------------------------------------- |
| $q_i$       | body configuration: stance, facing, limb/weapon position |
| $\dot{q}_i$ | motion: velocity, acceleration, directional change       |
| $g_i$       | guard geometry: protected and exposed lines              |
| $r_i$       | resource pressure: HP, Mana, Stamina, Reserve state      |
| $s_i$       | injury and condition state                               |
| $p_i$       | perception / belief state                                |
| $\tau_i$    | timing state: initiative, commitment, recovery           |

### Attack Vector

An attack vector is:

$$
\begin{aligned}
A
=

(
\mathrm{path},
\mathrm{power},
\mathrm{speed},
\mathrm{precision},
\mathrm{timing},
\mathrm{commitment},
\mathrm{intent},
\mathrm{expression}
)
\end{aligned}
$$

| Component    | Meaning                                                                  |
| ------------ | ------------------------------------------------------------------------ |
| `path`       | where the attack travels                                                 |
| `power`      | force or effect magnitude                                                |
| `speed`      | how quickly it arrives                                                   |
| `precision`  | how accurately it targets the intended line                              |
| `timing`     | when it lands relative to guard, movement, and recovery                  |
| `commitment` | how hard it is to redirect or cancel                                     |
| `intent`     | tactical purpose: kill, disable, stagger, bind, distract, force movement |
| `expression` | physical, magical, psychic, domain-based, or hybrid effect form          |

### Defense State

A defense state is:

$$
\begin{aligned}
D
=

(
\mathrm{position},
\mathrm{guard},
\mathrm{mobility},
\mathrm{awareness},
\mathrm{intercept},
\mathrm{deflection},
\mathrm{absorption},
\mathrm{resistance},
\mathrm{countertiming}
)
\end{aligned}
$$

| Component       | Meaning                                                                |
| --------------- | ---------------------------------------------------------------------- |
| `position`      | whether the defender is in the attack's resolved path                  |
| `guard`         | what lines are covered or exposed                                      |
| `mobility`      | ability to evade, pivot, retreat, advance, or change level             |
| `awareness`     | whether the defender reads the threat in time                          |
| `intercept`     | ability to place weapon, limb, shield, barrier, or ally into the line  |
| `deflection`    | ability to change impact angle or path                                 |
| `absorption`    | ability to eat force through armor, shield, barrier, body, or resource |
| `resistance`    | ability to tolerate what gets through                                  |
| `countertiming` | ability to turn the attacker's commitment into an opening              |

### Environment State

Environment matters when it changes the reachable branches of an exchange.

$$
\begin{aligned}
E
=

(
\mathrm{terrain},
\mathrm{footing},
\mathrm{visibility},
\mathrm{cover},
\mathrm{crowding},
\mathrm{hazards},
\mathrm{weather},
\mathrm{verticality},
\mathrm{noise}
)
\end{aligned}
$$

Examples:

* Bad footing widens uncertainty in dodge, brace, and recovery.
* Darkness increases perception error and makes feints stronger.
* Crowding restricts weapon paths and retreat lines.
* Cover can break line of effect but also trap movement.
* Verticality changes reach, falling risk, and momentum.
* Noise can degrade coordination and warning response.

## Timing State

Timing state tracks whether an actor can still alter the outcome.

$$
\begin{aligned}
T
=
(
\mathrm{initiative},
\mathrm{reactionWindow},
\mathrm{commitment},
\mathrm{recovery},
\mathrm{tempoPressure}
)
\end{aligned}
$$

A practical reaction margin:

$$
\begin{aligned}
\mathrm{ReactionMargin}
=
T_{\mathrm{impact}}
-
T_{\mathrm{perception}}
-
T_{\mathrm{decision}}
-
T_{\mathrm{movement}}
\end{aligned}
$$

If:

$$
\mathrm{ReactionMargin} > 0
$$

the defender can meaningfully respond.

If:

$$
\mathrm{ReactionMargin} \leq 0
$$

the defender is relying on prior positioning, passive guard, armor, shield, barrier, Luck, or outside intervention.

### Combat Result Object

A resolved exchange should output a structured result:

```text
CombatResult:
  contactClass
  contactQuality
  defenseMode
  penetration
  hpDamage
  injuryRisk
  resourceCost
  armorOrShieldDamage
  positionChange
  tempoChange
  tacticalOpening
  routedOutputs
```

Where `routedOutputs` names downstream owners:

```text
routedOutputs:
  embodiment_injury.md
  resource_system.md
  perception_information.md
  strategy_decision_systems.md
```

Plain rule:

```text
Combat & Defense decides what happened in the exchange. Other owner files decide what that consequence means inside their domain.
```

---


## Luck/Fortune Adapter

This subsystem uses the canonical Luck/Fortune model from `luck_fortune.md`.

### Local Possibility State

$$
\begin{aligned}
z_{\mathrm{combat}} = (\mathrm{aimError},\ \mathrm{timingError},\ \mathrm{defenderMotion},\ \mathrm{guardAngle},\ \mathrm{weaponPath},\ \mathrm{penetrationAngle},\ \mathrm{woundDepth},\ \mathrm{organProximity},\ \mathrm{footingStability},\ \mathrm{reactionWindow})
\end{aligned}
$$

The local possibility state tracks where an attack lands, how deep it goes, and whether marginal timing or geometry resolves favorably.

### Baseline Drift

Without Luck, outcomes evolve according to attacker skill, speed, weapon path, Strength, technique; defender guard, movement, armor, awareness; terrain, visibility, fatigue, and injury.

$$
b_{\mathrm{combat}}(z,t) \text{ pushes toward skill-weighted expected trajectories.}
$$

### Uncertainty / Diffusion

Uncertainty enters through chaotic body motion, marginal timing, partial visibility, unstable footing, weapon deflection, armor glance angles, reaction delay, and battlefield interference.

High uncertainty means Luck has more leverage. Low uncertainty means Luck has less leverage.

### Favorability Function

$$
U_{\mathrm{combat}}(z)
$$

In this subsystem, favorable outcomes depend on **whose perspective is measured** — graze instead of deep wound for the defender; clean opening instead of bind for the attacker. Define $U_{\mathrm{combat}}$ per side before applying Fortune or Misfortune.

### Luck Interaction

Fortune biases trajectories toward favorable reachable combat states for the favored side. Misfortune biases toward harmful reachable states. Volatility increases spread — miraculous saves and absurd catastrophes from the same instability.

A practical local drift expression:

$$
\begin{aligned}
u_{L,\mathrm{combat}} = \lambda_L R_{\mathrm{combat}}(z,t)\,\nabla U_{\mathrm{combat}}(z,t)
\end{aligned}
$$

## Luck Hook Points

Luck does not replace skill, armor, movement, timing, awareness, or power. Luck biases uncertain reachable branches after deterministic combat variables have created the possibility space.

Use Luck after the exchange has established:

1. what physical paths exist,
2. what timing windows remain,
3. what contact classes are plausible,
4. what injuries are physically reachable,
5. what environmental slips or openings are plausible,
6. what outcomes are causally impossible.

Luck may enter only where uncertainty remains.

### Hook Point 1 — Aim and Path Error

Luck may bias marginal aim and path deviation.

$$
\begin{aligned}
\Delta_{\mathrm{aim}}
=

f(
\mathrm{attackerPrecision},
\mathrm{defenderMotion},
\mathrm{visibility},
\mathrm{weaponPath},
\mathrm{interference}
)
\end{aligned}
$$

Possible Luck effects:

* clean hit becomes graze,
* graze becomes miss,
* near miss becomes graze,
* projectile catches shield rim instead of neck,
* wild swing happens to occupy a useful lane,
* chaotic melee creates a plausible opening.

Luck cannot turn an attack into a hit if no physical path exists.

### Hook Point 2 — Timing Window

Luck may bias marginal timing when a reaction, parry, dodge, or interruption is close.

$$
\begin{aligned}
\Delta_{\mathrm{timing}}
=

T_{\mathrm{impact}}

T_{\mathrm{response}}
\end{aligned}
$$

Possible Luck effects:

* parry arrives barely in time,
* dodge clears by a handspan,
* counter lands during recovery,
* attacker overcommits one fraction too long,
* defender's warning shout reaches the target just soon enough.

Luck cannot create reaction time where the defender had no awareness, no pre-positioning, and no plausible response branch.

### Hook Point 3 — Contact Quality

Luck may bias which contact class resolves when several are plausible.

$$
\begin{aligned}
\mathrm{ContactQuality}
=

\Phi_{\mathrm{contact}}
(
\mathrm{pathAlignment},
\mathrm{guardCoverage},
\mathrm{angle},
\mathrm{range},
\mathrm{footing},
\mathrm{reactionWindow}
)
\end{aligned}
$$

Possible Luck effects:

* clean contact becomes partial contact,
* partial contact becomes armor glance,
* bind breaks favorably,
* shield catches the worst of the force,
* edge alignment slips at impact,
* blow lands with worse leverage than intended.

Luck cannot erase a deterministic clean strike that has already passed guard, position, and resistance with no plausible deviation.

### Hook Point 4 — Penetration Angle

Luck may bias marginal penetration where angle, armor, shield, or tissue path is uncertain.

$$
\begin{aligned}
\theta_{\mathrm{penetration}}
=

\angle(
\mathrm{attackVector},
\mathrm{surfaceNormal}
)
\end{aligned}
$$

Possible Luck effects:

* blade skids along armor instead of biting,
* spear point catches a seam instead of plate,
* arrow hits at a shallow angle,
* claw tears fabric and skin but misses tendon,
* shield turn converts puncture into blunt transfer.

Luck cannot make weak armor stop overwhelming force if the power gap leaves no plausible defensive branch.

### Hook Point 5 — Wound Path and Criticality

Luck may bias wound-path variation after penetration, especially around organ proximity, depth, bleeding, and disabling structures.

$$
\begin{aligned}
\mathrm{Criticality}
=

\Psi(
\mathrm{woundDepth},
\mathrm{organProximity},
\mathrm{tissueType},
\mathrm{bleedingRisk},
\mathrm{shockRisk}
)
\end{aligned}
$$

Possible Luck effects:

* wound misses the artery by a narrow margin,
* cut opens flesh but spares tendon,
* rib deflects the point away from lung,
* impact stuns without concussing,
* deep wound becomes disabling instead of lethal,
* shallow wound becomes worse because it crosses the wrong structure.

Luck cannot make a clean decapitation, crushed skull, destroyed heart, or other deterministic lethal injury harmless.

### Hook Point 6 — Footing and Balance

Luck may bias unstable terrain, slips, stumbles, landings, and recovery when footing is already uncertain.

$$
\begin{aligned}
\mathrm{FootingStability}
=

f(
\mathrm{terrain},
\mathrm{momentum},
\mathrm{stance},
\mathrm{injury},
\mathrm{load},
\mathrm{visibility}
)
\end{aligned}
$$

Possible Luck effects:

* defender's heel finds stone instead of mud,
* attacker slips during overcommitment,
* bad landing becomes ugly but survivable,
* shield-bearer staggers but does not fall,
* loose gravel breaks pursuit timing.

Luck cannot let a character stand on nothing, ignore gravity, or recover from a movement state with no causal recovery path.

### Hook Point 7 — Battlefield Interference

Luck may bias chaotic third-party interference.

Possible Luck effects:

* stray projectile clips a guard line,
* falling debris interrupts a pursuit,
* panicked civilian blocks a killing angle,
* enemy ally steps into the wrong lane,
* noise masks or reveals a movement at the right moment.

Luck cannot invent an actor, object, or hazard that was not present or plausibly introduced.

### Hook Point 8 — Resource Crash Branches

Resource-side consequences route to `resource_system.md`, but Combat & Defense may identify when combat pressure creates a marginal crash branch.

Possible Luck effects:

* overguard causes clean Stamina collapse instead of joint injury,
* barrier failure drains Mana instead of triggering Reserve backlash,
* desperate movement burns Reserve but avoids immediate seizure-equivalent failure,
* exhaustion causes stumble instead of full collapse.

Luck cannot create extra HP, Mana, Stamina, or Reserve, erase resource debt, or let a character continue freely after a deterministic hard stop.

### Hook Point 9 — Perception and Interface Margins

Perception-side consequences route to `perception_information.md` and interface display rules, but Combat & Defense may identify when a read is marginal.

Possible Luck effects:

* Marcus notices the real attack line before the feint completes,
* a warning arrives without enough detail but soon enough to matter,
* salience suppression flickers at the wrong moment,
* a combatant catches a breath change, shoulder turn, or weight shift.

Luck cannot turn partial data into wisdom, make an interface understand what it cannot classify, or guarantee correct interpretation.

### Hook Point Order

Use this order when resolving Luck in combat:

1. Establish deterministic state.
2. Remove impossible outcomes.
3. Identify uncertain reachable branches.
4. Define whose favorability function is being measured.
5. Apply Fortune, Misfortune, or Volatility only to reachable branches.
6. Classify the final result.
7. Route downstream consequences.

Author-facing sequence:

$$
\begin{aligned}
z_{\mathrm{reachable}}
=

\mathrm{Reachable}
(
z_{\mathrm{combat}},
A,
D,
E,
R,
I
)
\end{aligned}
$$

$$
\begin{aligned}
z_{\mathrm{biased}}
=

z_{\mathrm{reachable}}
+
u_{L,\mathrm{combat}}
\end{aligned}
$$

$$
\begin{aligned}
\mathrm{Result}_{\mathrm{combat}}
=

\mathrm{Classify}*{\mathrm{combat}}
(
z*{\mathrm{biased}}
)
\end{aligned}
$$

Plain rule:

```text
Luck chooses among plausible endings. It does not write a new scene.
```

### Fortune, Misfortune, and Volatility

**Fortune** narrows uncertainty toward favorable reachable outcomes.

Examples:

* graze instead of deep wound,
* shield bind instead of disarm,
* stumble instead of fall,
* delayed crash instead of immediate collapse.

**Misfortune** narrows uncertainty toward harmful reachable outcomes.

Examples:

* shallow cut crosses tendon,
* parry angle worsens,
* armor seam catches the point,
* recovery step lands badly.

**Volatility** widens the spread.

Examples:

* miraculous save,
* absurd catastrophe,
* both sides losing their planned line,
* chaotic opening neither side intended.

Volatility should feel unstable, not benevolent.

### Do-Not-Use Luck For

Luck must not be used to:

* replace training,
* ignore armor,
* ignore speed,
* ignore awareness,
* cancel overwhelming power,
* force plot armor,
* make characters survive deterministic lethal mistakes,
* convert failed positioning into free success,
* increase resource maximums,
* override agency,
* make the interface wise.

Luck is strongest at the margins. Combat should still belong to skill, position, timing, cost, and consequence.

### Reachability Constraints

Luck can affect graze vs clean hit, wound path, organ proximity, footing slip, marginal parry angle, timing window, critical severity, stray projectile placement, and whether chaotic melee creates a favorable opening.

Luck cannot negate a clean deterministic strike, make an unblocked lethal blow vanish, replace skill/positioning/armor/speed/awareness, override an overwhelming power gap with no plausible branch, or turn a missed attack into a hit if no physical path exists.

Plain rule: Luck can bias reachable outcomes. It cannot select outcomes with no causal path.

### Result Classifier

$$
\begin{aligned}
\mathrm{Result}_{\mathrm{combat}} = \mathrm{Classify}_{\mathrm{combat}}(z_{\mathrm{final}})
\end{aligned}
$$

Examples: clean miss, near miss, graze, shallow wound, deep wound, disabling wound, organ-threatening wound, lethal wound, armor deflection, weapon bind, footing failure, timing advantage.

### Notes

Luck should not replace combat skill, training, armor, speed, awareness, or enemy agency. It biases uncertainty around those factors.

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





