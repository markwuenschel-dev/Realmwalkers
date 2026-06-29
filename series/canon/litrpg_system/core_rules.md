---
id: core_rules
name: LitRPG System — Core Rules
kind: system
status: canon
---

# LitRPG System — Core Rules

> **Purpose:** Single source of truth for the Dominion Realm LitRPG system architecture. This file defines the interface premise, the top-level mechanical boundaries, prose-use rules, and the routing map agents must follow before expanding any subsystem.
> **Agent rule:** Start here. Do not invent or relocate mechanics until you have checked the owner file named below. If a subsystem does not exist yet, create or use its scaffold; do not bury new rules in an unrelated document.

---

## 0. SSOT / Agent Routing

Core Rules owns the **system boundary**, not every detailed formula. It tells agents where each rule belongs and what must not be conflated.

### Precedence

1. Latest explicit author decision.
2. `core_rules.md` for system-wide architecture, interface premise, routing, terminology, and prose-use rules.
3. The subsystem owner file listed in the taxonomy below.
4. Scaffolds/placeholders for missing subsystems.
5. Older synthesis notes, drafts, or manuscript passages.

If a detail appears in the wrong file, move it to the owner file or leave a cross-reference. Do not duplicate competing versions.

### System Taxonomy

| Parent domain | Detailed field | Owner / scaffold | Subsystem examples |
|---|---|---|---|
| **Resources & Capacity** | Resources & Capacity | `resource_system.md` | HP, mana, stamina, reserve, caps, regen, depletion, crash states |
| **Embodiment & Injury** | Embodiment & Injury | `embodiment_injury.md` *(scaffold)* | anatomy, wounds, trauma, disease, poison, organ damage |
| **Action Systems** | Motion & Positioning | `motion_positioning.md` *(scaffold)* | movement, balance, momentum, terrain traversal |
| **Action Systems** | Combat & Defense | `combat_defense.md` *(scaffold)* | attacks, penetration, armor, shields, timing, tactics |
| **Action Systems** | Power Expression | `power_expression.md` *(scaffold; domains cross-ref `classes.md` / `mechanics.md`)* | spells, domains, rituals, class abilities, resonance |
| **Action Systems** | Perception & Information | `perception_information.md` *(scaffold; Insight baseline summarized here)* | senses, Insight, stealth, illusion, salience, inference |
| **Action Systems / Social & Strategic** | Strategy & Decision Systems | `strategy_decision_systems.md` *(scaffold)* | AI, tactics, risk, counterplay, planning |
| **World Systems** | Space & Environment | `space_environment.md` *(scaffold)* | terrain, portals, zones, boundaries, weather, hazards |
| **Growth Systems** | Progression & Identity | `progression_identity.md` *(scaffold; class taxonomy in `classes.md`; tier ladders in `mechanics.md`)* | levels, classes, skills, soul, species, mastery |
| **Creation & Infrastructure** | Crafting & Materials | `crafting_materials.md` *(scaffold; item/gem ladders in `mechanics.md`)* | gear, alchemy, enchanting, repair, construction materials |
| **Creation & Infrastructure** | Base & Infrastructure | `base_infrastructure.md` *(scaffold)* | buildings, supply lines, wards, defenses, population support |
| **Creation & Infrastructure / Social & Strategic** | Economy & Logistics | `economy_logistics.md` *(scaffold)* | costs, trade, transport, scarcity, production chains |
| **Social & Strategic Systems** | Social & Faction Systems | `social_faction_systems.md` *(scaffold)* | reputation, diplomacy, law, alliances, institutions |
| **Interface & Abstraction** | Interface & Abstraction | `interface_abstraction.md` *(scaffold; foundational premise here)* | stat display, hidden values, diagnostics, system compression |
| **Cross-Cutting** | Luck / Fortune | `luck_fortune.md` | probability-flow bias, Fortune/Misfortune/Volatility, subsystem adapters |

### Existing Owner Files

| File | Owns | Does not own |
|---|---|---|
| `luck_fortune.md` | Canonical Luck/Fortune model, reachability, entropy cost, adapter pattern | Subsystem-specific $z_X$, classifiers, local combat/craft/etc. rules |
| `resource_system.md` | Resource formulas, caps, regen, depletion states, Reserve buffer, resource crash states, XP curve/pacing until split | Class taxonomy, tier ladder prose, combat injury detail |
| `classes.md` | Base classes, specializations, class rarity, class attribute profiles/multipliers, domain terminology firewall | Resource formulas, bonus attribute point cadence, full spell/item/soul ladders |
| `mechanics.md` | Tier ladders: Spell Strength, Item Quality, Item Rarity, Gemstone Quality, Spell Skill Mastery, Soul Level | Resource formulas, class attribute growth, domain taxonomy ownership |
| `system_taxonomy.md` | Expanded routing table, scaffold instructions, unresolved subsystem list | Detailed mechanics |
| Scaffold docs | Boundaries, placeholders, questions, eventual owner content | Contradicting owner files |

