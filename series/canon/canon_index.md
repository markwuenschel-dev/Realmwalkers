# Dominion Realm — Canon Index & Wiring

> **Purpose:** Single entry point for the canon set. Agents start **here**. Establishes read order, precedence, which file owns which fact, the naming/term canon, per-file status after the overhaul, and what's ready to draft.

---

## Repo layout (series monorepo)

Shared canon lives under `series/` (`canon/`, `style/`, `voice/`); each book's planning/manuscript/outline lives under `bookN/`. Book 1 = `book1/`. **Rule:** *persists across books → `series/`; about one book's chapters or prose → `bookN/`.*

- **Series:** `series/canon/` (characters, continuity, factions, litrpg_system, locations, world, `relationship_invariants.md`, `dominion_realm_story_bible.md`, `canon_index.md`, `CHANGELOG.md`) · `series/style/` · `series/voice/`
- **Book 1:** `book1/planning/` (`book1_chapter_timeline`, `book1_chapter_plan`, `book_roadmap`, `finale_spine`, `scene_queue`, `setup_payoff_tracker`, `master_timeline`, `act3_sequence` [retired]) · `book1/manuscript/` · `book1/outline/`

---

## Precedence & Tiebreak

1. Newest explicit author decisions
2. **`relationship_invariants.md`** — **owner** for relational facts (standing, recognition, dynamic). For any fact about how two characters stand relative to each other, THIS file wins; scenes/dossiers/`relationship_map.md` reconcile *to* it. **"Newest artifact wins" does NOT apply here** — a scene that contradicts an invariant is the thing that's wrong.
3. **`relationship_map.md`** — ordinary relational description; tiebreak authority on relational conflicts **not covered by an invariant**. Subordinate to `relationship_invariants.md`.
4. `character_power_architecture.md` — power/classes/interfaces (Character-Origin model + Realm power stack)
5. This index — the change record
6. Other current planning/canon docs
7. Original manuscript
8. Previously proposed but **unselected** options

**"Unselected" ≠ "retired."** Ashblade, Veilwalker, Mara-as-biologist may resurface as rare/unique paths.

---

## Source of Truth — which file owns which fact

