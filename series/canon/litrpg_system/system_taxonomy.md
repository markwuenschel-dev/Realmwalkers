---
id: system_taxonomy
name: System Taxonomy & Agent Boundaries
kind: system
status: scaffold
---

# System Taxonomy & Agent Boundaries — Dominion Realm

> **Purpose:** Agent-facing routing and expansion scaffold for Dominion Realm mechanics.
> **Authority:** `core_rules.md` is the SSOT and routing authority. This file expands the taxonomy and gives agents a safe place to put placeholders when a subsystem is not mature yet.
> **Rule:** If a subsystem is undeveloped, create a scaffold entry with boundaries and open questions. Do not invent mature mechanics in the wrong owner file.

---

## Taxonomy Table

| Parent domain | Detailed field | Owner / scaffold | Subsystem examples |
|---|---|---|---|
| **Resources & Capacity** | Resources & Capacity | `resource_system.md` | HP, mana, stamina, reserve, caps, regen, depletion, crash states |
| **Embodiment & Injury** | Embodiment & Injury | `embodiment_injury.md` | anatomy, wounds, trauma, disease, poison, organ damage |
| **Action Systems** | Motion & Positioning | `motion_positioning.md` | movement, balance, momentum, terrain traversal |
| **Action Systems** | Combat & Defense | `combat_defense.md` | attacks, penetration, armor, shields, timing, tactics |
| **Action Systems** | Power Expression | `power_expression.md` | spells, domains, rituals, class abilities, resonance |
| **Action Systems** | Perception & Information | `perception_information.md` | senses, Insight, stealth, illusion, salience, inference |
| **Action Systems / Social & Strategic** | Strategy & Decision Systems | `strategy_decision_systems.md` | AI, tactics, risk, counterplay, planning |
| **World Systems** | Space & Environment | `space_environment.md` | terrain, portals, zones, boundaries, weather, hazards |
| **Growth Systems** | Progression & Identity | `progression_identity.md` | levels, classes, skills, soul, species, mastery |
| **Creation & Infrastructure** | Crafting & Materials | `crafting_materials.md` | gear, alchemy, enchanting, repair, construction materials |
| **Creation & Infrastructure** | Base & Infrastructure | `base_infrastructure.md` | buildings, supply lines, wards, defenses, population support |
| **Creation & Infrastructure / Social & Strategic** | Economy & Logistics | `economy_logistics.md` | costs, trade, transport, scarcity, production chains |
| **Social & Strategic Systems** | Social & Faction Systems | `social_faction_systems.md` | reputation, diplomacy, law, alliances, institutions |
| **Interface & Abstraction** | Interface & Abstraction | `interface_abstraction.md` | stat display, hidden values, diagnostics, system compression |

---

## Agent Expansion Protocol

When asked to expand rules:

1. Identify the taxonomy field.
2. Open `core_rules.md`.
3. Open the owner/scaffold file.
4. Add or edit rules only inside the owner/scaffold.
5. Add a cross-reference in `core_rules.md` only if routing changes.
6. Preserve all terminology firewalls.
7. Prefer placeholders over false precision.

---

## Placeholder Format

Every scaffold should use this structure:

```markdown
# <Subsystem Name>

> **Owner field:**
> **Status:**
> **Owns:**
> **Does not own:**
> **Inputs from:**
> **Outputs to:**

## Canon Locks

## Working Rules

## Placeholder Rules

## Open Questions

## Agent Boundaries
```

---

## Cross-System Firewalls

- **Affinity ≠ Domain.** Affinity is skill-growth chance. Domain is power expression/source.
- **Class ≠ Interface.** Class is method. Interface is personal substrate architecture.
- **HP ≠ Injury.** HP is survivability under damage. Injury is bodily condition.
- **Soul Level ≠ Combat Level.** Soul Level governs metaphysical weight, not direct fight ranking.
- **Item Quality ≠ Item Rarity.** Craft execution and scarcity/significance are independent.
- **Spell Strength ≠ Spell Skill Mastery.** Manifestation quality and practitioner mastery are independent.
- **Aether ≠ ordinary domain.** Aether is higher-order synthesis of the eight Elemental Domains.
- **Frost/Poison/Arcane ≠ domains.** They are disciplines/schools/recipes.
- **Class rarity ≠ bonus attribute point cadence.** Rarity affects XP/energy burden; classes define Prime/Core multipliers.
- **Interface display ≠ reality.** The UI compresses reality; it is useful and incomplete.

---

## Current Maturity

| Field | Maturity | Notes |
|---|---|---|
| Resources & Capacity | Canon working | `resource_system.md` is usable for Book 1. |
| Embodiment & Injury | Scaffold | Needs anatomy, wound, disease, poison, trauma model. |
| Motion & Positioning | Scaffold | Needs movement, momentum, terrain traversal rules. |
| Combat & Defense | Scaffold | Needs damage, armor, penetration, shields, timing. |
| Power Expression | Scaffold | Needs spell/domain/ritual/resonance expansion. |
| Perception & Information | Scaffold | Needs Insight, stealth, illusion, salience integration. |
| Strategy & Decision Systems | Scaffold | Needs AI/tactics/risk/counterplay model. |
| Space & Environment | Scaffold | Needs portals, zones, weather, hazards, terrain. |
| Progression & Identity | Scaffold | Existing pieces split across `core_rules.md`, `classes.md`, `mechanics.md`, `resource_system.md`. |
| Crafting & Materials | Scaffold | Item/gem tiers exist in `mechanics.md`; application rules missing. |
| Base & Infrastructure | Scaffold | Needed for Eriadne/base-building. |
| Economy & Logistics | Scaffold | Needed for trade, scarcity, transport, production chains. |
| Social & Faction Systems | Scaffold | Needed for law, reputation, institutions, diplomacy. |
| Interface & Abstraction | Scaffold | Core premise exists; compression/error/diagnostic rules need expansion. |