---

## 1. Foundational Truth

The interface is not the world.

It is Marcus's implant translating incomprehensible biological, magical, social, and metaphysical structures into data his mind can process. Because Marcus is a gamer and systems thinker, the implant compresses reality into RPG-like terms: levels, attributes, resources, statuses, warnings, and skills.

Other beings in the Realm do not see panels or numbers. Natives experience the same underlying reality through instinct, tradition, ritual, sensation, faith, bloodline memory, training, pain, social practice, and learned judgment.

Marcus sees numbers. This is not superior. It is different.

**Blind spot:** numbers feel like certainty. They are not certainty. The interface is a translation layer built from an incomplete model. It can be wrong, partial, compressed, delayed, misleading, or unable to render something at all.

> **Core test:** The interface can describe a pressure pattern. It cannot guarantee Marcus understands what the pressure means.

---

## 2. System Prominence — Between Lite and Medium

The system is real and progression matters, but it serves character and story rather than becoming the main attraction.

### Early story

Marcus is learning to read the interface. Notifications appear often, sometimes at bad moments, and sometimes with incomplete or confusing information. The system is prominent because he is dependent on it.

### Mid story

Marcus internalizes the interface's ordinary logic. Notifications become less frequent. The system surfaces for meaningful thresholds, unexpected changes, or information Marcus would otherwise miss.

### Late story

A system notification is rare enough to be significant. When the interface appears unexpectedly, it is information in itself.

**Prose test:** If removing a notification from a scene would lose something essential, it belongs. If removing it makes the prose cleaner and the scene stronger, it does not.

---

## 3. Core Terminology

### Attribute

A primary measurable trait. The public nine are:

| Attribute | Governs |
|---|---|
| Strength | Physical force output |
| Agility | Speed, reaction time, balance, directional change |
| Dexterity | Fine motor control, precision, hand/weapon control |
| Constitution | Physical resilience, tissue integrity, toxin/organ stress tolerance |
| Endurance | Sustained effort, fatigue resistance, recovery under exertion |
| Intelligence | Processing, learning efficiency, working memory, structured analysis |
| Wisdom | Judgment, regulation, magical sensitivity/control, disruption resistance |
| Charisma | Force of presence, projection, social/magical imprint |
| Luck | Interface-visible **LCK** — compressed projection of passive Fortune coupling; canonical probability-flow model in `luck_fortune.md` (not a resource weight) |

Two hidden attributes exist for all creatures but normally remain unseen:

| Hidden Attribute | Governs |
|---|---|
| Faith | Divine/covenantal resonance, conviction-pressure, sacred addressability |
| Occult | Demonic/chaotic/forbidden resonance, appetite-pressure, occult addressability |

Faith and Occult are not created by Marcus's transformation. The transformation makes them visible and relevant to his interface.

### Resource

A capacity pool such as HP, Mana, Stamina, or Reserve. Detailed formulas, regen, caps, crash states, and Reserve buffering live in `resource_system.md`.

Quick current pools:

```text
HP      = survivability / immediate bodily integrity under damage
Mana    = usable magical fuel and magical nervous-system tolerance
Stamina = physical exertion capacity
Reserve = deep strain tolerance: interface load, overuse buffering, organ stress, soul/metaphysical strain
```

### Skill Affinity

Skill Affinity is a progression mechanic, not a power type.

- A skill has XP.
- When skill XP reaches 100% for the current level, Skill Affinity is the chance that the skill actually levels.
- On ordinary success, Skill Affinity decreases by an amount tied to natural aptitude.
- On failure, XP resets while Skill Affinity remains.
- Marcus's **Unbound Affinity** inverts this: Skill Affinity increases on successful level-up.

### Domain

Domain means power expression/source category: Fire, Water, Shadow, Death, Psychic, Planar, Celestial, and so on.

**Do not use Affinity to mean Domain.**

Domain taxonomy currently lives in `classes.md` and will eventually be expanded in `power_expression.md`.

### Class

Class is the Realm recognizing a repeated method of becoming.

