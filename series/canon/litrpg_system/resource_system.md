---
id: resource_system
name: Resources & Capacity — Dominion Realm
kind: system
status: canon
last_updated: 2026-07-14
---

# Resources & Capacity — Dominion Realm

> **Owner field:** Resources & Capacity.
> **Owns:** HP, Mana, Stamina, Reserve, resource caps, resource formulas, regeneration, depletion states, crash states, Reserve buffering, resource-facing attribute growth, and final combat-resource debits.
> **Does not own:** detailed injury anatomy (`embodiment_injury.md`), combat damage/penetration (`combat_defense.md`), class taxonomy and class attribute profiles (`classes.md`), tier ladder prose (`mechanics.md`), XP thresholds/rarity burden/scene XP (`xp_progression_formulas.md`), or Interface display style (`interface_abstraction.md` / style rules).
> **Inputs from:** `classes.md`, `mechanics.md`, `combat_defense.md`, `embodiment_injury.md`, conditions/environment owners.
> **Outputs to:** `combat_defense.md`, `xp_progression_formulas.md`, `interface_abstraction.md`, and any subsystem gated by affordability or depletion.

---

## 1. Core Thesis

The interface translates real biological, magical, and metaphysical conditions into numbers.

The numbers are useful, but they are not the underlying reality.

```text
The UI is a translation layer, not the world itself.
```

---

## 2. Four Primary Resources

| Resource | Represents | Zero state |
|---|---|---|
| **HP** | Immediate survivability, trauma tolerance, bodily integrity under damage | Death, dying, or catastrophic incapacitation |
| **Mana** | Usable magical fuel and magical nervous-system tolerance | Mana crash; magical numbness, sensory distortion, failed casting |
| **Stamina** | Physical exertion capacity, breath, muscular output, coordination | Collapse, pass out, or hard physical stop |
| **Reserve** | Deep strain tolerance: interface load, overuse buffering, organ stress, soul/metaphysical routing | Interface crash, severe organ stress, seizure-equivalent, soul strain, metaphysical injury risk |

Important distinction:

```text
HP ≠ Injury
```

A character can have high current HP and still suffer an injury condition that reduces max HP, movement, regeneration, concentration, or organ function.

---

## 3. Attribute Abbreviations

| Abbreviation | Attribute |
|---|---|
| STR | Strength |
| AGI | Agility |
| DEX | Dexterity |
| CON | Constitution |
| END | Endurance |
| INT | Intelligence |
| WIS | Wisdom |
| CHA | Charisma |
| LUCK | Luck — Interface projection of passive Fortune coupling; **does not feed resource max formulas** (see `luck_fortune.md`, `interface_abstraction.md`) |
| FAI | Faith |
| OCC | Occult |

Faith and Occult are true hidden attributes for all creatures, including non-sapient animals. Baseline humans normally have:

```text
FAI = 5
OCC = 5
```

They are usually hidden until an interface, ritual, species trait, divine/occult exposure, or special condition makes them visible.

---

## 4. Canonical Resource Formula

Class rarity no longer grants bonus attribute point cadence. Class influence enters resource math through **class attribute multipliers** defined in `classes.md`.

### General formula

```text
FinalResource_R =
(BaseResource_R + AttributeResource_R + FeatureResource_R)
× RaceMod_R
× ConditionMod_R
```

### Attribute resource term

```text
AttributeResource_R =
Σ(Attribute_A × ResourceWeight_R,A × ClassAttributeMultiplier_C,A)
```

Where:

| Term | Meaning |
|---|---|
| `Attribute_A` | Current raw attribute value. |
| `ResourceWeight_R,A` | How strongly that attribute feeds the resource. |
| `ClassAttributeMultiplier_C,A` | Class multiplier for that attribute: Prime, Core, Secondary, Neutral, or Dissonant. Defined by `classes.md`. |
| `FeatureResource_R` | Explicit resource feature, milestone, item, blessing, curse, or story effect. Not a class cadence bonus. |
| `RaceMod_R` | Species/body scaling. |
| `ConditionMod_R` | Injury, illness, curse, buff, exhaustion, suppression, environment. |

### Reserve formula

```text
FinalReserve =
(BaseReserve + AttributeReserve + FeatureReserve)
× SoulMultiplier
× RaceReserveMod
× ConditionReserveMod
```

Reserve does not use an ordinary class rarity bonus. A class or interface may have a named feature that affects Reserve, but that feature must be explicit.

---

## 5. Attribute Resource Weights

