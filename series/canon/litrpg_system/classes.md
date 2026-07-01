---
id: classes
name: Class System
kind: system
status: canon
---
# Class System — Dominion Realm

> **Status:** Canon · working draft
> **Model:** Base class = *method* · Specialization = *refined role* · Domain = *power expression/source* · Skill Affinity = *skill-progression aptitude* · Epic/Fabled/Legendary/Mythic/Unique titles = *rare evolved expression, title, or state*.
> **Rarity ladder:** Common → Uncommon → Rare → Epic → Fabled → Legendary → Mythic → Unique. **Rarer classes cost more XP _and_ more energy per level.** XP thresholds and scene XP formulas live in `xp_progression_formulas.md`; do not duplicate full XP equations here.
> **Classes are earned through behavior, not selected.** The Realm recognizes what someone repeatedly becomes.
> **Attribute model:** Classes do not grant bonus attribute-point cadence. They define Prime/Core attribute multipliers that shape how efficiently relevant attributes express through that class.
> **Cast quick-ref:** Marcus = Mage → **Realmwalker** (Legendary); Serra = **Warrior** → **Worldbreaker** (Mythic; Book-2 direction); Seb = Warrior → **Reaver** (specialization, broker grants it *early*); Mara = **Psion** (Rare) → **Arbiter** *(placeholder specialization; Book-2 direction; rename likely because of Marcus's Arbiter Aspect)*; Mathias = **Scout** → **Emissary** *(specialization; Book-2 direction)*; Brent = **Warden** → **Reckoner** *(specialization; Book-2 direction)*.

---

## Read-This-First Rules

The clean model:

**Base classes = method.**  
**Specializations = refined role.**  
**Domains = power expression/source.**  
**Skill Affinity = progression aptitude, not power type.**  
**Epic/Fabled/Legendary/Mythic/Unique titles = rare evolved expression, recognition, mutation, or state.**

### Terminology Firewall

Do **not** use **Affinity** to mean Fire, Shadow, Death, Planar, etc.

- **Domain** means the supernatural power-source or expression category: Fire, Water, Psychic, Celestial, Void, and so on.
- **Skill Affinity** means a skill's progression aptitude: the chance a skill levels after reaching 100% XP.
- **Skill Affinity affects progression only.** It does not directly increase combat damage, spell strength, class power, or domain output.
- On ordinary skill level-up success, Skill Affinity decreases; on failure, skill XP resets while Skill Affinity remains. Marcus's **Unbound Affinity** inverts this by increasing Skill Affinity on successful level-up.

### Layer Discipline

Class taxonomy does not own the whole power system. A full Realm build resolves through:

**Skills · Base Class · Specialization · Multiclass · Interface · Domain · Items**

- **Skills** are learned/earned abilities.
- **Base Class** is the broad method the Realm recognizes.
- **Specialization** is the sharpened role inside that method.
- **Multiclass** is secondary class access, where earned.
- **Interface** is a personal biological/metaphysical power architecture.
- **Domain** is the power-source/expression category.
- **Items** are external tools, artifacts, Uniques, and relics.

A class and an interface can align, conflict, or partially overlap, but they are not the same layer. Serra's **Warrior** class and **Pressure-Severance Interface** both point forward, but Warrior is the Realm's method-label while Pressure-Severance is her personal substrate architecture. Marcus's **Realmwalker** class is not the same thing as his Neurochromatic Eyes or the Eyes of Meszkhal.

### XP Progression Formula Ownership

Class taxonomy lives in this file. XP progression math lives in `xp_progression_formulas.md`.

Use `xp_progression_formulas.md` as the canonical owner for:

- level-to-level XP thresholds
- prevalence-derived rarity information
- rarity emergence across levels
- per-scene XP calculation
- combat XP adaptation
- medical / physical / neural / resource strain terms
- recovery and integration effects
- nonzero class-method coupling
- interface and domain coupling for XP gain

This file owns what classes **are**:

- base class = method
- specialization = refined role
- domain = power expression/source
- skill affinity = skill-progression aptitude
- rarity ladder and class taxonomy
- class profiles and class-specific method notes

Do not duplicate the full XP equations here. Cross-reference `xp_progression_formulas.md` instead.

#### Current Rarity Ladder for XP Tables

The current XP progression model uses this rarity ladder:

```text
Common → Uncommon → Rare → Epic → Fabled → Legendary → Mythic → Unique
```

`Exceptional` has been retired/replaced by `Epic` unless a later author decision restores it.

Current prevalence assumptions:

| Rarity | Prevalence relative to Common | Meaning |
|---|---:|---|
| Common | 1 | Widely repeatable method basin. |
| Uncommon | 1 / 10 | Regularly seen, but requires more specialized aptitude, training, or circumstance. |
| Rare | 1 / 100 | Dependent on unusual talent, institutions, rituals, or exposure. |
| Epic | 1 / 1,000 | Extraordinary but still socially legible; replacement name for old `Exceptional`. |
| Fabled | 1 / 100,000 | Known through stories, reports, institutions, and uncertain records; bridge tier before Legendary. |
| Legendary | 1 / 1,000,000 | History-shaping, world-significant, and institution-altering. |
| Mythic | 1 / 100,000,000 | Principle-scale, barely repeatable, and often not fully understood. |
| Unique | one per cosmic cycle | One-of-one across a cosmic cycle or causal impossibility. |

Class rarity does **not** directly grant attribute points and does **not** act as a flat power multiplier.

Class rarity affects:

- XP threshold burden through prevalence-derived self-information
- energy burden
- difficulty of clean embodiment
- rarity of the class basin / method pattern
- scene XP interpretation through class-method coupling

Class rarity must not be reintroduced as recurring bonus attribute-point cadence.


### Class Attribute Multiplier Firewall

Class rarity no longer grants recurring bonus attribute points. Do not reintroduce any rule where class rarity grants a recurring extra attribute point after a fixed number of class-held levels.

Class influence now enters through **class attribute profiles**.

Each class may identify:

| Attribute role | Default multiplier | Meaning |
|---|---:|---|
| **Prime** | ×1.15 | Defining attributes. The class gets more out of them. |
| **Core** | ×1.08 | Important support attributes. |
| **Secondary** | ×1.03 | Useful but not defining; optional. |
| **Neutral** | ×1.00 | Normal expression. |
| **Dissonant** | ×0.95 | Only when explicitly locked. Avoid casual penalties. |

Rules:

- Multipliers are **not extra points**.
- Multipliers do **not** replace ordinary level/species attribute growth.
- Multipliers do **not** retroactively change earlier levels.
- Rarity does **not** automatically make multipliers larger; rarity burden belongs to XP/energy progression in `xp_progression_formulas.md`.
- Legendary/Mythic/Unique classes may define custom multipliers only in their profile.
- Resource application lives in `resource_system.md`; class profiles live here.
- If a class does not name an attribute, treat it as Neutral.
- A specialization may promote one Core attribute to Prime, add a Secondary/Core attribute, or define a narrow feature-specific multiplier, but it must not become hidden bonus-point cadence.

Class profile schema:

```text
Class:
Rarity:
Method:
Prime Attributes:
Core Attributes:
Secondary Attributes:
Multiplier Overrides:
Resource Shape:
Boundary Notes:
```


### Aetherfall Carryover Rule

Nothing from **Aetherfall: Genesis** carries over mechanically.

Aetherfall roles provide:

- tactical habits
- muscle-memory patterns from VR embodiment
- role expectations
- battlefield instincts
- preferred problem-solving angles

They do **not** provide Realm skills, spells, passives, levels, or class abilities. Those must be learned, unlocked, taught, discovered, or earned in the Realm.

Therefore:

- Serra's Aetherfall assassin role helps explain her disruption instincts, but it does not grant Rogue/Assassin abilities in the Realm.
- Brent's Aetherfall healer/support role does not make him a Realm Healer. His Realm direction is **Warden → Reckoner**.
- Marcus's Aetherfall Aspect role shapes his tactical thinking, but his Realm class path is **Mage → Realmwalker**.

### Spell Mastery Firewall

Spell Strength and Spell Skill Mastery are independent ladders owned by `mechanics.md`.

- **Class** answers: how does this person act?
- **Domain** answers: what kind of power expresses through that action?
- **Spell Skill Mastery** answers: how technically developed is the relevant spell skill?
- **Spell Strength** answers: how complete and forceful is this manifestation?
- **Skill Affinity** answers: how naturally does that skill progress?

A Fire-domain Mage with poor Fire mastery is not automatically superior to a non-Fire specialist with higher mastery in the relevant discipline. Domain is not execution. Skill Affinity is not execution. Class is not execution.

---

# Domain Taxonomy

Domains are grouped by rarity, scope, and metaphysical depth. They are not classes and not Skill Affinities.

## Elemental Domains

The eight foundational domains:

**Fire · Water · Air · Earth · Light · Life · Shadow · Death**

These are the most widely legible and most commonly encountered power expressions. They can appear in simple spells, martial techniques, rituals, items, monsters, environments, and cultural magic systems.

## Primordial Domains

Structural, deep-system domains:

**Runic · Psychic · Spirit · Blood · Force**

These govern deeper patterns: language-as-structure, mind/will/perception, soul-presence/ghost-continuity, life-blood/flesh/inheritance, and force/pressure/impact/vector mechanics.

## Cosmic Domains

Reality-scale domains:

**Chaos · Celestial · Void · Planar · Time · Entropy · Eldritch**

These operate closer to law, meaning, scale, impossibility, cosmic identity, and reality failure.

## Aether

**Aether is not an ordinary domain.**

Aether is the higher-order synthesis of the eight Elemental Domains in harmony. It should not casually appear as an ordinary domain, ordinary school, or common class flavor. Aether paths belong in Epic, Fabled, Legendary, Mythic, Unique, item, interface, or world-event territory unless a specific culture or artifact has a carefully bounded partial expression.

## Disciplines, Schools, and Recipes

Some familiar fantasy powers are not Domains.

- **Arcane** is spellcraft: mana structure, metamagic, magical syntax, spell matrices, counterspells, wards, and casting theory. Arcane is a discipline, not a foundational domain.
- **Frost** is a discipline/school/recipe expression, not a domain. A plausible recipe is Water + Air + thermal extraction, though cultures may model it differently.
- **Poison** is a discipline/school/recipe expression, not a domain. A plausible recipe is Life + Death + Blood/Alchemy, though cultures may model it differently.

This prevents drift like Frost Affinity, Poison Affinity, or Arcane Affinity unless the phrase is explicitly in-world slang and not system canon.

---

# Class Attribute Profiles

These profiles define default attribute resonance for base classes. They are tuning scaffolds: specific cultures, species, specializations, items, and interfaces can alter the expression, but agents should start here.

## Common Class Profiles

| Base Class      | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                        |
| --------------- | ---------------- | --------------- | -------------------- | ------------------------------------------------------------------------------ |
| **Warrior**     | STR, END         | CON, AGI, DEX   | WIS, CVN             | Stamina-heavy direct confrontation; force, endurance, weapons, commitment.     |
| **Fighter**     | STR, CON         | END, DEX        | AGI, WIS             | Practical combat fundamentals; grit, weapons, brawling, survival.              |
| **Rogue**       | DEX, AGI         | WIS, INT        | END, MYS             | Precision, misdirection, weak-point exploitation, concealed movement.          |
| **Mage**        | INT, WIS         | MYS, CHA        | DEX, END             | Mana-heavy supernatural manipulation through knowledge and shaped intent.      |
| **Hunter**      | WIS, DEX         | AGI, END        | STR, INT             | Tracking, pursuit, targeting, terrain use, kill-window recognition.            |
| **Scout**       | AGI, WIS         | END, DEX        | INT, CON             | Movement, pathfinding, reconnaissance, escape, route discovery.                |
| **Healer**      | WIS, INT         | CHA, CVN        | DEX, END             | Repair, stabilization, restoration, triage, body-system support.               |
| **Artisan**     | DEX, INT         | END, WIS        | STR, MYS             | Skilled creation, refinement, material understanding, durable output.          |
| **Merchant**    | CHA, INT         | WIS, DEX        | LCK, CVN             | Exchange, appraisal, leverage, contracts, logistics, value-flow.               |
| **Performer**   | CHA, DEX         | AGI, WIS        | INT, MYS             | Influence through rhythm, attention, emotion, presence, audience state.        |
| **Laborer**     | END, STR         | CON, DEX        | WIS, CVN             | Work capacity, hauling, building, mining, farming, repetitive force.           |
| **Scribe**      | INT, DEX         | WIS, MYS        | CHA, END             | Records, copying, translation, indexing, symbol discipline.                    |
| **Adventurer**  | END, WIS         | STR, AGI, INT   | DEX, CON, LCK        | Flexible survival, delving, mixed-skill adaptation, practical problem-solving. |
| **Envoy**       | CHA, WIS         | INT, CVN        | DEX, MYS             | Negotiation, access, representation, de-escalation, faction crossing.          |
| **Beastkeeper** | WIS, CHA         | END, CON        | DEX, CVN             | Animal partnership, care, training, command through relationship.              |
| **Mariner**     | END, WIS         | DEX, STR        | AGI, INT             | Ships, tides, weather, sea survival, crew rhythm.                              |
| **Cultivator**  | WIS, END         | CON, MYS        | INT, DEX             | Growth, land, crops, ecosystems, husbandry, long-cycle improvement.            |
| **Sentinel**    | WIS, CON         | END, DEX        | STR, CVN             | Vigilance, holding watch, alarm, positional endurance.                         |
| **Cook**        | DEX, WIS         | END, INT        | CHA, MYS             | Nourishment, preparation, preservation, morale, body-state support.            |
| **Caretaker**   | WIS, CHA         | CON, END        | CVN, DEX             | Care, maintenance, shelter, recovery, vulnerable-person protection.            |
| **Courier**     | AGI, END         | WIS, DEX        | CON, LCK             | Speed, delivery, route memory, evasion, endurance movement.                    |
| **Rider**       | AGI, WIS         | END, CHA        | STR, DEX             | Mounted movement, beast coordination, speed, balance, mobility.                |


## Uncommon Class Profiles

| Base Class       | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                        |
| ---------------- | ---------------- | --------------- | -------------------- | ------------------------------------------------------------------------------ |
| **Priest**       | CVN, CHA         | WIS, MYS        | INT, END             | Invocation, rites, sacred law, purification, vow mediation.                    |
| **Warden**       | CON, WIS         | END, CVN        | STR, CHA             | Boundaries, protection, containment, structural integrity, transferred cost.   |
| **Summoner**     | CHA, MYS         | WIS, INT        | CVN, END             | Calling, binding, command, borrowed agency, externalized force.                |
| **Commander**    | CHA, WIS         | INT, CVN        | END, STR             | Coordination, morale, role assignment, timing, collective action.              |
| **Tactician**    | INT, WIS         | DEX, CHA        | AGI, MYS             | Positioning, timing, formation logic, engagement structure.                    |
| **Alchemist**    | INT, DEX         | WIS, MYS        | END, LCK             | Reaction control, distillation, potions, toxins, catalysts.                    |
| **Artificer**    | INT, DEX         | MYS, WIS        | END, STR             | Magical mechanisms, constructs, devices, engines, repeatable systems.          |
| **Investigator** | INT, WIS         | DEX, CHA        | MYS, END             | Evidence reconstruction, questioning, pattern linkage, hidden-cause discovery. |
| **Judge**        | WIS, CVN         | INT, CHA        | MYS, END             | Verdict, consequence, arbitration, lawful settlement, authority pressure.      |
| **Scholar**      | INT, WIS         | MYS, DEX        | CHA, END             | Study, interpretation, theory, preservation, deep-system comprehension.        |
| **Mystic**       | WIS, MYS         | CVN, CHA        | INT, END             | Inner revelation, hidden experience, altered awareness, unseen law.            |
| **Duelist**      | DEX, AGI         | WIS, STR        | END, CHA             | Single-opponent timing, counters, precision pressure, combat rhythm.           |
| **Keeper**       | WIS, CVN         | CON, INT        | CHA, END             | Preservation, custody, continuity, archives, inherited duties.                 |
| **Architect**    | INT, WIS         | DEX, CVN        | END, STR             | Structures, cities, fortifications, spatial systems, durable design.           |
| **Gambler**      | LCK, WIS         | DEX, CHA        | MYS, INT             | Risk, wagers, bluffing, uncertainty exploitation, probability pressure.        |


## Rare Class Profiles

| Base Class     | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                |
| -------------- | ---------------- | --------------- | -------------------- | ---------------------------------------------------------------------- |
| **Psion**      | WIS, MYS         | INT, CHA        | DEX, END             | Mind, will, perception, attention, pressure, intent.                   |
| **Oracle**     | WIS, MYS         | CVN, INT        | CHA, LCK             | Omens, prophecy, causal sensitivity, fate-pressure, uncertain futures. |
| **Binder**     | CVN, MYS         | INT, CHA        | WIS, DEX             | Contracts, seals, restraints, containment, oath-structures.            |
| **Namekeeper** | MYS, CVN         | WIS, INT        | CHA, DEX             | Names, recognition, essence, addressability, identity continuity.      |
| **Soulkeeper** | MYS, WIS         | CVN, CHA        | INT, END             | Souls, ghosts, afterlife thresholds, continuity of self.               |
| **Shaper**     | MYS, INT         | WIS, DEX        | STR, END             | Body, matter, form, environment reshaping, structural alteration.      |
| **Votary**     | CVN, END         | CHA, WIS        | CON, MYS             | Self-binding, vows, devotion, sacrifice-fueled endurance.              |
| **Medium**     | MYS, CHA         | WIS, CVN        | INT, CON             | Spirits, echoes, possession-risk, ghost contact, unseen presences.     |
| **Seer**       | WIS, MYS         | INT, LCK        | CVN, CHA             | Hidden truths, distant sight, pattern glimpses, incomplete revelation. |

## Epic Classes

| Base Class       | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                     |
| ---------------- | ---------------- | --------------- | -------------------- | --------------------------------------------------------------------------- |
| **Arbiter**      | WIS, CVN         | INT, CHA        | MYS, END             | Binding judgment, dispute finality, consequence allocation.                 |
| **Inquisitor**   | WIS, CVN         | INT, DEX        | CHA, MYS             | Truth extraction, corruption detection, pursuit of hidden violation.        |
| **Thaumaturge**  | INT, MYS         | WIS, CHA        | DEX, END             | Advanced magical method; miracles through technical supernatural precision. |
| **Runewright**   | INT, DEX         | MYS, WIS        | END, CVN             | Written power, runes, arrays, durable magical instruction.                  |
| **Exorcist**     | CVN, WIS         | MYS, CHA        | END, INT             | Expulsion, possession resistance, spiritual severance, cleansing rites.     |
| **Oathbearer**   | CVN, END         | CHA, WIS        | CON, STR             | Power through sworn burdens, promise-weight, personal binding.              |
| **Dreamwalker**  | MYS, WIS         | CHA, INT        | CVN, LCK             | Dreams, inner landscapes, sleeping minds, symbolic passage.                 |
| **Void-Touched** | MYS, CON         | WIS, CVN        | INT, END             | Survival against absence, emptiness, null pressure, impossible spaces.      |

## Fabled Classes

Fabled is the bridge tier between Epic and Legendary. These classes are known through stories, records, elite institutions, or uncertain reports, but they are not yet the history-defining class states that reshape an age.

Agent rule: do not automatically promote every impressive class to Fabled. Use this tier for class expressions that are too rare for Epic, too repeatable for Legendary, and broad enough to appear more than once across an era.

| Base Class      | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                        |
| --------------- | ---------------- | --------------- | -------------------- | ------------------------------------------------------------------------------ |
| **Archmage**    | INT, MYS         | WIS, CHA        | DEX, END             | Master-scale spell architecture, deep theory, high-order casting.              |
| **Dreadnought** | CON, END         | STR, CVN        | WIS, MYS             | Immovable endurance, battlefield anchoring, catastrophic punishment tolerance. |
| **Dragonrider** | CHA, WIS         | END, STR        | AGI, CVN             | Apex beast-bond, aerial command, shared will, high-risk mobility.              |
| **Gravemaster** | MYS, WIS         | CVN, INT        | CHA, END             | Death-continuity, grave authority, ancestor/ghost command without cheap necromancy. |
| **Star-Singer** | CHA, MYS         | WIS, CVN        | INT, END             | Celestial resonance, song-as-law, harmonic authority over distance and omen.   |

## Legendary Classes

| Base Class       | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                             |
| ---------------- | ---------------- | --------------- | -------------------- | ----------------------------------------------------------------------------------- |
| **Realmwalker**   | MYS, WIS         | INT, END        | AGI, CVN             | Planar crossing, distance rupture, boundary traversal, world-pathing.               |
| **Saint**        | CVN, CHA         | WIS, MYS        | END, INT             | Sacred authority, miracle-bearing, spiritual gravity, devotion made manifest.       |


## Mythic Classes

| Base Class       | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                                                                  |
| ---------------- | ---------------- | --------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Aetherist**    | MYS, WIS         | INT, CVN        | CHA, END             | Aether synthesis, foundational elemental harmony, substrate-level manipulation.                                          |
| **Fatewright**   | LCK, MYS         | WIS, CVN        | INT, CHA             | Probability-flow shaping, fate pressure, entropy cost, uncertain-outcome control.                                        |
| **Name-Eater**   | MYS, CVN         | WIS, CHA        | INT, CON             | Devouring addressability, erasure pressure, identity predation.                                                          |
| **Incarnate**    | CVN, MYS         | CON, CHA        | WIS, END             | Embodiment of a principle rather than ordinary technique.                                                                |
| **Worldroot**    | WIS, CON         | MYS, END        | CVN, CHA             | Ecological anchoring, land-body continuity, place-scale vitality.                                                        |
| **Chronarch**    | MYS, INT         | WIS, CVN        | END, LCK             | Time authority, sequence pressure, causality burden, temporal rulership.                                                 |
| **Worldbreaker** | CVN, STR         | END, WIS        | CON, MYS             | Severance-at-scale; breaks impossible opposition, imposed structures, false continuity, and pressure-stabilized systems. |


## Unique Classes

| Base Class                    | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape                                                                                                   |
| ----------------------------- | ---------------- | --------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **First Wound of Heaven**     | CVN, MYS         | WIS, CHA        | END, INT             | A living breach in celestial authority; power emerges from the first successful injury dealt to a divine/celestial order. |
| **Last Door of the Dead**     | MYS, CVN         | WIS, CON        | INT, CHA             | One-of-one death-threshold class; governs a singular passage no other soul can open, close, or survive.                   |
| **The Unnamed King**          | MYS, CHA         | CVN, WIS        | INT, CON             | Authority without addressability; rulership that cannot be cleanly invoked, bound, inherited, or erased.                  |
| **Grief-Engine Saint**        | CVN, END         | WIS, MYS        | CHA, CON             | Converts irrecoverable loss into miracle-pressure; holy not because pure, but because the wound keeps producing force.    |
| **Crown of the Broken World** | CVN, MYS         | CON, WIS        | CHA, END             | Recognized when a person becomes the stabilizing sovereign-symbol of a reality fracture.                                  |
| **The Seventh Silence**       | MYS, WIS         | INT, CVN        | DEX, CHA             | A silence-class tied to truths that cannot be spoken without changing the world-state.                                    |
| **Godsbane Witness**          | WIS, MYS         | CVN, INT        | CON, LCK             | Survives seeing a god, divine law, or cosmic authority fail; perception becomes a permanent wound in certainty.           |
| **The Unreturning Path**      | MYS, END         | WIS, AGI        | CVN, INT             | A traversal class born from crossing a route that should only be crossed once and cannot be retraced.                     |
| **Ashes of the First Flame**  | CVN, END         | STR, MYS        | WIS, CON             | One-of-one inheritor of an origin-fire after its extinction; power from what no longer exists.                            |
| **The Hollow Sun**            | MYS, CON         | WIS, CVN        | INT, CHA             | Radiance turned inward; authority through absence, null light, and impossible containment.                                |
| **The Mercy That Refused**    | CVN, WIS         | CHA, MYS        | END, INT             | A contradiction-class born when mercy refuses the ordained outcome and survives divine/cosmic correction.                 |
| **The Name Beneath Names**    | MYS, CVN         | WIS, INT        | CHA, CON             | Identity-root class; touches the substrate beneath true names, addressability, vows, and self-continuity.                 |



### Cast Path Notes

| Character | Path | Attribute emphasis |
|---|---|---|
| Marcus | Mage → Realmwalker | Mage begins INT/WIS/CHA; Realmwalker later adds Planar movement pressure, likely WIS/INT/END plus Planar-specific cost rules. |
| Serra | Warrior → Worldbreaker | Warrior STR/CON/END, with Worldbreaker likely promoting WIS or FAI/OCC-equivalent commitment/severance pressure once defined. |
| Seb | Warrior → Reaver | Warrior base with Reaver bending END/CON/STR toward consumption, blood, pain, momentum, and dangerous overextension. |
| Mara | Psion → Arbiter placeholder | Psion WIS/CHA/INT, with Mirror-Salience interface separate from class. |
| Mathias | Scout → Emissary | Scout AGI/END/WIS, Emissary likely promotes CHA/INT for first-contact and diplomacy. |
| Brent | Warden → Reckoner | Warden CON/WIS/END, Reckoner likely promotes INT/CHA for cost/accountability recognition. |

---

# Cast-Linked Specializations

Specializations / evolved directions tied to the main cast. **Book-2 trajectory unless noted** — planted in Book 1, not unlocked in it. Marcus's **Realmwalker** and Seb's **Reaver** are the two that *are* Book-1 unlocks; see their files.

| Spec | Base → | Concept | Cast |
|---|---|---|---|
| **Reaver** | Warrior specialization | Consumes advantage, pain, blood, lives, momentum, or enemy collapse to keep pushing past normal limits. Broker grants it early to Seb, bending his leadership-under-desperation into dangerous self/other consumption. | Seb |
| **Reckoner** | Warden specialization | Accounts for **cost / consequence / debt / transferred risk**; makes the bill visible and **forces it answered**. Not “tank” — responsibility over what must not fail, with a hard edge that names who benefits from harm being hard to see. | Brent |
| **Worldbreaker** | Warrior → Mythic lane | Breaks the impossible opposition; **severance-at-scale**; parallel to **Realmwalker**. Folds precise Severance Pulse into overwhelming frontline force, not a generic bruiser. Also appears in Mythic Options above. | Serra |
| **Emissary** | Scout specialization | **Contact / diplomacy / first-contact**; opens doors socially, politically, culturally where others trigger rejection. Class spec only — unrelated to Mathias's Enteric Lattice interface, which is unchanged. | Mathias |
| **Arbiter** *(placeholder; rename on lock)* | Psion specialization | Determines **which interpretation survives the room**; Mindblade-like combat expression: psychic cutting, severing mental bindings, precision execution. Name is a placeholder because it collides with Marcus's Mage **Arbiter Aspect** (`mc.md`, Book-1 canon, kept as-is). | Mara |

---

# Class and Domain Design Philosophy

The Realm's class system is built around a simple principle:

> **Classes describe how a person engages with the world. Domains describe what kind of power shapes that engagement. Skill Affinity describes how easily related skills progress.**

A class is not merely a job title, combat role, or fantasy aesthetic. It is an emergent pattern recognized by the Realm after a person repeatedly develops a coherent method of action. A Warrior solves problems through direct confrontation. A Rogue exploits openings. A Mage manipulates supernatural forces directly. A Summoner borrows agency by calling, binding, or commanding other beings. A Healer restores and stabilizes. A Scholar understands, records, and interprets. A Warden protects boundaries, places, peoples, systems, or laws.

This means base classes should remain broad. A good base class can branch into many futures. It should describe a method, not a finished identity.

Specializations refine that method. A Warrior may become a Knight, Champion, Berserker, Reaver, Guardian, Duelist, or Juggernaut. A Mage may become an Arcanist, Elementalist, Evoker, Warder, Hexer, Illusionist, Battlemage, or Spellweaver. A Summoner may become a Beast Caller, Spirit Binder, Elemental Caller, Golem Caller, Familiar Sage, Swarmkeeper, or Gate Caller. Specializations answer the question:

> **What part of the base method has this person sharpened?**

Domains are separate from classes. Death applied to a Warrior may produce a Death Knight. Death applied to a Mage may produce a Necromancer or Bone Mage. Death applied to a Priest may produce a Funerary Priest or Grave Saint. Death applied to a Summoner may produce a Grave Binder or Necromantic Caller. The domain is the same, but the method changes the final expression.

Likewise, similar fantasy archetypes should not automatically occupy the same system layer. A Death Knight is not a base class because it already implies a martial method, a Death domain expression, and a developed identity. It is better understood as an evolved title: Warrior plus Knight, Champion, Guardian, or Reaver, shaped by Death. A Realmwalker is not a base class either; it is a Legendary Planar title reached through mastery of gates, boundaries, worlds, or dimensional travel. An Incarnate is not a class at all, but a mythic or unique state in which a person embodies a principle rather than merely using it.

Rarity measures availability and information-burden, not raw strength. A Common Warrior can become far more dangerous than a Rare novice Oracle. Common means widely repeatable. Uncommon means regularly seen but requiring more specialized aptitude, training, or circumstance. Rare means dependent on unusual talent, institutions, rituals, or exposure. Epic means extraordinary but still socially legible; it replaces the old `Exceptional` label for current XP tables. Fabled means known through stories, records, institutions, or uncertain reports, but not yet history-defining. Legendary belongs to historical figures and world-shaping masters whose classes can alter institutions or ages. Mythic belongs to disputed, principle-scale, or barely understood beings. Unique belongs to one-of-one outcomes across a cosmic cycle or causal impossibility.

The system should avoid turning every cool concept into a base class. If a concept depends on a specific domain, it is probably a domain-shaped title or specialization. If it depends on a high level of mastery, it is probably an evolved title. If it describes the person's core way of acting even without supernatural flavor, it may be a base class or specialization.

A useful test is:

> **Can this concept exist without its domain?**

Knight can exist without Death, so Knight is a specialization. Death Knight cannot exist without Death, so Death Knight is a final title or domain-shaped specialization. Mage can exist without Time, so Mage is a base class. Chronomancer cannot exist without Time, so Chronomancer is a domain-shaped title or specialization. Summoner can exist without demons, spirits, or elementals, so Summoner can be a base class. Demonologist requires a specific domain of entities, so it is a specialization or final expression of Summoner, Mage, Scholar, Priest, or Binder depending on method.

Another useful test is:

> **Does this describe method, refinement, source, progression, execution, or state?**

- **Method** belongs to base class.
- **Refinement** belongs to specialization.
- **Source/expression** belongs to domain.
- **Progression aptitude** belongs to Skill Affinity.
- **Technical execution** belongs to skill mastery.
- **Recognition, mutation, embodiment, or one-off consequence** belongs to title/state/interface/item depending on the mechanism.

This keeps the system expandable without collapsing into overlap. A Reaver may remain a Warrior specialization because, in ordinary use, it describes a martial combatant who gains momentum through destruction, pain, blood, predation, or collapse. The broader metaphysical idea of gaining power by taking, consuming, harvesting, or stealing can still exist, but it should be named separately if it becomes a cross-class principle. Otherwise the system risks making every specialization universal.

The goal is not to eliminate overlap entirely. Overlap is useful when it creates distinct expressions. The goal is to prevent category collapse. Shadow is not Void. Death is not Spirit. Light is not Celestial. Eldritch is not Chaos. Arcane is not Aether. Frost and Poison are not domains. Skill Affinity is not a domain. Each domain must be defined by what it governs, not merely by its aesthetic.

The cleanest structure is therefore:

**Base Class** — how someone acts.  
**Specialization** — what part of that method they refine.  
**Domain** — what supernatural source/expression shapes them.  
**Skill Affinity** — how naturally a specific skill progresses.  
**Skill Mastery** — how technically developed a skill is.  
**Interface** — personal substrate architecture.  
**Item** — external power/tool/artifact.  
**Title / Evolution / State** — what the world recognizes once the combination becomes distinct.

Examples:

Warrior + Knight + Death = Death Knight.  
Warrior + Reaver + Shadow = Shadow Reaver.  
Mage + Arcanist + Time = Chrono-Arcanist.  
Mage + Evoker + Force = Force Evoker.  
Summoner + Gate Caller + Planar = Planar Invoker.  
Warden + Boundary Keeper + Planar = Gatewarden.  
Artificer + Runesmith + Aether synthesis = Aetherwright.  
Priest + Exorcist + Celestial = Celestial Exorcist.  
Mystic + Seer + Void = Void Listener.  
Scholar + Theorist + Eldritch = Eldritch Scholar.

---

## Luck/Fortune Adapter

This subsystem uses the canonical Luck/Fortune model from `luck_fortune.md`.

### Local Possibility State

$$
z_{\mathrm{class}} = (\mathrm{classRole},\ \mathrm{allowedPowers},\ \mathrm{skillSynergy},\ \mathrm{progressionThreshold},\ \mathrm{classConstraint},\ \mathrm{probabilityAccess})
$$

### LCK in Class Profiles

**LCK** in class attribute profiles is a passive Fortune-coupling coefficient for class resonance — not a standalone Luck system and not a resource weight. Canonical Luck → `luck_fortune.md`; Interface projection → `interface_abstraction.md`.

### Luck-Adjacent Classes

Some classes explicitly manipulate uncertainty through features defined in `power_expression.md`:

| Class | Luck interaction |
|---|---|
| **Fatewright** | Active probability-flow shaping; entropy cost; $u_{\mathrm{active},X}$ |
| **Gambler** | Uncertainty exploitation, wagers, probability pressure |
| **Oracle** | Omen/prophecy read of uncertain futures; amplitude layer may apply |
| **Seer**, **Dreamwalker** | Marginal revelation branches; not omniscience |

### Reachability Constraints

Luck cannot make every class a luck class, bypass class identity, override class constraints, or convert ordinary class skill into probability manipulation without explicit feature support.

Most classes do not directly manipulate Luck; they remain subject to subsystem adapters (combat, craft, etc.) when uncertainty remains.

### Notes

Class features that force probability incur entropy/control cost per `luck_fortune.md`. Do not duplicate canonical equations here.

The Realm's class system should feel vast, but not arbitrary. It should allow thousands of possible outcomes while preserving understandable logic. A person is not assigned a class because a menu says so. The Realm recognizes what they repeatedly become.