| Topic | Authoritative file |
|---|---|
| Relational invariants (standing, recognition, dynamic) | `relationship_invariants.md` |
| Relationships; Marcus↔Serra; the Earth Thing (ordinary description; **defers to `relationship_invariants.md`** on any standing/recognition/dynamic fact) | `relationship_map.md` |
| The **Marcus–Serra relationship** — conflict engine, progression, motifs, guardrails (the series-spine pairing; governs with INV-1/INV-3 + `forbidden_drift.md` #10; does **not** own chapter placement or days) | `characters/marcus_serra_relationship.md` |
| How relationships are *written* (Critic enforcement; RR1–RR8) — **style**-tier, sibling to `prose_clarity_rules.md` | `relational_clarity_rules.md` |
| Power models (Character-Origin + Realm power stack), cast power table | `character_power_architecture.md` |
| Class taxonomy, rarity tiers, specializations | `classes.md` |
| Tier ladders: spell strength, item quality/rarity, gems, skill mastery, soul level | `mechanics.md` |
| Cosmic powers, factions, the Xyloryn, Realm Walkers | `cosmology.md` |
| The six interfaces (biology, 5 stages, Happy/Forbidden) | character dossiers + `character_power_architecture.md` |
| LitRPG mechanics, UI, the Eyes split, namebinding | `core_rules.md` |
| Chapter-by-chapter structure | `book1_chapter_timeline.md` |
| Event chronology (Earth + Realm dates) | `master_timeline.md` |
| Act structure, themes, emotional spine | `book_roadmap.md` |
| Settled vs. open questions | `unresolved_character_threads.md` |
| Planted promises & required payoffs | `setup_payoff_tracker.md` |
| Per-character status snapshots | `character_state_log.md` |
| Planned scenes & briefs | `scene_queue.md` |
| Eriadne (city/ruins/ley knot) | `eriadne.md` |
| Realm calendar | `realm_calendar.md` |
| Series overview | `dominion_realm_story_bible.md` |

If two files disagree, the **owner** above wins for its topic. **Aggregates lag dossiers:** summary files (timeline, roadmap, bible, state-log) trail the owner files, so a draft that leans on a summary can inherit stale canon. Rule: cite the *owner* file for any fact; never inherit a prior cycle's notes. *(Durable lesson from the now-archived `continuity_reconciliation.md`.)* For relational facts (standing/recognition/dynamic), `relationship_invariants.md` wins over `relationship_map.md` and is **not** overridden by a newer artifact. For relations *not* covered by an invariant, `relationship_map.md` and newest author decisions remain the tiebreak.

---

## Per-File Status (post-overhaul)

| File | Status | Action needed |
|---|---|---|
| `relationship_invariants.md` | ✅ current (new) | Owner file for relational facts; INV-1…INV-6. Wins over `relationship_map.md` on standing/recognition/dynamic. |
| `relational_clarity_rules.md` | ✅ current (new, style) | Style-tier Critic enforcement (RR1–RR8); lives in `series/style/`, sibling to `prose_clarity_rules.md`. |
| `relationship_map.md` | ✅ current | Ordinary relational description; **defers to `relationship_invariants.md`**. |
| `marcus_serra_relationship.md` | ✅ current (new) | Relationship bible for the series-spine pairing (conflict engine / progression / motifs / guardrails). Governs with INV-1, INV-3, `forbidden_drift.md` #10; defers to `book1_chapter_timeline.md` on placement and `master_timeline.md` on days. |
| `character_power_architecture.md` | ✅ current | — |
| `canon_index.md` | ✅ this file | — |
| `book1_chapter_timeline.md` | 🔧 updated this batch | Act 5 finale choreography pending |
| `unresolved_character_threads.md` | 🔧 regenerated this batch | — |
| `dominion_realm_story_bible.md` | ✅ updated (Batch 2) | Reframed as overview; SWAT-Serra, Eyes naming, 4-layer, Angelic Fortitude all fixed |
| `master_timeline.md` | ✅ updated (Batch 2) | Angelic Fortitude, Ashblade, Mathias-lives, finale Myrmidon fixed; redundant sections merged
| `core_rules.md` | ✅ updated | Eyes split; namebinding; **Class System + 6-tier ladder added** (this pass) |
| `classes.md` | ✅ current (new) | Full taxonomy; Fighter/Adventurer added; Planeswalker→Riftwalker; **+Exceptional tier** |
| `mechanics.md` | ✅ current (new) | Spell/item/gem/skill/soul ladders + affinity tiers; Soul Level wired to names/vows |
| `cosmology.md` | ✅ current (new) | 7 powers + Xyloryn deep model + Realm Walkers; several names open |
| `scene_queue.md` | ✅ updated (Batch 3) | Consolidated; retired dead entries; Ashblade→Warrior; satellites added |
| `setup_payoff_tracker.md` | ✅ updated (Batch 3) | Eyes split; CH-017 Warrior; WF-011/012 namebinding+parent; Mathias lives |
| `book_roadmap.md` | ✅ updated (Batch 3) | Angelic Fortitude removed; Serra-wrong resolved; finale = Myrmidon |
| `character_state_log.md` | ✅ updated (Batch 3) | Angelic Fortitude removed; Serra=Warrior; interfaces; finale checkpoint |
| `eriadne.md` | ✅ verified current | No changes — already on the redesigned (ruins-on-outskirts) version. Stays in project. |
| `realm_calendar.md` | ✅ updated | **CAL-01 resolved** (the Forgotten Nine) + A.C. notation; CAL-02/03 still open. |

**Batch plan:** Batch 1 ✅ · Batch 2 ✅ · **Batch 3 ✅ — consistency overhaul complete.** Every canon file is now internally consistent and wired through this index. `eriadne.md` and `realm_calendar.md` were verified current and stay in the project unchanged; all updated files are in `/outputs` to merge back.

---

## Naming & Term Canon (global find/replace + do-not-use)

**USE:**
- Earth name **Marcus Vye** (never ~~Chad~~) — the suited Astria figure may address him as "Mr. Vye"
- **No Realm alias** — he keeps his real name, **Marcus** (rejects the gamer-tag instinct, "Phoenix" included); his **true name** is separate, hidden, deferred (S-09)
- Earth guild = **404: Aim Not Found** (top-25 Aetherfall; "Aim Not Found" is an ironic name, NOT Marcus's role — he is an **Aspect**, four-form/adaptive, not an archer)
- **Roth** = the suited Astria figure / the hijacker who commandeers the scrim (canon name; kept **unnamed on-page**; first on-page in SCENE-002, NOT SCENE-001)
- **Kip** = 404 frontline warrior (recurring_minor); impatient forward-pressure foil to Mathias
- **Serra Hawthorne** — name is **Serra** (surname **Hawthorne** retained → "Serra Hawthorne"); **"Sarah" is retired** (do not use as a live name). Self-made social anchor, ordinary background, warm-but-guarded, privately lonely. **No SWAT, no justice-from-trauma.** In SCENE-001 she is **Dead Hand's star rogue** — late to log in, then **duels Marcus on-page**. Marcus↔Serra relational facts (standing/recognition/dynamic) are governed by **INV-1 (`relationship_invariants.md`)**: parity is FIXED (equals, revealed not earned), recognition is EARNED & one-directional (Serra→Marcus). (Full Sarah→Serra prose rename pending pass 2b.)
- Ocular **interface = Neurochromatic Eyes** (perception; six stages Limbal Shift→Prism Coherence)
- **Item = Eyes of Meszkhal** (interpretation/certainty overlay; demon-biased; "certainty that lies with confidence")
- Swarm = **Xyloryn** (never Xylorane/Xylorin)
- Cosmic threat = **N'hal** (locked)
- Marcus's progression trait = **Unbound Affinity** (renamed from *Chaotic Affinity*; inversion mechanic unchanged — do not use the old name)
- Seb Aetherfall class = **Warlord** (locked)
- **Insight** = baseline read-skill, learned *before* the mindscape
- Per-character Realm classes & interfaces → `character_power_architecture.md` (cast table) owns these; not restated here, to keep one source of truth.
- **Aetherfall: Genesis** = full/proper name (first reference, formal contexts); **Aetherfall** = shorthand in prose/conversation.

**DO NOT USE as current canon:**
- **Angelic Fortitude** (removed — Zazriel gave nothing)
- **Ashblade / Veilwalker** as Realm classes (unselected; rare/unique at most)
- **Mara as sole biologist** (Xyloryn is collaborative)
- "Eyes of Meszkhal" to mean the ocular system/progression (that is the *interface*, Neurochromatic Eyes)
- **Champion / empower-others** framing for Seb (retired)
- **Fire-mage Brent** (retired — Brent's Aetherfall role is **Healer/Support / squad medic**, confirmed on-page in SCENE-001; never a fire mage)
- **Veilwalker / Ashblade** as Marcus's class on Earth or as live Earth vocabulary (do not leak Realm terms into Earth context)

---

## Project Canon Principle (applies project-wide, beyond any one pairing)

> **Canon exists to preserve the strongest story decisions. Story decisions do not exist to preserve canon.** An invariant remains locked only while the narrative function it protects remains necessary and superior to the alternatives.

*(Lives also in `marcus_serra_relationship.md` §12, where it was first stated.)*

---

## Drafting Readiness

**Ready to draft now (canon locked):** the **opening** — Act 1 (Earth / Astria / scrim / coerced consent / LeBlanc hidden-voice) and Act 2 (mindscape → death → resurrection → Ayla → naming = keeps Marcus, refusing the game-handle). The Marcus–Serra Earth Thing is locked, so the early emotional charge can be written.
→ **Gate cleared (Batch 2):** the series bible is now consistent, so an agent can be pointed at the opening. (Batch 3 files are planning/tracking docs, not character/world references — they won't poison drafting, but should be finished before deep Act 3+ work.)

**Not yet draft-ready (open):** Act 5 **finale choreography** (Xyloryn invasion × reunion × severance) and who forces the portal (W-05); Brent's first practical discovery (BR-02). *(Resolved: Seb = Warlord + disclosed-vs-hidden cost; Mara's post-court arc = the Walking Grove.)*

---

## Open Blockers

Full registry and statuses live in `unresolved_character_threads.md`. This index does not duplicate the list.
---

*This index is the front door.* <!-- propagated: CCR-007 -->
