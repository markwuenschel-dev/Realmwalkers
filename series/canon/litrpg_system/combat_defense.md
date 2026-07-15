---
id: combat_defense
name: Combat & Defense
kind: system
status: working
last_updated: 2026-07-14
---

# Combat & Defense — Dominion Realm

> **Owner field:** Action Systems → Combat & Defense.
> **Status:** Working system document.
> **Owns:** local combat exchange, contact, effect transfer, penetration, armor, shields, barriers, timing, recovery, interposition resolution, and exchange-created tactical openings.
> **Does not own:** final resource accounting, detailed injury progression, full motion, full perception, full strategy, power taxonomy, tier ladders, XP/progression math, Luck/Fortune law, or interface formatting.
> **Inputs from:** `resource_system.md`, `motion_positioning.md`, `embodiment_injury.md`, `perception_information.md`, `strategy_decision_systems.md`, `power_expression.md`, `mechanics.md`, `luck_fortune.md`.
> **Outputs to:** `resource_system.md`, `embodiment_injury.md`, `motion_positioning.md`, `perception_information.md`, `strategy_decision_systems.md`, `xp_progression_formulas.md`, `luck_fortune.md`, `system_message_rules.md`.

---
## Agent Navigation

This file is long. Agents should not read it as linear prose.

Read order for combat-defense work:

1. **Canon Locks** — binding file-level rules and ownership boundaries.
2. **Combat Resolution Thesis** — what combat resolution is for.
3. **Local Combat State** — shared state variables used by the rest of the file.
4. **Exchange Engine** — attack vectors, defense states, timing, contact, impact, penetration, and damage output.
5. **Defensive Layers** — armor, shields, barriers, coverage, deflection, leakage, durability, and collapse.
6. **Cost and Consequence** — resource pressure, HP damage, injury-risk routing, Luck-cost routing, raw progression traces, and downstream outputs.
7. **Information and Team Systems** — perception, feints, interface reads, tactical graph, team defense, and formation pressure.
8. **Luck/Fortune Adapter** — stochastic marginal outcomes after deterministic reachability is established.
9. **Canon Constraints and Character Applications** — scene locks, Book-1 requirements, and character combat-defense signatures.
10. **Presentation Rules** — prose rendering, interface display, and reader-facing clarity rules.
11. **Optional Computational Reduction / Simulator** — non-canon implementation model, fixtures, and tests.

Do not infer subsystem ownership from nearby examples. Use the owner boundaries below.

---

## Owner Boundaries — Quick Reference

### Combat & Defense owns

* attack vectors
* defense states
* defense modes
* timing, commitment, recovery, and tempo
* contact quality
* impact and effect transfer
* penetration logic
* armor, shields, and barriers
* combat-layer durability and failure states
* tactical openings created by exchanges
* local battlefield consequences

### Combat & Defense does not own

* final HP / Mana / Stamina / Reserve accounting → `resource_system.md`
* detailed wound progression, organ damage, poison, trauma, and long-term impairment → `embodiment_injury.md`
* detailed motion/travel/pathfinding outside the local exchange → `motion_positioning.md`
* spell taxonomy, Domain taxonomy, casting architecture, or power-source rules → `power_expression.md`
* perception, Insight, stealth, illusion, salience, and full information mechanics → `perception_information.md`
* large-scale strategy, campaign planning, faction strategy, and nonlocal tactical doctrine → `strategy_decision_systems.md`
* interface formatting and UI display rules → `system_message_rules.md`
* general system routing and terminology → `core_rules.md`
* Luck/Fortune global mechanics, active-control cost, entropy debt, and Volatility law → `luck_fortune.md`
* Spell Strength, Spell Skill Mastery, Soul Level, Item Quality, Item Rarity, and other tier ladders → `mechanics.md`
* class-level XP thresholds, class-rarity XP burden, rarity-linked signature-ability energy burden, combat adaptation, scene XP, and recovery integration → `xp_progression_formulas.md`

Plain rule:

```text
Combat & Defense decides what happened in the exchange.
Other owner files decide what that consequence means inside their domains.
```

---


## Section Contracts — Master Map

The contracts below govern this file even when an example touches another subsystem.

| Section | Combat & Defense owns here | Required inputs | Outputs | Hard boundary |
|---|---|---|---|---|
| Combat Resolution Thesis | the purpose and causal order of a combat exchange | owner-file state summaries | resolution principles | does not define another subsystem's internal mechanics |
| Local Combat State | the minimum local state references needed by an exchange | motion, resource, injury, perception, environment, power-expression state, typed Mechanics projections | a combat-local state snapshot | referenced state and tier meaning remain owned by their source files |
| Exchange Engine | reachability, timing use, defense-mode interaction, contact, transfer, penetration, local consequence | attack, defense, motion feasibility, perception intake, resource tolerance, typed Mechanics projection | contact, penetration, HP-damage output, injury-risk output, cost pressure, displacement, tempo, opening | does not finalize resource debits, injury anatomy, XP, or strategic choice |
| Defensive Layers | armor, shield, barrier, weapon, terrain, and ally-layer interaction | effective impact, layer state, angle, coverage, compatibility | leakage, deflection, absorption, durability/coherence pressure, transmitted effect | does not own material crafting, final resource debit, or wound progression |
| Cost and Consequence | combat-facing consequence classification and downstream routing | exchange and layer results | resource-pressure, injury-risk, motion, perception, strategy, Luck-cost, and raw progression-evidence handoffs | owner files determine final meaning inside their domains; Combat never calculates final XP |
| Information and Team Systems | how supplied information and declared team actions alter this exchange | perception-state output and strategy/local-team plan | available defense branches, local interposition result, formation-pressure result, tactical opening | does not own perception generation, team planning, target priority, or graph evaluation |
| Luck/Fortune Adapter | combat-specific reachable uncertainty hook points | canonical Luck/Fortune state | biased marginal branch classification | cannot create a causal path or replace skill |
| Canon Constraints and Character Applications | binding story constraints and combat signatures | owner canon and character files | safe application guidance | examples cannot create new class, interface, resource, or injury canon |
| Presentation Rules | what combat information prose must render | resolved exchange facts | reader-facing causal clarity | UI formatting remains owned by `system_message_rules.md` |
| Optional Computational Reduction / Simulator | testable author-tool reduction of this document | normalized owner-file inputs, including typed Mechanics and canonical Luck adapters | trace, result object, and handoffs | code is subordinate to prose canon, performs no ordinal-tier arithmetic, and cannot become canon by implementation |

Plain rule:

```text
A section may consume another subsystem's output.
Consumption does not transfer ownership.
```

---

## Canon Locks

* This file exists so agents have a correct destination for future rules.
* Do not place combat & defense rules in `core_rules.md` unless they are routing or terminology rules.
* Do not duplicate mature formulas from owner files; cross-reference them.
* Do not use Affinity to mean Domain. Affinity is progression aptitude; Domain is power expression/source category.
* Do not treat interface readouts as underlying reality.
* Do not make resource formulas canonical here unless this file is explicitly made the resource owner.
* Do not make detailed injury anatomy canonical here unless this file is explicitly made the injury owner.
* Do not allow Luck/Fortune to select outcomes with no causal path.
* Do not reproduce or locally replace the canonical probability-flow, active-control, entropy-cost, or Volatility equations from `luck_fortune.md`.
* Do not perform arithmetic directly on ordinal tier labels from `mechanics.md`.
* Do not use Item Rarity as armor power, barrier power, weapon power, or durability.
* Do not use Soul Level as a general physical or magical combat multiplier; it applies only where identity, binding, possession, erasure, overwrite, vows, death, or resurrection are causally involved.
* Do not use class rarity as a direct combat-power multiplier.
* `xp_progression_formulas.md` is the current owner of XP thresholds, rarity burden, combat adaptation, and scene XP. Combat outputs raw evidence only; legacy XP tables elsewhere are not consumed here.

---

## Mathematical Conventions and Invariants

These conventions prevent the author-facing reductions from silently changing type, scale, or ownership.

### Symbol and Type Discipline

* $\mathcal I$ denotes information or belief state. $I$ denotes physical, magical, psychic, spiritual, or other effect intensity.
* Calligraphic symbols such as $\mathcal B$, $\mathcal D$, and $\mathcal Z$ denote sets, graphs, or structured state spaces. They are not added to ordinary state vectors.
* $Q$, $P$, coverage factors, bite factors, deflection fractions, and passive transfer fractions are dimensionless and lie in $[0,1]$.
* Multipliers $M_*$ are dimensionless and nonnegative. A multiplier above $1$ must identify the mechanism and source that supplies the additional effect.
* Intensities $I_*$ and defense powers $D_{\mathrm{pow},*}$ use the same local effect-equivalent scale inside one exchange. They are author-facing comparison quantities, not universal SI measurements.
* $K$, $J$, $\Delta\mathcal S_{\mathrm{layer}}$, $O$, $F_{\mathrm{pressure}}$, and $\Delta\mathcal I$ are structured outputs unless a subsection explicitly selects a scalar component. They must not be compared as if they shared one numeric unit.
* $\Delta x$ and $\Delta\tau$ are signed state changes. In a multi-actor exchange they are actor-keyed maps rather than one shared scalar. Indicator variables $\chi_*$ and $\mathbf 1[\cdot]$ are binary.
* Every threshold ratio uses a small positive guard $\varepsilon$ so the zero-intensity / zero-defense case is defined.
* If target substitution or a distributed attack creates more than one nonzero target share, all path-local quantities—$P$, $H$, $J$, $\Delta\mathcal S_{\mathrm{layer}}$, $\mathbf I_{\mathrm{final}}$, and the layer trace—lift to target-keyed maps. Scalar notation is single-target shorthand.
* Per-target layer recurrences suppress the target index $k$ for readability. They must be evaluated independently after the incoming carrier or exposure has been conservatively allocated; no target receives the full pre-allocation intensity unless its weight is $1$.
* Combat symbols are locally namespaced. In particular, combat $H$ means HP-damage output and combat $K$ means resource/concentration pressure. They are not the hormetic window $H_j$ or class-method coupling $K_{\mathcal C}$ from `xp_progression_formulas.md`.
* $\Delta\mathcal S_{\mathrm{layer}}$ is the defensive-layer state-change map. The symbol $\Lambda(t)$ is reserved by `xp_progression_formulas.md` for organismic adaptive load and is never used for layer damage in this file.

### Typed Mechanics Projection

The tier ladders in `mechanics.md` are typed ordinal classifications. They are not numbers and cannot enter combat equations without an explicit owner-supplied projection.

Use:

$$
\begin{aligned}
\mathcal T_{\mathrm{mechanics}}
&=
\bigl(
\mathrm{SpellStrengthTier},
\mathrm{SpellSkillMasteryTier},
\mathrm{ItemQualityTier},
\mathrm{ItemRarityTier},
\mathrm{SoulLevelTier}
\bigr)
\\
\boldsymbol\mu_{\mathrm{mechanics}}
&=
\Pi_{\mathrm{mechanics}\rightarrow\mathrm{combat}}
\bigl(
\mathcal T_{\mathrm{mechanics}},
\mathrm{expression},
\mathrm{targetChannel},
\mathrm{context}
\bigr)
\end{aligned}
$$

$\boldsymbol\mu_{\mathrm{mechanics}}$ is a channel- and context-specific projection supplied by the owning mechanics and power-expression rules. It may contain a numeric Spell Skill Mastery bonus, spell-expression components, item-performance information, or identity-resistance information. Combat consumes that projection; it does not invent a crosswalk between tiers.

Binding rules:

* **Spell Strength** measures completeness of expression across properties such as power, precision, duration, range, stability, control, dispel resistance, or permanence. It is not a universal damage scalar.
* **Spell Skill Mastery** may strengthen applicable spell expression and resistance only for the matching discipline and only through the owner-supplied bonus. It does not increase unrelated physical defense or all magic universally.
* **Item Quality** may affect reliability, execution, balance, durability, and closeness to the item's design ideal when the item/material owner supplies that projection.
* **Item Rarity** describes scarcity and significance. It supplies no automatic attack, defense, penetration, or durability bonus.
* **Soul Level** may supply identity/metaphysical resistance only when the attack actually targets the soul, name, vow, possession boundary, erasure resistance, overwrite resistance, death continuity, or resurrection continuity.
* **Class rarity** is absent from the local exchange equation. It affects XP and energy burden through its owner files, not automatic combat superiority.
* Repeated words such as `Divine`, `Mythic`, `Legendary`, `Epic`, `Fabled`, `Exceptional`, or `Common` on different ladders do not imply equal magnitude or any automatic conversion.

### No-Double-Counting Rule

A causal factor may shape more than one stage only when each use represents a different mechanism. In particular:

```text
contact quality scales arrival once,
coverage partitions the contacted line once,
layer transfer is resolved once per layer,
location vulnerability enters body consequence rather than raw impact,
and timing may not be counted again after it has already been absorbed into contact quality.
```

### Effect-Accounting Invariant

For every defensive layer $j$, effect must be accounted for through inward transfer, deflection, absorption/dissipation, layer damage, or an explicit active source:

$$
\begin{aligned}
\mathbf I_j
+\mathbf I_{\mathrm{source},j}
&=
\mathbf I_{j+1}
+\mathbf I_{\mathrm{deflected},j}
+\mathbf I_{\mathrm{absorbed},j}
+\mathbf I_{\mathrm{layerDamage},j}
\end{aligned}
$$

For passive layers, $\mathbf I_{\mathrm{source},j}=\mathbf 0$. Any nonzero source term must route to the power or resource mechanism that supplied it. The vectors may contain kinetic, cutting, piercing, thermal, psychic, spiritual, identity/namebinding, Domain, or other effect channels. All ledger terms must be expressed in one declared accounting basis and retain direction/path tags where needed, so outward deflection cannot masquerade as inward transfer. Scalar $I_j$ is only a weighted magnitude or a single-channel reduction of $\mathbf I_j$.

Plain rule:

```text
The math may transform effect. It may not create unexplained effect.
```

---


## Combat Resolution Thesis

Combat is not an attack roll subtracting armor from damage. Combat is the resolution of bodies, weapons, powers, perception, timing, terrain, resources, and uncertainty into a physical outcome.

At the highest level:

$$
\begin{aligned}
\mathrm{CombatResolution}
&=
\Phi_{\mathrm{combat}}
\bigl(
\mathrm{Geometry},
\mathrm{Motion},
\mathrm{Force},
\mathrm{MaterialResistance},
\mathrm{PowerExpression},
\\
&\qquad
\mathrm{Information},
\mathrm{Timing},
\mathrm{ResourceTolerance},
\mathrm{InjuryConsequence},
\mathrm{Uncertainty}
\bigr)
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
\begin{aligned}
\mathrm{DefenseOutcome}
&=
\Phi_{\mathrm{defense}}
\bigl(
\mathrm{Avoidance},
\mathrm{Interception},
\mathrm{Deflection},
\mathrm{Absorption},
\\
&\qquad
\mathrm{Resistance},
\mathrm{Recovery},
\mathrm{Counterpressure}
\bigr)
\end{aligned}
$$

A character with strong defense is not simply “hard to damage.” They may be hard to line up, hard to surprise, hard to penetrate, hard to stagger, hard to exhaust, hard to disable, or hard to keep down. These are different defensive strengths.

### 3. HP damage and injury are related, not identical

HP represents immediate survivability and bodily integrity under damage. Injury represents specific functional harm.

Combat & Defense may output the paired body-facing projection:

$$
\begin{aligned}
\mathbf Y_{\mathrm{body}}
&=
(H,J)
\\
&=
(
\mathrm{HPDamageOutput},
\mathrm{InjuryRiskOutput}
)
\end{aligned}
$$

But detailed wound progression, organ damage, trauma, poison, disease, and long-term impairment route to `embodiment_injury.md`.

A clean hit may cause high HP damage without a specific lasting injury. A lower-damage hit may cause a serious functional injury if it reaches the wrong structure. A character can remain alive and combat-capable while still carrying an injury that changes movement, concentration, regeneration, organ function, or future risk.

---

## Local Combat State

Combat state tracks the minimum variables needed to resolve an exchange without turning the entire story into a physics simulation.

The broad local state is:

$$
\begin{aligned}
x_{\mathrm{combat}}(t)
&=
(B, W, G, A_r, S, R, \mathcal I, E, T, \mathcal L, \boldsymbol\mu_{\mathrm{mechanics}})
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
| $\mathcal I$ | Information state | awareness, target model, visibility, read accuracy, deception |
| $E$    | Environment state       | terrain, footing, cover, weather, light, crowding, hazards             |
| $T$    | Timing state            | initiative, reaction window, commitment, recovery, tempo               |
| $\mathcal L$ | Luck/Fortune state | structured canonical Fortune/Misfortune/Volatility input; not a resource or ordinary stat |
| $\boldsymbol\mu_{\mathrm{mechanics}}$ | Mechanics projection | typed, channel-specific combat projection supplied from `mechanics.md` / power owners |

This local state does not replace subsystem owners. It references them.

* Detailed resources route to `resource_system.md`.
* Detailed anatomy and injury route to `embodiment_injury.md`.
* Detailed motion and terrain traversal route to `motion_positioning.md`.
* Detailed perception, stealth, salience, Insight, and illusion route to `perception_information.md`.
* Detailed power-domain expression routes to `power_expression.md`.
* Tier definitions route to `mechanics.md`; only typed combat projections enter $A$, $D$, or the layer state.
* Luck/Fortune probability flow routes to `luck_fortune.md`; $\mathcal L$ is a structured adapter input, never a flat roll bonus.
* XP/progression state is not part of exchange resolution. Combat emits raw adaptation facts after the exchange for `xp_progression_formulas.md`.

### Actor State

For a combatant $i$:

