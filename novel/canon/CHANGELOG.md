# Dominion Realm — Canon Changelog

> Single home for change history. Individual files no longer carry "Last updated: …overhaul" footers; record material canon changes here instead.

## Dossier fold-in (mc / seb / mara / eriadne)
- **`seb.md`:** Aetherfall class set to **Warlord**; the Broker reconciled from *bluff* to **honest-but-misread** (restoration genuinely possible; real cost = self-transformation; restoration ≠ continuity) per INV-6; SB-01/SB-02 marked resolved.
- **`mc.md`:** added the **Eyes of Meszkhal** cost (20 + 1%/sec), damage progression, and visible tells; added **Unbound Affinity** and **Insight** (learned before the mindscape); fixed a stale `relationship_map is tiebreak` line -> INV-1/canon_index.
- **`mara.md`:** added the **Walking Grove** second arc (salience-suppression foil; routes to Eriadne); MA-02 flagged.
- **`eriadne.md`:** the convergence is now the **eight elemental ley lines** holding a **dormant natural portal**; the finale forces it open (Myrmidon through it; the breach draws the Realm Walker); W-05 flagged.

## LitRPG mechanics + Book-1 scope batch
- **`Chaotic Affinity` -> `Unbound Affinity`** (rename only; inversion mechanic unchanged — the doc's cross-system redefinition was NOT adopted).
- **Resource formulas locked** (`mechanics.md` is now owner; `core_rules.md` points to it): Health = Con×6+End×2+Str×2; Mana = Int×6+Wis×3+Cha×1; Stamina = End×5+Con×2+Str+Agi+Dex (all -> 50 at 5/5/5). Replaces the old Con×10 placeholder. Added full per-attribute effects + leveling/racial growth (humans 4 free points).
- **Eyes of Meszkhal cost reconciled:** activation **20 mana flat** (was "20% max") + 1%/sec sustained; added the cost-curve/damage-progression/visible-tells pointer.
- **Insight locked + retimed:** notification text and `????` partial-read behavior in `core_rules.md`; Marcus now learns it **before the mindscape** (master_timeline + book1 Ch 4).
- **Seb locked:** Aetherfall **Warlord**; INV-6 refined — the Broker's offer is **honest wording Seb misreads** (not a bluff); real cost = **self-transformation** (become the mechanism); restoration ≠ continuity. Supersedes the provisional "surrender your true name" framing. SB-01/SB-02 resolved; CH-008 updated.
- **Mara locked:** post-court arc = **the Walking Grove** (moving civilization on a sick behemoth; salience-suppression foil for Mirror-Salience; routes her to Eriadne). MA-01 resolved; MA-02 opened (nature of the Grove threat — no N'hal link).
- **N'hal pulled off the Book-1 page** (per scope ruling): Ch 23 reframed to "The Convergence"; master_timeline/roadmap/scene_queue beats de-N'haled; WF-006 marked Book 2+. N'hal remain only in the bible/cosmology as the series-level threat.
- **Ruins/portal/Realm-Walker spine wired:** ruins = convergence of the **eight elemental ley lines** + a **dormant natural portal**; the Myrmidon comes *through* it; the breach draws a **Realm Walker** who witnesses the fight and contacts Marcus late. W-05 opened (who forces the portal).

## N'hal + Nightbound batch
- **Cosmic threat name locked: `Zalgoran` -> `N'hal`** across all canon files.
- **New owner file `nightbound.md`** (factions/): the seven-member interventionist order that brought the six. Resolves "who brought the six": Nightbound (client) -> Soulkeepers' Exchange (contractor) -> Broker (operator) -> Astria. Closes the cosmology TBA slot and registry W-04.
- **N'hal reframed in `cosmology.md`:** retired the substrate-overdraw *trigger*; they advance on their own schedule (why-the-Realm/why-now held open as the founder's hypothesis). Added the load-bearing signature: **N'hal erase the legible structure — the interface/skills/levels degrade near them** (ties Marcus's "interface is not the world" arc and `core_rules.md`). Beatable only by impossible total unity.
- **Registry:** W-04 resolved; NB-01..04 opened (founder identity, the inheritance [held open by design], the seven members, Realm-Walker-as-pathfinder); W-03 note updated (mechanic now canon, on-page visual still open).

## Consolidation pass (this batch)
- **`book1_chapter_timeline.md` reconciled:** retired "Sarah"→"Serra" (7 spots), reframed the Ch-1 duel line to INV-1 (parity FIXED/revealed; recognition EARNED & one-directional Serra→Marcus), routed its open-items list to the registry, kept its file-local chapter-boundary and finale-pending caveats.
- **De-duplicated open items.** `unresolved_character_threads.md` is the sole registry. Removed the duplicated "Open ___" sections from `book_roadmap`, `master_timeline` (kept the timeline-local day-spacing note), `dominion_realm_story_bible`, and `canon_index` (Open Blockers); each now points to the registry.
- **Single precedence source.** Stripped the stale per-file "`relationship_map.md` wins ties / tiebreak" headers from six files (`character_state_log`, `dominion_realm_story_bible`, `master_timeline`, `setup_payoff_tracker`, `scene_queue`, `book_roadmap`). Precedence now lives only in `canon_index.md` (where `relationship_invariants.md` is the top relational authority).
- **Retired `act3_sequence.md`** → its beats live in `master_timeline`/`scene_queue`; its power-state constraint and Dara/Cael/child/Vulture cast texture folded into the `scene_queue` Act-3 section. Tombstoned.
- **Archived `continuity_reconciliation.md`** (sweep complete; lesson moved to `canon_index`). Tombstoned.
- **Slimmed `dominion_realm_story_bible.md`** to a true overview: dropped the per-character `*Power:*` restatements (owned by `character_power_architecture.md`) and collapsed the re-narrated plot spine to a skeleton + pointers. Kept the threat-structure section it owns.
- **Slimmed `canon_index.md`**: per-character class/interface restatement → pointer to `character_power_architecture.md`; folded in the "aggregates lag dossiers" lesson; declared `master_timeline.md` the beat-chronology owner.
- **Footers** across files routed here.

## Prior: consistency overhaul (Batches 1–3)
- SWAT-Serra removed; Serra = self-made social anchor, Realm class **Warrior** (was Ashblade).
- Eyes split: **Neurochromatic Eyes** (interface, six stages) vs **Eyes of Meszkhal** (Unique item).
- **Angelic Fortitude removed** (Zazriel gave no boon).
- Marcus Realm class **Mage → Riftwalker** (Veilwalker retired); **Mara → Psion** (was Spellblade).
- Marcus Earth identity **ML engineer at Astria** / name **Marcus Fahr** (Chad retired); hijacker **Roth**; **Seb's brother dies in the Day-0 scrim** (consent = grief).
- **Mathias lives** (injured at finale); finale = **Xyloryn invasion, one Myrmidon**.
- Opposing guild renamed **Dead Hand**; Aspect forms **Sentinel/Predator/Arbiter/Oracle**.
- New owner files added: `relationship_invariants.md`, `relational_clarity_rules.md`, `classes.md`, `mechanics.md`, `cosmology.md`, `naming_magic.md`.
