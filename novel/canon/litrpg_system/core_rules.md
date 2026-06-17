# LitRPG System — Core Rules

> **Purpose:** Defines how the game-like system works in Dominion Realm, how prominent it is in the reading experience, and the rules governing its use in prose. The system exists between lite and medium — it is real and progression matters, but it serves character and story rather than being a draw in itself.

---

## The Foundational Truth

The interface is not the world. It is Marcus's implant translating incomprehensible metaphysical structures into data his mind can process — because his mind is a gamer's mind, and RPG logic is the framework it reaches for.

Other beings in the Realm do not see panels or numbers. Natives experience the same underlying reality through instinct, tradition, ritual, sensation, faith, bloodline memory, or training. When a Realm-born warrior has mastered a combat technique, they don't see a skill level. They have calluses and muscle memory and the accumulated weight of ten thousand repetitions.

Marcus sees numbers. This is not superior. It is different — and it has specific advantages and specific blind spots.

**The blind spot:** Numbers feel like certainty. They are not. The interface is a translation layer built by people who didn't fully understand what they were building. It can be wrong. It can be incomplete. It can fail to render something entirely.

---

## System Prominence — Between Lite and Medium

### What this means in practice

The system is real and Marcus engages with it consciously — especially early, when he's learning to read it. Progression matters. Skill development matters. The interface provides information that affects decisions.

But the system does not pause the story to deliver rewards. It does not generate excitement through level-up announcements. Progression is felt through capability and cost, not through numerical celebrations.

**The test:** If removing a system notification from a scene would lose something essential, it belongs. If removing it would make the prose cleaner and the scene stronger, it doesn't.

### Early story — system is prominent

Marcus is learning to read the interface. Notifications appear frequently, sometimes at bad moments, sometimes with information he doesn't understand yet. Some appear to be errors or incomplete translations. The system is present in nearly every significant scene.

### Mid story — system recedes

Marcus has internalized much of the interface's logic. Notifications become less frequent. He doesn't need to check his status to know roughly where he stands. The system surfaces for significant changes — new abilities, meaningful thresholds, things the interface catches that he missed.

### Late story — system is sparse and significant

A system notification in late story carries weight precisely because it's rare. When the interface fires unexpectedly, something has changed that Marcus couldn't have anticipated. Its appearance is information in itself.

---

## Core Mechanics

### Attributes

Nine primary attributes plus two that appear after Marcus's hybrid transformation:

| Attribute | Governs |
|---|---|
| Strength | Physical force output |
| Agility | Speed, reaction time |
| Dexterity | Fine motor control, precision |
| Constitution | Physical resilience, health pool |
| Endurance | Sustained effort, stamina pool |
| Intelligence | Mental processing, learning rate |
| Wisdom | Judgment, magical sensitivity |
| Charisma | Social force, presence |
| Luck | Probability nudges — unpredictable |
| Faith | Post-hybrid; divine resonance |
| Occult | Post-hybrid; demonic/chaos resonance |

**Derived stats:**
- **Health** = Constitution×6 + Endurance×2 + Strength×2  *(start 5/5/5 → 50)*
- **Mana** = Intelligence×6 + Wisdom×3 + Charisma×1  *(→ 50)*
- **Stamina** = Endurance×5 + Constitution×2 + Strength + Agility + Dexterity  *(→ 50)*
- Luck does not feed the pools; it tilts unresolved outcomes. Full per-attribute effects and the leveling/racial-growth model live in `mechanics.md`.

### Skills and Affinities

Skills are learned through use. Each skill has an affinity — a percentage representing natural aptitude.