Base weights before class multipliers, race modifiers, conditions, and explicit features:

```text
AttributeHP      = 6CON + 2END + 2STR
AttributeMana    = 6INT + 3WIS + CHA
AttributeStamina = 5END + 2CON + STR + AGI + DEX
AttributeReserve = 2CON + 2END + 2WIS + FAI + OCC
```

Baseline human with all visible and hidden attributes at 5, before class multipliers:

| Resource | Attribute value |
|---|---:|
| HP | 50 |
| Mana | 50 |
| Stamina | 50 |
| Reserve | 40 |

Luck feeds no base pool. It tilts unresolved margins and never makes impossible outcomes happen. Canonical model → `luck_fortune.md`.


`LUCK` is an Interface-facing projection of passive Fortune coupling, not the underlying probability-flow state. It may support an explicitly named class feature or local Luck adapter, but it is not a `ResourceWeight`, does not receive a pool multiplier, and cannot be converted into a universal combat or loot percentage.

---

## 6. Class Attribute Multipliers

Class profiles live in `classes.md`.

Default values:

| Attribute role | Default multiplier | Meaning |
|---|---:|---|
| Prime | 1.15 | Defining class resonance. The class gets more out of this attribute. |
| Core | 1.08 | Important support resonance. |
| Secondary | 1.03 | Useful but not defining. Optional; use only if class profile names it. |
| Neutral | 1.00 | Normal value. |
| Dissonant | 0.95 | Only when explicitly locked; avoid casual penalties. |

Agent rules:

- Multipliers are not extra attribute points.
- Multipliers do not retroactively change prior level allocations.
- Rarity does not automatically increase multiplier size.
- Legendary/Mythic/Unique classes may define custom multipliers only in their class profile.
- If a class profile does not define a multiplier for an attribute, treat it as Neutral.
- Do not stack multiple active classes casually; multiclass rules need their own future section.

### Worked shorthand

A Warrior with STR 10, CON 10, END 10 and all three marked Prime would calculate those attributes as 11.5 for class-resource purposes before rounding conventions.

Rounding convention for tables:

```text
Round final displayed resource down unless a feature says otherwise.
```

---

## 7. Soul Multiplier

Soul Level modifies Reserve as a multiplier, not a flat bonus. The tier ladder is owned by `mechanics.md`.

Default tuning:

| Soul Level | Reserve Multiplier |
|---|---:|
| Fractured | 0.90 |
| Faint | 0.94 |
| Weak | 0.96 |
| Lesser | 0.98 |
| Common | 1.00 |
| Strong | 1.04 |
| Luminous | 1.07 |
| Radiant | 1.10 |
| Brilliant | 1.13 |
| Resplendent | 1.16 |
| Exalted | 1.19 |
| Transcendent | 1.22 |
| Divine | 1.25 |
| Absolute | 1.30 |

This keeps Soul Level relevant without making ordinary mortal Reserve explode.

---

## 8. Regeneration Formulas

### HP regeneration

Safe rest, per hour:

```text
SafeRestHPRegen = (MaxHP × 0.03) + CON/2
```

Light rest, per hour:

```text
LightRestHPRegen = (MaxHP × 0.015) + CON/4
```

Active travel, per hour:

```text
ActiveTravelHPRegen = MaxHP × 0.005
```

Combat:

```text
CombatHPRegen = 0
```

### Mana regeneration

Meditation, per minute:

```text
MeditationManaRegen = (MaxMana × 0.05) + WIS/5
```

Calm noncombat, per minute:

```text
CalmManaRegen = (MaxMana × 0.02) + WIS/10
```

Active travel, per minute:

```text
ActiveManaRegen = MaxMana × 0.01
```

Combat stress, per minute:

```text
CombatManaRegen = MaxMana × 0.005
```

Sustained channeling may suppress mana regeneration:

```text
ChannelingManaRegen = ManaRegen × ChannelSuppression
0.00 ≤ ChannelSuppression ≤ 0.50
```

### Stamina regeneration

Full rest, per minute:

```text
FullRestStaminaRegen = (MaxStamina × 0.12) + END/2
```

Catching breath, per minute:

```text
CatchingBreathStaminaRegen = (MaxStamina × 0.08) + END/3
```

Light movement, per minute:

```text
LightMovementStaminaRegen = MaxStamina × 0.03
```

Combat, when not actively exerting, per minute:

```text
CombatStaminaRegen = MaxStamina × 0.01
```