- **Base Class = method**
- **Specialization = refined role**
- **Domain = power expression/source**
- **Skill Affinity = progression aptitude**
- **Legendary / Mythic / Unique titles = rare evolved expressions, recognitions, mutations, or states**

Full taxonomy lives in `classes.md`.

---

## 4. Resource Summary

The current resource owner is `resource_system.md`.

Core current formula shape:

```text
FinalResource =
(BaseResource + AttributeResource + FeatureResource)
× RaceMod
× ConditionMod
```

For Reserve:

```text
FinalReserve =
(BaseReserve + AttributeReserve + FeatureReserve)
× SoulMultiplier
× RaceReserveMod
× ConditionReserveMod
```

Class influence no longer uses bonus attribute point cadence. Class influence comes through class attribute profiles/multipliers and class feature/resource rules owned by `classes.md` and resolved in `resource_system.md`.

### Attribute pool shorthand

Current base formulas before species, class multipliers, conditions, and special features:

```text
AttributeHP      = 6CON + 2END + 2STR
AttributeMana    = 6INT + 3WIS + CHA
AttributeStamina = 5END + 2CON + STR + AGI + DEX
AttributeReserve = 2CON + 2END + 2WIS + FAI + OCC
```

Luck feeds no base pool. It tilts unresolved margins and should never make impossible outcomes happen. Full model → `luck_fortune.md`.

---

## Luck / Fortune

Luck/Fortune is a cross-cutting uncertainty system, not a standard resource or ordinary attribute. It modifies probability flow through reachable possibility space and is defined in `luck_fortune.md`.

Core invariant: Luck cannot create impossible outcomes. It can only bias plausible outcomes where uncertainty remains.

Subsystem files instantiate the model via local **Luck/Fortune Adapter** sections; they do not redefine Luck.

---

## 5. Insight — Baseline Read Skill

> `Congratulations! You have learned the skill: Insight. Cost: 5 Mana. Focus on a being to discern available information.`

The load-bearing phrase is **available information**.

Insight is not omniscience. At low level it may reveal only the categories the interface can parse and show `????` for the rest.

Example:

```text
Name: ????
Level: ????
Health: 1,890 / 1,890
Mana: ????
Stamina: ????
Race: Human
```

As Insight improves it may reveal condition, injuries, emotional state, resistances, class, affiliations, active effects, hidden resource strain, or threat category. It does not automatically reveal true names, hidden classes, disguised species, or every important fact.

Insight can succeed partially by revealing one exact useful category rather than vague information about everything.

Full future expansion belongs in `perception_information.md`.

---

## 6. Levels and Growth

Levels exist. Their exact meaning remains deliberately incomplete early because Marcus does not yet understand the Realm's scale.

Levels represent accumulated integration with the Realm's underlying systems. A level 1 arrival is barely integrated. A level ??? archdemon or cosmic being has existed through orders of magnitude more pressure, practice, metaphysical weight, and world interaction.

### Attribute growth

Detailed growth currently lives in `resource_system.md`.

Current lock:

- Humans gain **4 free attribute points per level** and have no forced allocation.
- Specialized species may receive forced/favored growth plus fewer free points.
- Exotic/powerful species may receive more total growth but less freedom.
- Class rarity does **not** grant bonus attribute point cadence.

### Class rarity

Class rarity still matters through cost, not bonus points.

Rarity ladder:

```text
Common → Uncommon → Rare → Exceptional → Legendary → Mythic → Unique
```

Rarer classes cost more XP to level and demand more energy from signature abilities. A rare or legendary class is a heavier engine, not a free stat fountain.

---

## 7. Class System Summary

Full taxonomy lives in `classes.md`.

Agent rules:

- Do not add a new base class if it is really a domain-shaped title.
- Do not add a specialization if it is really a base method.
- Do not use class rarity to hand out bonus attribute points.
- Do use class profiles to identify **Prime Attributes** and **Core Attributes**.
- Do treat Legendary/Mythic/Unique as rare outcomes, recognitions, states, or evolved expressions.

### Class attribute multipliers

Classes may define multipliers for class-relevant attributes:

```text
Prime Attribute   = strongest class resonance
Core Attribute    = important support resonance
Secondary         = usable but not defining
Neutral           = ordinary effect
Dissonant         = only if explicitly locked
```

Default values and class profiles live in `classes.md`; resource application lives in `resource_system.md`.

---

## 8. Marcus's Specific Abilities

### Unbound Affinity

*Passive — always active*