**Affinity mechanics (from original draft):**
- Affinity is the chance of leveling a skill upon reaching 100% experience at the current level
- If you don't level up, you lose all experience at that level but keep the affinity
- If you do level up, affinity decreases by an amount tied to natural ability for that skill
- Unbound Affinity (Marcus's ability) inverts this: affinity increases instead of decreasing on level-up

**What this means narratively:** Some people hit natural ceilings — the genius factor. Marcus's Unbound Affinity means he doesn't have that ceiling, but the increase is random. His growth is unpredictable rather than constrained.

**Spell Skill Mastery (per discipline).** Separate from a skill's affinity (the *chance* it levels) and its raw level, each magical discipline carries a **mastery tier** (Novice → Divine) granting escalating Spell Strength and resistance bonuses. Affinity is the dice; mastery is the payoff; the full ladder lives in `mechanics.md`. Marcus's Meszkhal +100% skill XP just means he climbs it twice as fast — when he can afford the mana.

### Insight — the baseline read-skill

> `Congratulations! You have learned the skill: Insight. Cost: 5 Mana. Focus on a being to discern available information.`

The load-bearing phrase is **available information** — Insight is *not* omniscience. At low level it resolves only what the interface currently can, and surfaces `????` for the rest:

```
Name: ????
Level: ????
Health: 1,890 / 1,890
Mana: ????
Stamina: ????
Race: Human
```

As it levels it may reveal condition, injuries, emotional state, resistances, class, affiliations, active effects — but never automatically everything important. Blocked names, hidden classes, disguised races, appraisal-resistance, and over-level targets all read as `????`. This preserves mystery and keeps wrong interpretation possible. Marcus **learns Insight before the mindscape**, so he carries a read into it. (Distinct from the Eyes: Insight is the cheap baseline; the Meszkhal item is the expensive interpretive overlay.)

### Levels

Levels exist. Their specific mechanical meaning is deliberately vague in early story — Marcus doesn't know what level 50 means relative to level 200, and the interface can't always tell him what level something is. This is a feature, not a gap. The uncertainty keeps the Realm dangerous.

What levels represent thematically: accumulated experience with the Realm's underlying systems. A level 1 Marcus has been here for days. A level ??? archdemon has existed across planes for centuries. The gap is not primarily about numbers.

### Health, Mana, and Stamina

Real costs in this world. Losing health hurts. Going to zero means death — and death in the Realm is different from logging out; Marcus knows this now from direct experience. Mana and stamina depletion have real effects on capability and cognition.

The interface displays these as bars and numbers. The body feels them as exhaustion, pain, mental fog, and diminishing returns on everything.

---

## The Class System

Full taxonomy lives in `classes.md`; the tier ladders for spells, items, gems, skill-mastery, and souls live in `mechanics.md`. The class model and the rules that matter in prose:

- **Base class = method** (Warrior, Fighter, Mage, Rogue, Scout, Adventurer, Psion…) — *how* a person solves problems.
- **Specialization = refined role** (Reaver, Trickster, Troubleshooter…) — earned through behavior, not selected.
- **Affinity = power domain** (Fire, Shadow, Gravity, Aether…) — flavors the class.
- **Legendary / Mythic / Unique titles = rare evolved expressions** (Riftwalker, Aetherist, one-of-one breach classes) — outcomes, not professions.

Classes are **earned, not chosen.** The interface may *label* something early, but the class is shaped by what a person actually does; specializations unlock progressively as it develops.

### Class Tiers

Seven rarity tiers — and rarity sets cost, not just prestige:

| Tier | What it is | In the world |
|---|---|---|
| Common | The civilization layer | Every village has Warriors, Fighters, Healers, Artisans, Adventurers |
| Uncommon | Needs training or unusual aptitude | Known, but not in every hamlet |
| Rare | Culturally significant | A kingdom may hold only a handful (Psion, Binder, Oracle, Soulkeeper) |
| Exceptional | Known but noteworthy | Many people may never meet one |
| Legendary | Evolved, institution-level | Named in histories (Riftwalker, Archmage) |
| Mythic | Principle-scale | Spoken of like myth (Aetherist, Name-Eater) |
| Unique | One-of-one | Tied to artifacts, gods, breaches, world-events |

**The cost rule (load-bearing for Marcus).** The rarer the class, the **more XP it takes to level** *and* **the more energy its signature abilities demand.** A rare or legendary class is not a free power-up handed to a low-level character — it is a heavier engine than their body can yet fuel.

> **Worked example — Marcus.** His Mage class upgrades to **Riftwalker** (Legendary) late in Book 1 when a Realm Walker recruits him. Plane/world/distance-crossing is the marquee Riftwalker ability — and at ~level 20 he cannot afford its energy cost, while the class now levels slower than a common one. He owns a power he can't use. That gap is the engine of his progression into Book 2 — by design, not a flaw in the build.

---

## Marcus's Specific Abilities

### Unbound Affinity
*Passive — always active*

Affinities for skills increase instead of decrease upon leveling. The amount of increase is random.

**Narrative meaning:** Marcus doesn't cap out the way most people do. His growth is unpredictable and slightly chaotic — sometimes a big jump, sometimes a small one. He cannot plan his development the way a more linearly-progressing character could. This is consistent with his character: a systems thinker who has to deal with a system that won't be fully systematized.

### The Eyes — Two Separate Systems

Marcus has two distinct ocular systems; **never conflate them** (full detail in `mc.md`):

**Eyes of Meszkhal — _Unique item_** (archdemon Xazzidiuk's gift; Item Rarity **Unique**, see `mechanics.md`).
*Active — costs **20 mana** to activate, then **1% of maximum mana per second** while sustained (at 50 mana ≈ 0.5/sec; ~60s fully drains him). See `mechanics.md` / `mc.md` for the cost curve, the physical damage progression, and the visible-activation tells.*
By actively watching combat actions, Marcus absorbs the muscle memory required to replicate them; **skill experience +100% while active** (it accelerates his climb up the skill-mastery ladder). The item also *interprets* — collapsing ambiguity into one confident, demon-biased verdict that can be wrong. It bills in **mana**, so it competes with his casting.

**Neurochromatic Eyes — _interface_** (emergent, biological; six stages → Prism Coherence / Prism Fracture).
*Perception only, and accurate.* Reallocates perceptual/cognitive bandwidth by emotional state; creates no power. Bills in **the body** — ocular reserve, vascular strain, blood tears, feedback-lock — not mana. **Current stage:** Stage 1 (Limbal Shift). Full stage breakdown in `mc.md`. Whether it ever echoes the item's copycat/XP power (weaker or stronger) is **undecided**.

**Narrative meaning:** he sees patterns, he sees too much — his flaw is *accurate perception paired with flawed interpretation.* The interface sees true; the item supplies the confident, sometimes-false conclusion.

---

## Notification Formatting

See `style/system_message_rules.md` for complete formatting spec. Summary:

- Standard: `[ SYSTEM ]` — three lines max
- Warning: `[ WARNING ]` — three lines max
- Ability change: `[ INTERFACE ]` — four lines max
- Stat panel: boxed — early story only, deliberate consultation only
- Ayla: `[ AYLA ]` — her register, not the system's

**The interface is never excited.** No exclamation points. No dramatic flair. It is functional. The drama is in the prose around it, not the notification itself.

---

## What the System Cannot Do

The interface cannot:
- Classify something it has never encountered before
- Accurately render N'hal entities — they damage the conditions that make classification possible
- Access true names — these exist below the layer the implant reads
- Override Marcus's instincts when his instincts are wrong
- Tell him what a number means in context — it can give him the stat, not the wisdom to interpret it

The interface sometimes:
- Mistranslates a Realm concept into an Earth framework that doesn't quite fit
- Produces a warning with no actionable information
- Goes silent at a critical moment
- Displays something that looks like an error but isn't

**When the interface fails or goes silent, that is a story event.** Marcus has encountered something outside the implant's model of the Realm. This is always significant.

---

## The Naming System

True names are metaphysically dangerous — resistance is governed by **Soul Level** (`mechanics.md`): low and average souls can be bound or overwritten, which is exactly what the namebinding faction preys on. The interface enforces this:
- Marcus's status shows `????` for his name until he chooses a Realm alias
- Choosing his real Earth name (Marcus Fahr) would make it visible — and usable by anyone who could read his status
- Ayla's warning is absolute: never say your true name

**Narrative use:** True names are a later-story tool. The danger established early becomes plot-relevant when something needs his true name to do what it does. Do not assign casually. Track in `unresolved_character_threads.md` when it becomes relevant.

---

## Progression Philosophy

Marcus's progression should feel earned through reasoning and cost, not granted through power spikes.

His victories come from:
- Finding the leverage point in a system
- Using what he observes through the Eyes
- Accepting the cost of what the interface demands
- The gap between what the interface tells him and what he figures out himself

His growth is not linear. Unbound Affinity means some skills jump unexpectedly. The Eyes mean he can develop certain abilities faster than his level would suggest — but only if he's actively watching and learning. Progress has a price: the ocular reserve, the mana cost, the risk of false certainty.

**The rule:** every significant progression moment should cost something real or require something genuinely clever. Nothing should feel like a reward delivered by the story for reaching a checkpoint.

---

*Cross-reference: `style/system_message_rules.md` for notification formatting. `characters/major/mc.md` for Marcus's full ability progression.*

*Last updated: working draft — change history in `CHANGELOG.md`.*