Heavy exertion creates active drain rather than passive regen.

### Reserve regeneration

Deep sleep, per hour:

```text
DeepSleepReserveRegen = (MaxReserve × 0.08) + WIS/4
```

Meditation / trained recovery, per hour:

```text
MeditationReserveRegen = (MaxReserve × 0.05) + WIS/5
```

Ordinary rest, per hour:

```text
OrdinaryRestReserveRegen = MaxReserve × 0.03
```

Active travel, per hour:

```text
ActiveReserveRegen = MaxReserve × 0.01
```

Combat / active interface strain:

```text
CombatReserveRegen = 0
```

Optional recovery condition:

| Recovery condition | Modifier |
|---|---:|
| Safe, calm, sleeping | 1.00 |
| Comfortable / emotionally supported | 1.10 |
| Meditation / trained recovery | 1.15 |
| Anxious / hypervigilant | 0.75 |
| Grief / severe stress | 0.50 |
| Panic / nightmare / hostile environment | 0.25 |
| Active corruption / soul wound | 0.00 |

---

## 9. Depletion States

### HP

| HP State | Meaning |
|---:|---|
| 100–75% | Healthy / combat capable |
| 74–50% | Bloodied, bruised, slowed |
| 49–25% | Impaired, pain penalties, concentration harder |
| 24–1% | Critical, unstable, likely injury conditions |
| 0% | Death, dying, or catastrophic incapacitation |

### Mana

| Mana State | Meaning |
|---:|---|
| 100–50% | Normal casting |
| 49–25% | Headache, sensory pressure, spell inefficiency |
| 24–10% | Migraine, nausea, hand tremor, poor spell control |
| 9–1% | Mana-starved, feedback pain, failed casting risk |
| 0% | Mana crash: confusion, migraine, vomiting, sensory distortion, magical numbness, temporary inability to cast; not always unconsciousness |

### Stamina

| Stamina State | Meaning |
|---:|---|
| 100–50% | Normal exertion |
| 49–25% | Heavy breathing, slower reactions |
| 24–10% | Shaking, poor coordination, weak grip |
| 9–1% | Collapse risk |
| 0% | Collapse, pass out, or helpless exhaustion |

Zero Stamina is a hard physical stop.

### Reserve

| Reserve State | Meaning |
|---:|---|
| 100–50% | Stable interface/system strain tolerance |
| 49–25% | Strain signs: eye pain, pressure, tremors, emotional bleed |
| 24–10% | Interface instability, backlash risk |
| 9–1% | System warning, soul/body routing failure |
| 0% | Interface crash, severe organ stress, seizure-equivalent, soul strain, metaphysical injury risk |

Zero Reserve is the worst non-HP crash.

---

## 10. Reserve Buffer Rule

Reserve begins buffering when Mana or Stamina falls below 20%.

```text
If Mana < 20%, forced casting consumes Reserve.
If Stamina < 20%, forced exertion consumes Reserve.
```

Forced overuse may borrow from Reserve:

```text
1 Reserve = 5 Mana deficit
1 Reserve = 5 Stamina deficit
```

Zero Reserve is a hard stop. Borrowing past zero requires catastrophic injury or story-level consequence.

This is the canonical Reserve conversion rule. Do not replace it with quadratic Reserve-strain accounting. A quadratic expression may be used only as an author-facing risk/severity estimator, not as the resource debit.

---

## 11. Reserve Depletion vs. Reserve Injury

```text
ReserveDepletion = temporary strain
ReserveInjury    = actual damage to the routing system
```

| Type | Examples | Recovery |
|---|---|---|
| Reserve depletion | Eye strain, headache, pressure fatigue, post-interface crash | Rest, meditation, sleep, stabilization |
| Reserve injury | Blood tears, neural scarring, soul bruise, damaged channels, severance backlash | Treatment, time, specialized healing, story intervention |

Reserve injury can reduce max Reserve until treated.

---

## 12. Injury Interface Boundary

This file may record simple resource consequences of injury. It does not own detailed anatomy, disease, poison, trauma, organ damage, or long-term wound modeling.

For detailed injury rules, use `embodiment_injury.md`.

Example shorthand allowed here:

```text
Cracked Rib:
Immediate HP Damage: -6
Temporary Max HP: -8 until healed
HP Regen: -20%
Stamina Regen: -15%
Functional Penalty: pain when sprinting, climbing, or casting under pressure
```

---

## Combat Resource Intake

This section receives combat-facing resource outputs from `combat_defense.md`.

