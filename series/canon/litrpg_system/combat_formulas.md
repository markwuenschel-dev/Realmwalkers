---
id: combat_formulas
name: Combat Damage Resolution
kind: system
status: canon
---
# Combat Damage Resolution — Dominion Realm

> **Status:** Canon · working draft
> **Owns:** how power becomes injury — the damage/penetration model, momentum & ambush terms, mitigation (α), defense, secondary-effect severity, spellcasting output, AoE, and injury pressure. (Fills the "damage and injury model" that `resource_system.md` §21 flagged as the highest-value future section.)
> **Defers to:** `resource_system.md` (the four resources HP/Mana/Stamina/**Reserve**, regen/depletion, **the Reserve buffer rule that governs overchannel**, stat templates) · `mechanics.md` (Spell Strength, Spell Skill Mastery, Item Quality ladders) · `classes.md` (class taxonomy & methods) · `core_rules.md` (attributes, the Eyes split, UI/namebinding).
> **Design principle:** damage is not a dice roll. The interface *translates a real physical / magical / biological / metaphysical event into a readable number.* Power is generated, expressed, resisted, and converted into injury / strain / status; the UI shows HP loss, status severity, resource drain, or condition text. This stays consistent with `core_rules.md`: numbers are a translation layer, not the world.

---

## Reconciliations & flags (read first)

This file was reconciled to existing canon on the way in. Where the source spec and canon disagreed, **canon won**:

- **Reserve & overchannel belong to `resource_system.md`.** This file does **not** define its own Reserve-strain math. Forced casting/exertion past the safe envelope draws on Reserve **by the canon rule** (`resource_system.md` §9): buffering begins when Mana **or** Stamina drops below 20%; `1 Reserve = 5 Mana deficit` and `1 Reserve = 5 Stamina deficit`; zero Reserve is a hard stop, and borrowing past it requires catastrophic injury / story consequence. The overchannel section below points there rather than restating it.
- **FAI & OCC are universal, not exotic.** `resource_system.md` §3: Faith and Occult are *true hidden attributes for all creatures* (baseline humans FAI 5 / OCC 5). Marcus's hybrid transformation only made them **visible to his interface** (`core_rules.md`) — every caster, priest, occultist, monster, and animal already has them. The spell weights `ω_FAI` / `ω_OCC` therefore apply to all casters.
- **"Affinity" and "Domain" are two distinct canon axes; this model uses Domain.** In `core_rules.md` (tagged *"from original draft"*) **Affinity = the per-skill chance that a skill levels** when it hits 100% XP — the leveling dice, never a damage concept. The **power domain / source** (Fire, Shadow, Planar, Death…) is the separate axis now named **Domain** throughout canon (`mechanics.md` → Domain Tiers, `classes.md`). The damage model never touches leveling-chance Affinity. Power-domain effects enter as **two non-overlapping factors, never double-counted**: `DomainResonanceMod` (domain ↔ environment/source, inside `SpellSourcePower`) and `DomainInteraction χ` (attacking domain ↔ defending domain, inside `Context_α`). χ defaults to **neutral/dormant** until dramatic domain pairs are authored — see Contextual α below.
- **Spell Strength is an *expression* ladder, not a damage dial.** `mechanics.md` is explicit that a higher Spell Strength is "not necessarily larger or more destructive" — it is a more complete realization of the spell's concept (power / efficiency / precision / duration / range / stability / control). `SpellStrengthMod` below is therefore a **tunable offensive *projection*** of that ladder, used only for damaging spells; non-damaging spells apply the same ladder to their relevant dimension instead.

---

## Direct-damage core

```
HPDamage    = ImpactPower × Expression × Penetration
Penetration = ImpactPower^α / (ImpactPower^α + DefensePower^α)
ImpactPower = StaticPower + λ·d(mv)/dt + η·max(0, dThreat/dt − dAwareness/dt)
```

| Term | Meaning |
|---|---|
| StaticPower | baseline source + attribute + skill/class |
| λ·d(mv)/dt | momentum / force-generation (committed force) |
| η·max(0, dThreat/dt − dAwareness/dt) | ambush / suddenness |
| Expression | how cleanly power becomes harm |
| DefensePower | armor, toughness, guard, barrier, resistance |
| α | contextual mitigation sensitivity |
| Penetration | fraction of impact that gets through |

`HPDamage` reduces current HP (`resource_system.md` §8 depletion bands govern what each HP level *means*). Injury condition is separate — see **Injury pressure**, and note the canon invariant `HP ≠ Injury` (`resource_system.md` §2).

## Static power

`StaticPower = SourcePower + AttributePower + SkillPower + ClassPower`

**SourcePower by attack type:**
- **Weapon:** `MaterialPower + MassReachPower + GeometryPower` (material = iron / steel / monster-bone / mythic; mass/reach = size / weight / leverage; geometry = edge / point / crush). *Quality is not material* — it lives in Expression: `Expression_weapon = QualityMod × DurabilityMod × ContactQuality × FormFit`, where `QualityMod` is the **Item Quality** ladder (`mechanics.md`: Shoddy → Ascendant).
- **Natural:** `BodyMassPower + NaturalWeaponPower + EvolutionPower` (claw, bite, slam, sting, Xyloryn blade-limb).
- **Spell:** see **Spellcasting** below.
- **Ability:** `ResourceCost^β × AbilityTierMod × DeliveryMod × ControlMod` (resource = Stamina / Mana / Reserve strain / hybrid).

**AttributePower by method** (canon attribute abbreviations, `resource_system.md` §3 — STR/AGI/DEX/CON/END/WIS):

```
Heavy melee    = STR/2 + END/4          (axes, hammers, shield-charge, committed body-force)
Finesse melee  = DEX/2 + AGI/4          (daggers, precision cuts)
Balanced melee = (STR+DEX)/4 + AGI/6    (swords, spears, versatile)
Bow / ranged   = DEX/2 + STR/4 + WIS/6  (DEX aim, STR draw, WIS targeting)
```

These define the combat model's attribute contribution; they are tunable and consistent with the canon attribute meanings (`core_rules.md`: STR = force, AGI = speed/reaction, DEX = precision, CON = resilience, END = sustained effort, WIS = judgment / magical sensitivity).

**Skill & class:** `SkillPower = SkillTierPower + TechniquePower`; `ClassPower = ClassMethodPower + ClassBonusPower`. Class is **method-shaped**, not a flat damage sticker — consistent with `classes.md` (*base class = method*). The table below is the **Book-1 combat-relevant subset** of the ~24 canon base classes (`classes.md`); it is representative, not exhaustive (Fighter, Hunter, Adventurer, Monk and others also fight, by their own methods):

| Class (rarity) | Adds |
|---|---|
| Warrior (Common) | impact, stability, guard retention, committed-force control |
| Rogue (Common) | precision, suddenness, defense bypass, payload delivery |
| Scout (Common) | positioning, movement efficiency, exposure / route advantage |
| Mage (Common) | spell structure, mana shaping, supernatural manipulation |
| Healer (Common) | restoration precision, stabilization, biological repair |
| Warden (Uncommon) | boundary, guard, protection, consequence absorption |
| Psion (Rare) | will-pressure, mental force, perception, psychic penetration |

(Class methods match `classes.md`: Warden = "protection of places, borders, peoples, laws"; Psion = "mind, will, pressure, perception, force of intent"; Scout = "movement, discovery, pathfinding, reconnaissance". Rarer classes cost more XP and energy per level — `core_rules.md` / `resource_system.md` §14 — so a high-tier class is not a free damage bonus at low level.)

## Momentum & ambush (derivative terms)

- **Momentum:** `MomentumPower = λ·d(mv)/dt` — a committed strike is *not* `AttackPower × 1.5`; it is **added force**. Recovery cost if avoided:
  `RecoveryExposure = ρ·|d(mv)/dt| / (GuardRetention × FootingControl)` — high committed force = high damage on hit, high exposure on miss.
- **Ambush:** `AmbushPower = η·max(0, dThreat/dt − dAwareness/dt)`. It also degrades defense:
  `EffectiveDefense = DefensePower × ResponseReadiness`, where `ResponseReadiness = 1 / (1 + e^(−k(Awareness − Surprise)))`. This makes a Rogue's ambush strong through *readiness collapse*, not arbitrary dagger numbers — consistent with the canon Rogue method ("force one impossible opening", `resource_system.md` §19).

## Contextual α (mitigation sensitivity)

```
α = clamp( 0.75 + 0.35·tanh((DefensePower − ImpactPower)/Scale_α) + Context_α , 0.40 , 1.50 )
Context_α = (DefenseRigidity + ContactPenalty) − (PenetrationQuality + PrecisionQuality) + DomainInteraction(χ)
```

High α = threshold-like defense (hard armor / carapace / barrier and glancing contact raise it). Low α = softer resistance / more leakage (armor-piercing, a clean joint / weak-point, perfect precision lower it). `DomainInteraction(χ)` occupies the slot the source spec's `AffinityOpposition` once held — renamed and rescoped to the **Domain** axis, and **dormant by default** (`χ ≈ 0`).

**DomainInteraction (χ) — attacking domain vs defending domain.**
```
χ = D⃗_attack^T · M_c · D⃗_defense
```
| Symbol | Meaning |
|---|---|
| D⃗_attack / D⃗_defense | attacking / defending domain vectors |
| M_c | context interaction matrix (environment / form / material) |
| c | the context |

`M_c` defaults to **neutral** — most domains are orthogonal, so `χ ≈ 0` and the term vanishes. The author fills only the **dramatic pairs** that actually appear in scenes (Fire↔Water, Light↔Shadow/Death, …); no full matrix is required. χ's canonical home is this `Context_α` term for direct damage, with an **optional documented hook** into secondary-effect severity for status spells. (The domain ↔ environment factor is *different* — that is `DomainResonanceMod` in `SpellSourcePower`; never count both for the same effect.)

## Defense power

```
DefensePower = max(0, ArmorStructure + BodyHardness + GuardStructure + BarrierPower + ResistancePower − BypassPower)
```

(BodyHardness = CON + species toughness; GuardStructure = active stance / shield / weapon-guard; BypassPower = precision / armor-pierce / weak-point / phase.)

## Secondary effects (no roll checks — severity formulas)

`EffectSeverity = (EffectPower − ResistancePower) / EffectScale` → `<0` absorbed · `0–0.5` minor · `0.5–1` noticeable · `1–1.5` severe · `1.5–2` fight-shaping · `2+` decisive.

- **Stagger:** `(ImpactPower × ControlExpression / StabilityPower) / StaggerScale`; `StabilityPower = END + CON + BalanceSkill + GuardStructure + MassAnchor + FootingMod`.
- **Mental / psychic:** `(PsychicPressure / MentalResistance) / MentalScale`; `MentalResistance = WIS + FAI + MentalDiscipline + SoulStability + ActiveDefense`. (WIS = magical-disruption resistance per `core_rules.md`; SoulStability ties to the Soul Level ladder, `mechanics.md`.)
- **Poison:** `ToxinLoad = ∫ ToxinIntensity·DeliveryExposure·BiologicalPenetration dt`; `PoisonSeverity = (ToxinLoad − ToxinResistance)/PoisonScale`; `ToxinResistance = CON + END/2 + PoisonResistance + MetabolicDefense` (CON = organ/toxin resilience per `core_rules.md`).

## Injury pressure (author-facing — no UI number required)

```
InjuryPressure = HPDamage/MaxHP + LocationVulnerability + PrecisionQuality + DamageTypeSeverity − (ArmorCoverage × TissueResilience)
```

`<0.10` bruise/shallow · `0.10–0.25` minor · `0.25–0.45` moderate · `0.45–0.70` severe · `0.70+` critical. Author picks the exact injury by scene logic. This is the combat-side feed into `resource_system.md` §11 **Injury Logic** (current-HP loss, max-HP reduction, regen/functional/pain penalties, status condition) — which owns how an injury condition is *recorded*.

## Spellcasting

Preserves the canon layering: mana → Spell Strength → Spell Skill Mastery → form → compression → stability → class method → domain → dual-cast.

```
SpellSourcePower = C^β × SpellStrengthMod × DisciplineMasteryMod × FormMod × CompressionMod × StabilityMod × DomainResonanceMod × DualCastMod
SpellImpactPower = SpellSourcePower + AttributePower_spell + ClassPower_spell
SpellHPDamage    = SpellImpactPower × ReleaseExpression × (SpellImpactPower^α / (SpellImpactPower^α + DefensePower^α))
```

- **C^β** — C = mana committed; β = conversion efficiency (crude 0.70 → clean 1.00 → overchannel 1.15+). Mana does not convert linearly. **Overchannel is not free:** pushing past the safe envelope (notably casting below 20% mana) bills **Reserve** per the canon rule (`resource_system.md` §9) — see **Overchannel** below.
- **SpellStrengthMod** — *offensive projection* of the `mechanics.md` Spell Strength ladder (flagged; see Reconciliations): Feeble 0.50 · Weak 0.70 · Minor 0.85 · Normal 1.00 · Enhanced 1.15 · Potent 1.30 · Strong 1.50 · Mighty 1.75 · Grand 2.10 · Supreme 2.50 · Sovereign 3.00+. Non-damaging spells apply the **same ladder** to their relevant dimension (duration / range / precision / stability / permanence) rather than this damage multiplier.
- **DisciplineMasteryMod** = `1 + MasteryBonus`, from the **Spell Skill Mastery** ladder in `mechanics.md` (Novice +0% → Divine +150%, 15 tiers). Per canon it raises both **output and resistance** in that discipline; it is **not** re-tabled here. (A *discipline* / school — e.g. Frost Magic, Poison Magic — is a learned skill-family that *wields* a domain; disciplines are **not** domains. Per `core_rules.md` Spell Skill Mastery.)
- **AttributePower_spell** = `ω_INT·INT/2 + ω_WIS·WIS/2 + ω_FAI·FAI/2 + ω_OCC·OCC/2 + ω_CHA·CHA/3`, weights by **domain** (arcane / elemental → INT; healing → WIS/FAI; divine / celestial → FAI; occult / curse → OCC; psionic → WIS+INT; command → CHA+WIS), summing ≈ 1.0. (CHA = projecting presence into the substrate, `mechanics.md`, not attractiveness. FAI/OCC are universal hidden attributes — see Reconciliations.)
- **StabilityMod** (disrupted 0.50 → perfect 1.25) · **ReleaseExpression** (glancing 0.60 → perfect placement 1.25) — *outside* SourcePower; how cleanly the spell connects.
- **DualCastMod** = `1 + ε_dual·(m−1)`, m = total/base mana; ε rises with mastery (untrained 0.35 → grandmaster 0.95) — always **sub-linear** (2× mana never reaches 2× effect).
- **DomainResonanceMod** — how strongly the setting/source amplifies or damps this **domain's** casting (fire in a furnace vs underwater): the domain ↔ environment/source factor. This is the only domain term inside `SpellSourcePower`; the attacking-vs-defending matchup is `DomainInteraction χ` in `Context_α` (never double-count). Neither is the leveling-chance **Affinity** of `core_rules.md`.
- **FormMod:** Bolt 1.10 · Lance 1.25 · Beam 1.00/tick · Burst 0.80 · Cone 0.75 · Cloud 0.60 · Curse 0.50 · Field 0.50 · Rupture 0.85. AoE forms trade single-target efficiency for spread.

## AoE / field (distributed, not point damage copied)

`HPDamage = ∫∫_B I(x,t) · Expression(x,t) · Penetration(x,t) dx dt` over body volume B.

- **Fireball:** `I(r,t) = I0 · Falloff(r) · Pulse(t)`, `Falloff = max(0, 1 − r/R)^n` or `e^(−kr)` — burst heat/pressure; stronger versions add lingering flame (consistent with `mechanics.md`'s "Sovereign Fireball … leaves lingering magical flames").
- **Cone of Cold:** radial × angular falloff × exposure — lower immediate HP than a focused lance, stronger movement / reaction / stamina suppression.
- **Poison Cloud:** exposure-integral (inhalation + contact × biological penetration over time) — brief contact irritates, long exposure is dangerous.

## Overchannel & Reserve — *deferred to `resource_system.md`*

This file does **not** define a separate Reserve-strain formula. Overchannel is accounted for by canon (`resource_system.md` §9, §10, §22):

- Reserve begins buffering when **Mana or Stamina** falls below **20%**.
- Forced casting below 20% mana consumes Reserve at `1 Reserve = 5 Mana deficit`; forced exertion below 20% stamina consumes Reserve at `1 Reserve = 5 Stamina deficit`.
- **Zero Reserve is the worst non-HP crash** (interface crash, organ stress, seizure-equivalent, soul strain). Borrowing past zero requires catastrophic injury or a story-level consequence.
- Reserve **depletion** (temporary strain, rest/meditation recovers) is distinct from Reserve **injury** (routing-system damage that can lower max Reserve until treated) — `resource_system.md` §10.

So in combat terms: the `β` conversion rising into "overchannel 1.15+" is *purchased* with Reserve under the rule above, not with a parallel quadratic strain model. Marcus's Eyes-of-Meszkhal ocular cost is one specific face of this general Reserve (`core_rules.md`; bills in body/Reserve, the item's copycat power bills in mana) — the Eyes do not get separate combat math here.

## Calibration sanity-checks (not hard rules)

Targets only — they should *vibe* with `resource_system.md` §17 (Book-1 combat standing) and §16 (threat readout), not override them.

- L1 Warrior clean sword vs Rogue ≈ 15–25% HP; vs Mage ≈ 55–70% (lower Mage defense, `resource_system.md` §19). Heavy committed hit vs Rogue ≈ 35–50%. Rogue clean dagger vs Warrior ≈ 8–15%; glancing vs armor ≈ 3–6%; **ambush** = a strong opener (HP + injury/status/position).
- Firebolt ≈ a solid light-weapon hit (never weaker than a glancing dagger); Flame Lance stronger single-target; Fireball lower single-target but AoE; Cone of Cold = control; Poison Cloud = exposure/status.
- Class identity: Warrior wins direct exchanges (impact / dp-dt / stability); Rogue alters dThreat/dt + bypass; Scout wins via movement / exposure; Mage via conversion / structure / range; Warden via boundaries / absorption; Psion via pressure / will; Healer by reversing damage.
- **Book-1 guardrail:** these numbers must not make Marcus the strongest general fighter — Seb and Serra lead direct combat early; Marcus is situational/middle (`resource_system.md` §17, §22).

---

*Cross-reference: `resource_system.md` (resources, Reserve, regen/depletion, injury logic, stat templates) · `mechanics.md` (Spell Strength, Spell Skill Mastery, Item Quality, Soul Level) · `classes.md` (class methods) · `core_rules.md` (attributes, the Eyes, UI). Change history in `CHANGELOG.md`.*