Skill Affinities increase instead of decrease upon successful level-up. The increase is random.

Narrative meaning: Marcus does not hit natural ceilings the way most people do, but his growth is unpredictable rather than cleanly planned.

### The Eyes — Two Separate Systems

Marcus has two distinct ocular systems. Never conflate them.

#### Eyes of Meszkhal — Unique item

The Eyes of Meszkhal are an external Unique item gifted by Xazzidiuk. They are not Marcus's interface.

- Costs Mana.
- Copies/accelerates observed combat learning.
- Grants +100% skill XP while active.
- Interprets ambiguity through a demon-biased certainty overlay that can be wrong.
- Competes with Marcus's casting because it bills in Mana.

#### Neurochromatic Eyes — interface

The Neurochromatic Eyes are Marcus's emergent biological/metaphysical interface.

- Perception only.
- Accurate within what they can actually perceive.
- Creates no power by itself.
- Bills in the body/Reserve strain rather than Mana.
- Its danger is not false sight, but accurate perception paired with flawed interpretation.

Full stage breakdown belongs in Marcus's character file and `interface_abstraction.md` when that scaffold matures.

---

## 9. Notification Formatting

Complete formatting belongs in `style/system_message_rules.md`.

Summary:

- Standard: `[ SYSTEM ]` — three lines max.
- Warning: `[ WARNING ]` — three lines max.
- Ability change: `[ INTERFACE ]` — four lines max.
- Stat panel: boxed — early story only, deliberate consultation only.
- Illyri: `[ ILLYRI ]` — her register, not the system's.

The interface is never excited. No exclamation points. No dramatic flair. The drama belongs in the prose around the notification.

---

## 10. What the Interface Cannot Do

The interface cannot:

- Classify something it has never encountered before.
- Accurately render N'hal entities.
- Access true names by default.
- Tell Marcus what a number means in social, tactical, or moral context.
- Override Marcus's instincts when his instincts are wrong.
- Turn partial data into wisdom.

The interface sometimes:

- Mistranslates a Realm concept into an Earth framework.
- Produces a warning with no actionable information.
- Goes silent at a critical moment.
- Displays something that looks like an error but is really a category failure.

When the interface fails or goes silent, that is a story event.

---

## 11. Naming System

True names are metaphysically dangerous. Resistance is governed by Soul Level in `mechanics.md`.

Any soul can be bound or overwritten in principle. Soul Level governs difficulty and cost, not a yes/no immunity switch. Low and average souls are cheap and routine. High souls are prohibitively costly. The top tiers approach but never reach practical immunity.

- Marcus's public name, **Marcus Vye**, is safe to speak.
- His hidden true name is separate and must not be casually assigned.
- The interface cannot read true names by default.
- Illyri's warning means never reveal the hidden true name, not never say "Marcus."

Illyri's distinction:

> **Marcus:** "You're saying I shouldn't tell people my name?"
>
> **Illyri:** "No, idiot. I'm saying you shouldn't tell people your real name."
>
> **Marcus:** "Marcus is my real name."
>
> **Illyri:** "Marcus is one of them."
>
> **Marcus:** "One of them?"
>
> **Illyri:** "You are using the vocabulary of a species that names pets and sandwiches. The distinction is important."

---

## 12. Expansion Rules for Agents

When adding a mechanic:

1. Identify the taxonomy field first.
2. Check the owner file.
3. If no owner exists, use the scaffold.
4. Define what the mechanic owns and what it explicitly does not own.
5. Add cross-references instead of duplicating formulas.
6. Preserve terminology firewalls:
   - Affinity ≠ Domain.
   - Interface ≠ Class.
   - HP ≠ Injury.
   - Soul Level ≠ combat level.
   - Item Quality ≠ Item Rarity.
   - Spell Strength ≠ Spell Skill Mastery.
   - Aether ≠ ordinary domain.
   - Frost/Poison/Arcane ≠ domains.
7. Prefer placeholders over fake precision.
8. Do not add class bonus attribute cadence.

---

## 13. Progression Philosophy

Progression should feel earned through reasoning and cost, not delivered as a prize for reaching a checkpoint.

Victories come from:

- Finding leverage points.
- Understanding what the interface can and cannot tell him.
- Accepting real costs.
- Using skills under pressure.
- Translating numbers back into bodily, social, and tactical reality.
- Learning when the interface is compressing too much.

**Rule:** every significant progression moment should cost something real, require something genuinely clever, or force a meaningful change in identity, relationship, body, or capacity.