Resources & Capacity owns final HP, Mana, Stamina, Reserve, regeneration, depletion states, crash states, and Reserve buffering.

Combat & Defense may output pressure values, but this file resolves final resource state.

Plain rule:

```text
combat_defense.md produces resource pressure.
resource_system.md decides final resource state.
```

---

### Combat Resource Handoff

Combat & Defense may send:

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

This file resolves:

$$
\begin{aligned}
R_{i,t+1}
&=
\Phi_{\mathrm{resource}}
\bigl(
R_{i,t},
H_i^{\mathrm{candidate}},
K_{\mathrm{sta},i},
K_{\mathrm{mana},i},
K_{\mathrm{reserve},i},
\mathrm{forcedAction}_i,
\mathrm{context}_i
\bigr)
\end{aligned}
$$

Where:

| Symbol                 | Meaning                                                       |
| ---------------------- | ------------------------------------------------------------- |
| $R_{i,t}$              | current resource state for actor $i$                                        |
| $H_i^{\mathrm{candidate}}$ | actor-keyed HP-damage candidate from Combat; this file resolves final HP state                                  |
| $K_{\mathrm{sta},i}$   | actor-keyed Stamina pressure                                              |
| $K_{\mathrm{mana},i}$  | actor-keyed Mana pressure                                                 |
| $K_{\mathrm{reserve},i}$ | actor-keyed Reserve-pressure signal                                       |
| `forcedAction`         | whether the character keeps acting below safe resource levels |
| `context`              | injury, environment, stress, power use, conditions            |

---

### Reserve Pressure Boundary

Combat & Defense may output:

```text
Reserve-pressure risk: none / low / moderate / high / catastrophic
```

This file owns the canonical Reserve conversion:

```text
If Mana < 20%, forced casting consumes Reserve.
If Stamina < 20%, forced exertion consumes Reserve.

1 Reserve = 5 Mana deficit
1 Reserve = 5 Stamina deficit
```

Plain rule:

```text
Reserve pressure is a warning from combat.
Reserve conversion is resolved here.
```

---

### Resource Output Back to Combat

After resolving resource state, this file may output:

```text
ResourceCombatOutput:
  currentHPState
  currentManaState
  currentStaminaState
  currentReserveState
  depletionState
  crashState
  forcedActionAllowed
  regenerationSuppressed
  combatCapacityChanged
  routedFrom: resource_system.md
  routedTo: combat_defense.md
```

Combat & Defense may consume those values for future exchanges, especially:

```text
available defense modes
recovery debt
barrier maintenance
forced exertion
collapse risk
tempo loss
```

Plain rule:

```text
Resources change what future combat branches are reachable.
```


## 13. Eyes of Meszkhal Resource Costs

The Eyes of Meszkhal currently have one active mode.

Activation:

```text
BaseActivation = 20 Mana
```

Upkeep:

```text
BaseUpkeep = 1% MaxMana per second
```

Cost scaling by Eyes Mastery:

```text
Activation_n = 20 × 0.9^(n-1)
Upkeep_n     = 1.0% × 0.9^(n-1)
```

Where `n = Eyes Mastery`.

Eyes Mastery uses hidden attunement with scene-based thresholds, not visible XP. Eyes Mastery can level mid-fight. Eyes Mastery 2 gives cost reduction only.

### Eyes cost table at 50 Max Mana

| Eyes Mastery | Activation | Upkeep | Mana/sec at 50 Max Mana | Continuous use after activation |
|---:|---:|---:|---:|---:|
| 1 | 20.00 | 1.000%/sec | 0.500/sec | 60 sec |
| 2 | 18.00 | 0.900%/sec | 0.450/sec | 71 sec |
| 3 | 16.20 | 0.810%/sec | 0.405/sec | 83 sec |
| 4 | 14.58 | 0.729%/sec | 0.365/sec | 97 sec |
| 5 | 13.12 | 0.656%/sec | 0.328/sec | 112 sec |
| 6 | 11.81 | 0.590%/sec | 0.295/sec | 129 sec |
| 7 | 10.63 | 0.531%/sec | 0.266/sec | 148 sec |
| 8 | 9.57 | 0.478%/sec | 0.239/sec | 169 sec |
| 9 | 8.61 | 0.430%/sec | 0.215/sec | 193 sec |
| 10 | 7.75 | 0.387%/sec | 0.194/sec | 218 sec |

Reserve appears on Marcus's UI after Eyes backlash, not immediately.

---

