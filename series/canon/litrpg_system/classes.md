---
id: classes
name: Class System
kind: system
status: canon
---
# Class System — Dominion Realm

> **Status:** Canon · working draft
> **Model:** Base class = *method* · Specialization = *refined role* · Domain = *power expression/source* · Skill Affinity = *skill-progression aptitude* · Legendary/Mythic/Unique titles = *rare evolved expression, title, or state*.
> **Rarity ladder:** Common → Uncommon → Rare → Exceptional → Legendary → Mythic → Unique. **Rarer classes cost more XP _and_ more energy per level** (see `core_rules.md` → The Class System / Class Tiers).
> **Classes are earned through behavior, not selected.** The Realm recognizes what someone repeatedly becomes.
> **Attribute model:** Classes do not grant bonus attribute-point cadence. They define Prime/Core attribute multipliers that shape how efficiently relevant attributes express through that class.
> **Cast quick-ref:** Marcus = Mage → **Riftwalker** (Legendary); Serra = **Warrior** → **Worldbreaker** (Legendary-lane; Book-2 direction); Seb = Warrior → **Reaver** (specialization, broker grants it *early*); Mara = **Psion** (Rare) → **Arbiter** *(placeholder specialization; Book-2 direction; rename likely because of Marcus's Arbiter Aspect)*; Mathias = **Scout** → **Emissary** *(specialization; Book-2 direction)*; Brent = **Warden** → **Reckoner** *(specialization; Book-2 direction)*.

---

## Read-This-First Rules

The clean model:

**Base classes = method.**  
**Specializations = refined role.**  
**Domains = power expression/source.**  
**Skill Affinity = progression aptitude, not power type.**  
**Legendary/Mythic/Unique titles = rare evolved expression, recognition, mutation, or state.**

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

A class and an interface can align, conflict, or partially overlap, but they are not the same layer. Serra's **Warrior** class and **Pressure-Severance Interface** both point forward, but Warrior is the Realm's method-label while Pressure-Severance is her personal substrate architecture. Marcus's **Riftwalker** class is not the same thing as his Neurochromatic Eyes or the Eyes of Meszkhal.


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
- Rarity does **not** automatically make multipliers larger.
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
- Marcus's Aetherfall Aspect role shapes his tactical thinking, but his Realm class path is **Mage → Riftwalker**.

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

Aether is the higher-order synthesis of the eight Elemental Domains in harmony. It should not casually appear as an ordinary domain, ordinary school, or common class flavor. Aether paths belong in Exceptional, Legendary, Mythic, Unique, item, interface, or world-event territory unless a specific culture or artifact has a carefully bounded partial expression.

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

| Base Class | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape |
|---|---|---|---|---|
| **Warrior** | STR, CON, END | AGI, DEX, WIS | CHA | Direct combat durability, weapon pressure, staying power |
| **Rogue** | DEX, AGI | INT, WIS, END | STR, LUCK | Exploitation, burst movement, weak-point timing |
| **Mage** | INT, WIS | CHA, DEX, END | CON | Spellcasting capacity, control, structured manipulation |
| **Hunter** | DEX, WIS, END | AGI, STR, INT | CON | Tracking, pursuit, ranged lethality, survival pressure |
| **Scout** | AGI, END, WIS | DEX, INT, CON | LUCK | Movement, routes, threat-reading, sustained traversal |
| **Healer** | WIS, INT | CHA, CON, DEX | FAI | Restoration, stabilization, diagnosis, controlled repair |
| **Priest** | WIS, CHA, FAI | INT, CON | OCC | Invocation, rites, sacred law, vow pressure |
| **Artisan** | DEX, INT | STR, WIS, END | CHA | Skilled creation, craft control, material execution |
| **Merchant** | CHA, INT, WIS | LUCK, DEX | END | Exchange, appraisal, leverage, logistics |
| **Performer** | CHA, DEX | AGI, WIS, INT | LUCK | Attention, rhythm, presence, social/magical performance |
| **Laborer** | STR, END, CON | DEX, AGI | WIS | Physical work, endurance, practical force |
| **Scribe** | INT, DEX | WIS, CHA | END | Records, writing, symbols, administrative precision |
| **Fighter** | STR, CON, END | AGI, DEX | WIS | Practical combat fundamentals, grit, basic weapons |
| **Adventurer** | END, WIS, LUCK | STR, AGI, DEX, CON, INT | CHA | Flexible survival and mixed-skill adaptation |

## Uncommon Class Profiles

| Base Class | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape |
|---|---|---|---|---|
| **Summoner** | CHA, WIS, INT | FAI, OCC, END | DEX | Calling, binding, command, borrowed agency |
| **Monk** | END, WIS, CON | STR, AGI, DEX | FAI | Internal cultivation, body-soul discipline |
| **Artificer** | INT, DEX, WIS | END, STR, CHA | Runic/domain-dependent | Magical engineering, devices, imbued tools |
| **Alchemist** | INT, WIS, DEX | CON, OCC, END | CHA | Transformation through substances, reactions, mutagens |
| **Scholar** | INT, WIS | DEX, CHA, END | LUCK | Knowledge, analysis, theory, records |
| **Commander** | CHA, WIS, INT | END, STR, CON | LUCK | Coordination, morale, strategy, authority |
| **Warden** | CON, WIS, END | STR, CHA, FAI | INT | Protection of places, people, laws, borders, systems |
| **Tamer** | CHA, WIS, END | CON, DEX, FAI | STR | Partnership with beasts, monsters, mounts |
| **Navigator** | WIS, INT, END | AGI, DEX, LUCK | CHA | Routes, travel, orientation, stars, currents |
| **Diplomat** | CHA, WIS, INT | DEX, LUCK, END | FAI | Negotiation, status, treaties, social leverage |

## Rare Class Profiles

| Base Class | Prime Attributes | Core Attributes | Secondary Attributes | Resource / method shape |
|---|---|---|---|---|
| **Ritualist** | WIS, INT, FAI/OCC | CHA, END, DEX | CON | Preparation, circles, sacrifice, ceremony, scale |
| **Binder** | CHA, WIS, INT | FAI, OCC, DEX | END | Contracts, seals, names, oaths, containment |
| **Mystic** | WIS, CHA | INT, FAI, OCC | END | Intuition, altered perception, inner revelation |
| **Oracle** | WIS, FAI, INT | CHA, LUCK, END | OCC | Prophecy, omens, causal insight |
| **Shaper** | WIS, INT, CON | DEX, STR, OCC | CHA | Reshaping body, matter, form, environment |
| **Psion** | WIS, CHA, INT | DEX, END, OCC | CON | Mind, will, pressure, perception, force of intent |
| **Namekeeper** | INT, WIS, CHA | FAI, OCC, DEX | END | Names, identity, binding, recognition, essence |
| **Soulkeeper** | WIS, FAI, CHA | OCC, INT, CON | END | Souls, ghosts, afterlife, continuity of self |

### Cast Path Notes

| Character | Path | Attribute emphasis |
|---|---|---|
| Marcus | Mage → Riftwalker | Mage begins INT/WIS/CHA; Riftwalker later adds Planar movement pressure, likely WIS/INT/END plus Planar-specific cost rules. |
| Serra | Warrior → Worldbreaker | Warrior STR/CON/END, with Worldbreaker likely promoting WIS or FAI/OCC-equivalent commitment/severance pressure once defined. |
| Seb | Warrior → Reaver | Warrior base with Reaver bending END/CON/STR toward consumption, blood, pain, momentum, and dangerous overextension. |
| Mara | Psion → Arbiter placeholder | Psion WIS/CHA/INT, with Mirror-Salience interface separate from class. |
| Mathias | Scout → Emissary | Scout AGI/END/WIS, Emissary likely promotes CHA/INT for first-contact and diplomacy. |
| Brent | Warden → Reckoner | Warden CON/WIS/END, Reckoner likely promotes INT/CHA for cost/accountability recognition. |


# Base Class List

Current normal base-class ecosystem: **32 normal base classes** across Common, Uncommon, and Rare.

## Common Base Classes

| Base Class | Core Method | Example Specializations | Domain-Shaped Examples |
|---|---|---|---|
| **Warrior** | Direct confrontation, weapons, endurance | Soldier, Knight, Champion, Guardian, Berserker, Reaver, Duelist, Vanguard, Juggernaut | Death Knight, Flame Champion, Force Juggernaut, Shadow Reaver |
| **Rogue** | Exploitation, precision, misdirection | Thief, Assassin, Spy, Saboteur, Infiltrator, Trickster, Poisoner, Acrobat | Shadow Assassin, Voidknife, Psychic Spy, Runic Saboteur |
| **Mage** | Direct spellcasting and supernatural manipulation | Arcanist, Elementalist, Evoker, Warder, Hexer, Illusionist, Battlemage, Spellweaver | Pyromancer, Chronomancer, Void Mage, Force Evoker, Lightweaver |
| **Hunter** | Tracking, pursuit, survival, targeting | Ranger, Archer, Trapper, Monster Hunter, Sniper, Stalker, Bounty Hunter | Grave Hunter, Shadow Stalker, Celestial Marksman, Planar Pursuer |
| **Scout** | Movement, discovery, pathfinding, reconnaissance | Pathfinder, Explorer, Outrider, Wayfarer, Cartographer, Courier, Trailblazer | Windrunner, Planar Wayfarer, Time-Lost Scout, Shadow Scout |
| **Healer** | Repair, restoration, stabilization | Medic, Mender, Surgeon, Herbalist, Lifewarden, Boneknitter, Plague Doctor | Life Mender, Water Healer, Blood Surgeon, Spirit Healer |
| **Priest** | Invocation, devotion, sacred law, rites | Cleric, Exorcist, Confessor, Shrinekeeper, Inquisitor, Funerary Priest | Celestial Cleric, Light Inquisitor, Death Priest, Spirit Exorcist |
| **Artisan** | Skilled physical creation | Smith, Mason, Carpenter, Weaver, Tailor, Chef, Jeweler, Architect | Flame Smith, Earth Mason, Lightglass Artisan, Shadow Tailor |
| **Merchant** | Exchange, logistics, leverage | Trader, Broker, Fence, Quartermaster, Appraiser, Caravan Master, Relic Dealer | Shadow Broker, Death-Tithe Collector, Planar Trader, Fate Broker |
| **Performer** | Influence through art, story, rhythm, attention | Bard, Dancer, Actor, Storyteller, Herald, Muse, Satirist, Glamourist | Shadow Dancer, Death Dirgesinger, Light Bard, Dream Muse |
| **Laborer** | Physical work, endurance, practical force | Porter, Miner, Farmer, Builder, Dockhand, Teamster, Hauler | Earth Miner, Flame Kilnworker, Force Hauler, Life Farmer |
| **Scribe** | Recording, copying, administration, written systems | Clerk, Copyist, Notary, Recordkeeper, Translator, Indexer | Runic Scribe, Light Notary, Shadow Archivist, Spirit Recordkeeper |
| **Fighter** | Practical combat, grit, basic weapons; winning through fundamentals rather than refined doctrine | Scrapper, Sellsword, Pit Fighter, Guard, Militiaman, Freeblade, Bruiser, Shieldhand, Spearman, Bladehand | Flame Scrapper, Stone Shieldhand, Shadow Sellsword, Light Militiaman, Force Bruiser |
| **Adventurer** | Flexible problem-solving, survival, exploration, mixed-skill adaptation | Delver, Quest-Taker, Dungeon Runner, Relic Seeker, Wanderer, Troubleshooter, Ruin Explorer, Expeditionary, Freeblade, Survivalist | Flame Delver, Shadow Wanderer, Planar Expeditionary, Death-Touched Ruin Seeker, Lightbound Quest-Taker |

These are the civilization layer. They make the world feel populated instead of only built around adventurers.

---

## Uncommon Base Classes

| Base Class | Core Method | Example Specializations | Domain-Shaped Examples |
|---|---|---|---|
| **Summoner** | Calling, binding, commanding, borrowing agency | Beast Caller, Spirit Binder, Elemental Caller, Golem Caller, Swarmkeeper, Familiar Sage, Gate Caller | Grave Binder, Celestial Summoner, Eldritch Caller, Planar Invoker |
| **Monk** | Internal cultivation, discipline, body-soul mastery | Martial Adept, Soul Fist, Iron Body, Ascetic, Breath Master, Temple Guardian | Flame Fist, Void Palm, Force Fist, Light Palm |
| **Artificer** | Magical engineering, devices, imbued tools | Enchanter, Runesmith, Golemwright, Mechanist, Relic Maker, Wardwright, Clockworker | Runic Engineer, Planar Architect, Force Mechanist, Aetherwright |
| **Alchemist** | Transformation through substances and reactions | Brewer, Toxicologist, Transmuter, Mutagenist, Bombardier, Elixirist, Apothecary | Blood Alchemist, Chaos Mutagenist, Death Toxicologist, Void Distiller |
| **Scholar** | Knowledge, analysis, theory, records | Archivist, Historian, Runologist, Monster Scholar, Linguist, Lorekeeper, Theorist | Planar Theorist, Eldritch Scholar, Aether Theorist, Light Archivist |
| **Commander** | Coordination, morale, strategy, authority | Captain, Marshal, Tactician, Banneret, Drillmaster, Field Commander | Celestial Marshal, Psychic Strategist, Shadow General, Flame Commander |
| **Warden** | Protection of places, borders, peoples, laws | Sentinel, Guardian, Grove Warden, Oathwarden, Boundary Keeper, Jailor | Planar Gatekeeper, Deathwarden, Light Sentinel, Void Jailor |
| **Tamer** | Partnership with beasts, monsters, mounts | Beastmaster, Monster Tamer, Falconer, Drake Rider, Packleader, Chimera Handler | Life Beastmaster, Flame Drake Rider, Shadow Houndmaster, Spirit Beastspeaker |
| **Navigator** | Routes, travel, orientation, stars, currents | Sailor, Star-Reader, Cartographer, Pilot, Caravan Guide, Astral Navigator | Planar Navigator, Celestial Starfinder, Wind Sailor, Time-Lost Navigator |
| **Diplomat** | Negotiation, status, treaties, social leverage | Envoy, Mediator, Ambassador, Courtier, Hostage-Speaker, Peacebinder | Light Envoy, Shadow Courtier, Psychic Mediator, Celestial Ambassador |

These are still common enough to be known, but they require more structure, training, institution, exposure, or unusual aptitude.

---

## Rare Base Classes

| Base Class | Core Method | Example Specializations | Domain-Shaped Examples |
|---|---|---|---|
| **Ritualist** | Preparation, circles, sacrifice, ceremony, large-scale magic | Circle Mage, Blood Ritualist, Gate Ritualist, Weather Caller, Cursewright, Funeral Adept | Runic Circle-Mage, Planar Gatewright, Blood Ritekeeper, Void Funeralist |
| **Binder** | Contracts, seals, names, oaths, containment | Pactbinder, Seal Master, Chain Mage, Oathwright, Namebinder, Spirit Binder | Runic Sealkeeper, Eldritch Binder, Light Oathwright, Planar Sealkeeper |
| **Mystic** | Intuition, altered perception, inner revelation | Seer, Dreamwalker, Trance Adept, Visionary, Empath, Void Listener | Psychic Seer, Shadow Dreamwalker, Spirit Mystic, Time Visionary |
| **Oracle** | Prophecy, omens, divine/causal insight | Doomseer, Star Reader, Bone Oracle, Truthspeaker, Omen Keeper, Battle Oracle | Time Oracle, Celestial Starseer, Death Doomseer, Light Truthspeaker |
| **Shaper** | Reshaping body, matter, form, environment | Flesh Shaper, Stone Shaper, Biomancer, Warper, Transmuter, Formwright | Blood Sculptor, Chaos Mutator, Force Shaper, Life Shaper |
| **Psion** | Mind, will, pressure, perception, force of intent | Telekinetic, Telepath, Mindblade, Empath, Memory Thief, Thought-Warden | Psychic Mindblade, Force Adept, Shadow Telepath, Light Mindseer |
| **Namekeeper** | Names, identity, binding, recognition, essence | Name Scholar, True-Namer, Namebinder, Herald, Identity Warden | Runic Namekeeper, Spirit Namer, Eldritch Name-Thief, Light Truthnamer |
| **Soulkeeper** | Souls, ghosts, afterlife, continuity of self | Medium, Psychopomp, Soul Warden, Ancestor Speaker, Grave Listener | Spirit Soulkeeper, Death Psychopomp, Light Soulwarden, Void Exorcist |

Rare classes are culturally significant. Villages may have Warriors, Mages, Healers, Artisans, Scribes, and Laborers. A kingdom may only have a handful of true Binders, Oracles, Psions, Namekeepers, or Soulkeepers.

---

# Classes Moved Out of the Base List

These are useful concepts, but they should not be normal base classes.

| Former Base | New Category | Why |
|---|---|---|
| **Riftwalker** | Legendary Planar title/class evolution | Too advanced and too specific for a normal base class. Marcus's Book-1 Legendary upgrade; distinct from the Realm Walkers faction. |
| **Aetherist** | Legendary/Mythic Aether title | Aether is synthesis, not a normal method or ordinary domain. |
| **Incarnate** | Mythic/Unique state | Embodiment of a principle, not a profession/class method. |
| **Archmage** | Legendary Mage evolution | High mastery title, not a starting method. |
| **Saint** | Exceptional/Legendary Priest/Healer/Mystic evolution | Too spiritually elevated for a base class. |
| **Fateweaver** | Legendary Time/Celestial/Shadow title | Depends on derived higher-order magic. |
| **Worldspeaker** | Legendary/Mythic title | Reality-scale authority. |

This keeps the base-class ecosystem cleaner while preserving the “oh damn” outcomes.

---

# Back-Pocket Legendary / Mythic / Unique Options

This is where advanced titles, one-off states, and world-recognized evolutions live without breaking the base-class table.

## Legendary Options

| Title | Likely Built From | Concept |
|---|---|---|
| **Riftwalker** | Scout/Mage/Warden/Summoner + Planar | Crosses planes, worlds, or distant points without fixed gates. **Marcus's Book-1 Legendary upgrade** — the class, distinct from the **Realm Walkers** faction that recruits him. |
| **Archmage** | Mage + high mastery | A mage whose spellcraft becomes institution-level power. |
| **Aetherwright** | Artificer + Aether synthesis | Crafts devices using synthesized eightfold power. |
| **Aetherblade** | Warrior + Aether synthesis | Weapon master channeling harmonized foundational magic. |
| **Worldspeaker** | Scholar/Mystic/Priest + Spirit/Planar/Aether synthesis | Speaks to places, worlds, or reality-structures. |
| **Fateweaver** | Oracle/Mystic + Time/Celestial/Shadow | Reads and alters probability, consequence, or destiny. |
| **Void Saint** | Priest/Mystic + Void/Light | A holy figure who purifies through emptiness, revelation, or renunciation. |
| **Force Knight** | Warrior + Force | Armored fighter who controls pressure, weight, impact, and vector expression. |
| **Eldritch Savant** | Scholar/Mage + Eldritch | Understands alien principles without fully breaking. |
| **Star Marshal** | Commander + Celestial | Leads through heavenly mandate, omens, and cosmic authority. |
| **Gatewarden Prime** | Warden + Planar | Guardian of major interplanar thresholds. |
| **Dragonbound Sovereign** | Tamer/Summoner/Commander + Life/Fire/Spirit | Bonded to a dragon-level entity and recognized as a force of history. |
| **Worldbreaker** | Warrior + severance/Force | **Warrior Legendary-lane evolved form** for Serra's Book-2 direction. Breaks impossible opposition through severance-at-scale; parallel-not-copy to **Riftwalker**. Folds precise Severance Pulse into overwhelming frontline force; not a generic bruiser. |

---

## Mythic Options

| Title | Likely Built From | Concept |
|---|---|---|
| **Aetherist** | Any base + Aether synthesis | Someone who can consciously wield harmonized foundational magic. |
| **Eightfold Vessel** | Any base + Aether synthesis | Body/soul capable of containing all eight foundational domains in harmony. |
| **Origin Sage** | Scholar/Mystic + Aether synthesis/Time/Celestial | Understands pre-system or creation-level principles. |
| **Voidborn** | Any base + Void exposure | A person who survived contact with unbeing and came back changed. |
| **Eldritch Vessel** | Any base + Eldritch | A living conduit for alien law or outer intelligence. |
| **Time-Lost King** | Commander/Oracle + Time | A ruler displaced across eras, remembered before they appear. |
| **Soul Ark** | Healer/Priest/Soulkeeper + Spirit/Death/Light | Carries or preserves multitudes of souls. |
| **Worldseed Bearer** | Warden/Shaper/Mystic + Life/Planar/Aether synthesis | Contains the seed of a future realm or pocket world. |
| **Entropy Saint** | Priest/Mystic + Entropy/Death/Time/Void | Sacred figure of endings, decay, release, and terminal transformation. |
| **Unwritten Oracle** | Oracle + Time/Shadow/Eldritch | Sees futures that technically should not exist. |
| **Lawbreaker Monk** | Monk + Force/Time/Void | Internal cultivation has broken a physical law. |
| **Name-Eater** | Binder/Namekeeper + Shadow/Eldritch/Void | Consumes names, identities, and remembered existence. |

---

## Unique Options

These should be one-of-one, usually tied to artifacts, interfaces, gods, breaches, or world-events.

| Unique Title | Concept |
|---|---|
| **The First Aetherist** | First known person to harmonize the eight foundational domains. |
| **The Last Death** | A Death Incarnate whose existence changes how mortality works. |
| **The Gate Without a World** | A person who became a living planar threshold. |
| **The Unnamed King** | A ruler erased from history but still obeyed by oaths. |
| **The Eighth Witness** | Someone who remembers every version of a changed timeline. |
| **The Hollow Sun** | Celestial/Void contradiction embodied in one person. |
| **The Living Grimoire** | A person whose body/soul became a spellbook or magical archive. |
| **The Boundary That Walks** | A Warden/Planar anomaly who defines where worlds begin and end. |
| **The Star Beneath the Grave** | Death + Celestial paradox; a dead star, saint, or monarch given form. |
| **The Aether Scar** | Someone wounded by Aether synthesis and permanently leaking impossible power. |
| **The One Who Was Not Summoned** | A being/person who entered the Realm without any valid gate, ritual, or cause. |
| **The Silence After Names** | A Void/Namekeeper entity who erases identity at the conceptual level. |

---

# Cast-Linked Specializations

Specializations / evolved directions tied to the main cast. **Book-2 trajectory unless noted** — planted in Book 1, not unlocked in it. Marcus's **Riftwalker** and Seb's **Reaver** are the two that *are* Book-1 unlocks; see their files.

| Spec | Base → | Concept | Cast |
|---|---|---|---|
| **Reaver** | Warrior specialization | Consumes advantage, pain, blood, lives, momentum, or enemy collapse to keep pushing past normal limits. Broker grants it early to Seb, bending his leadership-under-desperation into dangerous self/other consumption. | Seb |
| **Reckoner** | Warden specialization | Accounts for **cost / consequence / debt / transferred risk**; makes the bill visible and **forces it answered**. Not “tank” — responsibility over what must not fail, with a hard edge that names who benefits from harm being hard to see. | Brent |
| **Worldbreaker** | Warrior → Legendary lane | Breaks the impossible opposition; **severance-at-scale**; parallel to **Riftwalker**. Folds precise Severance Pulse into overwhelming frontline force, not a generic bruiser. Also appears in Legendary Options above. | Serra |
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

Likewise, similar fantasy archetypes should not automatically occupy the same system layer. A Death Knight is not a base class because it already implies a martial method, a Death domain expression, and a developed identity. It is better understood as an evolved title: Warrior plus Knight, Champion, Guardian, or Reaver, shaped by Death. A Riftwalker is not a base class either; it is a Legendary Planar title reached through mastery of gates, boundaries, worlds, or dimensional travel. An Incarnate is not a class at all, but a mythic or unique state in which a person embodies a principle rather than merely using it.

Rarity measures availability, not raw strength. A Common Warrior can become far more dangerous than a Rare novice Oracle. Common means widely repeatable. Uncommon means regularly seen but requiring more specialized aptitude, training, or circumstance. Rare means dependent on unusual talent, institutions, rituals, or exposure. Exceptional means known but noteworthy; many people may never meet one. Legendary belongs to historical figures and world-shaping masters. Mythic belongs to disputed or barely understood beings. Unique belongs to one-of-one outcomes that cannot normally be duplicated.

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

The Realm's class system should feel vast, but not arbitrary. It should allow thousands of possible outcomes while preserving understandable logic. A person is not assigned a class because a menu says so. The Realm recognizes what they repeatedly become.