$$
\begin{aligned}
X_i^{\mathrm{combat}}(t)
&=
(
q_i,
\dot{q}_i,
g_i,
r_i,
s_i,
\iota_i,
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
| $\iota_i$ | perception / belief state |
| $\tau_i$    | timing state: initiative, commitment, recovery           |

---


## Exchange Engine

The Exchange Engine resolves a local combat clash as a constrained interaction between an attack vector, a defense state, timing, environment, resources, and information.

It does not ask:

```text
Did the attack roll beat the armor class?
```

It asks:

```text
What path did the attack take?
What did the defender perceive?
What defensive mode was available?
Did timing allow an active response?
What kind of contact occurred?
What force or effect transferred?
What layer resisted it?
What consequence resulted?
```

The Exchange Engine is the local collision point between multiple mathematical fields:

| Field | Combat Function |
|---|---|
| Geometry / topology | Defines range, angle, line, cover, reachable space, blocked paths, and impossible branches |
| Dynamics / mechanics | Resolves motion, acceleration, momentum, impact, force transfer, stagger, and recovery |
| Control theory | Models commitment, response windows, action cancellation, tempo control, and optimal defensive choice under constraint |
| Differential games | Handles attacker/defender interaction where each actor adapts to the other's available moves |
| Information theory / Bayesian inference | Models perception, feints, misreads, hidden intent, uncertainty, and threat classification |
| Nonlinear threshold systems | Resolves penetration, armor failure, barrier collapse, stagger thresholds, and sudden failure states |
| Integral field models | Resolves AoE, aura, swarm, environmental, psychic, or domain exposure over space and time |
| Stochastic / probability-flow models | Routes marginal uncertainty to the Luck/Fortune adapter without replacing deterministic causality |

The practical reduction is:

$$
\begin{aligned}
\mathrm{ExchangeTrace}
&=
(
\mathrm{Reachability},
\mathrm{Timing},
\mathrm{Contact},
\mathrm{Transfer},
\mathrm{Resistance},
\mathrm{Consequence}
)
\end{aligned}
$$

Plain rule:

```text
High-level math determines the hidden structure of the exchange. Reader-facing prose shows bodies, choices, costs, and consequences.
```

---

### Combat Exchange Spine

Use this as the provisional author-facing spine:

$$
\begin{aligned}
\mathrm{Exchange}
&=
\Phi_{\mathrm{exchange}}
(
A,
D,
T,
E,
R,
\mathcal I
)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $A$ | Attack Vector |
| $D$ | Defense State |
| $T$ | Timing, commitment, and recovery state |
| $E$ | Environment state |
| $R$ | Resource state |
| $\mathcal I$ | Information / perception state |

The exchange produces one authoritative result object:

$$
\begin{aligned}
\mathrm{CombatExchangeResult}
&=
(
C,
Q,
P,
H,
J,
K,
\Delta\mathcal S_{\mathrm{layer}},
\Delta x,
\Delta \tau,
O,
F_{\mathrm{pressure}},
\Delta\mathcal I,
\mathcal{R}_{\mathrm{route}}
)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $C$ | contact class |
| $Q$ | contact quality |
| $P$ | target-keyed bounded passive penetration / effect-transfer result; scalar for one target |
| $H$ | target-keyed HP-damage output; scalar for one target |
| $J$ | target-keyed injury-risk and target-location output |
| $K$ | resource and concentration pressure created by the exchange |
| $\Delta\mathcal S_{\mathrm{layer}}$ | defensive-layer durability/coherence change, leakage, collapse, or failure output |
| $\Delta x$ | local position-change request |
| $\Delta \tau$ | tempo, commitment, recovery, or initiative change |
| $O$ | tactical opening created or closed |
| $F_{\mathrm{pressure}}$ | local formation-pressure output |
| $\Delta\mathcal I$ | information consequence created by the physical exchange |
| $\mathcal{R}_{\mathrm{route}}$ | explicit owner-file routing map for downstream consequences |

$\mathcal{R}_{\mathrm{route}}$ may point to resources, injury, motion, perception, strategy, Luck/Fortune cost evaluation, progression evidence aggregation, and Interface-display candidates. Routing to `xp_progression_formulas.md` adds no new combat consequence field; it projects raw facts from the result and causal trace.

$K$ and $\Delta\mathcal S_{\mathrm{layer}}$ are structured rather than scalar:

$$
\begin{aligned}
K
&=
\left\{
K_{i,\rho}
\;\middle|\;
 i\in\mathcal A_{\mathrm{exchange}},
\rho\in\mathcal R_i
\right\}
\\
K_{i,\rho}
&=
\bigl(
K_{i,\rho,\mathrm{Stamina}},
K_{i,\rho,\mathrm{Mana}},
K_{i,\rho,\mathrm{ReservePressure}},
K_{i,\rho,\mathrm{Concentration}}
\bigr),
\\
&\qquad
\mathcal R_i
\subseteq
\{\mathrm{attack},\mathrm{defense},\mathrm{sustain}\}
\\
\Delta\mathcal S_{\mathrm{layer}}
&=
\left
\{
\Delta\mathcal S_{\mathrm{layer},k,j}
\;\middle|\;
 k\in\mathcal T_{\mathrm{resolved}},
 j\in\mathcal L_{\mathrm{path},k}
\right
\}
\end{aligned}
$$

For target-facing outputs:

$$
\begin{aligned}
P
&=
\left\{P_k\right\}_{k\in\mathcal T_{\mathrm{resolved}}}
\\
H
&=
\left\{H_k\right\}_{k\in\mathcal T_{\mathrm{resolved}}}
\\
J
&=
\left\{J_k\right\}_{k\in\mathcal T_{\mathrm{resolved}}}
\end{aligned}
$$

In an ordinary single-target exchange, $\mathcal T_{\mathrm{resolved}}$ contains one target with weight $1$, and the subscripts may be omitted.

$K$ records actor-keyed resource-pressure candidates created by the exchange; the resource owner performs final accounting. Index $i$ identifies the affected actor, while $\rho$ identifies why the pressure exists—attack execution, defense execution, or sustained continuation. Each $K_{i,\rho}$ carries the resource-channel candidates relevant to that actor and role. Each $\Delta\mathcal S_{\mathrm{layer},k,j}$ records target-path and layer-local integrity/coherence change, failure state, and accounted effect partitions.

The exchange trace also carries $\mathbf I_{\mathrm{final},k}$, the channel-resolved effect vector that reaches target path $k$. It is required evidence for $P_k$, $H_k$, and $J_k$, but it is not a competing consequence field. $P_k$ summarizes bounded passive transfer; $\mathbf I_{\mathrm{final},k}$ preserves channel composition and any explicitly sourced addition.

All smaller objects in this file—including `DamageOutput`, `LayerResult`, the consequence bundle, simulator views, and owner handoffs—are projections of `CombatExchangeResult`. They are not competing result contracts.

Compact chain:

$$
\begin{aligned}
(A,D,E,\mathcal I)
&\rightarrow
\mathcal B
\rightarrow
\{M_R(d)\}_{d\in\mathcal D_{\mathrm{candidate}}}
\rightarrow
\mathcal D_{\mathrm{modes}}
\rightarrow
(Q,C)
\\
&\rightarrow
I_{\mathrm{eff}}
\rightarrow
\mathcal L_{\mathrm{path}}
\rightarrow
\{P_j,\Delta\mathcal S_{\mathrm{layer},j}\}_{j=1}^{n}
\rightarrow
(P,\mathbf I_{\mathrm{final}})
\rightarrow
\mathrm{CombatExchangeResult}
\end{aligned}
$$

Plain rule:

```text
The exchange engine decides what happened in the clash. It does not own every consequence of what happened.
```

---

### Attack Vector / Defense State

An attack is not only a damage number. It is a shaped attempt to place force, effect, constraint, or meaning into vulnerable space.

A defense is not only resistance. It is the defender's full local response state at the moment the attack resolves: position, guard, movement options, brace, armor, shield, barrier, resource tolerance, recovery, and possible counterpressure. Awareness remains in the perception intake rather than being duplicated inside $D$.

#### Attack Vector

Use:

$$
\begin{aligned}
A
&=
(
p_A,
v_A,
F_A,
\theta_A,
\sigma_A,
\tau_A,
\kappa_A,
\chi_A,
\delta_A
)
\end{aligned}
$$

Where:

| Symbol | Field | Meaning |
|---|---|---|
| $p_A$ | path | spatial route: line, arc, cone, field, pulse, projectile, bind |
| $v_A$ | velocity | arrival speed and directional change |
| $F_A$ | force / intensity | physical force, spell intensity, psychic pressure, domain pressure |
| $\theta_A$ | angle | impact angle, edge alignment, leverage line |
| $\sigma_A$ | precision | targeting accuracy and line discipline |
| $\tau_A$ | timing | arrival moment relative to guard, recovery, and attention |
| $\kappa_A$ | commitment | how much future freedom the attack spends |
| $\chi_A$ | intent | kill, disable, stagger, bind, interrupt, expose, force movement, test |
| $\delta_A$ | expression | physical, magical, psychic, spiritual, identity/namebinding, planar, Domain-based, or hybrid |

Simplified author-facing projection:

$$
\begin{aligned}
A_{\mathrm{author}}
&=
\pi_{\mathrm{author}}(A)
\\
&=
(
\mathrm{Path},
\mathrm{Power},
\mathrm{Speed},
\mathrm{Angle},
\mathrm{Precision},
\mathrm{Timing},
\mathrm{Commitment},
\mathrm{Intent},
\mathrm{Expression}
)
\end{aligned}
$$

Reader-facing shorthand:

```text
An attack is where it goes, how hard it arrives, when it arrives, what it is trying to do, and what kind of power carries it.
```

#### Defense State

Use:

$$
\begin{aligned}
D
&=
(
q_D,
g_D,
m_D,
b_D,
s_D,
\rho_D,
\omega_D,
\zeta_D
)
\end{aligned}
$$

Where:

| Symbol | Field | Meaning |
|---|---|---|
| $q_D$ | position | location, facing, stance, reach, distance, elevation |
| $g_D$ | guard | covered lines, exposed lines, weapon position, shield angle |
| $m_D$ | mobility | available dodge, pivot, retreat, advance, level change |
| $b_D$ | brace | ability to receive force without losing structure |
| $s_D$ | substrate | armor, shield, barrier, body, weapon, terrain, ally |
| $\rho_D$ | resistance | bodily, material, magical, spiritual, identity/metaphysical, or Domain tolerance |
| $\omega_D$ | counterpressure | threat imposed while defending |
| $\zeta_D$ | recovery state | free, committed, stunned, staggered, overextended, resetting |

Simplified author-facing projection:

$$
\begin{aligned}
D_{\mathrm{author}}
&=
\pi_{\mathrm{author}}(D)
\\
&=
(
\mathrm{Position},
\mathrm{Guard},
\mathrm{Mobility},
\mathrm{Brace},
\mathrm{Substrate},
\mathrm{Resistance},
\mathrm{Counterpressure},
\mathrm{Recovery}
)
\end{aligned}
$$

Reader-facing shorthand:

```text
A defense is where the defender is, what protects them, what the perception intake permits them to know, what they can still do, and what they can survive.
```

#### Reachability

Before damage exists, the system determines which outcomes are physically and tactically reachable.

$$
\begin{aligned}
\mathcal{B}
&=
\mathrm{ReachableBranches}
(
A,
D,
E,
\mathcal I
)
\end{aligned}
$$

Where $\mathcal{B}$ is the set of possible branches.

Examples:

$$
\begin{aligned}
\mathcal{B}
&\subseteq
\{
\mathrm{miss},
\mathrm{forcedMiss},
\mathrm{nearMiss},
\mathrm{graze},
\mathrm{partialContact},
\mathrm{cleanContact},
\mathrm{committedCleanContact},
\mathrm{criticalLineContact},
\mathrm{guardContact},
\mathrm{shieldContact},
\mathrm{armorGlance},
\mathrm{armorBite},
\mathrm{barrierContact},
\mathrm{bind},
\mathrm{jam},
\mathrm{interrupted},
\mathrm{overpenetration},
\mathrm{fieldExposure}
\}
\end{aligned}
$$

A branch is reachable only if it has a valid causal path.

$$
\begin{aligned}
b_i \in \mathcal{B}
&\iff
\mathrm{CausallyPossible}
(
b_i
\mid
A,D,T,E,\mathcal I
)
\end{aligned}
$$

Plain rule:

```text
No later formula may select an outcome that was removed from the reachable branch set.
```

---

### Defense Modes

Defense modes describe what the defender is doing, not what material they are using.

The core defense modes are:

```text
Avoidance
Interception
Deflection
Absorption
Resistance
Recovery
Counterpressure
Disruption
```

A single exchange can use several modes in sequence.

```text
Serra avoids the first line, intercepts the second with her forearm, deflects the blade with her hip turn, then counterpressures during the attacker's recovery.
```

#### Defense Mode Values

Each defense mode contributes a response value. The normalized timing-availability input $q_R(d)$ is defined in **Timing, Commitment, and Recovery** and is reused here rather than recomputed by each mode.

Avoidance:

$$
\begin{aligned}
V_{\mathrm{avoid}}
&=
f(
m_D,
q_R(\mathrm{avoidance}),
E_{\mathrm{space}},
\chi_{\mathrm{pathRead}},
q_D
)
\end{aligned}
$$

Interception:

$$
\begin{aligned}
V_{\mathrm{intercept}}
&=
f(
g_D,
q_R(\mathrm{interception}),
v_{\mathrm{intercept}},
\theta_{\mathrm{intercept}},
s_D
)
\end{aligned}
$$

Deflection:

$$
\begin{aligned}
V_{\mathrm{deflect}}
&=
f(
\theta_A,
\theta_D,
\mathrm{skill}_D,
\mathrm{friction},
\mathrm{edgeAlignment},
q_R(\mathrm{deflection})
)
\end{aligned}
$$

Absorption:

$$
\begin{aligned}
V_{\mathrm{absorb}}
&=
f(
s_D,
b_D,
\rho_D,
R_{\mathrm{tolerance}},
\mathrm{forceDistribution},
q_R(\mathrm{absorption})
)
\end{aligned}
$$

Resistance:

$$
\begin{aligned}
V_{\mathrm{resist}}
&=
f(
\rho_D,
\mathrm{bodyIntegrity},
\mathrm{materialStrength},
\mathrm{DomainCompatibility},
\mathrm{statusEffects},
q_R(\mathrm{resistance})
)
\end{aligned}
$$

Recovery:

$$
\begin{aligned}
V_{\mathrm{recover}}
&=
f(
\zeta_D,
\mathrm{balance},
\mathrm{stagger},
\mathrm{fatigue},
\mathrm{injury},
\mathrm{availableSpace}
)
\end{aligned}
$$

Counterpressure:

$$
\begin{aligned}
V_{\mathrm{counter}}
&=
f(
\omega_D,
\kappa_A,
q_R(\mathrm{counterpressure}),
\mathrm{range},
\mathrm{lineAccess}
)
\end{aligned}
$$

Disruption:

$$
\begin{aligned}
V_{\mathrm{disrupt}}
&=
f(
q_R(\mathrm{disruption}),
\mathrm{interference},
\mathrm{concentrationBreak},
\mathrm{limbControl},
\mathrm{terrainShift},
\mathrm{salienceShift}
)
\end{aligned}
$$

Pre-contact defensive response:

$$
\begin{aligned}
V_D^{\mathrm{pre}}
&=
\Phi_D
(
V_{\mathrm{avoid}},
V_{\mathrm{intercept}},
V_{\mathrm{deflect}},
V_{\mathrm{absorb}},
V_{\mathrm{resist}},
V_{\mathrm{counter}},
V_{\mathrm{disrupt}}
)
\end{aligned}
$$

Each pre-contact $V_d\in[0,1]$ is a normalized viability/quality score for that mode under the current state. $\Phi_D$ resolves compatibility and sequence; it is not a sum of the scores. $V_{\mathrm{recover}}$ is evaluated only after contact, avoidance, or displacement has established the new state; it contributes to $\Delta\tau$ and the next reachable branch set rather than retroactively improving the current contact.

The aggregation function $\Phi_D$ should not be a simple sum by default. Defense modes can conflict, sequence, or substitute for each other.

Examples:

An attempted dodge can reduce rather than improve the combined defense when it destroys the brace needed for absorption:

$$
\begin{aligned}
\Phi_D
\bigl(
V_{\mathrm{avoid}},
V_{\mathrm{absorb}};
 b_D=0
\bigr)
&<
\Phi_D
\bigl(
0,
V_{\mathrm{absorb}};
 b_D=1
\bigr)
\end{aligned}
$$

under a state where avoidance breaks the required brace.

By contrast, a compatible sequence may be represented as an ordered composition:

$$
\begin{aligned}
D_{\mathrm{chain}}
&=
\Phi_{\mathrm{counterpressure}}
\circ
\Phi_{\mathrm{deflection}}
\circ
\Phi_{\mathrm{interception}}
(D)
\end{aligned}
$$

when each stage preserves the state required by the next.

Plain rule:

```text
Defense modes are compositional, not merely additive.
```

#### Mode Summaries

| Mode | What it does | Failure case |
|---|---|---|
| Avoidance | prevents contact | space denied, late read, wrong path prediction |
| Interception | places something into the attack path | intercept arrives late or at bad angle |
| Deflection | changes angle/path/effective transfer | force arrives too squarely or too strongly |
| Absorption | accepts contact and spends structure/resource/body integrity | capacity exceeded |
| Resistance | tolerates what gets through | wrong resistance type or threshold exceeded |
| Recovery | returns to useful action after contact/evasion | stagger, crash, or lost balance |
| Counterpressure | makes the attacker defend during commitment | no line, no timing, no credible threat |
| Disruption | prevents attack from expressing cleanly | disruption arrives after resolution |

Plain rule:

```text
Defense Mode = what the defender is doing.
Defensive Layer = what receives, redirects, or absorbs the attack.
```

---

### Timing, Commitment, and Recovery

Timing determines whether a defense is chosen, forced, or impossible.

Commitment determines whether an actor can change course.

Recovery determines who owns the next beat.

Together:

$$
\begin{aligned}
T
&=
(
t_p,
t_d(d),
t_m(d),
t_i,
\kappa_A,
\kappa_D,
r_A,
r_D
)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $t_p$ | perception time |
| $t_d(d)$ | mode-specific decision / selection time |
| $t_m(d)$ | movement / execution time for candidate defense mode $d$ |
| $t_i$ | time until impact or effect resolution |
| $\kappa_A$ | attacker commitment |
| $\kappa_D$ | defender commitment |
| $r_A$ | attacker recovery burden |
| $r_D$ | defender recovery burden |

$t_p$ comes from `perception_information.md`. $t_m(d)$, movement feasibility, balance, footing, traction, reach, and available movement branches come from `motion_positioning.md`. Combat may compare those outputs against $t_i$; it does not invent perception, a dodge, or an interposition branch that an owner marked unavailable or `impossible`.

#### Reaction Margin

Reaction margin is defense-mode specific because a pivot, parry, full-body dodge, barrier trigger, and counterattack do not require the same execution time.

For candidate defense mode $d$:

$$
\begin{aligned}
M_R(d)
&=
t_i
-
\bigl(
t_p
+
t_d(d)
+
t_m(d)
\bigr)
\end{aligned}
$$

Interpretation:

| Condition | Meaning |
|---|---|
| $M_R(d) \ge m_{\min}(d)$ | mode $d$ is cleanly available |
| $0 \le M_R(d) < m_{\min}(d)$ | mode $d$ is reachable only as a partial or degraded execution |
| $M_R(d) < 0$ | active execution of mode $d$ is unavailable |

Available defense modes are constrained by their own margin, physical feasibility, and any standing precoverage. Precoverage is itself restricted to modes already instantiated in the defender's state:

$$
\begin{aligned}
\mathrm{Precovered}(d)
&=
\mathbf 1
\left[
 d\in\mathcal D_{\mathrm{standing}}
 \land
 \mathrm{StandingGeometryCovers}(d,A,D,E)
\right]
\end{aligned}
$$

$$
\begin{aligned}
\mathcal{D}_{\mathrm{modes}}
&=
\left\{
 d\in\mathcal D_{\mathrm{candidate}}
\;\middle|\;
\mathrm{Feasible}(d)
\land
\bigl[
M_R(d)\ge 0
\lor
\mathrm{Precovered}(d)=1
\bigr]
\right\}
\end{aligned}
$$

$\mathcal D_{\mathrm{standing}}$ may contain an already raised guard, shield already covering the line, active ward, braced body resistance, or another defense physically present before the threat is read. It cannot contain a not-yet-executed dodge, recovery, counterattack, or disruption. $m_{\min}(d)\ge 0$ is the margin required for a clean execution. A mode with $0\le M_R(d)<m_{\min}(d)$ remains reachable only as a partial or degraded branch, and its reduced quality must be carried by $V_d$. Recovery is post-contact and is therefore not a member of the pre-impact candidate set.

Where:

$$
\begin{aligned}
\mathcal{D}_{\mathrm{candidate}}
&\subseteq
\{
\mathrm{avoidance},
\mathrm{interception},
\mathrm{deflection},
\mathrm{absorption},
\mathrm{resistance},
\mathrm{counterpressure},
\mathrm{disruption}
\}
\end{aligned}
$$

Timing availability is converted into one normalized input used consistently by every pre-contact mode:

$$
\begin{aligned}
q_R(d)
&=
\begin{cases}
1,
& \mathrm{Precovered}(d)=1,
\\
0,
& M_R(d)<0,
\\
\dfrac{M_R(d)}{m_{\min}(d)},
& 0\le M_R(d)<m_{\min}(d),\quad m_{\min}(d)>0,
\\
1,
& M_R(d)\ge m_{\min}(d).
\end{cases}
\end{aligned}
$$

If $m_{\min}(d)=0$, every nonnegative margin is cleanly available. $q_R(d)$ represents timing availability only; geometry, skill, brace, and substrate quality remain separate inputs to $V_d$.

If $M_R(d)<0$, the defender cannot actively execute mode $d$ and must rely on:

```text
prior positioning,
passive guard,
armor,
shield already in line,
barrier already active,
body resistance,
Luck/Fortune at reachable margins,
or outside intervention.
```

#### Commitment

Commitment measures how much future freedom an action spends.

$$
\begin{aligned}
\kappa
&=
f(
\mathrm{momentum},
\mathrm{stanceLock},
\mathrm{channelLock},
\mathrm{weaponPath},
\mathrm{followthrough},
\mathrm{cancellationCost}
)
\end{aligned}
$$

High-commitment attacks:

- hit harder,
- threaten deeper penetration,
- create more pressure,
- are harder to cancel,
- create more recovery exposure,
- can be punished if read.

Low-commitment attacks:

- probe,
- test,
- threaten without fully spending position,
- recover faster,
- may lack finishing power.

Plain rule:

```text
Commitment buys force, reach, depth, or certainty by spending future options.
```

#### Recovery Debt

Recovery debt determines whether the actor can defend or act again after the current beat.

$$
\begin{aligned}
R_{\mathrm{debt}}
&=
f(
\kappa,
\mathrm{missDistance},
\mathrm{stagger},
\mathrm{resourceExpenditure},
\mathrm{injury},
\mathrm{balanceLoss}
)
\end{aligned}
$$

The result can be:

| Recovery result | Meaning |
|---|---|
| clean recovery | actor remains ready |
| soft recovery | actor can act, but with reduced options |
| delayed recovery | actor loses next beat |
| staggered recovery | actor must regain balance/structure |
| broken recovery | actor is open to follow-up |
| crash recovery | actor suffers resource or body failure |

#### Tempo Change

Tempo is ownership of the next meaningful decision.

$$
\begin{aligned}
\Delta \tau
&=
\Phi_{\mathrm{tempo}}
(
Q,
R_{\mathrm{debt},A},
R_{\mathrm{debt},D},
\Delta x,
K,
O
)
\end{aligned}
$$

Possible tempo outcomes:

```text
attacker retains tempo
defender resets tempo
defender steals tempo
both lose tempo
third party gains tempo
battlefield hazard gains tempo
```

Plain rule:

```text
Commitment buys power or certainty by spending future options.
Recovery decides who owns the next beat.
```

---

### Contact Quality

Contact Quality classifies what kind of physical or metaphysical meeting occurs between attack and defense.

It is determined before damage.

$$
\begin{aligned}
Q
&=
\Phi_Q
\bigl(
\mathrm{pathAlignment}_{\mathrm{resolved}},
\mathrm{rangeQuality}_{\mathrm{resolved}},
\mathrm{leverage}_{\mathrm{resolved}},
\kappa_A,
\mathrm{contactGeometry}_{\mathrm{resolved}}
\bigr)
\end{aligned}
$$

A normalized author-facing value:

$$
\begin{aligned}
Q &\in [0,1]
\end{aligned}
$$

Where:

| Range | Meaning |
|---|---|
| $0.00$ | no meaningful contact |
| $0.10$ | near miss / pressure only |
| $0.25$ | graze |
| $0.40$ | partial contact |
| $0.60$ | meaningful contact |
| $0.80$ | clean contact |
| $0.95$ | critical-line contact |
| $1.00$ | ideal or sustained damaging contact |

Contact class:

$$
\begin{aligned}
C
&=
\mathrm{ClassifyContact}
(
Q,
\mathcal{B},
A,
D,
T,
E
)
\end{aligned}
$$

#### Contact Classes

Use the following provisional contact classes:

| Class | Meaning |
|---|---|
| `miss` | attack does not intersect relevant target space |
| `forced_miss` | defender or ally causes miss through movement, pressure, or displacement |
| `near_miss` | attack nearly intersects; may create fear, pressure, or positioning change |
| `graze` | superficial contact; low force/effect transfer |
| `partial_contact` | meaningful contact with poor alignment, range, or leverage |
| `clean_contact` | attack lands with intended line and adequate structure |
| `committed_clean_contact` | clean hit with high follow-through and attacker commitment |
| `critical_line_contact` | contact reaches structurally or biologically dangerous line |
| `guard_contact` | attack meets active guard, weapon, limb, or prepared line |
| `shield_contact` | attack meets shield substrate |
| `armor_glance` | attack hits armor at poor bite angle |
| `armor_bite` | attack catches armor effectively and tests penetration |
| `barrier_contact` | attack meets active magical, psychic, or domain barrier |
| `bind` | weapons, bodies, powers, or shields lock into contest |
| `jam` | defender prevents attack from fully expressing |
| `interrupted` | attack fails before resolution |
| `overpenetration` | attack passes too far or too cleanly, creating different exposure |
| `field_exposure` | target remains inside damaging or controlling field over time |

Important distinction:

$$
\begin{aligned}
Q \neq H
\end{aligned}
$$

Contact quality is not HP damage. It is how well the attack arrived after movement, defense-mode execution, visibility, timing, guard, and footing have already produced a resolved path and contact geometry. Those upstream factors must not be multiplied into $Q$ again. Layer coverage remains separate and is resolved only after contact classification.

Plain rule:

```text
Damage is calculated after contact quality, not before it.
```

---

### Impact, Penetration, and Damage

Impact determines how much force or effect reaches the defensive layer.

Penetration determines how much gets through.

Damage determines immediate survivability cost.

Injury risk determines specific functional harm.

#### Impact Power

Raw incoming intensity combines intrinsic attack/effect production with the combat-facing motion pressure supplied by `motion_positioning.md`.

$$
\begin{aligned}
I_A
&=
F_A
+
\chi_{\mathrm{motionSeparate}}
\Pi_{\mathrm{impact}}
\bigl(
M_{\mathrm{combatMotion}}
\bigr)
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $F_A$ | intrinsic physical, magical, psychic, spiritual, or other effect intensity before any separately supplied carrier-motion term |
| $M_{\mathrm{combatMotion}}$ | combat-facing motion pressure supplied by `motion_positioning.md`, including its owner-resolved momentum, balance, traction, footing, facing, and terrain effects |
| $\Pi_{\mathrm{impact}}$ | projection from the motion owner's state into the exchange's local effect-equivalent scale |
| $\chi_{\mathrm{motionSeparate}}$ | $1$ only when $F_A$ does not already include the same carrier-motion contribution; otherwise $0$ |

Combat & Defense owns the **impact interpretation** of supplied motion. It does not recalculate velocity, acceleration, momentum, traction, or footing from raw coordinates when `motion_positioning.md` has already resolved them.

The owner-facing surprise diagnostic is:

$$
\begin{aligned}
G_{\mathrm{surprise}}
&=
\Phi_{\mathrm{surprise}}
\bigl(
\mathrm{threatRecognized},
\mathrm{threatConfidence},
t_p,
\mathrm{standingCoverage},
\mathrm{predictionState}
\bigr)
\end{aligned}
$$

The inputs come from `perception_information.md` and the standing defense state. Surprise does not create physical force and therefore does not multiply $I_A$. It may reduce a not-yet-accounted-for preparation, brace, or defense-mode state exactly once. When $t_p$, $M_R(d)$, $b_D$, or $V_d$ already reflects the lost preparation, $G_{\mathrm{surprise}}$ is diagnostic only.

#### Effective Impact

Contact quality scales arrival once. Expression then modifies the effect that reaches the first contacted layer:

$$
\begin{aligned}
I_{\mathrm{eff}}
&=
I_A
Q
M_{\mathrm{expr}}
\end{aligned}
$$

Where $M_{\mathrm{expr}}$ is a **resolved, dimensionless owner projection**, not an ordinal tier label:

$$
\begin{aligned}
M_{\mathrm{expr}}
&=
\Pi_{\mathrm{expression}}
\bigl(
\boldsymbol\mu_{\mathrm{mechanics}},
\mathrm{powerExpression},
\mathrm{technique},
\mathrm{attackContext}
\bigr)
\end{aligned}
$$

For spells, the Spell Skill Mastery bonus from `mechanics.md` applies only to the matching discipline and only to the expression components the spell actually uses. Spell Strength is not converted into one universal damage multiplier. Item Rarity contributes nothing here unless a separately owned item feature supplies an explicit effect. Target-location vulnerability and damage-type conversion enter body consequence later; timing, guard quality, and lost preparation already represented in the resolved defense mode or $Q$ must not be multiplied again here.

A contact-path gate prevents pressure-only near misses from becoming phantom damage:

$$
\begin{aligned}
\chi_{\mathrm{effectPath}}(C)
&=
\mathbf 1
\left[
C\notin
\{
\mathrm{miss},
\mathrm{forced\_miss},
\mathrm{near\_miss}
\}
\right]
\end{aligned}
$$

A declared field exposure or other non-contact carrier uses its own valid path class rather than bypassing this gate.

Examples:

```text
High raw impact + poor contact = reduced effect.
Low raw impact + critical location = serious injury risk.
Moderate impact + perfect timing = high tempo consequence.
```

#### Defense Power

Defense power is the relevant resistance presented against this specific attack, not a permanent defense score:

$$
\begin{aligned}
D_{\mathrm{pow}}
&=
\Phi_{\mathrm{pow}}
\bigl(
g_D,
s_D,
b_D,
\rho_D,
V_{d_{\mathrm{resolved}}},
C,
E,
\mathrm{attackType},
\mathrm{contactAngle},
\mathrm{substrate},
\mathrm{DomainCompatibility},
\boldsymbol\mu_{\mathrm{mechanics},D},
\mathrm{durability}
\bigr)
\end{aligned}
$$

Conditional examples, holding the unlisted state fixed:

$$
\begin{aligned}
D_{\mathrm{pow}}
\bigl(
\mathrm{plate},
\mathrm{slash}
\mid
\pi_{\mathrm{cutResistant}}
\bigr)
&>
D_{\mathrm{pow}}
\bigl(
\mathrm{plate},
\mathrm{heat}
\mid
\pi_{\mathrm{cutResistant}}
\bigr)
\end{aligned}
$$

for a declared plate profile $\pi_{\mathrm{cutResistant}}$ whose cutting resistance exceeds its thermal resistance, and:

$$
\begin{aligned}
D_{\mathrm{pow}}
\bigl(
\mathrm{shield},
\mathrm{cleanBlock}
\mid
\xi
\bigr)
&>
D_{\mathrm{pow}}
\bigl(
\mathrm{shield},
\mathrm{lateTurn}
\mid
\xi
\bigr)
\end{aligned}
$$

for the same shield, attack, brace, and environment state $\xi$.

Plain rule:

```text
Defense power is not a permanent armor number. It is the defense presented against this specific attack. The signature uses explicit attack projections rather than passing both the full attack object and the same extracted features; implementations must not count either representation twice.
```

$\boldsymbol\mu_{\mathrm{mechanics},D}$ is a channel-specific owner projection. A matching Spell Skill Mastery resistance bonus may contribute against the relevant magical discipline. Soul Level may contribute only against identity/metaphysical alteration channels. Item Rarity and class rarity never contribute automatically.

$D_{\mathrm{pow}}$ is a one-layer or classifier-level shorthand. When the full defensive stack is resolved, the implementation uses the path-local $D_{\mathrm{pow},j}$ values and must not apply $D_{\mathrm{pow}}$ again as an extra outer resistance.

#### Penetration / Effect Transfer

Use one guarded threshold function wherever an incoming effect is compared with contextual defense:

$$
\begin{aligned}
r(I,D)
&=
\frac{I}{\max(D,\varepsilon_D)}
\\
\mathsf T_{\alpha}(I,D)
&=
\begin{cases}
0, & I\le 0,
\\
1, & I>0 \land D\le 0,
\\
\dfrac{r(I,D)^{\alpha}}{1+r(I,D)^{\alpha}}, & I>0 \land D>0,
\end{cases}
\qquad
D\ge 0,
\quad
\alpha\ge 1
\end{aligned}
$$

For the contacted portion of layer $j$:

$$
\begin{aligned}
p_j
&=
\mathsf T_{\alpha_j}
\bigl(
I_{j,\mathrm{in}},
D_{\mathrm{pow},j}
\bigr)
\end{aligned}
$$

$p_j$ is the passive contacted-line transfer ratio. $P_j$ is the full **passive** layer-transfer summary after coverage, bypass, deflection, absorption, and source-free channel conversion. An active source term is excluded from $P_j$ and remains explicit in $\mathbf I_{\mathrm{source},j}$ and $\Delta\mathcal S_{\mathrm{layer},j}$. The canonical result field $P$ is the aggregate passive transfer summary after the full defensive path resolves; it is not independently applied before the layer stack.

Because $P$ excludes active source terms, every target-path value $P_k\in[0,1]$ even when a target's final effect vector exceeds its allocated incoming magnitude. In that case the trace must show which layer or power supplied the added effect. The table below uses scalar $P$ as single-target shorthand.

Interpretation:

| $P$ range | Meaning |
|---|---|
| $0.00$–$0.15$ | negligible transfer |
| $0.15$–$0.35$ | partial transfer |
| $0.35$–$0.60$ | meaningful transfer |
| $0.60$–$0.85$ | strong transfer / penetration |
| $0.85$–$1.00$ | overwhelming passive transfer |

Threshold behavior:

- $\alpha=1$ gives the smoothest permitted transition while preserving the capacity bound.
- Higher $\alpha$ gives sharper breakpoints.
- $I=0$ always yields zero transfer, including when $D=0$.
- Positive incoming intensity against zero contextual defense yields full passive transfer.
- $p_j$, $P_j$, and $P$ summarize passive transfer at different scopes; channel conversion remains visible in the layer trace, and active source terms remain separate from those bounded ratios.

Plain rule:

```text
When attack and defense are close, small differences in angle, contact, brace, and timing matter.
When one side overwhelms the other, the result becomes less negotiable.
```

#### HP Damage

HP damage is immediate survivability cost.

$$
\begin{aligned}
\mathbf I_{\mathrm{body},k}
&=
\Pi_{\mathrm{body},k}
\mathbf I_{\mathrm{final},k}
\\
I_{\mathrm{body},k}
&=
\left\|
\mathbf I_{\mathrm{body},k}
\right\|_{w}
\\
H_k
&=
I_{\mathrm{body},k}
M_{\mathrm{damageType},k}
M_{\mathrm{vulnerability},k}
M_{\mathrm{location},k}
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $H_k$ | HP-damage output for target $k$ |
| $\mathbf I_{\mathrm{final},k}$ | channel-resolved effect at the terminal point of target path $k$ |
| $\Pi_{\mathrm{body},k}$ | body projection; returns zero when the path terminates on an object, terrain, or another non-body target |
| $\|\cdot\|_w$ | author-facing weighted magnitude used for survivability conversion |
| $M_{\mathrm{damageType},k}$ | conversion from transferred channels into immediate survivability pressure |
| $M_{\mathrm{vulnerability},k}$ | target condition or susceptibility modifier |
| $M_{\mathrm{location},k}$ | body-location contribution to immediate survivability pressure |

This formula outputs HP damage only. Resource behavior routes outward.

Plain rule:

```text
HP damage says how much immediate combat integrity is lost.
It does not fully define the wound.
```

#### Injury Risk

Injury risk is separate from HP damage.

$$
\begin{aligned}
J_k
&=
\Psi_{\mathrm{injuryRisk}}
\bigl(
\mathbf I_{\mathrm{body},k},
C_k,
Q_k,
\mathrm{targetLocation}_k,
\mathrm{damageExpression}_k,
\mathrm{depthClass}_k,
\mathrm{duration}_k
\bigr)
\end{aligned}
$$

$J_k$ is a structured risk-and-location output for target $k$. It is derived from body-facing effect rather than reusing $H_k$ as an independent multiplier, which prevents target location from being counted twice. It does not calculate wound progression or anatomy; those details route to `embodiment_injury.md`.

Examples:

$$
\begin{aligned}
\mathcal E_{\mathrm{tendon}}
&=
(
\mathrm{HPDamageClass}=\mathrm{low},
\mathrm{InjuryRiskClass}=\mathrm{high}
)
\end{aligned}
$$

is possible for a low-damage tendon cut.

$$
\begin{aligned}
\mathcal E_{\mathrm{distributedBlunt}}
&=
\left(
\mathrm{HPDamageClass}=\mathrm{high},
\mathrm{InjuryRiskClass}\in
\{
\mathrm{low},
\mathrm{moderate}
\}
\right)
\end{aligned}
$$

is possible for a large blunt hit spread across armor.

Plain rule:

```text
HP asks whether the character can keep functioning now.
Injury asks what specific structure was harmed.
```

#### Field / AoE / Exposure Damage

For field effects, use exposure over space and time:

$$
\begin{aligned}
H_{\mathrm{field},k}
&=
\int_{t_0}^{t_1}
\int_{\Omega_k}
I_{\mathrm{body},k}(x,t)
M_{\mathrm{damageType},k}(x,t)
M_{\mathrm{vulnerability},k}(x,t)
\,d\mu_k(x)\,dt
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $I_{\mathrm{body},k}(x,t)$ | target $k$'s local effect intensity after local coverage, resistance, and transfer have resolved |
| $d\mu_k(x)$ | target-specific normalized or explicitly scaled exposure measure appropriate to surface area, volume, occupied region, or another declared geometry |
| $\Omega_k$ | target $k$'s exposed body area, body volume, or occupied space |
| $[t_0,t_1]$ | exposure interval |

For control, exposure, or debuff fields, use a structured load rather than overloading resource-pressure symbol $K$:

$$
\begin{aligned}
\mathbf Y_{\mathrm{field},k}
&=
\int_{t_0}^{t_1}
\int_{\Omega_k}
\mathbf S_k(x,t)
\,\mathrm{Susceptibility}_k(x,t)
\,\mathrm{DurationWeight}_k(t)
\,d\mu_k(x)\,dt
\end{aligned}
$$

$\mathbf S_k(x,t)$ is the owner-supplied local field stimulus vector for target $k$. $\mathbf Y_{\mathrm{field},k}$ may contain control, injury-risk, perception, resource-pressure, or status-load components. Each target-component pair routes to its owner; poison metabolism, injury evolution, and final resource debit are not resolved here.

Plain rule:

```text
Field damage is not one hit. It is consequence accumulated across space and time.
```

#### Resource Cost / Strain Output

Combat & Defense outputs resource cost pressure but does not own the final resource system.

Defensive cost:

$$
\begin{aligned}
K_{\mathrm{def}}
&\equiv
K_{i_D,\mathrm{defense}}
\\
&=
\Phi_K
\bigl(
\mathrm{defenseMode},
I_{\mathrm{eff}},
\Delta\mathcal S_{\mathrm{layer}},
b_D,
s_D,
R,
R_{\mathrm{debt}},
\mathrm{overextension}
\bigr)
\end{aligned}
$$

Possible components:

$$
\begin{aligned}
K_{\mathrm{def}}
&=
\bigl(
K_{i_D,\mathrm{defense},\mathrm{Stamina}},
K_{i_D,\mathrm{defense},\mathrm{Mana}},
K_{i_D,\mathrm{defense},\mathrm{ReservePressure}},
K_{i_D,\mathrm{defense},\mathrm{Concentration}}
\bigr)
\end{aligned}
$$

Combat & Defense may say:

```text
This exchange creates high Stamina pressure, moderate Mana pressure, and Reserve-pressure risk; a separate $\Delta\mathcal S_{\mathrm{layer}}$ output records shield durability damage.
```

Defensive-layer durability and coherence changes route through $\Delta\mathcal S_{\mathrm{layer}}$, not $K_{\mathrm{def}}$. Final resource accounting routes to `resource_system.md`.

#### Position Change

Position change:

$$
\begin{aligned}
\Delta x
&=
\Phi_x
\bigl(
A,
D,
C,
Q,
\Delta\mathcal S_{\mathrm{layer}},
\mathbf I_{\mathrm{final}},
V_D,
E,
R_{\mathrm{debt}}
\bigr)
\end{aligned}
$$

Possible outputs:

```text
no movement
forced step
retreat
knockback
stagger
fall
bind position
line opened
line closed
terrain displacement
formation break
```

Position matters because it changes the next reachable branch set:

$$
\begin{aligned}
\mathcal{B}_{t+1}
&=
\mathrm{ReachableBranches}
(
A_{t+1},
D_{t+1},
E_{t+1},
\mathcal I_{t+1}
)
\end{aligned}
$$

Plain rule:

```text
A low-damage exchange can still be decisive if it changes position.
```

#### Tactical Opening

A tactical opening is a newly available future branch created by the exchange. $O$ records the opening physically created or closed by the exchange. Whether an actor notices, understands, or exploits it belongs to $\Delta\mathcal I$ and `strategy_decision_systems.md`.

$$
\begin{aligned}
O
&=
\Phi_O
\bigl(
\Delta x,
\Delta \tau,
R_{\mathrm{debt},A},
R_{\mathrm{debt},D},
g_D,
\zeta_D,
E
\bigr)
\end{aligned}
$$

Examples:

```text
exposed flank
broken guard
delayed recovery
lost footing
line to protected target
caster concentration broken
shield displaced
ally exposed
escape route opened
attack chain enabled
counterattack line created
```

Plain rule:

```text
An exchange matters if it changes what can happen next.
Damage is only one way to matter.
```

#### Damage Output Object

`DamageOutput` is a convenience projection of `CombatExchangeResult` for body-facing and author-facing use. It is not a second canonical result contract.

A resolved damage event should output:

```text
DamageOutput:
  contactClass
  contactQuality
  effectiveImpact
  finalEffectVector
  defensePowerSummary
  passiveTransferSummary
  hpDamage
  injuryRisk
  damageType
  targetLocation
  forceTransfer
  resourceCost
  armorOrShieldDamage
  positionChange
  tempoChange
  tacticalOpening
  routedOutputs
```

Example:

```text
DamageOutput:
  contactClass: armor_bite
  contactQuality: moderate
  effectiveImpact: high
  finalEffectVector: low piercing + moderate blunt
  defensePowerSummary: high against puncture
  passiveTransferSummary: low_to_moderate
  hpDamage: moderate
  injuryRisk: bruising / cracked-rib risk
  damageType: blunt + piercing pressure
  targetLocation: left ribs
  forceTransfer: high
  resourceCost: stamina brace cost
  armorOrShieldDamage: armor dented / strap stressed
  positionChange: forced step
  tempoChange: defender staggered
  tacticalOpening: attacker follow-up line opens
  routedOutputs:
    - resource_system.md
    - embodiment_injury.md
```

---

### Exchange Resolution Order

Use this order:

```text
1. Define Attack Vector.
2. Define Defense State.
3. Determine reachable branches.
4. Resolve mode-specific reaction margins.
5. Determine available defense modes.
6. Resolve defense-mode and declared team interaction.
7. Classify contact quality and contact class.
8. Calculate raw and effective incoming intensity; apply contact quality once.
9. Identify the ordered defensive-layer path.
10. Partition coverage and resolve each layer's transfer, deflection, absorption, damage, source term, and Lambda output.
11. Aggregate layer transfer into P and determine the final body/target effect vector.
12. Output HP-damage pressure H.
13. Output injury-risk/location object J.
14. Output resource/concentration pressure K.
15. Output position change, tempo change, tactical opening, formation pressure, and information consequence.
16. Build the authoritative CombatExchangeResult.
17. Route downstream consequences.
```

Compact mathematical chain:

$$
\begin{aligned}
(A,D,E,\mathcal I)
&\rightarrow
\mathcal B
\rightarrow
\{M_R(d)\}
\rightarrow
\mathcal D_{\mathrm{modes}}
\rightarrow
(Q,C)
\\
&\rightarrow
I_{\mathrm{eff}}
\rightarrow
\mathcal L_{\mathrm{path}}
\rightarrow
\{P_j,\Delta\mathcal S_{\mathrm{layer},j}\}
\rightarrow
(P,\mathbf I_{\mathrm{final}})
\rightarrow
\mathrm{CombatExchangeResult}
\end{aligned}
$$

Plain rule:

```text
The exchange engine exists to preserve causality.
The reader does not need to see the equations, but the scene must obey them.
```

---

## Defensive Layers

Defensive Layers resolve what receives, redirects, absorbs, or fails under an incoming attack after the Exchange Engine has already determined contact quality, effective impact, timing, and available defense modes.

The core distinction:

```text
Defense Mode = what the defender is doing.
Defensive Layer = what receives, redirects, absorbs, or fails against the attack.
```

A shield can be used for interception, deflection, absorption, bracing, or team coverage. Armor can create a glance, absorb force, fail at a seam, or transfer blunt trauma. A barrier can stop an attack, distort it, bleed through, collapse, or backlash into the resource system.

Defensive Layers answer:

```text
What layer did the attack meet?
Did the layer cover the attacked line?
At what angle did contact occur?
Was the layer braced, anchored, coherent, or already damaged?
Did the layer deflect, absorb, fracture, collapse, or leak?
How much effect transferred inward?
```

The Defensive Layers section uses:

| Field | Defensive Function |
|---|---|
| Geometry | coverage, angle, surface normal, guard line, weak point, seam |
| Materials / structure | hardness, flexibility, density, thickness, durability, fracture |
| Dynamics / mechanics | force transfer, bracing, knockback, torque, deformation |
| Field theory | barrier density, coherence, anchoring, refresh, bleed-through |
| Nonlinear thresholds | shield break, armor puncture, barrier collapse, stagger transfer |
| Compatibility math | damage type vs material, Domain expression vs barrier type |
| Resource routing | Mana/Stamina/Reserve pressure outputs to `resource_system.md` |

Plain rule:

```text
Defensive layers do not make a character safe. They transform one kind of consequence into another.
```

---

### Defensive Layer Stack

An incoming attack may meet multiple layers in order.

Use:

$$
\begin{aligned}
\mathcal{L}_{\mathrm{path}}
&= (\ell_1, \ell_2, \ldots, \ell_n)
\end{aligned}
$$

Where $\mathcal{L}_{\mathrm{path}}$ is the ordered set of defensive layers intersected by the attack path.

Examples:

```text
shield → armor → body
barrier → shield → armor → body
weapon bind → shield edge → exposed arm
outer ward → personal barrier → robe enchantment → body
```

Each layer is:

$$
\begin{aligned}
\ell_j
&= (\mathrm{type}_j, c_j, \theta_j, d_j, u_j, \gamma_j, a_j, \nu_j, k_j^{\mathrm{comp}}, w_j, \phi_j)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $\mathrm{type}_j$ | armor, shield, barrier, weapon, body, terrain, ally interposition |
| $c_j$ | coverage of the attacked line |
| $\theta_j$ | contact angle / surface angle |
| $d_j$ | density, thickness, field density, or structural depth |
| $u_j$ | durability / remaining integrity |
| $\gamma_j$ | coherence / structural unity |
| $a_j$ | anchoring / bracing / stance connection |
| $\nu_j$ | refresh rate / recovery rate for barriers or active fields |
| $k_j^{\mathrm{comp}}$ | compatibility against attack type or Domain expression |
| $w_j$ | weak-point / seam / gap exposure |
| $\phi_j$ | current failure state |

Layer resolution proceeds outward-in.

Initial incoming effect vector:

$$
\begin{aligned}
\mathbf I_0
&=
\chi_{\mathrm{effectPath}}(C)
I_{\mathrm{eff}}
\mathbf e_{\delta_A}
\end{aligned}
$$

$\mathbf e_{\delta_A}$ is the declared channel mixture of the attack expression. A hybrid attack may begin with several nonzero channels.

Coverage partitions the incoming effect before resistance is applied:

$$
\begin{aligned}
\mathbf I_{j,\mathrm{in}}
&=
c_j\mathbf I_j
\\
\mathbf I_{j,\mathrm{bypass}}
&=
(1-c_j)\mathbf I_j
\end{aligned}
$$

For a discrete point or line attack, $c_j$ is the realized coverage indicator or partial-contact fraction. For a distributed effect, it is the covered share of the local exposure measure.

Layer transformation:

$$
\begin{aligned}
\mathbf I_{j+1}
&=
\mathbf I_{j,\mathrm{bypass}}
+
\mathbf T_j
\mathbf I_{j,\mathrm{in}}
+
\mathbf I_{\mathrm{source},j}
\end{aligned}
$$

Where $\mathbf T_j$ is the passive transfer/conversion operator for layer $j$. In the declared effect-accounting norm, a passive layer must be contractive:

$$
\begin{aligned}
\left\|
\mathbf T_j\mathbf v
\right\|_w
&\le
\left\|
\mathbf v
\right\|_w,
\qquad
\mathbf v\succeq\mathbf 0
\end{aligned}
$$

Equivalently, under a nonnegative unit-weight channel ledger its column sums do not exceed $1$. Any active amplification appears only in $\mathbf I_{\mathrm{source},j}$ and must route to its supplying mechanism.

Plain rule:

```text
Coverage decides what reaches the layer. The layer then redirects, absorbs, converts, leaks, fails, or transmits that contacted effect.
```

---

### Layer Coverage

A layer only matters if it covers the relevant line.

Use:

$$
\begin{aligned}
c_j
&=
c_j(x_{\mathrm{contact}},t_{\mathrm{contact}})
\in
[0,1]
\end{aligned}
$$

Where:

| Value | Meaning |
|---|---|
| $0$ | layer does not cover the attacked line |
| $0.25$ | partial / edge / unstable coverage |
| $0.50$ | meaningful but imperfect coverage |
| $0.75$ | strong coverage |
| $1.00$ | full coverage |

Coverage can be reduced by:

```text
bad angle
late guard
exposed seam
broken strap
flanked position
shield displacement
barrier gap
salience misread
terrain restriction
ally obstruction
```

Coverage is resolved by the partition $\mathbf I_{j,\mathrm{in}}=c_j\mathbf I_j$ and must not also multiply layer defense power. Once the contacted share is known, $D_{\mathrm{pow},j}$ describes the resistance of the material or field actually in that line.

Plain rule:

```text
A strong layer that is not in the line does not defend the line, and missing the line is not the same as becoming weaker material.
```

---

### Contact Angle and Bite

Angle determines whether an attack bites, glances, transfers blunt force, or redirects.

Let $\theta_j$ be the angle between the attack vector and the surface normal of the defensive layer.

Use a provisional bite factor:

$$
\begin{aligned}
B_{\mathrm{bite},j}
&= \max(0, \cos \theta_j)^{\beta_j}
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $B_{\mathrm{bite},j}$ | how well the attack bites into the layer |
| $\theta_j$ | contact angle relative to surface normal |
| $\beta_j$ | material / edge / attack-type sensitivity |

Interpretation:

| Contact | Effect |
|---|---|
| direct angle | high bite, high penetration test |
| oblique angle | lower bite, more glance/deflection |
| shallow angle | low bite, high deflection chance |
| unstable angle | unpredictable transfer, higher Luck leverage if marginal |

Geometric deflection availability:

$$
\begin{aligned}
B_{\mathrm{deflect},j}
&= 1 - B_{\mathrm{bite},j}
\end{aligned}
$$

This complement is a geometric availability score, not the final deflected fraction; skill, timing, material response, and the effect-accounting invariant still govern actual deflection. Angle does not erase force. It changes what kind of force transfers.

```text
A shallow sword strike may fail to cut but still stagger.
A spear point may fail to puncture plate but still drive the wearer backward.
A shield turn may stop penetration but transfer torque into the shoulder.
```

Plain rule:

```text
Glancing is not immunity. It is force arriving wrong.
```

---

### Layer Defense Power

Each layer presents contextual defense power against the incoming attack.

Use:

$$
\begin{aligned}
D_{\mathrm{pow},j}
&=
\Phi_{\mathrm{layerPower}}
\bigl(
d_j,
 u_j,
\gamma_j,
a_j,
k_j^{\mathrm{comp}},
\theta_j,
w_j,
\mathrm{attackType}
\bigr)
\end{aligned}
$$

Expanded author-facing form:

$$
\begin{aligned}
D_{\mathrm{pow},j}
&= D_{\mathrm{base},j} M_{\mathrm{angle},j} M_{\mathrm{integrity},j} \\
&\quad{}\times M_{\mathrm{brace},j} M_{\mathrm{compatibility},j} M_{\mathrm{weakPoint},j}
\end{aligned}
$$

Where:

| Modifier | Meaning |
|---|---|
| $D_{\mathrm{base},j}$ | base material or field resistance |
| $M_{\mathrm{angle},j}$ | angle, bite, glance, and surface orientation |
| $M_{\mathrm{integrity},j}$ | remaining durability / coherence |
| $M_{\mathrm{brace},j}$ | stance, anchoring, grip, formation support |
| $M_{\mathrm{compatibility},j}$ | resistance against this attack type or Domain expression |
| $M_{\mathrm{weakPoint},j}$ | seams, gaps, joints, cracks, flickers, flaws |

For manufactured equipment, `mechanics.md` keeps **Item Quality** and **Item Rarity** independent. Quality may affect $D_{\mathrm{base},j}$, integrity, weak-point frequency, reliability, or angle control only through an item/material profile that translates craftsmanship into those properties. Rarity supplies no automatic defense value. A Shoddy or Poor rare item may defend worse than an Average common item when their materials and special features do not reverse that result.

Plain rule:

```text
Defense power is contextual. A layer is strong only against what it is actually good at stopping.
```

---

### Layer Penetration / Leakage

Each contacted layer uses the guarded threshold function from the Exchange Engine. In a scalar, passive, no-conversion reduction:

$$
\begin{aligned}
I_{j,\mathrm{contactOut}}
&=
p_j I_{j,\mathrm{in}}
\\
I_{j+1}
&=
I_{j,\mathrm{bypass}}
+
I_{j,\mathrm{contactOut}}
\\
P_j
&=
\operatorname{clamp}
\left(
\frac{I_{j+1}}{\max(I_j,\varepsilon_I)},
0,
1
\right)
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $p_j$ | transfer ratio through the contacted portion of layer $j$ |
| $P_j$ | total passive layer transfer summary after coverage and bypass |
| $I_{j,\mathrm{in}}$ | intensity that actually contacts the layer |
| $I_{j,\mathrm{bypass}}$ | intensity that misses or bypasses the layer |
| $I_{j+1}$ | inward intensity after the layer |

Armor, shield, and barrier subsections may calculate several intermediate ratios, but each must end by assigning one canonical contacted-line ratio $p_j=p_{\mathrm{layer}}^{\mathrm{resolved}}$. The coverage-inclusive $P_j$ is then computed by the generic recurrence above. Specialized formulas replace the corresponding generic terms; they are not additional multipliers stacked on top of them.

The stopped contacted intensity is:

$$
\begin{aligned}
I_{j,\mathrm{stopped}}
&=
I_{j,\mathrm{in}}
-
I_{j,\mathrm{contactOut}}
\end{aligned}
$$

It must be partitioned among deflection, absorption/dissipation, and layer damage:

$$
\begin{aligned}
\eta_{\mathrm{deflect},j}
+
\eta_{\mathrm{absorb},j}
+
\eta_{\mathrm{layer},j}
&=
1
\\
\eta_{*,j}
&\in
[0,1]
\\
I_{\mathrm{deflected},j}
&=
\eta_{\mathrm{deflect},j} I_{j,\mathrm{stopped}}
\\
I_{\mathrm{absorbed},j}
&=
\eta_{\mathrm{absorb},j} I_{j,\mathrm{stopped}}
\\
I_{\mathrm{layerDamage},j}
&=
\eta_{\mathrm{layer},j} I_{j,\mathrm{stopped}}
\end{aligned}
$$

The vector/operator form governs mixed-channel conversion. The scalar form is only a traceable reduction. Durability and coherence changes such as $\Delta u_j$ or $\Delta\gamma_j$ are state updates caused by the already allocated layer-damage share; they are not additional effect sinks.

Plain rule:

```text
Stopping an attack means the layer paid for the stop somewhere, and the trace must say where.
```

---

### Armor

Armor is passive or semi-active protection worn on the body. It modifies contact, penetration, force transfer, injury risk, and sometimes movement.

Armor state:

$$
\begin{aligned}
A_m
&= (m, d, c, f, u, \theta, w, k^{\mathrm{comp}}, \mu)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $m$ | material: leather, mail, plate, scale, chitin, enchanted fiber, etc. |
| $d$ | thickness / density / layered depth |
| $c$ | coverage |
| $f$ | fit / articulation |
| $u$ | durability |
| $\theta$ | contact angle |
| $w$ | weak points: seams, joints, straps, gaps, prior damage |
| $k^{\mathrm{comp}}$ | compatibility against attack type |
| $\mu$ | mobility penalty / encumbrance |

Armor defense power excludes an additional angle multiplier because the armor-specific bite term below already resolves contact angle:

$$
\begin{aligned}
D_{\mathrm{armor}}
&= D_{\mathrm{material}} M_{\mathrm{thickness}} \\
&\quad{}\times M_{\mathrm{fit}} M_{\mathrm{durability}} M_{\mathrm{compatibility}} M_{\mathrm{weakPoint}}
\end{aligned}
$$

Armor can resolve into:

```text
armor_glance
armor_catch
armor_bite
armor_dent
armor_puncture
armor_shear
armor_crack
armor_gap_hit
blunt_transfer
strap_failure
joint_failure
```

#### Armor Glance

Armor glance occurs when coverage exists but bite is poor. $B_{\mathrm{deflect}}$ already contains the declared angle dependence; $\Phi_{\mathrm{glance}}$ may use only residual contact geometry not already encoded there.

$$
\begin{aligned}
G_{\mathrm{glance}}
&=
\Phi_{\mathrm{glance}}
\bigl(
B_{\mathrm{deflect}},
\mathrm{surfaceHardness},
\mathrm{edgeAlignment},
\mathrm{contactGeometry}_{\mathrm{residual}}
\bigr),
\qquad
G_{\mathrm{glance}}\in[0,1]
\end{aligned}
$$

A glance may produce:

```text
low penetration
low cut depth
moderate blunt transfer
position shift
spark / scrape / noise
tempo change
attacker recovery change
```

Plain rule:

```text
Armor glance prevents the intended wound. It may still create force transfer or tempo cost.
```

#### Armor Bite

Armor bite occurs when the attack catches the armor effectively enough to test penetration.

$$
\begin{aligned}
I_{\mathrm{armor,in}}
&=
c I_j
\\
\mathrm{BitePressure}
&=
I_{\mathrm{armor,in}}
B_{\mathrm{bite}}
\end{aligned}
$$

The contacted-line puncture transfer is:

$$
\begin{aligned}
p_{\mathrm{armor}}^{\mathrm{puncture}}
&=
\mathsf T_{\alpha_{\mathrm{armor}}}
\bigl(
\mathrm{BitePressure},
D_{\mathrm{armor}}
\bigr)
\end{aligned}
$$

A clean puncture branch exists when:

$$
\begin{aligned}
p_{\mathrm{armor}}^{\mathrm{puncture}}
&\ge
\Theta_{\mathrm{puncture}}
\end{aligned}
$$

Armor bite may produce:

```text
puncture
tear
dent
crack
split
partial cut-through
seam failure
body trauma under intact armor
```

#### Blunt Transfer Through Armor

Armor can stop penetration while still transferring force.

$$
\begin{aligned}
I_{\mathrm{puncture}}
&=
\mathrm{BitePressure}
p_{\mathrm{armor}}^{\mathrm{puncture}}
\\
I_{\mathrm{nonpuncture}}
&=
I_{\mathrm{armor,in}}
-
I_{\mathrm{puncture}}
\\
I_{\mathrm{bluntTransfer}}
&=
\eta_{\mathrm{bodyCoupling}}
I_{\mathrm{nonpuncture}},
\qquad
0\le \eta_{\mathrm{bodyCoupling}}\le 1
\\
I_{\mathrm{stopped,armor}}
&=
I_{\mathrm{nonpuncture}}
-
I_{\mathrm{bluntTransfer}}
\\
I_{\mathrm{armor,out}}
&=
I_{\mathrm{puncture}}
+
I_{\mathrm{bluntTransfer}}
\\
p_{\mathrm{armor}}^{\mathrm{resolved}}
&=
\frac{I_{\mathrm{armor,out}}}
{\max(I_{\mathrm{armor,in}},\varepsilon_I)}
\end{aligned}
$$

$p_{\mathrm{armor}}^{\mathrm{puncture}}$ is the fraction of the bite-tested load that punctures, while $p_{\mathrm{armor}}^{\mathrm{resolved}}$ is the total body-facing transfer after puncture and blunt coupling are combined. In mixed-channel use, puncture and blunt transfer remain separate vector channels rather than being physically added as identical effects.

$I_{\mathrm{stopped,armor}}$ must be assigned to armor deformation/damage, deflection, or dissipation. Brace failure may increase $\eta_{\mathrm{bodyCoupling}}$, but transferred blunt effect cannot exceed $I_{\mathrm{nonpuncture}}$ without an explicit source. Because puncture is computed from $\mathrm{BitePressure}$, the puncture output cannot exceed the load actually tested against armor resistance.

This may route to:

```text
HP damage
stagger
breath loss
bruising
rib risk
joint strain
Stamina cost
position change
```

Plain rule:

```text
No hole does not mean no damage.
```

#### Armor Durability Damage

Armor durability changes after meaningful contact.

$$
\begin{aligned}
\Delta u_{\mathrm{armor}}
&=
-f\bigl(
\lVert \mathbf I_{\mathrm{layerDamage,armor}}\rVert_w,
B_{\mathrm{bite}},
\mathrm{damageType},
\mathrm{materialFatigue}
\bigr)
\end{aligned}
$$

The durability debit is derived from the layer-damage share already closed by the effect ledger. It must not consume the incoming effect a second time.

Armor state after contact:

$$
\begin{aligned}
u_{\mathrm{armor},t+1}
&=
\operatorname{clamp}
\bigl(
u_{\mathrm{armor},t} + \Delta u_{\mathrm{armor}},
0,
u_{\mathrm{armor},\max}
\bigr)
\end{aligned}
$$

As durability falls:

```text
coverage worsens
weak points widen
angle performance degrades
fit may fail
future penetration becomes easier
noise / visible damage may reveal vulnerability
```

---

### Shields

A shield is an active defensive substrate. Its strength depends on material, angle, coverage, grip, bracing, timing, and the defender's body behind it.

Shield state:

$$
\begin{aligned}
S_h
&= (m, c, \theta, b, u, s, g, a, \omega)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $m$ | material / construction |
| $c$ | coverage |
| $\theta$ | shield angle |
| $b$ | brace quality |
| $u$ | durability |
| $s$ | stability / balance |
| $g$ | grip / strap integrity |
| $a$ | anchoring through stance, terrain, ally support |
| $\omega$ | counterpressure enabled by shield position |

Shield defense power:

$$
\begin{aligned}
D_{\mathrm{shield}}^{\mathrm{base}}
&= D_{\mathrm{material}} M_{\mathrm{brace}} \\
&\quad{}\times M_{\mathrm{stability}} M_{\mathrm{durability}} M_{\mathrm{grip}}
\\
D_{\mathrm{shield}}^{\mathrm{block}}
&=
D_{\mathrm{shield}}^{\mathrm{base}}
M_{\mathrm{blockAngle}}
\end{aligned}
$$

$M_{\mathrm{blockAngle}}$ is used only for a receiving block. A shield turn uses $B_{\mathrm{deflect}}$ for geometric angle and then tests residual effect against $D_{\mathrm{shield}}^{\mathrm{base}}$; it does not receive a second angle multiplier. $M_{\mathrm{turnExecution}}$ is the resolved turn-execution quality not already consumed by contact classification; if the implementation has already folded that quality into the resolved contact geometry, set it to $1$.

A shield can resolve into:

```text
clean_block
late_block
shield_turn
shield_catch
shield_bind
shield_bash
shield_splinter
shield_displace
shield_break
shield_bypass
```

#### Shield Block

A shield block receives the attack.

$$
\begin{aligned}
I_{\mathrm{shieldIn}}
&=
c I_j
\\
p_{\mathrm{shield}}^{\mathrm{block}}
&=
\mathsf T_{\alpha_{\mathrm{shield}}}
\bigl(
I_{\mathrm{shieldIn}},
D_{\mathrm{shield}}^{\mathrm{block}}
\bigr)
\end{aligned}
$$

The uncovered share $(1-c)I_j$ bypasses the shield rather than disappearing. A successful-block classification requires the transmitted share to stay below the declared block threshold:

$$
\begin{aligned}
p_{\mathrm{shield}}^{\mathrm{block}}
&\le
\Theta_{\mathrm{block}}
\end{aligned}
$$

But even a successful block can create:

```text
Stamina cost
shoulder strain
forced step
lost tempo
reduced visibility
shield displacement
follow-up vulnerability
```

#### Shield Turn / Deflection

A shield turn changes angle and path.

$$
\begin{aligned}
\eta_{\mathrm{deflect,shield}}
&=
\operatorname{clamp}
\bigl(
B_{\mathrm{deflect}}
M_{\mathrm{skill}}
M_{\mathrm{turnExecution}},
0,
1
\bigr)
\\
I_{\mathrm{deflected}}
&=
\eta_{\mathrm{deflect,shield}}
I_{\mathrm{shieldIn}}
\\
I_{\mathrm{residual,shield}}
&=
I_{\mathrm{shieldIn}}
-
I_{\mathrm{deflected}}
\\
p_{\mathrm{shield}}^{\mathrm{turn}}
&=
\mathsf T_{\alpha_{\mathrm{shield}}}
\bigl(
I_{\mathrm{residual,shield}},
D_{\mathrm{shield}}^{\mathrm{base}}
\bigr)
\\
I_{\mathrm{transmitted}}
&=
p_{\mathrm{shield}}^{\mathrm{turn}}
I_{\mathrm{residual,shield}}
\end{aligned}
$$

The remaining residual is absorbed, dissipated, or assigned to shield damage under the effect-accounting invariant. The resolved shield transfer ratio is mode-specific:

$$
\begin{aligned}
p_{\mathrm{shield}}^{\mathrm{resolved}}
&=
\begin{cases}
p_{\mathrm{shield}}^{\mathrm{block}},
& d_{\mathrm{resolved}}=\mathrm{block},
\\
\dfrac{I_{\mathrm{transmitted}}}
{\max(I_{\mathrm{shieldIn}},\varepsilon_I)},
& d_{\mathrm{resolved}}=\mathrm{turn},
\\
\Phi_{\mathrm{shieldMode}}(d_{\mathrm{resolved}}),
& \text{for another explicitly defined shield mode.}
\end{cases}
\end{aligned}
$$

Every additional shield mode must close the same accounting ledger; `bind` is not permitted to hide unassigned effect merely because contact persists.

Shield turn is stronger when:

```text
reaction margin is positive,
angle is correct,
brace is sufficient,
the defender is not overcommitted,
and the attack has a deflectable path.
```

Plain rule:

```text
A shield block says no. A shield turn says no, and points the attack somewhere worse.
```

#### Shield Bind

A shield bind occurs when shield and attack remain in contact and create a contest of leverage.

$$
\begin{aligned}
B_{\mathrm{bind}}
&= f(I_{\mathrm{shieldIn}}, b, s, \kappa_A, \kappa_D, \omega_D, \mathrm{footing})
\end{aligned}
$$

A bind may create:

```text
stalemate
attacker weapon trapped
defender shield pinned
opening to flank
ally follow-up line
forced strength contest
tempo freeze
```

#### Shield Displacement

Shield displacement matters even without penetration.

$$
\begin{aligned}
\Delta q_{\mathrm{shield}}
&= f(I_{\mathrm{shieldIn}}, b, s, g, a, \theta)
\end{aligned}
$$

If displacement opens a protected line:

$$
\begin{aligned}
O_{\mathrm{line}}
&= \mathrm{OpenLine}(g_D, \Delta q_{\mathrm{shield}})
\end{aligned}
$$

Plain rule:

```text
A shield can stop the first attack and still lose the second by moving out of position.
```

#### Shield Damage

$$
\begin{aligned}
\Delta u_{\mathrm{shield}}
&= -f\bigl(
\lVert \mathbf I_{\mathrm{layerDamage,shield}}\rVert_w,
\mathrm{damageType},
\mathrm{material},
\mathrm{edgeAlignment},
\mathrm{repeatedLoad}
\bigr)
\\
u_{\mathrm{shield},t+1}
&=
\operatorname{clamp}
\bigl(
u_{\mathrm{shield},t}+\Delta u_{\mathrm{shield}},
0,
u_{\mathrm{shield},\max}
\bigr)
\end{aligned}
$$

The durability debit is derived from the shield's allocated layer-damage share. It is a state update, not a second transfer or absorption term.

Shield failure states:

```text
scratched
dented
cracked
splintered
strap torn
rim broken
center punched
handle damaged
fully broken
```

---

### Barriers

A barrier is an active or semi-active field that resists, redirects, filters, absorbs, or delays incoming effects.

Barrier state:

$$
\begin{aligned}
B_r
&= (\rho_b, \gamma_b, a_b, \nu_b, u_b, c_b, k_b^{\mathrm{comp}}, \psi_b, \epsilon_b)
\end{aligned}
$$

Where:

| Symbol | Meaning |
|---|---|
| $\rho_b$ | barrier density / field strength |
| $\gamma_b$ | coherence / structural order |
| $a_b$ | anchoring: caster, object, terrain, formation, ritual point |
| $\nu_b$ | refresh rate / regeneration rate on the declared effect-load scale |
| $u_b$ | remaining integrity as expendable effect-load capacity |
| $c_b$ | coverage / shape |
| $k_b^{\mathrm{comp}}$ | compatibility against attack type or Domain expression |
| $\psi_b$ | permeability / filtering behavior |
| $\epsilon_b$ | backlash / instability risk |

Barrier defense power:

$$
\begin{aligned}
D_{\mathrm{barrier}}
&=
D_{\mathrm{barrier,base}}
M_{\mathrm{density}}
M_{\mathrm{coherence}}
M_{\mathrm{anchoring}}
M_{\mathrm{compatibility}}
\end{aligned}
$$

$D_{\mathrm{barrier,base}}$ and its modifiers may consume a spell-expression projection supplied from `mechanics.md` and `power_expression.md`. Spell Strength may improve whichever barrier properties the technique actually expresses—such as density, coverage, stability, duration, control, or resistance to disruption. The matching Spell Skill Mastery bonus may contribute to those declared properties and to relevant resistance. Neither tier is converted into a universal barrier multiplier inside this file.

Coverage $c_b$ partitions the incoming line before this resistance is applied. Coherence $\gamma_b$ shapes baseline resistance; remaining integrity $u_b$ supplies the expendable interval capacity below. Refresh rate $\nu_b$ restores that capacity over time. The same $u_b$ is therefore not also multiplied into instantaneous defense power.

A barrier can resolve into:

```text
clean_stop
partial_stop
bleed_through
distortion
redirection
absorption
overload
flicker
collapse
backlash
resonant_failure
```

#### Barrier Coverage and Shape

Barrier coverage depends on geometry and field stability.

$$
\begin{aligned}
c_b(x,t)
&= \mathrm{CoverageField}(x,t)
\end{aligned}
$$

Barrier shape matters:

| Shape | Strength | Weakness |
|---|---|---|
| flat wall | strong from front | flanking, anchoring stress |
| dome | broad coverage | high resource cost |
| skin barrier | full-body close protection | direct transfer/backlash risk |
| directional ward | efficient | limited angle |
| layered ward | redundancy | slower refresh |
| lattice barrier | filters specific effects | vulnerable to incompatible patterns |

Plain rule:

```text
A barrier is geometry plus coherence plus resource pressure.
```

#### Barrier Penetration / Bleed-Through

Barrier resolution uses one interval load ledger. Permeability is partitioned first; resistance and refreshed integrity then act once on the blockable share.

For a declared interval $\Delta t>0$:

$$
\begin{aligned}
L_{\mathrm{barrier,in}}
&=
c_b I_j\Delta t
\\
L_{\mathrm{permeable}}
&=
\psi_b L_{\mathrm{barrier,in}}
\\
L_{\mathrm{blockable}}
&=
(1-\psi_b)L_{\mathrm{barrier,in}},
\qquad
0\le\psi_b\le1
\\
\widehat u_b
&=
\min
\bigl(
\nu_b\Delta t+u_{b,t},u_{b,\max}
\bigr)
\\
C_{\mathrm{base}}
&=
D_{\mathrm{barrier}}\Delta t
\\
C_{\mathrm{stop}}
&=
C_{\mathrm{base}}+\widehat u_b
\end{aligned}
$$

$\psi_b$ is the fraction intentionally or structurally permitted through the barrier. In mixed-channel use it is a bounded channel operator, and the two partitions must still sum to the incoming effect vector. $C_{\mathrm{stop}}$ is a diagnostic total capacity; the sequential ledger below applies its baseline and integrity components exactly once.

The blockable share is stopped by baseline capacity first, then by refreshed integrity. Any remainder is transmitted as overcapacity load:

$$
\begin{aligned}
L_{\mathrm{baseStopped}}
&=
\min
\bigl(
L_{\mathrm{blockable}},
C_{\mathrm{base}}
\bigr)
\\
L_{\mathrm{afterBase}}
&=
L_{\mathrm{blockable}}-L_{\mathrm{baseStopped}}
\\
L_{\mathrm{integrity}}
&=
\min
\bigl(
L_{\mathrm{afterBase}},
\widehat u_b
\bigr)
\\
L_{\mathrm{blockable,out}}
&=
\max
\bigl(
0,
L_{\mathrm{afterBase}}-\widehat u_b
\bigr)
\\
O_{\mathrm{barrier}}
&=
L_{\mathrm{blockable,out}}
\\
&=
\bigl[ L_{\mathrm{blockable}}-C_{\mathrm{stop}} \bigr]_+
\\
u_{b,t+1}
&=
\operatorname{clamp}
\bigl(
\widehat u_b-L_{\mathrm{integrity}},
0,
u_{b,\max}
\bigr)
\end{aligned}
$$

The inward output is the direct permeable share plus the capacity-resolved blockable share:

$$
\begin{aligned}
L_{\mathrm{barrier,out}}
&=
L_{\mathrm{permeable}}
+
L_{\mathrm{blockable,out}}
\\
I_{\mathrm{bleed}}
&=
\frac{L_{\mathrm{barrier,out}}}{\Delta t}
\\
p_{\mathrm{barrier}}^{\mathrm{resolved}}
&=
\frac{L_{\mathrm{barrier,out}}}
{\max(L_{\mathrm{barrier,in}},\varepsilon_L)},
\qquad
\varepsilon_L=\varepsilon_I\Delta t
\end{aligned}
$$

This construction credits $D_{\mathrm{barrier}}\Delta t$, refresh $\nu_b\Delta t$, and current integrity $u_{b,t}$ exactly once. $L_{\mathrm{baseStopped}}$ becomes baseline absorption or dissipation; $L_{\mathrm{integrity}}$ becomes the barrier's allocated layer damage; and $L_{\mathrm{barrier,out}}$ becomes inward transfer. $O_{\mathrm{barrier}}$ is exactly the transmitted blockable overcapacity share, not an additional effect term.

Examples:

```text
kinetic force blocked, heat passes through the permeability partition
fire blocked, smoke/pressure leaks through
psychic pressure muted, fear trace remains
spatial shear stopped, nausea/backlash transfers through an explicit source route
poison cloud delayed, vapor seep continues
```

#### Barrier Overload

Barrier overload is the state in which blockable interval load exceeds the one credited stop capacity:

```text
O_barrier > 0
```

It may produce integrity loss, Mana-pressure candidates, caster stun, flicker, coverage loss, collapse, backlash, or Reserve-pressure risk. Any backlash that adds a new effect vector must appear in the active-source ledger and route to the mechanism that supplied it. Resource accounting remains in `resource_system.md`.

#### Barrier Compatibility

Barrier compatibility determines whether the barrier is resisting the right kind of effect.

$$
\begin{aligned}
M_{\mathrm{compatibility}}
&= \mathrm{Compat}(\delta_A, \delta_B)
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $\delta_A$ | attack expression / damage type / Domain expression |
| $\delta_B$ | barrier expression / defensive pattern |

Possible outcomes:

```text
resistant
neutral
poorly matched
resonant absorption
resonant instability
bypass
conversion
amplification risk
```

Plain rule:

```text
A strong barrier of the wrong kind can fail worse than a weaker barrier of the right kind.
```

#### Barrier Collapse

Barrier collapse is a threshold event. $\Theta_{\mathrm{collapse}}$ is expressed on the same interval-load scale as $O_{\mathrm{barrier}}$.

$$
\begin{aligned}
\mathrm{Collapse}
&= \begin{cases}
1, & u_{b,t+1} \leq 0 \ \text{or} \ O_{\mathrm{barrier}} \ge \Theta_{\mathrm{collapse}} \\
0, & \text{otherwise}
\end{cases}
\end{aligned}
$$

Collapse may produce:

```text
clean failure
explosive failure
delayed failure
partial opening
localized rupture
caster backlash
resource crash pressure
formation exposure
```

Plain rule:

```text
Barrier failure is not always a hole. Sometimes it is a debt.
```

#### Specialized-to-Generic Transfer Map

The specialized layer equations feed the generic stack through one contacted-line transfer ratio:

$$
\begin{aligned}
p_j
&=
\begin{cases}
p_{\mathrm{armor}}^{\mathrm{resolved}},
& \mathrm{type}_j=\mathrm{armor},
\\
p_{\mathrm{shield}}^{\mathrm{resolved}},
& \mathrm{type}_j=\mathrm{shield},
\\
p_{\mathrm{barrier}}^{\mathrm{resolved}},
& \mathrm{type}_j=\mathrm{barrier},
\\
\mathsf T_{\alpha_j}
\bigl(I_{j,\mathrm{in}},D_{\mathrm{pow},j}\bigr),
& \text{otherwise.}
\end{cases}
\end{aligned}
$$

After this assignment, the generic coverage recurrence computes $P_j$. No specialized ratio is multiplied by the generic threshold a second time.

---

### Layer Interaction

Layers can sequence, interfere, reinforce, or undermine each other.

#### Sequential Defense

For a passive, scalar, source-free stack with each $P_j$ already including coverage and bypass:

$$
\begin{aligned}
I_n
&=
I_0
\prod_{j=1}^{n}
P_j
\\
P
&=
\frac{I_n}{\max(I_0,\varepsilon_I)}
\end{aligned}
$$

Mixed-channel conversion or active source terms must use the operator recurrence $\mathbf I_{j+1}=\mathbf I_{j,\mathrm{bypass}}+\mathbf T_j\mathbf I_{j,\mathrm{in}}+\mathbf I_{\mathrm{source},j}$ instead of the product shorthand.

For the canonical bounded passive aggregate, run the same layer path with source terms set to zero:

$$
\begin{aligned}
\mathbf I_{j,\mathrm{in}}^{(0)}
&=
c_j\mathbf I_j^{(0)}
\\
\mathbf I_{j,\mathrm{bypass}}^{(0)}
&=
(1-c_j)\mathbf I_j^{(0)}
\\
\mathbf I_{j+1}^{(0)}
&=
\mathbf I_{j,\mathrm{bypass}}^{(0)}
+
\mathbf T_j
\mathbf I_{j,\mathrm{in}}^{(0)}
\\
P
&=
\operatorname{clamp}
\left(
\frac{
\left\|
\mathbf I_n^{(0)}
\right\|_w
}{
\max
\bigl(
\left\|
\mathbf I_0
\right\|_w,
\varepsilon_I
\bigr)
},
0,
1
\right)
\end{aligned}
$$

The full recurrence with $\mathbf I_{\mathrm{source},j}$ produces $\mathbf I_{\mathrm{final}}$. The source-free recurrence produces $P$. This prevents an active counterpulse, reflected spell, or powered amplification from being mislabeled as passive penetration above $1$.

This is the cleanest case:

```text
barrier reduces blast
shield catches remaining force
armor spreads impact
body receives reduced trauma
```

#### Reinforcing Defense

Layers reinforce when one improves the effectiveness of another.

Examples:

```text
shield angle improves armor angle
barrier slows projectile before shield contact
armor lets defender brace more confidently
ally pressure gives defender time to set shield
terrain brace increases shield stability
```

Math form:

$$
\begin{aligned}
D_{\mathrm{pow},j}^{\mathrm{reinforced}}
&=
D_{\mathrm{pow},j}^{\mathrm{base}}
\bigl(
1+M_{\mathrm{reinforce},j}
\bigr),
\qquad
M_{\mathrm{reinforce},j}\ge 0
\end{aligned}
$$

#### Interfering Defense

Layers interfere when one worsens another.

Examples:

```text
dodging ruins brace
shield blocks visibility
heavy armor reduces mobility
barrier curvature redirects force into ally
armor gap aligns with shield displacement
panic movement opens a covered line
```

Math form:

$$
\begin{aligned}
D_{\mathrm{pow},j}^{\mathrm{effective}}
&=
D_{\mathrm{pow},j}^{\mathrm{reinforced}}
\bigl(
1-M_{\mathrm{interference},j}
\bigr),
\qquad
0\le M_{\mathrm{interference},j}\le 1
\end{aligned}
$$

Plain rule:

```text
More layers do not automatically mean better defense.
```

---

### Defensive Layer Result Object

`LayerResult` is a defensive-layer projection of `CombatExchangeResult`, centered on $P_j$, $K$, and $\Delta\mathcal S_{\mathrm{layer},j}$. It does not replace the exchange-level result contract.

For layer $j$, the minimum state-change record is:

$$
\begin{aligned}
\Delta\mathcal S_{\mathrm{layer},j}
&=
\bigl(
\Delta u_j,
\Delta\gamma_j,
\phi_{j,t+1},
\mathbf I_{\mathrm{deflected},j},
\mathbf I_{\mathrm{absorbed},j},
\mathbf I_{\mathrm{layerDamage},j},
\mathbf I_{\mathrm{source},j}
\bigr)
\end{aligned}
$$

A layer may omit inapplicable fields only by recording them as zero or `not_applicable`; it may not leave effect unaccounted for.

A resolved defensive layer event should output:

```text
LayerResult:
  layerType
  incomingEffectVector
  coverage
  contactedEffectVector
  bypassEffectVector
  contactAngle
  defensePower
  passiveTransferSummary
  outgoingEffectVector
  absorbedIntensity
  transmittedIntensity
  deflectedIntensity
  durabilityDamage
  resourcePressure
  positionChange
  tempoChange
  failureState
  tacticalOpening
  routedOutputs
```

Example:

```text
LayerResult:
  layerType: shield
  incomingEffectVector: high kinetic + cutting
  coverage: strong
  contactedEffectVector: most incoming effect
  bypassEffectVector: low
  contactAngle: oblique
  defensePower: high
  passiveTransferSummary: low
  outgoingEffectVector: low_to_moderate kinetic
  absorbedIntensity: moderate
  transmittedIntensity: low_to_moderate
  deflectedIntensity: high
  durabilityDamage: rim cracked
  resourcePressure: high Stamina brace cost
  positionChange: forced half-step
  tempoChange: defender resets tempo
  failureState: damaged_not_failed
  tacticalOpening: attacker weapon line displaced
  routedOutputs:
    - resource_system.md
```

---

### Defensive Layer Resolution Order

Use this order after the Exchange Engine has resolved contact and effective impact:

```text
1. Identify defensive layers intersecting the attack path.
2. Order layers from outside to inside.
3. Check coverage for each layer.
4. Check contact angle and bite/glance behavior.
5. Calculate contextual defense power for the active layer.
6. Resolve penetration, leakage, deflection, or absorption.
7. Apply durability/coherence damage to the layer.
8. Transform transmitted intensity for the next layer.
9. Repeat until attack is stopped, redirected, dissipated, or reaches body/resource/metaphysical target.
10. Output layer result.
11. Route resource, injury, perception, or tactical consequences to owner files.
```

Compact chain:

$$
\begin{aligned}
\mathbf I_0
&=
\chi_{\mathrm{effectPath}}(C)
I_{\mathrm{eff}}\mathbf e_{\delta_A}
\rightarrow
\mathcal L_{\mathrm{path}}
\rightarrow
\bigl(D_{\mathrm{pow},1},P_1,\Delta\mathcal S_{\mathrm{layer},1}\bigr)
\rightarrow
\mathbf I_1
\\
&\rightarrow
\bigl(D_{\mathrm{pow},2},P_2,\Delta\mathcal S_{\mathrm{layer},2}\bigr)
\rightarrow
\mathbf I_2
\rightarrow
\cdots
\rightarrow
\mathbf I_{\mathrm{final}}
\end{aligned}
$$

Plain rule:

```text
Defensive layers convert incoming consequence into transmitted force, layer damage, resource pressure, position change, tempo change, or tactical opening.
```

---


## Cost and Consequence

### Section Contract

This section consolidates the outputs already produced by the Exchange Engine and Defensive Layers and routes them to the correct owner files.

This section owns:

* combat-facing consequence classification,
* the causal trace linking contact to consequence,
* output packaging,
* owner-file routing,
* consistency checks between the exchange result and downstream handoffs.

This section does **not** own:

* final HP, Mana, Stamina, or Reserve debit,
* wound anatomy or injury progression,
* full displacement, fall, or terrain resolution,
* new perception or inference,
* the team's next decision,
* interface formatting.

Plain rule:

```text
Combat & Defense states what the exchange produced.
The receiving owner files determine how that output changes their state.
```

---

### Resource Costs of Defense

Defense does not erase consequence. It changes which consequence is paid and where it lands.

The Exchange Engine owns the combat-facing defensive-pressure projection $K_{\mathrm{def}}\equiv K_{i_D,\mathrm{defense}}$, where $i_D$ is the defending actor. This section does not redefine that formula. It consolidates its routed meaning:

$$
\begin{aligned}
K_{\mathrm{defenseRoute}}
&=
\mathrm{Project}_{\mathrm{resource}}
(
K_{\mathrm{def}},
\Delta\mathcal S_{\mathrm{layer}},
\Delta \tau,
\mathrm{forcedContinuation}
)
\end{aligned}
$$

Possible routed components include:

```text
Stamina pressure from bracing, dodging, holding structure, or forced recovery
Mana pressure from shields, wards, barriers, or active reinforcement
Reserve-pressure risk from forced continuation after ordinary capacity is insufficient
concentration pressure from maintaining control under impact
recovery debt that changes the next exchange
separate $\Delta\mathcal S_{\mathrm{layer}}$ state changes when a defensive layer loses durability or coherence
```

Combat & Defense determines which defensive act occurred, what pressure it generated, and whether the act remained physically and tactically valid. `resource_system.md` determines affordability, final debits, buffering, depletion, crash states, regeneration consequences, and hard stops.

Combat consumes the resource owner's returned branch constraints:

```text
0 Stamina  → hard physical stop / collapse; active exertion branches close
0 Reserve  → hard overuse/interface stop; further borrowing branches close
0 Mana     → mana crash and casting loss; consciousness is not automatically removed
```

These are received state constraints, not locally recalculated depletion rules.

When a signature ability carries rarity-linked energy burden, the cost projection must arrive through `xp_progression_formulas.md` / the power owner and be adjudicated by `resource_system.md`. Combat may record that the ability was attempted and what pressure it created; it must not derive energy cost directly from a class-rarity label.

Plain rule:

```text
Combat decides the price created by the defense.
Resources decides how that price is paid.
```

---

### HP Damage vs Injury Output

HP damage and injury risk are related projections of the same exchange, not interchangeable labels.

$$
\begin{aligned}
(
H,
J
)
&=
\mathrm{Project}_{\mathrm{bodyConsequence}}
(
C,
Q,
P,
\mathbf I_{\mathrm{final}},
\mathrm{targetLocation},
\mathrm{damageExpression}
)
\end{aligned}
$$

Combat & Defense owns the causal inputs and the immediate outputs:

```text
HP-damage output
injury-risk class
target location
penetration-depth class
force direction
transmitted intensity
critical-structure risk
```

`resource_system.md` owns final HP accounting. `embodiment_injury.md` owns the specific wound, anatomy, progression, impairment, bleeding, contamination, healing course, and long-term bodily evolution.

Valid combinations include:

```text
high HP damage + low specific injury risk
low HP damage + high functional injury risk
low HP damage + high tactical consequence
stopped penetration + high layer/resource pressure
```

Plain rule:

```text
HP reports immediate survivability pressure.
Injury reports what specific function may have been harmed.
```

---

### Consequence Bundle

The consequence bundle is a projection of the authoritative `CombatExchangeResult`:

$$
\begin{aligned}
\mathbf Y_{\mathrm{consequence}}
&=
\pi_{\mathrm{consequence}}
(
\mathrm{CombatExchangeResult}
)
\\
&=
(
H,
J,
K,
\Delta\mathcal S_{\mathrm{layer}},
\Delta x,
\Delta \tau,
O,
F_{\mathrm{pressure}},
\Delta\mathcal I,
\mathcal{R}_{\mathrm{route}}
)
\end{aligned}
$$

Where:

| Symbol | Meaning | Final owner |
|---|---|---|
| $H$ | target-keyed HP-damage output; scalar for one target | `resource_system.md` |
| $J$ | injury-risk and location output | `embodiment_injury.md` |
| $K$ | resource and concentration pressure created by the exchange | `resource_system.md` or the relevant concentration owner |
| $\Delta\mathcal S_{\mathrm{layer}}$ | defensive-layer durability/coherence change, leakage, collapse, or failure output | Combat & Defense for combat-layer state; other owners when material state is affected |
| $\Delta x$ | local displacement request | `motion_positioning.md` |
| $\Delta \tau$ | tempo, recovery, and initiative change | Combat & Defense locally; strategy consumes tactical meaning |
| $O$ | tactical opening created or closed | `strategy_decision_systems.md` |
| $F_{\mathrm{pressure}}$ | formation pressure created by the exchange | `strategy_decision_systems.md` |
| $\Delta\mathcal I$ | evidence or information consequence produced by the physical event | `perception_information.md` |
| $\mathcal{R}_{\mathrm{route}}$ | explicit routing from each consequence to its owner | all receiving owner files |

The bundle is not one scalar damage score. A low-$H$ exchange can produce high $O$, high $\Delta x$, high $J$, or high $F_{\mathrm{pressure}}$. A stopped attack can still create high $K$ or $\Delta\mathcal S_{\mathrm{layer}}$.

---

### Combat Result Routing

#### Routing Order

Use this routing order after the local exchange is resolved:

```text
1. Freeze the contact, penetration, and defensive-layer trace.
2. Package HP-damage output and resource-pressure output.
3. Package target location, damage expression, and injury-risk output.
4. Package displacement, force direction, balance disruption, and recovery burden.
5. Package new visible evidence, broken concealment, and interface-read candidates.
6. Package tempo, protection-edge, formation-pressure, and tactical-opening outputs.
7. Package the canonical Luck trace and active-cost evaluation request when Luck participated.
8. Package raw combat-adaptation facts for the scene-level XP owner; do not calculate XP.
9. Send each package to its owner file.
10. Reconcile returned owner states before resolving the next exchange.
```

Owner files may refine their own state. They must not silently rewrite the already-resolved contact path. If a downstream owner proves the handoff impossible, return the exchange for a contract-level correction. State-changing returns from resources, injury, motion, perception, and strategy may constrain the next exchange; progression aggregation may wait until scene close and recovery integration.

---

### Resource / Injury Ownership Boundaries

The following handoffs are projections selected by $\mathcal{R}_{\mathrm{route}}$. They carry resolved combat outputs into the resource and embodiment owners without finalizing either subsystem locally.

#### Resource-System Handoff

```text
CombatResourceHandoff:
  hpDamageCandidateByTarget
  staminaPressureByActor
  manaPressureByActor
  reservePressureByActor
  concentrationPressureByActor
  defensiveLayerResourcePressure
  crashRisk
  affordabilityFlagUsed
  depletionStateAtExchange
  forcedAction
  forcedContinuationReason
  staminaDeficitCandidate
  manaDeficitCandidate
  routedFrom: combat_defense.md
  routedTo: resource_system.md
```

Combat & Defense may classify pressure as qualitative or author-facing quantitative input. `resource_system.md` owns the actual debit, buffering, crash state, regeneration consequences, and hard stop.

The canonical Reserve rule remains external: Combat & Defense must not invent a separate Reserve debit or strain formula. `resource_system.md` decides whether Mana or Stamina is below the canonical $20\%$ threshold, whether an action is forced, whether Reserve buffering activates, and whether the canonical $1$ Reserve to $5$ Mana/Stamina-deficit conversion applies. Combat supplies pressure and deficit candidates only.

Combat consumes the returned `ResourceCombatOutput` fields—current HP, Mana, Stamina, and Reserve state; depletion/crash state; forced-action permission; regeneration suppression; and combat-capacity change—when constructing the next exchange's reachable branches.

---

#### Embodiment / Injury Handoff

```text
CombatInjuryHandoff:
  targetLocation
  contactClass
  contactQuality
  damageExpression
  effectiveImpact
  penetrationDepthClass
  forceDirection
  transmittedIntensity
  injuryRiskClass
  criticalStructureRisk
  contaminationOrOngoingExposureHint
  routedTo: embodiment_injury.md
```

Combat & Defense may say that an exchange creates tendon risk, organ-threatening depth, concussion risk, crush risk, burn depth risk, or metaphysical-structure risk.

It must not finalize:

```text
specific organ failure
wound progression
bleeding timeline
infection
poison metabolism
healing time
permanent impairment
```

---

### Motion / Positioning Handoff

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

`motion_positioning.md` returns the physically resolved `MotionCombatOutput`: updated position, updated facing, balance, footing, traction, reach envelope, $M_{\mathrm{combatMotion}}$, mode-specific execution times $t_m(d)$, feasibility by candidate mode, fall risk, recovery burden, and available movement branches.

Combat & Defense must not assume a knockback distance, successful landing, recovered footing, or available follow-up movement before that return.

---

### Perception / Information Handoff

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

The physical event may expose evidence. `perception_information.md` decides who can notice, parse, infer, remember, or misread it.

Plain rule:

```text
Exposure is not recognition.
Evidence is not interpretation.
```

---

### Strategy / Decision Handoff

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

`strategy_decision_systems.md` values the opening, updates the tactical graph, and chooses the next action. Combat & Defense does not spend the opening automatically.

---

### Luck / Fortune Handoff

Combat uses one canonical Luck/Fortune update from `luck_fortune.md`. It may record which local coordinates were unresolved and how the canonical adapter affected the exchange, but it does not calculate active entropy cost or resource backlash locally.

```text
CombatLuckHandoff:
  favoredSide
  localPossibilityState
  baselineReachableSet
  unresolvedCombatCoordinates
  fortuneOrMisfortuneDriftApplied
  volatilityDiffusionApplied
  activeControlUsed
  activeControlTrace
  entropyCostEvaluationRequired
  finalReachableState
  combatResultClassifier
  routedFrom: combat_defense.md
  routedTo: luck_fortune.md
  costConsequencesRoutedTo: resource_system.md
```

Fortune and Misfortune alter drift. Volatility alters diffusion. Active control cost, KL distortion, probability debt, recoil, and metaphysical consequences remain owned by `luck_fortune.md`; any actual HP, Mana, Stamina, or Reserve consequence remains owned by `resource_system.md`.

---

### Progression Evidence Handoff

Combat & Defense does not award XP. It emits a raw, per-exchange adaptation trace that `xp_progression_formulas.md` may aggregate across a scene after resource, injury, recovery, novelty, and class-method context are known.

$$
\begin{aligned}
\mathcal T_{\mathrm{adaptation}}
&=
\pi_{\mathrm{adaptation}}
\bigl(
\mathrm{CombatExchangeResult},
\mathrm{exchangeTrace}
\bigr)
\end{aligned}
$$

```text
CombatAdaptationTrace:
  exchangeId
  beatDuration
  actorRoles
  threatExposureFacts
  simultaneousThreatCount
  surpriseOrAttentionSplitFacts
  resolvedContactClass
  resolvedContactQuality
  hpDamageOutputCandidate
  injuryRiskSignal
  forcedExertionFacts
  resourcePressureByActorAndChannel
  interfaceOrAethericInstabilityFacts
  tacticalPatternFacts
  protectionOrIdentityChoiceFacts
  classMethodActionTags
  outcomeContributionTrace
  recoveryDebtCreated
  ownerStatesRequiredBeforeXP:
    resource_system
    embodiment_injury
    perception_information
  routedFrom: combat_defense.md
  routedTo: xp_progression_formulas.md
```

This trace contains facts and tags, not:

```text
adaptive evidence E_C
organismic adaptive load Lambda(t)
hormetic window H_j
class coupling K_C
recovery integration Theta_e
scene XP
level thresholds
class-rarity XP burden
```

Those quantities are owned by `xp_progression_formulas.md`. The XP owner may combine this trace with final HP/resource changes, injury severity, neural load, novelty history, identity stakes, class-method coupling, and post-scene recovery. A combat result does not become fully integrated XP merely because the exchange ended.

Current precedence rule:

```text
Use xp_progression_formulas.md for XP thresholds, rarity burden, combat adaptation, and scene XP.
Consume XP thresholds and rarity burden only from `xp_progression_formulas.md`; `resource_system.md` and `mechanics.md` now provide ownership pointers rather than competing XP formulas.
```

For XP metadata only, the current rarity sequence is:

```text
Common → Uncommon → Rare → Epic → Fabled → Legendary → Mythic → Unique
```

`Exceptional` remains valid on unrelated Mechanics ladders where `mechanics.md` defines it, but it is retired as the current XP-table rarity name. Combat never converts between those ladders.

---

### Interface-Display Candidate Handoff

```text
CombatDisplayCandidates:
  hpChangeCandidate
  resourceWarningCandidate
  armorFailureCandidate
  barrierInstabilityCandidate
  statusHintCandidate
  threatDirectionCandidate
  recoveryWarningCandidate
  confidence
  unknownFields
  routedTo: system_message_rules.md
```

These are candidates only. `system_message_rules.md` decides whether the interface renders them, how precise they appear, and whether the scene is better without a notification.

---

### Consequence Consistency Checks

Before advancing to the next exchange, verify:

* the HP-damage output follows from contact and transmitted effect,
* the injury-risk output follows from location, expression, and penetration,
* the resource-pressure output follows from an action, defense, layer, or forced continuation that actually occurred,
* the displacement request follows the force direction and contact geometry,
* the tactical opening follows a changed line, tempo, guard, layer, position, or information state,
* the interface candidate does not claim more precision than the available evidence supports,
* Fortune/Misfortune changed only reachable drift and Volatility changed only diffusion,
* no ordinal tier label was used directly as a scalar,
* Item Rarity and class rarity supplied no automatic combat multiplier,
* Soul Level affected only a causally relevant identity/metaphysical channel,
* the progression handoff contains raw facts rather than locally calculated XP,
* no handoff finalizes a foreign subsystem locally.

Plain rule:

```text
Every consequence must point backward to a resolved cause and forward to an explicit owner.
```

---

## Information and Team Systems

### Section Contract

This section owns the **combat-facing use** of information and declared team actions after their owner files have produced them.

This section owns:

* whether supplied awareness permits an active defense in this exchange,
* how a supplied misread changes defense selection,
* how a declared feint changes the defender's modeled threat,
* how a supplied local tactical graph constrains exchange branches,
* whether a declared interposition physically succeeds,
* how local protection lines alter contact, position, tempo, and tactical openings,
* formation pressure created by resolved exchanges.

This section consumes but does **not** own:

* threat recognition, senses, Insight, stealth, illusion, salience, and inference from `perception_information.md`,
* movement feasibility, reach, balance, footing, and displacement from `motion_positioning.md`,
* team-defense planning, formation logic, target priority, risk, and tactical graph evaluation from `strategy_decision_systems.md`,
* resource affordability and final expenditure from `resource_system.md`,
* class, Interface, and Domain behavior from their owner files,
* Spell Strength, Spell Skill Mastery, Soul Level, Item Quality, and Item Rarity projections from `mechanics.md`.

Plain rule:

```text
Perception decides what the combatant knows or believes.
Strategy decides what the team intends to do.
Combat & Defense decides what those inputs permit in the local clash.
```

---

### Perception, Feints, and Interface Reads

#### Combat Information Intake

`perception_information.md` supplies a combat-facing information state. Combat & Defense may consume a reduced intake:

$$
\begin{aligned}
\mathcal I_{\mathrm{combat}}
&=
\bigl(
\chi_{\mathrm{recognized}},
c_{\mathrm{threat}},
\chi_{\mathrm{read}},
\hat p_A,
\hat \tau_A,
\hat \chi_A,
\hat\delta_A,
\Sigma_I,
r_{\mathrm{misread}},
\mathcal I_{\mathrm{Insight}},
\epsilon_I,
t_p,
\mathcal E_{\mathrm{available}}
\bigr)
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $\chi_{\mathrm{recognized}}$ | whether the threat was recognized, predicted, continuously tracked, or remained unrecognized |
| $c_{\mathrm{threat}}$ | threat-confidence output supplied by `perception_information.md` |
| $\chi_{\mathrm{read}}$ | clean, partial, late, false-line, false-timing, false-intent, or corrupted read |
| $\hat p_A$ | perceived attack path |
| $\hat \tau_A$ | optional perceived arrival timing when the perception owner can support it |
| $\hat \chi_A$ | perceived attack intent |
| $\hat\delta_A$ | perceived damage type / expression hint |
| $\Sigma_I$ | salience state |
| $r_{\mathrm{misread}}$ | misread risk |
| $\mathcal I_{\mathrm{Insight}}$ | partial Insight output, never omniscient truth |
| $\epsilon_I$ | residual uncertainty supplied by the perception owner |
| $t_p$ | perception time supplied to the existing reaction-margin model |
| $\mathcal E_{\mathrm{available}}$ | evidence actually available to the combatant |

The required owner fields are `perceptionTime`, `threatRecognized`, `threatConfidence`, `perceivedAttackPath`, `perceivedIntent`, `perceivedDamageType`, `salienceState`, `misreadRisk`, `insightRead`, and `uncertainty`. `perceivedTiming` and `availableEvidence` are optional refinements; Combat may not synthesize them when the owner did not.

Combat & Defense converts this intake into **response permission**, not new perception truth:

$$
\begin{aligned}
\chi_{\mathrm{response}}
&=
\Phi_{\mathrm{responsePermission}}
(
\mathcal I_{\mathrm{combat}},
\{M_R(d)\},
g_D,
\mathcal B,
\mathrm{precoverage}
)
\end{aligned}
$$

Possible outputs:

```text
no_active_response
prediction_only
precovered_response
late_active_response
partial_active_response
full_active_response
```

A combatant may defend without a clean read when the threatened line is already covered, a standing ward is active, an ally interposes, or trained prediction created a viable branch before recognition completed.

A combatant may not select a precise active defense against information they did not perceive, predict, or pre-cover.

---

#### Feints

A feint attacks the defender's **model of the exchange**. It does not retroactively change physical reality.

`perception_information.md` owns whether the feint is noticed or misread. Combat & Defense consumes the supplied read and resolves the consequence of defending the wrong line, timing, or intent.

$$
\begin{aligned}
D_{\mathrm{selected}}
&=
\Phi_{\mathrm{defenseSelection}}
(
D_{\mathrm{available}},
\hat p_A,
\hat \tau_A,
\hat \chi_A,
\chi_{\mathrm{read}},
\mathrm{training},
\mathrm{commitment}
)
\end{aligned}
$$

The selected defense may differ from the best defense against the true attack:

$$
\begin{aligned}
\Delta D_{\mathrm{feint}}
&=
\mathrm{Mismatch}
(
D_{\mathrm{selected}},
D_{\mathrm{bestAgainstTrueAttack}}
)
\end{aligned}
$$

$\Delta D_{\mathrm{feint}}$ is an author-facing mismatch classification, not a universal numeric distance or vector subtraction.

Possible combat consequences:

```text
guard raised on wrong line
parry arrives early
parry arrives late
retreat opens intended lane
brace commits against false impact
counterattack fires into bait
attention leaves protected target
real attack gains angle
feint is ignored and attacker overcommits
```

Plain rule:

```text
A successful feint makes the defender spend a real response on the wrong problem.
It does not make an impossible attack become possible by declaration.
```

---

#### Interface Reads

The Interface is a translation layer. It may render underlying combat state as labels, percentages, warnings, classifications, or incomplete symbols, but the readout is not the underlying event.

Use the boundary model:

$$
\begin{aligned}
\mathcal R_{\mathrm{interface}}
&=
\mathcal T_{\mathrm{interface}}
(
X_{\mathrm{underlying}},
\mathcal E_{\mathrm{available}},
\mathcal C_{\mathrm{classifier}},
\epsilon_{\mathrm{translation}}
)
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $X_{\mathrm{underlying}}$ | the real biological, physical, magical, spiritual, or Domain state |
| $\mathcal E_{\mathrm{available}}$ | signals the Interface can actually detect |
| $\mathcal C_{\mathrm{classifier}}$ | the Interface's current category model |
| $\epsilon_{\mathrm{translation}}$ | uncertainty, omission, compression, or bias in translation |
| $\mathcal R_{\mathrm{interface}}$ | the rendered combat-facing read |

Combat & Defense may consume a read such as:

```text
threat_direction
barrier_instability
armor_breach_risk
resource_strain_warning
injury_risk_hint
status_hint
unknown_classification
confidence_low
```

Combat & Defense must not treat the rendered category as omniscient fact.

Examples:

* `Barrier integrity: critical` may mean coherence is near collapse; it does not mean reality contains a literal critical-integrity meter.
* `Threat: lethal` is a classifier judgment; it does not guarantee death.
* `Unknown` means the translation layer cannot resolve the state, not that the state lacks structure.
* A correct read can still be interpreted incorrectly by the character.

Formatting, source tags, panel shape, notification frequency, and prose integration route to `system_message_rules.md`.

---

### Tactical Graph / Team Defense

#### Owner Boundary

`strategy_decision_systems.md` owns the tactical graph, team-defense plan, target priority, protection priority, formation logic, risk evaluation, and decision to interpose.

Combat & Defense receives only the local slice needed for the current exchange:

$$
\begin{aligned}
G_{\mathrm{local}}
&=
\mathrm{Slice}_{\mathrm{exchange}}
(
G_{\mathrm{tac}},
A,
D,
\Delta t_{\mathrm{exchange}}
)
\end{aligned}
$$

A local slice may contain:

```text
current attacker
current target
protecting allies
reachable interposers
threat lines
protection lines
suppression lines
cover edges
escape or extraction edge
formation adjacency
objective-critical edge
```

Combat & Defense does not decide whether the team *should* protect the target. It resolves whether the declared protection physically works now.

---

#### Local Team-Defense Resolution

A declared team defense modifies the attacker's reachable branches before or during contact.

$$
\begin{aligned}
D_{\mathrm{team,local}}
&=
\Phi_{\mathrm{localTeamDefense}}
(
G_{\mathrm{local}},
\mathcal P,
\mathcal A_{\mathrm{interpose}},
\mathcal S,
\{M_R(d)\},
E,
\mathcal I_{\mathrm{combat}}
)
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| $\mathcal P$ | active protection lines such as shield wall, guard coverage, ward overlap, or body coverage |
| $\mathcal A_{\mathrm{interpose}}$ | declared interposition attempts |
| $\mathcal S$ | suppression and counterpressure that worsen the attacker's branch |
| $\{M_R(d)\}$ | mode-specific reaction margins |
| $E$ | local environment and obstruction state |

Possible local effects:

```text
attack redirected to interposer
attack angle worsened
clean hit reduced to partial contact
protected target gains retreat branch
attacker forced to abort
attacker accepts counterattack exposure
shield or barrier receives shared load
formation line holds
formation line bends
formation line breaks
```

Team defense is not additive armor. It changes geometry, timing, target access, and consequence routing.

---

### Team Protection, Formation Pressure, and Interposition

#### Protection Lines

A protection line exists when an ally, shield, barrier, weapon threat, body position, or controlled zone creates a real obstacle between attacker and protected target.

$$
\begin{aligned}
\mathcal P_{ij}
&=
\mathrm{ProtectionEdge}
(
q_i,
q_j,
\mathrm{reach}_i,
\mathrm{coverage}_i,
\mathrm{timing}_i,
\mathrm{threat}_i,
E
)
\end{aligned}
$$

A protection edge may be:

```text
hard: physically blocks or intercepts the path
soft: makes the path costly through counterattack or suppression
conditional: exists only if timing, facing, or resource support holds
broken: no longer protects the intended line
```

The protection edge belongs to the tactical graph. Combat & Defense only resolves its current exchange effect.

---

#### Interposition

Interposition is a target-substitution event caused by real movement, coverage, or projected defense.

An interposition attempt is valid only when the interposer has a causal path to the attack line:

$$
\begin{aligned}
\chi_{\mathrm{interpose}}
&=
\mathbf 1
\left[
\mathrm{Reachable}
\land
\mathrm{Timely}
\land
\mathrm{LineIntersected}
\land
\mathrm{DefenseAvailable}
\right]
\end{aligned}
$$

If valid, resolve:

$$
\begin{aligned}
\eta_{\mathrm{interpose}}
&=
\chi_{\mathrm{interpose}}
\operatorname{clamp}
\bigl(
\Phi_{\mathrm{lineCapture}}
(
q_R(d_{\mathrm{interpose}}),
\mathrm{coverage},
\mathrm{contactGeometry}
),
0,
1
\bigr)
\\
\mathcal T_{\mathrm{resolved}}
&=
\left\{
\bigl(\mathrm{Target}_{\mathrm{original}},1-\eta_{\mathrm{interpose}}\bigr),
\bigl(\mathrm{Interposer},\eta_{\mathrm{interpose}}\bigr)
\right\}
\end{aligned}
$$

Possible results:

```text
full substitution
partial substitution
shared contact
attack deflected off protected target
interposer arrives late
interposer creates collision
interposer opens a second vulnerability
interposition fails
```

Interposition does not erase the attack. It changes who or what receives it, how the angle changes, and what new opening appears. The weights in $\mathcal T_{\mathrm{resolved}}$ sum to $1$. $\eta_{\mathrm{interpose}}=1$ is full substitution, $0<\eta_{\mathrm{interpose}}<1$ is partial or shared contact, and $\eta_{\mathrm{interpose}}=0$ leaves the original target unchanged.

For a divisible carrier or distributed exposure, allocation conserves pre-contact incoming intensity:

$$
\begin{aligned}
I_{A,\mathrm{original}}
&=
(1-\eta_{\mathrm{interpose}})I_A
\\
I_{A,\mathrm{interposer}}
&=
\eta_{\mathrm{interpose}}I_A
\\
I_{A,\mathrm{original}}
+
I_{A,\mathrm{interposer}}
&=
I_A
\end{aligned}
$$

Every nonzero share then receives its own recomputed attack vector, $Q_k$, contact geometry, and layer path. A nondivisible rigid carrier must resolve as full substitution or ordered sequential contact rather than cloning one full-strength strike across two targets.

A successful interposition may still be tactically bad if it exposes the interposer, breaks formation, sacrifices a critical resource, or creates a more valuable follow-up for the attacker. Strategy owns that valuation.

---

#### Formation Pressure

Combat & Defense may output **formation pressure** when local exchanges degrade team spacing, coverage, timing, trust-dependent execution, or protection edges.

$$
\begin{aligned}
F_{\mathrm{pressure}}
&=
\Phi_{\mathrm{formationPressure}}
(
\Delta x,
\Delta \tau,
\mathrm{coverageLoss},
\mathrm{forcedFacing},
\mathrm{suppression},
\mathrm{allyCollision},
\mathrm{brokenProtectionEdges}
)
\end{aligned}
$$

Possible outputs:

```text
none
localized strain
line bent
coverage gap
support isolated
frontline split
retreat lane blocked
formation broken
```

Combat & Defense reports the local pressure. `strategy_decision_systems.md` updates the full graph, evaluates objective impact, and decides the next team action.

---

### Information and Team Handoffs

Perception input:

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

Strategy input:

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

Combat output to perception:

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

Combat output to strategy:

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

Plain rule:

```text
Combat reports the changed battlefield.
Perception and strategy decide what actors know and what they do next.
```

---

## Luck/Fortune Adapter

### Section Contract

This section is a **local adapter** to the canonical model in `luck_fortune.md`.

Combat & Defense owns here:

* the exchange-local possibility coordinates,
* the deterministic reachable set produced by the exchange,
* which combat-derived quantities must be recomputed after one canonical Luck update,
* the combat result classifier,
* the trace sent back to the Luck/Fortune owner.

Combat & Defense does **not** own:

* the coherent possibility-amplitude layer,
* the Fokker–Planck / stochastic differential equation,
* Fortune or Misfortune drift law,
* Volatility diffusion law,
* active Luck optimization,
* KL / entropy-distortion cost,
* fate debt, recoil, backlash, or probability compensation.

Plain rule:

```text
Combat defines the local uncertainty.
luck_fortune.md defines how probability moves through it.
```

---

### Canonical Reference and Typed Intake

The local Luck input is structured:

$$
\begin{aligned}
\mathcal L_{\mathrm{combat}}
&=
\bigl(
\mathrm{mode},
s,
\mathrm{reachabilityGate},
\mathrm{favorabilityDefinition},
\mathrm{driftInput},
\mathrm{diffusionInput},
\mathrm{activeControlFlag}
\bigr)
\end{aligned}
$$

Where:

| Field | Meaning |
|---|---|
| `mode` | Fortune, Misfortune, Volatility, mixed field, curse, or no active bias |
| $s$ | side, actor, protected target, or object whose favorability is being evaluated |
| `reachabilityGate` | canonical plausibility gate supplied or validated through `luck_fortune.md` |
| `favorabilityDefinition` | whose outcome counts as favorable and why |
| `driftInput` | canonical Fortune/Misfortune contribution |
| `diffusionInput` | canonical Volatility contribution |
| `activeControlFlag` | whether deliberate probability steering occurred and therefore requires owner-side cost evaluation |

$\mathcal L_{\mathrm{combat}}$ is not a resource, not a normal visible attribute, and not a flat roll modifier.

---

### Local Possibility State

Combat-local uncertainty is restricted to coordinates that the exchange itself owns:

$$
\begin{aligned}
\mathbf z_{\mathrm{combat}}
&=
\bigl(
\mathrm{aimPathResidual},
\mathrm{timingResidual},
\mathrm{guardAngleResidual},
\mathrm{contactGeometryResidual},
\mathrm{layerAngleResidual},
\mathrm{deflectionPathResidual},
\mathrm{penetrationMargin},
\mathrm{battlefieldInterference}
\bigr)
\end{aligned}
$$

These are residual uncertainties **after** owner files have supplied their states. Combat does not absorb another subsystem merely because one of its outputs affects the exchange.

Owner-routed uncertainty remains external:

| Uncertainty | Owner |
|---|---|
| footing, traction, fall, landing, collision, movement recovery | `motion_positioning.md` |
| evidence emergence, threat recognition, salience, Insight, misread | `perception_information.md` |
| Mana/Stamina/Reserve crash basin and recovery complication | `resource_system.md` |
| wound path, organ proximity, bleeding, functional injury evolution | `embodiment_injury.md` |
| plan-branch alignment, coordination failure, opponent adaptation | `strategy_decision_systems.md` |

Combat may consume the returned owner state and continue the exchange. It must not run those adapters internally.

---

### Baseline Drift and Uncertainty

Without Luck, combat-local trajectories follow the resolved attack, defense, timing, motion, information, environment, layer, and condition states:

$$
\begin{aligned}
\mathbf b_{\mathrm{combat}}
&=
\Phi_{\mathrm{combatBaseline}}
\bigl(
A,
D,
T,
M_{\mathrm{combatMotion}},
\mathcal I_{\mathrm{combat}},
E,
\mathcal L_{\mathrm{path}}
\bigr)
\end{aligned}
$$

Uncertainty may remain in:

```text
marginal aim or path error
near-threshold reaction timing
guard or surface angle
glance vs. bite
deflection direction
penetration threshold
chaotic third-party interference
```

The canonical stochastic evolution, noise process, diffusion tensor, and jump law remain in `luck_fortune.md`. This file does not restate them.

---

### Favorability Function

Favorability is side-specific:

$$
\begin{aligned}
U_{\mathrm{combat}}^{(s)}
\bigl(
\mathbf z,t
\bigr)
&=
\mathrm{FavorabilityForSide}
\bigl(
s,
\mathbf z,
t
\bigr)
\end{aligned}
$$

Examples:

* for a defender, a graze may be more favorable than deep penetration;
* for an attacker, a clean opening may be more favorable than a shield bind;
* for a protected ally, successful interposition may be favorable even when it is costly to the interposer;
* for a formation, holding the line may be favorable even when one actor takes more local damage.

The side or object measured must be declared. “Favorable” is not universal.

---

### Reachability Constraint

The combat adapter inherits the canonical support restriction:

$$
\begin{aligned}
\operatorname{supp}
\bigl(
p_{L,\mathrm{combat}}
\bigr)
&\subseteq
\operatorname{Reach}_{\mathrm{combat}}
\left(
\operatorname{supp}
\bigl(
p_{0,\mathrm{combat}}
\bigr)
\right)
\end{aligned}
$$

Plain rule:

```text
Luck may redistribute probability among reachable combat branches.
It may not create a path, actor, defense, resource, or survival branch that does not exist.
```

---

### Luck Interaction

The mode distinction is binding:

```text
Fortune    → favorable drift through reachable combat state
Misfortune → harmful drift through reachable combat state
Volatility → increased diffusion / spread / tail risk
```

Fortune does not “reduce variance” by definition. Misfortune does not add damage directly. Volatility does not choose which side benefits.

Active Luck is controlled probability steering, not outcome selection. If active control participates, Combat records the trace and requests cost evaluation from `luck_fortune.md`; it does not calculate entropy debt or debit a resource locally.

---

### Combat-Local Hook Coordinates

The following are coordinate views of **one** canonical Luck update. They are not sequential bonuses.

#### 1. Aim and Path Residual

Luck may bias a physically plausible residual path when multiple nearby trajectories remain reachable.

Examples:

```text
clean hit ↔ graze
graze ↔ miss
projectile catches shield rim instead of neck
wild swing happens to occupy a useful lane
```

Luck cannot create a hit when no attack path intersects the target or a valid exposure field.

#### 2. Timing Residual

Luck may bias a marginal reaction or interruption window only when both timing branches remain reachable around the relevant mode threshold.

After the canonical update, Combat recomputes the affected mode margin:

$$
\begin{aligned}
M_R^{\mathrm{resolved}}(d)
&=
t_i^{\mathrm{resolved}}
-
\bigl(
t_p
+
t_d(d)
+
t_m(d)
\bigr)
\end{aligned}
$$

Luck cannot create perception, precoverage, movement time, or an active response branch that did not exist. Perception time remains supplied by `perception_information.md`; movement time remains supplied by `motion_positioning.md`.

#### 3. Contact Geometry

Luck does not add an independent bonus directly to contact quality. It biases unresolved path, timing, angle, or interference coordinates, then Combat recomputes:

$$
\begin{aligned}
Q^{\mathrm{resolved}}
&=
\Phi_Q
\bigl(
\mathrm{resolvedPathAlignment},
\mathrm{resolvedRangeQuality},
\mathrm{resolvedLeverage},
\kappa_A,
\mathrm{resolvedContactGeometry}
\bigr)
\end{aligned}
$$

This preserves the single-application rule for $Q$.

#### 4. Layer Angle, Deflection, and Penetration

Luck may bias a marginal, physically plausible layer interaction:

```text
blade glances instead of bites
point catches a seam instead of plate
shield turn converts puncture into blunt transfer
deflection exits on a more or less dangerous line
penetration margin falls just above or below threshold
```

Luck cannot make weak passive defense stop overwhelming effect when no stopped branch exists. It cannot bypass the effect-accounting ledger or create unexplained transfer.

#### 5. Battlefield Interference

Luck may bias chaotic interference already present or causally entering the exchange:

```text
existing debris interrupts pursuit
a present third actor crosses the wrong lane
a stray projectile catches a guard line
a real noise masks or exposes movement
```

Luck cannot invent an actor, object, hazard, warning, or piece of evidence.

---

### Owner-Routed Luck Cases

These cases do not resolve inside the combat adapter:

```text
footing or landing margin             → motion_positioning.md
threat recognition or Insight margin  → perception_information.md
crash basin or Reserve backlash       → resource_system.md
wound path or organ proximity         → embodiment_injury.md
plan branch or coordination failure   → strategy_decision_systems.md
active entropy cost or fate debt      → luck_fortune.md
```

Combat may pause the local exchange for an owner return when the uncertain branch materially changes reachability.

---

### Resolution Order

Use this order:

1. Resolve deterministic owner inputs.
2. Enumerate physical and tactical branches.
3. Remove causally impossible branches.
4. Route foreign-subsystem uncertainty to its owner adapter.
5. Build $\mathbf z_{\mathrm{combat}}$ from remaining exchange-local uncertainty.
6. Declare side $s$ and $U_{\mathrm{combat}}^{(s)}$.
7. Apply one canonical Luck/Fortune update through `luck_fortune.md`.
8. Project back into the reachable set.
9. Recompute affected derived quantities: mode margins, $Q$, target allocation, layer angle, $P$, and the local classifier.
10. Route active-control cost evaluation and downstream consequences.

Plain rule:

```text
Update uncertain state once.
Recompute the exchange from that state.
Never stack independent Luck bonuses onto the same cause.
```

---

### Fortune, Misfortune, and Volatility Examples

**Fortune drift:**

```text
graze instead of deep contact
shield bind instead of disarm
point misses a seam by a narrow reachable margin
interference opens an existing escape lane
```

**Misfortune drift:**

```text
parry angle worsens
armor seam catches the point
deflection exits toward an ally
a marginal interruption arrives late
```

**Volatility diffusion:**

```text
both shallow and catastrophic tails become more likely
formation interaction becomes less predictable
deflection direction spreads across wider reachable outcomes
miraculous save and absurd catastrophe remain possible from the same instability
```

Volatility should feel unstable, not benevolent.

---

### Forbidden Simplifications

Do not use Luck to:

* replace skill, preparation, perception, movement, strategy, armor, or power;
* increase HP, Mana, Stamina, or Reserve maximums;
* erase resource debt or deterministic hard stops;
* override intelligent agency without another mechanism;
* convert Item Rarity, class rarity, or a visible `LUCK` label into a flat combat bonus;
* make the Interface understand evidence it cannot detect or classify;
* choose a deterministic lethal outcome away after its causal path has closed;
* run motion, perception, resource, injury, or strategy uncertainty inside Combat merely for convenience.

---

### Result Classifier

After projection into the reachable set, Combat classifies the local result from the resolved state:

$$
\begin{aligned}
\mathrm{Result}_{\mathrm{combat}}
&=
\mathrm{Classify}_{\mathrm{combat}}
\bigl(
\mathbf z_{\mathrm{combat,final}}
\bigr)
\end{aligned}
$$

Examples:

```text
clean miss
near miss
graze
clean contact
armor glance
armor bite
shield bind
partial penetration
full penetration
forced miss
timing advantage
formation interference
```

Wound anatomy, crash severity, perception outcome, and strategic branch labels remain classified by their owner files.

---

## Canon Constraints and Character Applications

### Section Contract

This section translates existing canon into combat-safe application rules. It does not create new abilities, progression stages, resource formulas, injury states, scene outcomes, or relationship facts.

When a character application conflicts with an owner file, the owner file wins.

---

### Existing Scene Constraints

#### Earth Scrim — 404 vs. Dead Hand

The opening Aetherfall duel must preserve these locked combat facts:

1. Astria does **not** arrange the scrim. It exploits an already scheduled 404 vs. Dead Hand match.
2. Serra attacks Brent because he is the healer/support target. Marcus interposes first.
3. Serra still wounds Brent severely enough that he must extract. Marcus then trusts Brent to get himself clear so Marcus can exploit Serra's overcommitment.
4. Marcus switches into Predator for the punish. He narrowly misses Serra but tears away her hood or veil, exposing her red hair and face.
5. Marcus recognizes Serra first. His hesitation lasts only a fraction of the same combat beat.
6. Serra counterattacks and forces an Aspect transition. Marcus's unmodified biometric avatar appears briefly between forms; Serra recognizes him a fraction later.
7. Personal recognition is mutual but staggered in the same beat: Marcus first, Serra second.
8. Before recognition, the exchange stays professional and tactical. Do not use proximity voice, a deliberately obvious bait, or playful showboating as the recognition mechanism.
9. After recognition, the duel becomes more synchronized and dangerous. Playful feints and creative bait may appear only as flavor inside that earned mutual flow.
10. Marcus and Serra remain equals in fact. The duel earns Serra's recognition of Marcus; it does not create skill parity.
11. They remain engaged one exchange too long. The team cost must be physical and tactically legible: a line opens, an objective suffers, an ally loses coverage, or formation pressure increases.
12. Seb's command to break contact matters. Marcus hears it and stays for one additional exchange.
13. Mathias can recognize the exposed identity through line-of-sight and scouting awareness. Mara can connect Marcus's biometric transition to the altana encounter. Neither needs to know exactly what happened on the rooftop.
14. Aetherfall avatars reproduce the player's real body through biometric scanning while class forms and gear alter appearance and capability. The recognition beat must obey that rule.
15. Aetherfall HP percentages may remain visible for fight-state clarity. Numeric Aspect-meter percentages should not appear; use qualitative language such as low, critical, red warning, or one bad switch from broken.

These are choreography constraints. The prose must render the physical middle of each load-bearing beat.

---

#### Realm Combat

Realm combat must preserve these baseline facts:

* The Realm is not a game. Pain, death, bodily damage, spiritual harm, and resource collapse are real.
* HP is an Interface translation of immediate survivability and integrity, not a replacement for anatomy or injury.
* Interface warnings may be useful, incomplete, compressed, biased, or wrong in interpretation. They are not underlying reality.
* A character cannot use a defense, movement branch, or counterattack that current geometry, timing, condition, and information do not permit.
* Violence creates cost. Even a clean tactical win changes body state, resource pressure, position, information, relationship, or future risk.
* Book-1 combat should prefer a small number of stable rules applied consistently over a large catalogue of one-off exceptions.

---

### Character Combat-Defense Signatures

These signatures describe **how existing canon appears inside an exchange**. They do not define the characters' full power systems.

| Character | Combat signature | Defensive signature | Exchange failure pressure | Owner boundary |
|---|---|---|---|---|
| Marcus | reads patterns, changes method, finds the hinge, and turns information into timing or route advantage | adaptive defense; uses wards, spacing, prediction, and later integrated movement rather than one fixed shell | false certainty, over-analysis, emotional lock, setup time, and trying to solve real violence from outside it | ocular truth and misread mechanics remain in his dossier and `perception_information.md`; Mana/Reserve cost remains in `resource_system.md` |
| Serra | direct entry, momentum theft, pressure-step chains, seam targeting, anti-support execution, and commitment that converts disruption into a second attack | active defense through movement, guard manipulation, counterpressure, and making the opponent's response become her screen | overcommitment, collateral pressure, reduced discrimination, and staying one exchange past necessity | Book 1 is Warrior fundamentals; full Severance Pulse expression is dormant until the finale; Worldbreaker language is Book 2+ |
| Seb | coordinated frontline pressure that becomes increasingly self-consuming through Warrior/Reaver and Pyric Blood | refuses shutdown, receives force through pain suppression and borrowed future function, and converts enemy vitality into continued pressure | delayed crash, debt spiral, narrowed judgment, and mistaking accepted cost for permission | Combat outputs pressure and continuation state; `resource_system.md` and his dossier own the actual debt and interface costs |
| Mara | control-first combat: alters which real threat becomes urgent, shapes timing, denies clean resets, and creates the kill architecture another fighter uses | avoids selection, redirects attention, controls space, and forces enemies to defend the emphasized problem | misread dependence, concealment becoming passivity, over-control of the frame, and physical vulnerability when attention control fails | salience generation and inference remain in `perception_information.md` and her dossier; Combat only resolves the branch created by the read |
| Mathias | route-fighting: finds unexpected lines through terrain, applies precise pressure from angles the enemy did not respect, and relocates before retaliation | distance, route choice, early warning, extraction lines, and keeping more movement branches open than the enemy expects | staying too open, delayed commitment, being caught when the route closes, and insufficient close-range stopping power | Earth/Aetherfall signature is Strider; Realm class is Scout. Path feasibility belongs to `motion_positioning.md`; tactical route value belongs to strategy |
| Brent | protects load-bearing people and structures, reads where force is actually traveling, and makes transferred cost concrete | bracing, interposition, impact redistribution, structural reinforcement, and adaptive body states; not a generic tank | mobility loss under Fortress-style adaptation, accumulated structural load, over-remodeling, and being chosen only when something is breaking | Realm class is Warden; Reckoner is Book-2 direction. Body adaptation belongs to his dossier/resources; injury and anatomy remain downstream |
| Illyri | celestial battle-support / striker hybrid: marks, reveals, interrupts, weakens, stabilizes, and later strikes with halo relics and Star-Glaive | Wing Aegis, halo interception, ally enhancement, collapse delay, and targeted stabilization | reduced embodiment, limited reach, memory gaps, dependency on Marcus, and stabilization being mistaken for full healing | Book-1 action is constrained by her damaged form. Full healing, resurrection, and injury repair are not granted here |

#### Pair and Team Signatures

* **Marcus + Serra:** Marcus identifies the weak point; Serra commits force through it. Their danger is exclusionary attention that creates team cost.
* **Serra + Mara:** Mara creates the urgent or invisible line; Serra turns it into entry. Mara remains the setup half, not a decorative observer.
* **Seb + team:** Seb improves coordination when healthy; under strain he can turn sacrifice and protection into override pressure. Strategy owns the decision; combat shows who pays.
* **Brent + protected ally:** Brent's interposition should expose load transfer and consequence. He does not simply add armor to another character.
* **Mathias + formation:** Mathias makes the battlefield larger for allies and less safe for enemies by preserving routes, warning of pressure, and opening unexpected lines.
* **Illyri + Marcus:** Illyri may improve information, timing, stability, or defensive coherence, but she does not make Marcus's read omniscient or erase his cost.

---

### Book-1 Minimum Rules

The following rules are sufficient for consistent Book-1 combat unless a scene requires an owner-file expansion:

1. **Reachability first.** Remove impossible branches before evaluating skill, Luck, or damage.
2. **Information gates active defense.** A threat must be perceived, predicted, pre-covered, or intercepted by another mechanism.
3. **Timing spends freedom.** Commitment and recovery determine what remains available after an action begins.
4. **Contact is classified before damage.** Miss, near miss, bind, glance, partial contact, and clean contact are materially different.
5. **Defense is layered.** Avoidance, interception, deflection, absorption, resistance, recovery, and counterpressure are distinct.
6. **Armor, shields, and barriers transform consequence.** They may deflect, leak, transfer force, consume resources, lose durability, or collapse.
7. **HP damage is not injury anatomy.** Combat outputs HP damage and injury risk; `embodiment_injury.md` resolves specific harm.
8. **Combat cost is not final accounting.** Combat outputs cost pressure; `resource_system.md` applies actual HP, Mana, Stamina, and Reserve changes.
9. **Position and tempo can decide a fight without high damage.** A forced step, broken line, delayed recovery, or exposed support may be the decisive result.
10. **Team defense changes branches, not statistics.** Interposition, suppression, shield overlap, and protection lines alter access and consequence routing.
11. **Domains are not Affinities.** Use Domain for power expression/source behavior. Affinity remains progression aptitude.
12. **The Interface translates.** A readout may inform a decision but does not replace the event in prose or become metaphysical truth.
13. **Luck acts only at reachable margins.** It cannot rescue a branch that geometry, timing, or overwhelming force has already removed.
14. **Violence leaves residue.** The scene acknowledges physical, emotional, tactical, relational, or resource cost.
15. **Mechanics tiers remain typed.** Spell Strength, Spell Skill Mastery, Item Quality, Item Rarity, Soul Level, and class rarity are not interchangeable scalar ladders.
16. **Rarity is not defense.** Item Rarity and class rarity provide no automatic attack, penetration, armor, barrier, or durability bonus.
17. **Soul Level is scoped.** It matters to identity/metaphysical alteration, not ordinary weapon force or generic spell resistance.
18. **Luck modes stay distinct.** Fortune and Misfortune change drift; Volatility changes diffusion.
19. **Combat does not award kill XP.** It supplies raw adaptation facts; `xp_progression_formulas.md` resolves recoverable combat adaptation, recovery integration, thresholds, and scene XP.
20. **Stable rules beat hidden exceptions.** Do not invent an invisible formula merely to force the desired scene result.

---

## Presentation Rules

### Section Contract

This section governs how resolved combat facts are handed to prose. It does not own the general narrative voice, dialogue, UI format, or character interiority rules; those remain in the style and voice files.

Combat prose must preserve causality without exposing the hidden mathematical machinery.

---

### Prose Rendering

Use this rendering spine for a choreography-load-bearing exchange:

```text
setup state
→ visible attack path
→ defender's available response
→ timing and commitment
→ contact or miss
→ force/effect transfer
→ layer response
→ body/resource/position consequence
→ changed next state
```

The reader usually does not need every item stated separately. The reader does need enough concrete sequence to reconstruct the beat.

#### Physical Event Before Meaning

Render what moved and where before naming what the action accomplished.

```text
Weak:
Serra turned his block into a screen and got through.

Strong direction:
He raised the shield toward her first step. She did not retreat. She cut inside the rim, planted past his lead foot, and used his shoulder to hide her blade from the ally behind him. The block became her screen.
```

The second version may be shortened, but the middle cannot disappear when the outcome depends on it.

#### Mechanism Before Exploitation

A limitation must be established before a character wins by exploiting it.

Examples:

* Show that the barrier refreshes from fixed anchors before someone attacks the anchor interval.
* Show that a shield turns slowly before Serra goes around it.
* Show that Marcus's form transition briefly exposes his biometric avatar before Serra recognizes him through it.
* Show that Brent's defensive adaptation trades mobility for resistance before the enemy attacks the movement gap.

#### Violence With Weight

A result should leave at least one observable residue:

```text
breath change
pain response
blood or damaged gear
resource warning
lost coordination
fear or hesitation
changed willingness
formation damage
moral cost
future injury risk
```

Do not add suffering for decoration. Use the specific residue that changes the character or the next decision.

---

### Interface Display Rules

Combat & Defense may generate **display candidates** but does not format them.

Display candidates may include:

```text
contact classification
HP change
resource strain warning
barrier instability
armor failure
status hint
threat direction
recovery warning
unknown classification
```

`system_message_rules.md` owns whether any candidate becomes a notification, warning, panel, sentence, percentage, symbol, or nothing at all.

Binding presentation constraints:

* The Interface intrudes; it does not pause combat.
* Prose must remain intelligible without a notification explaining the physical event.
* Mid-combat stat panels are not used in Realm prose.
* Early-story Interface presence may be higher; later presence becomes sparser and more significant.
* A notification may note or complicate an outcome. It should not narrate every exchange.
* Displayed precision must not exceed the Interface's evidence and classifier capability.
* `Unknown`, uncertain confidence, or incomplete categories are valid outputs.
* A readout can be factually useful while the character's interpretation remains wrong.
* Aetherfall game scenes may use game-native HP percentages when they carry tactical state. This exception does not make Realm reality a game.

Plain rule:

```text
The body performs the event.
The Interface translates part of it.
The prose carries the scene.
```

---

### Choreography-Load-Bearing Combat

A beat is choreography-load-bearing when the meaning depends on the exact physical or tactical sequence.

Hard test:

```text
Could a reader reconstruct what moved, where it moved, what response occurred, what made contact, and what changed next without inventing a missing step?
```

If no, the beat is not ready.

Minimum reconstruction fields:

| Field | Reader question |
|---|---|
| actors and lines | who threatens whom, and from where? |
| path | what route does the attack or effect take? |
| response | what does the defender or team actually do? |
| timing | why is the response early, timely, late, or impossible? |
| contact | what touches, misses, binds, glances, penetrates, or collapses? |
| consequence | what changes in body, layer, resource pressure, position, or information? |
| next state | what opening closes or appears because of that result? |

Compression is allowed when choreography is not carrying the meaning. Do not over-render routine footwork in an interiority-load-bearing beat. Do not compress the physical middle when the exchange itself is characterization, recognition, sacrifice, betrayal, or tactical revelation.

---

### Combat Presentation Failure Modes

Do not use:

* verdicts on unstaged actions,
* metaphors that replace missing physical sequence,
* unexplained counters invented at impact,
* game-walkthrough narration,
* notification spam,
* stat blocks as substitutes for sensation,
* generic “too fast to see” language when timing is load-bearing,
* armor that alternates between absolute and irrelevant without a changed mechanism,
* characters forgetting established abilities so the plot can land,
* cost-free violence,
* choreography that exists only to sound impressive and changes nothing.

---

## Optional Computational Reduction / Simulator

### Status and Boundary

This section is an optional author tool. It is **not** underlying cosmology, not reader-facing rules text, and not a replacement for narrative judgment.

```text
Code tests the math.
Prose stays canon.
```

The simulator may verify consistency, expose unreachable outcomes, and produce traces. It may not create canon merely because an implementation returns a result.

---

### Pseudocode Pipeline

```text
function resolve_exchange(input):
    validate_required_owner_inputs(input)

    motion = consume_motion_combat_output(input.motion)
    information = consume_perception_combat_output(input.information)
    tactical_slice = consume_strategy_combat_output(input.strategy)
    resource_tolerance = consume_resource_state(input.resources)
    mechanics_projection = consume_typed_mechanics_projection(input.mechanics)

    reject_ordinal_tier_arithmetic(mechanics_projection)
    reject_automatic_item_rarity_power(mechanics_projection)
    reject_automatic_class_rarity_power(input)
    validate_soul_level_scope(mechanics_projection, input.attack)

    normalized_attack = apply_owner_scoped_expression_projection(
        input.attack,
        mechanics_projection,
    )
    normalized_defense = apply_owner_scoped_resistance_projection(
        input.defense,
        mechanics_projection,
    )

    branches = enumerate_reachable_branches(
        attack=normalized_attack,
        defense=normalized_defense,
        motion=motion,
        information=information,
        environment=input.environment,
        tactical_slice=tactical_slice,
    )
    branches = remove_causally_impossible(branches)

    baseline_timing_by_mode = resolve_mode_specific_reaction_margins(
        input,
        motion,
        information,
        branches,
    )
    baseline_mode_candidates = identify_available_or_marginal_modes(
        input,
        baseline_timing_by_mode,
        branches,
    )

    foreign_uncertainty = identify_owner_routed_uncertainty(
        motion=motion,
        information=information,
        resources=input.resources,
        injury=input.injury,
        strategy=input.strategy,
    )
    owner_returns = resolve_foreign_uncertainty_through_owner_adapters(
        foreign_uncertainty
    )

    baseline_uncertainty_state = build_exchange_local_uncertainty_state(
        branches=branches,
        timing_by_mode=baseline_timing_by_mode,
        mode_candidates=baseline_mode_candidates,
        owner_returns=owner_returns,
        environment=input.environment,
    )
    reachable_uncertainty = identify_uncertain_reachable_combat_coordinates(
        baseline_uncertainty_state
    )
    resolved_uncertainty_state = apply_one_canonical_luck_update(
        state=baseline_uncertainty_state,
        reachable_set=reachable_uncertainty,
        drift_input=input.luck.fortune_or_misfortune,
        diffusion_input=input.luck.volatility,
        owner="luck_fortune.md",
    )
    resolved_uncertainty_state = project_to_reachable_set(
        resolved_uncertainty_state,
        reachable_uncertainty,
    )

    timing_by_mode = recompute_mode_margins(resolved_uncertainty_state)
    timing_quality_by_mode = resolve_mode_timing_quality(
        input,
        timing_by_mode,
        branches,
    )
    defense_modes = resolve_available_defense_modes(
        input,
        timing_by_mode,
        timing_quality_by_mode,
        branches,
    )
    team_effect = resolve_declared_local_team_actions(
        input,
        timing_by_mode,
        timing_quality_by_mode,
        branches,
    )
    contact = classify_contact(
        input,
        defense_modes,
        team_effect,
        resolved_uncertainty_state,
    )
    effective_impact = resolve_effective_impact_once(
        normalized_attack,
        contact,
        motion.combatMotionPressure,
    )
    layer_trace = resolve_defensive_layers_with_effect_accounting(
        input,
        effective_impact,
        contact,
    )
    validate_coverage_partitions(layer_trace)
    validate_effect_accounting_ledger(layer_trace)
    validate_active_source_routes(layer_trace)
    passive_transfer = summarize_source_free_bounded_transfer(layer_trace)
    final_effect_vectors = read_full_effect_vectors_by_target_with_explicit_sources(
        layer_trace
    )
    consequence = classify_combat_consequence(
        contact=contact,
        layer_trace=layer_trace,
        passive_transfer=passive_transfer,
        final_effect_vectors=final_effect_vectors,
        current_state=input.local_state,
    )

    result = build_authoritative_exchange_result(
        consequence,
        layer_trace,
        passive_transfer,
        final_effect_vectors,
    )
    handoffs = build_owner_handoffs_as_projections(result)
    handoffs.luck = build_luck_owner_trace(
        input.luck,
        baseline_uncertainty_state,
        resolved_uncertainty_state,
    )
    handoffs.progression = build_raw_combat_adaptation_trace(
        result,
        result.traceMetadata,
    )
    assert_no_xp_calculated(handoffs.progression)

    return result, handoffs
```

The pipeline stops short of:

```text
final resource debit
final wound anatomy
long-term impairment
full motion integration outside the exchange
new perception inference
next tactical decision
active Luck entropy-cost calculation
tier-ladder redefinition or ordinal-tier arithmetic
adaptive-evidence calculation
recovery integration
scene XP or level thresholds
UI formatting
```

---

### Input Schema

```text
CombatExchangeInput:
  exchangeId
  timestamp
  randomSeed                           # simulator only; omitted for narrative adjudication
  attack:
    path
    velocityOrCarrierState
    intrinsicIntensity
    angle
    precision
    timing
    commitment
    intent
    expression
    Domains
    spellDiscipline                    # optional; must match mechanics projection when present
    itemOrPowerFeatureIds
  defense:
    position
    guard
    mobilityOptions
    brace
    activeLayers
    resistanceSummary
    counterpressure
    recoveryState
    identityOrSoulChannelExposed       # true only for causally relevant metaphysical attacks
  motion:
    feasibilityByCandidateMove
    updatedPosition
    updatedFacing
    balanceState
    footingState
    tractionState
    combatMotionPressure
    reachEnvelope
    fallRisk
    recoveryBurden
    availableMovementBranches
    routedFrom: motion_positioning.md
  information:
    perceptionTime
    threatRecognized
    threatConfidence
    perceivedAttackPath
    perceivedIntent
    perceivedDamageType
    salienceState
    misreadRisk
    insightRead
    uncertainty
    optionalPerceivedTiming
    optionalAvailableEvidence
    routedFrom: perception_information.md
  mechanics:
    spellStrengthTier                  # typed ordinal; never used directly as a scalar
    spellSkillMasteryTier              # typed ordinal
    spellSkillMasteryBonus             # numeric owner projection for matching discipline only
    spellExpressionProjection          # property vector supplied by mechanics/power owners
    spellResistanceProjection          # matching-discipline resistance only
    itemQualityTier                    # may affect performance only through item profile
    itemRarityTier                     # no automatic combat modifier
    itemPerformanceProjection
    soulLevelTier                      # no generic combat modifier
    identityResistanceProjection       # present only for valid identity/metaphysical channel
    routedFrom: mechanics.md
  resources:
    toleranceState
    depletionFlags
    affordabilityFlags
    manaBelowTwentyPercent
    staminaBelowTwentyPercent
    forcedActionContext
    routedFrom: resource_system.md
  injury:
    conditionFlags
    functionalLimits
    routedFrom: embodiment_injury.md
  strategy:
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
    routedFrom: strategy_decision_systems.md
  environment:
    terrain
    cover
    hazards
    visibility
    crowding
  luck:
    mode
    favoredSide
    reachabilityGate
    favorabilityDefinition
    fortuneOrMisfortuneDriftInput
    volatilityDiffusionInput
    activeControlFlag
    routedFrom: luck_fortune.md
```

The schema accepts owner-file outputs. It must not recalculate their internal state from guessed values. Class rarity and XP state are intentionally absent from exchange inputs because neither is a direct combat-power variable.

---

### Output Schema

The simulator must serialize the same authoritative result contract used by the document. Diagnostic and implementation metadata may accompany it, but may not redefine it.

```text
CombatExchangeResult:
  exchangeId
  contactClass                         # C
  contactQuality                       # Q
  penetrationOrEffectTransfer          # P; target-keyed bounded passive aggregate when needed
  hpDamageOutput                       # H; target-keyed when needed
  injuryRiskAndLocationOutput          # J; target-keyed when needed
  resourceOrConcentrationPressure      # K; keyed by actor, role, and resource channel
  defensiveLayerStateChange            # DeltaS_layer; target-path and layer keyed
  positionChangeRequest                # deltaX
  tempoOrRecoveryChange                # deltaTau
  tacticalOpening                      # O
  formationPressure                    # F_pressure
  informationConsequence               # deltaInformation
  ownerRouting                         # R_route
  traceMetadata:
    reachableBranches
    rejectedBranches:
      branch
      reason
    reactionMarginsByMode
    reactionMarginClassesByMode
    timingQualityByMode
    standingPrecoverageModes
    defenseModesAvailable
    defenseModeResolved
    declaredTeamActionResolution
    targetAllocationWeights
    targetIndexedPaths
    impactClass
    finalEffectVectorByTarget
    passiveLayerTransferSummariesByTarget
    layerTrace
    effectAccountingLedger
    activeSourceLedger
    mechanicsProjectionApplied
    ownerRoutedUncertainty
    luckDriftApplied
    luckDiffusionApplied
    activeLuckCostEvaluationRequired
    uncertaintyApplied
    causalTrace
  projections:
    damageOutput
    layerResult
    consequenceBundle
    resource_system
    embodiment_injury
    motion_positioning
    perception_information
    strategy_decision_systems
    luck_fortune
    xp_progression_trace                # raw facts only; never scene XP
    system_message_candidates
```

The fields under `projections` must be mechanically derivable from the canonical result and its trace. They may not contain a different contact, penetration, damage, pressure, position, tempo, opening, formation, information, or routing result. The `xp_progression_trace` projection may contain only raw combat facts and owner routes; it must not contain adaptive evidence, recovery integration, XP, level thresholds, or class-rarity burden.

Every result should include a trace sufficient to answer:

```text
Why was this outcome reachable?
Why were nearby alternatives rejected?
Which owner file must resolve each downstream consequence?
```

---

### Required Fixtures

#### Fixture 1 — Deterministic Miss

```text
Attack path never intersects the target or any defensive layer.
Expected: miss; no penetration; no Luck conversion into a hit.
```

#### Fixture 2 — Late Read, Precovered Guard

```text
Defender recognizes the attack late but already covers the threatened line.
Expected: active precision defense unavailable; guard contact remains reachable.
```

#### Fixture 3 — Shield Deflection

```text
Oblique shield contact with sufficient brace and rim angle.
Expected: high deflection, some Stamina pressure, changed weapon line, possible counteropening.
```

#### Fixture 4 — Armor Bite With Blunt Transfer

```text
Point fails to fully penetrate plate but transfers substantial force.
Expected: low penetration, armor damage, moderate HP-damage output, blunt injury risk, forced step.
```

#### Fixture 5 — Barrier Bleed-Through

```text
Barrier remains coherent but incompatible expression leaks inward.
Expected: partial transmitted effect, Mana pressure candidate, no invented full collapse.
```

#### Fixture 6 — Successful Interposition

```text
Protector reaches the line before contact and substitutes as target.
Expected: target change, altered angle, protector consequence, formation/opening update.
```

#### Fixture 7 — Failed Interposition

```text
Protector lacks the reaction margin or path clearance.
Expected: original target remains; protector may incur position or collision debt; no teleporting body block.
```

#### Fixture 8 — Feint Creates Wrong-Line Defense

```text
Perception owner supplies wrong-line read; defender commits guard accordingly.
Expected: real line becomes more reachable; feint does not alter attack physics by itself.
```

#### Fixture 9 — Formation Break Without Major Damage

```text
Repeated forced steps sever protection edges around support.
Expected: low HP damage, high formation pressure, serious tactical opening.
```

#### Fixture 10 — Luck at a Reachable Margin

```text
Two plausible armor-contact angles remain after deterministic resolution.
Expected: Luck may bias glance versus bite; cannot choose a miss or catastrophic penetration outside the reachable set.
```

#### Fixture 11 — Interface Translation Error

```text
Underlying barrier instability exists; Interface reports an overconfident category.
Expected: physical resolution uses underlying supplied state, while the actor may choose from the rendered read. The readout does not rewrite reality.
```

#### Fixture 12 — Resource Hard Stop

```text
Resource owner reports an action unaffordable or a hard-stop state.
Expected: branch removed or converted to a forced/catastrophic route defined by the owner; simulator does not mint extra resource.
```

#### Fixture 13 — Zero Load / Zero Defense

```text
Incoming intensity and contextual defense power are both zero.
Expected: guarded threshold returns zero transfer; no NaN, division error, phantom contact, or source-free effect appears.
```

#### Fixture 14 — Active Defensive Source

```text
A barrier converts stored Mana into an outward counterpulse after contact.
Expected: passive P remains bounded at or below one; counterpulse appears in the active-source ledger with a resource-owner route; final effect may exceed the original incoming magnitude only by that recorded source.
```

#### Fixture 15 — Partial Interposition

```text
Protector reaches only part of a broad or sweeping attack line.
Expected: target-allocation weights sum to one; both original target and interposer receive separately recomputed geometry and consequences; no duplicated full-strength hit.
```

#### Fixture 16 — Pressure-Only Near Miss

```text
Attack passes close enough to force attention and movement but does not establish an effect path.
Expected: near-miss contact class may create tempo, information, or formation pressure; body-facing effect, HP damage, and injury risk remain zero.
```

#### Fixture 17 — Recovery Is Post-Contact

```text
Defender blocks successfully but ends displaced and staggered.
Expected: recovery quality changes deltaTau and the next reachable branch set; it does not retroactively improve the completed block or contact quality.
```

#### Fixture 18 — Item Rarity Independence

```text
Two otherwise identical shields differ only in Item Rarity. Neither has a special feature.
Expected: identical combat defense, durability, and layer behavior. Rarity changes no combat quantity.
```

#### Fixture 19 — Scoped Spell Mastery

```text
A fire-discipline mastery bonus is supplied for a fire barrier and an unrelated kinetic defense.
Expected: the owner projection may improve declared fire-barrier expression/resistance; it does not improve the unrelated kinetic defense.
```

#### Fixture 20 — Soul Level Scope

```text
A high-Soul-Level defender is struck once by an ordinary mace and once by a namebinding/identity attack.
Expected: Soul Level supplies no automatic resistance to the mace. It may contribute to the identity-resistance projection for the namebinding attack.
```

#### Fixture 21 — Volatility Is Diffusion

```text
A marginal shield-angle exchange is simulated with equal baseline drift and increased Volatility.
Expected: the reachable result distribution widens without an automatic favorable or harmful mean shift.
```

#### Fixture 22 — Progression Trace Without XP

```text
A dangerous exchange produces damage pressure, resource pressure, tactical pattern facts, and recovery debt.
Expected: Combat emits a raw CombatAdaptationTrace. No adaptive evidence, class coupling, recovery integration, scene XP, or level threshold is calculated.
```

---

### Test Requirements

The computational reduction should test:

* **reachability:** impossible outcomes never re-enter through scoring or Luck,
* **timing:** each pre-contact candidate mode uses its own decision time, execution time, reaction margin, and normalized $q_R(d)$; partial mode quality is preserved between zero and the clean-margin threshold; recovery is evaluated only after the new state exists,
* **precoverage:** only defenses already instantiated in the standing state can survive a negative active reaction margin,
* **single application:** contact quality, coverage, timing, angle, surprise/preparation, and location vulnerability are each applied at their declared stage and are not silently multiplied twice; armor bite pressure scales both the tested load and puncture output consistently,
* **Luck recomputation:** Luck updates underlying reachable coordinates once; derived $Q$, defense modes, and target substitution are recomputed rather than receiving independent duplicate bonuses,
* **Luck mode typing:** Fortune/Misfortune affect drift, Volatility affects diffusion, and active-control cost is routed to `luck_fortune.md`,
* **owner-routed uncertainty:** motion, perception, resource, injury, and strategy uncertainty is resolved by its owner adapter rather than copied into the combat adapter,
* **tier typing:** Spell Strength, Spell Skill Mastery, Item Quality, Item Rarity, Soul Level, and class rarity are never used as interchangeable numbers,
* **rarity independence:** Item Rarity and class rarity create no automatic combat modifier,
* **Soul Level scope:** Soul Level affects only causally relevant identity/metaphysical resistance,
* **progression boundary:** Combat emits raw adaptation facts only; no adaptive evidence, class coupling, recovery integration, threshold, or XP value is calculated locally,
* **layer order:** outer-to-inner resolution is stable and traceable,
* **coverage conservation:** contacted and bypass shares sum to the incoming layer effect within tolerance,
* **effect accounting:** transmitted, absorbed, deflected, layer-damage, converted, and explicit-source terms close the ledger in one declared basis without unexplained effect,
* **passive boundedness:** every passive $p_j$, $P_j$, and $P_k$ remains in $[0,1]$; any final magnitude above incoming magnitude is explained only by an explicit active source,
* **barrier one-credit accounting:** baseline resistance, refresh, and current integrity are each credited once, permeability is partitioned before capacity, and the transmitted overcapacity share is not added twice,
* **state-change accounting:** durability and coherence debits derive from the allocated layer-damage share rather than consuming incoming effect as a second sink,
* **zero-state safety:** zero intensity and zero defense produce a defined zero-transfer result rather than NaN or phantom effect,
* **active sources:** any source term has a declared mechanism, owner route, and cost or stored-state debit,
* **interposition:** target allocation requires a real path and timing window; partial/shared-contact weights sum to one, conserve divisible pre-contact intensity, and do not duplicate effect,
* **ownership:** resource, injury, motion, perception, strategy, Luck-cost, progression, and UI outputs remain handoffs rather than local finalization,
* **translation:** Interface labels never replace supplied underlying state,
* **Domain terminology:** schema and fixtures use Domains, not Affinities, for expression behavior,
* **determinism:** fixed inputs and fixed random seed reproduce the same trace,
* **counterexample quality:** failures state which contract broke,
* **prose parity:** a human-readable trace can be converted into a physically reconstructable beat without exposing equations.

Suggested failure codes:

```text
CD-REACH-001 impossible_branch_selected
CD-TIME-001 active_defense_without_response_path
CD-TIME-002 non_mode_specific_reaction_margin
CD-TIME-003 recovery_used_as_precontact_defense
CD-TIME-004 partial_margin_quality_discarded
CD-LAYER-001 layer_order_violation
CD-LAYER-002 coverage_partition_mismatch
CD-XFER-001 unexplained_effect_creation
CD-XFER-002 contact_or_modifier_double_applied
CD-XFER-003 active_source_without_owner
CD-XFER-004 effect_accounting_ledger_open
CD-XFER-005 undefined_zero_state
CD-XFER-006 passive_transfer_out_of_bounds
CD-XFER-007 barrier_capacity_double_credited
CD-XFER-008 incompatible_accounting_basis
CD-XFER-009 pressure_only_contact_transferred
CD-XFER-010 durability_debit_not_derived_from_layer_damage
CD-XFER-011 armor_bite_output_exceeds_bite_load
CD-TEAM-001 interposition_without_causal_path
CD-TEAM-002 target_allocation_not_conserved
CD-TEAM-003 nondivisible_carrier_duplicated
CD-OWNER-001 foreign_subsystem_finalized_locally
CD-UI-001 interface_read_treated_as_reality
CD-LUCK-001 unreachable_outcome_selected
CD-LUCK-002 duplicate_luck_hook_application
CD-LUCK-003 fortune_or_misfortune_applied_as_diffusion
CD-LUCK-004 volatility_applied_as_directional_drift
CD-LUCK-005 foreign_uncertainty_resolved_inside_combat
CD-LUCK-006 active_control_cost_not_routed
CD-MECH-001 ordinal_tier_used_as_scalar
CD-MECH-002 item_rarity_used_as_combat_power
CD-MECH-003 class_rarity_used_as_combat_power
CD-MECH-004 spell_mastery_applied_outside_matching_discipline
CD-MECH-005 soul_level_used_as_generic_defense
CD-XP-001 xp_calculated_inside_combat
CD-XP-002 progression_trace_contains_owner_formula
CD-TERM-001 affinity_used_as_domain
CD-TRACE-001 result_without_causal_trace
```

---

### Code and Canon Rule

The simulator is useful when it catches inconsistency. It is wrong when it pressures the story to preserve an implementation artifact.

When code and prose disagree:

1. Check whether the prose violates a locked combat contract.
2. Check whether the code imported the correct owner-file inputs.
3. Check whether a simplified reduction discarded a causal mechanism the prose established.
4. Fix the code when the prose is canon-compliant.
5. Fix the prose when the exchange is physically or canonically impossible.
6. Escalate to an owner-file decision only when the conflict reveals a genuine open rule.

Plain rule:

```text
The simulator must explain canon.
It is never allowed to silently replace it.
```

---

## Agent Boundaries

Agents may:

- Add scoped exchange, contact, penetration, defensive-layer, timing, interposition-resolution, and local consequence rules.
- Add examples, fixtures, author-facing reductions, and cross-references.
- Add a combat-facing intake or handoff when the owner file remains explicit.

Agents must not:

- Finalize HP, Mana, Stamina, or Reserve accounting here.
- Define detailed wound anatomy, healing progression, poison, trauma, or long-term impairment here.
- Define full movement, pathfinding, balance, or terrain traversal here.
- Define threat recognition, Insight, stealth, illusion, salience, or inference here.
- Define team strategy, target priority, formation planning, risk policy, or the next tactical decision here.
- Define Interface formatting, notification cadence, or panel style here.
- Reproduce Luck/Fortune probability-flow, active-control, entropy-cost, or Volatility equations here.
- Resolve motion, perception, resource, injury, or strategy uncertainty inside the combat Luck adapter.
- Redefine Spell Strength, Spell Skill Mastery, Soul Level, Item Quality, Item Rarity, or any other Mechanics tier.
- Use Item Rarity or class rarity as an automatic combat multiplier.
- Use Soul Level as generic physical or magical defense.
- Calculate adaptive evidence, class coupling, recovery integration, scene XP, or level thresholds here.
- Move resource formulas here unless this file becomes the explicit owner.
- Reintroduce class rarity bonus attribute-point cadence.
- Conflate Skill Affinity with Domain; Skill Affinity is progression aptitude, while Domain is power expression/source category.
- Treat Interface readouts as the underlying reality.
- Let simulator output override canon without a contract-level review.