## 14. Insight Resource Cost

Insight costs:

```text
InsightCost = 5 Mana
```

Perception behavior belongs in `perception_information.md` once expanded. Core summary remains in `core_rules.md`.

---

## 15. Growth Rules

### Attribute points

Every creature gains attribute points every level.

Humans:

```text
HumanGrowth = 4 free attribute points / level
```

Most sapient non-human species:

```text
4–5 attribute points / level
```

Powerful or rare species:

```text
6 attribute points / level
```

Humans have no forced growth, but less total growth. Non-human species often have forced or semi-forced biological growth.

### Removed rule

Do **not** use class rarity bonus attribute point cadence.

Class rarity affects XP burden, energy burden, and possible class-profile sophistication. It does not hand out recurring bonus points after a fixed number of class-held levels.

### Species growth

Species templates may define:

```text
FreePoints
ForcedPoints
FavoredAttributes
ForbiddenOrDissonantGrowth
MaturityStageGrowth
```

Monsters use:

```text
Creature = SpeciesTemplate + MaturityStage + Level + Traits
```

Maturity stages can change point distribution and point quantity.

---

## 16. Progression and XP Boundary

`xp_progression_formulas.md` is the authoritative owner of:

* class-level XP thresholds,
* class-rarity prevalence and self-information,
* embodied rarity burden,
* per-scene adaptive evidence,
* combat XP adaptation,
* recovery integration,
* progression pacing math,
* the class-rarity energy-cost link.

This file supplies resource-facing inputs to progression—final HP loss, Mana/Stamina/Reserve depletion, crash state, forced overuse, and recovery state—but does not calculate XP.

```text
Resource state is evidence consumed by progression.
Resource state is not itself an XP award.
```

Current progression-facing class-rarity sequence, for cross-reference only:

```text
Common → Uncommon → Rare → Epic → Fabled → Legendary → Mythic → Unique
```

Do not restore the retired flat model:

```text
BaseXP(L) × fixed class-rarity multiplier
```

Do not use `Exceptional` as the current class-XP rarity name. It remains valid on unrelated Mechanics ladders.

Narrative level targets and class-acquisition timing belong in `xp_progression_formulas.md`, `classes.md`, character dossiers, and book planning as appropriate. They are not resource formulas.

---

## 17. Threat Readout

Threat readout is a reference frame, not exact encounter math.

```text
ThreatRatio = EnemyHP / ObserverHP
```

| Threat ratio | Interpretation |
|---:|---|
| 0.5–1.5x | Peer range |
| 2–4x | Dangerous if trained |
| 5–10x | Do not trade blows |
| 10–25x | Class/race/level mismatch |
| 25x+ | Solve sideways or run |
| 50x+ | Environmental threat, boss, monster, or scripted death risk |

A creature with 1,890 HP against Level 1 Marcus at 50 HP:

```text
1890 / 50 = 37.8
```

This means Marcus is massively outclassed and cannot trade damage. It does not automatically mean apex cosmic being.

---

## 18. Class Template Snapshot

Class templates now express **attribute multiplier profiles**, not bonus attribute point cadence.

Detailed profiles live in `classes.md`. This snapshot exists only for resource-facing shorthand.

| Class | Rarity | Prime Attributes | Core Attributes | Primary resource shape |
|---|---|---|---|---|
| Warrior | Common | STR, CON, END | AGI, DEX | HP / Stamina durability |
| Mage | Common | INT, WIS | CHA, DEX, END | Mana / control |
| Rogue | Common | DEX, AGI | INT, WIS, END | Stamina / burst exploitation |
| Scout | Common | AGI, END, WIS | DEX, INT, CON | Stamina / routes / threat-reading |
| Healer | Common | WIS, INT | CHA, CON, DEX | Mana / stabilization |
| Warden | Uncommon | CON, WIS, END | STR, CHA, FAI | HP / Reserve / boundary endurance |
| Psion | Rare | WIS, CHA, INT | DEX, END, OCC | Mana / Reserve pressure |
| Adventurer | Common | END, WIS, LUCK | STR, AGI, DEX, CON, INT | Flexible survival |


`LUCK` in this snapshot is an Interface-facing class/profile label. Because no resource formula includes a LUCK weight, marking it Prime does not increase a primary resource pool unless an explicit feature defines a separate resource effect.

---

## 19. Combat-Standing Boundary

Book-specific combat hierarchy, character signatures, scene constraints, and opponent classification are owned by `combat_defense.md`, character dossiers, and book planning.

This file contributes only the resource state that helps make those standings true: pool sizes, depletion, Reserve buffering, crash risk, and resource-facing class profiles. It must not become a second character-combat canon file.

---

## 20. Working Locks / Do-Not-Drift Notes

- Reserve is general, not Marcus-specific.
- Reserve appears on Marcus's UI after Eyes backlash.
- Eyes keep 20 Mana activation and 1% MaxMana/sec upkeep.
- Eyes use the 10%-remaining scaling formula.
- Eyes Mastery 2 is cost reduction only.
- Eyes Mastery can level mid-fight.
- Mana crash is not automatically unconsciousness.
- Zero Stamina is collapse/pass-out/hard stop.
- Zero Reserve is the worst non-HP crash.
- Name erasure does not kill by default.
- Monsters have levels; many also have maturity stages.
- Maturity stages can alter point distribution and point quantity.
- Humans get 4 fully free points per level and no forced growth.
- Most sapient species get 4–5 points per level, often partially forced.
- Powerful/rare species can get 6 points per level.
- Class rarity bonus points are removed.
- XP thresholds and rarity burden are owned by `xp_progression_formulas.md`; no flat rarity multiplier table lives here.
- Classes use Prime/Core attribute multipliers instead of bonus point cadence.

---

## Luck/Fortune Adapter

This subsystem uses the canonical model from `luck_fortune.md` and defines resource-local uncertainty only.

### Local Possibility State

General resource uncertainty:

$$
\begin{aligned}
z_{\mathrm{resource}}
&=
\bigl(
\mathrm{currentHP},
\mathrm{currentMana},
\mathrm{currentStamina},
\mathrm{currentReserve},
\mathrm{deficitLoad},
\mathrm{regenStability},
\mathrm{crashRisk},
\mathrm{recoveryMargin}
\bigr)
\end{aligned}
$$

Reserve-backlash uncertainty:

$$
\begin{aligned}
z_{\mathrm{reserve}}
&=
\bigl(
\mathrm{strainLoad},
\mathrm{organStress},
\mathrm{manaDeficit},
\mathrm{staminaDeficit},
\mathrm{interfaceCoherence},
\mathrm{soulShear},
\mathrm{recoveryMargin}
\bigr)
\end{aligned}
$$

### Baseline Drift

Without Luck, trajectories follow the depletion, regeneration, Reserve-buffering, forced-overuse, and crash rules owned by this file.

### Uncertainty / Diffusion

Uncertainty remains in marginal crash timing, failure-basin selection, overchannel side effects, recovery complications, and severity tails when more than one result remains causally reachable.

### Favorability Function

Favorability is actor-specific and does not include larger maximum pools:

$$
\begin{aligned}
U_{\mathrm{resource}}^{(a)}(z,t)
&=
\mathrm{ResourceFavorabilityForActor}
\bigl(
 a,z,t
\bigr)
\end{aligned}
$$

### Luck Interaction

Fortune and Misfortune bias drift among reachable crash/recovery states. Volatility widens diffusion and tail risk. This file supplies its local adapter contract to `luck_fortune.md`; it does not copy the canonical flow equations.

### Reachability Constraints

Luck cannot create extra HP, Mana, Stamina, or Reserve; erase resource debt; bypass the canonical Reserve conversion; prevent deterministic collapse after no margin remains; turn forced overuse into free power; or increase maximum pools.

### Result Classifier

$$
\begin{aligned}
\mathrm{Result}_{\mathrm{reserve}}
&=
\mathrm{Classify}_{\mathrm{reserve}}
\bigl(
 z_{\mathrm{reserve,final}}
\bigr)
\end{aligned}
$$

Examples: mild crash, clean collapse, blackout, organ strain, Interface failure, temporary Reserve injury, permanent Reserve injury, or soul strain.

### Forbidden Simplifications

Do not use Luck as free resources, a maximum-pool multiplier, a substitute for resource accounting, or a way to resolve injury anatomy, combat contact, perception, motion, or strategy inside Resources.

### Owner Handoff

```text
ResourceLuckAdapterInput:
  actor
  localPossibilityState
  baselineReachableCrashOrRecoverySet
  unresolvedResourceCoordinates
  favorabilityPerspective
  reachabilityGate
  classifier
  routedFrom: resource_system.md
  routedThrough: luck_fortune.md
```

Plain rule:

```text
Luck may bias which reachable failure basin captures the actor.
It cannot make an unaffordable action affordable.
```
