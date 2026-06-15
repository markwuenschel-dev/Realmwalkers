# Relational Clarity Rules — Dominion Realm

> **Status:** Style/enforcement canon. Binding on the Coder, enforced by the Critic.
> **Home:** `novel/style/relational_clarity_rules.md`
> **Source of truth:** the facts live in `relationship_invariants.md`. This file is *how the Critic checks a scene against them* — the relational counterpart to `prose_clarity_rules.md` (which guards physical clarity).
> **Why this exists:** invariants drift when corrections over-apply — a fix to one axis deletes another. These are the named ways a scene gets *standing* wrong, written to be caught mechanically.

---

## The one test (Critic runs this on every scene)

**For every relationship this scene touches, is each axis shown as itself — FIXED axes as already true, EARNED axes as in motion — with neither failure pole on the page?**

A scene that collapses a two-axis relationship into one, shows a FIXED standing as earned, or shows an EARNED standing as given, fails. Every "no" is a flag, tied to the invariant (INV-#) it breaks.

---

## RR1 — Don't collapse two axes into one
If `relationship_invariants.md` lists two axes for a pair, keep them separate. The classic break is treating parity and recognition as one thing.
- **Broken:** "they recognized each other as equals through play." *(parity + recognition fused; implies the recognition established the equality.)*
- **Fixed:** the equality is a given the scene never argues; what moves is *her* recognition of *him*.
- **Critic check:** name the axes the scene touches (INV-#). Any pair treated as a single quantity? Flag.

## RR2 — A FIXED standing is shown as already true, never earned on-screen
- **Broken:** the duel makes Marcus Serra's equal — he climbs to her level.
- **Fixed:** he is already her equal; the duel lets her *see* it.
- **Critic check:** does any beat depict a FIXED axis being *acquired*? Flag (INV-1 A; INV-2 D/E).

## RR3 — An EARNED standing is shown in motion, never granted free
- **Broken:** Serra arrives already treating Marcus as her rival/target ("you're the one I'm here for").
- **Fixed:** she's there to win; he makes himself her problem; recognition arrives across the exchange.
- **Critic check:** is an EARNED axis present at full strength before the scene earns it? Flag (INV-1 B).

## RR4 — Direction discipline (one-directional stays one-directional)
- **Broken:** mutual-at-first-sight — both clock each other as peers simultaneously.
- **Fixed:** Marcus rates her; she doesn't rate him going in. Recognition flows one way until earned.
- **Critic check:** for any ONE-DIRECTIONAL axis, is the scene making it reciprocal early? Flag (INV-1 B).

## RR5 — Block both poles, not just the named-wrong one
Every invariant has two failure poles; fixing one can overshoot into the other.
- **Broken (pole 1):** Marcus dominates Serra. **Broken (pole 2):** Marcus is the underdog who levels up to her.
- **Fixed:** equal in fact; the only gap is her awareness. *Reveals parity, does not confer it.*
- **Critic check:** for each invariant touched, confirm **neither** pole is present. A scene can be clean of one and guilty of the other.

## RR6 — Present ≠ surfaced
- **Broken (delete):** the Marcus/Serra charge written out entirely "because no romantic tension on first read." **Broken (announce):** the charge stated or staged as romantic tension.
- **Fixed:** charge present and unspoken — recognition reads as *this specific person matters*, not attraction.
- **Critic check:** for any PRESENT·SUBSURFACE axis, is it (a) missing or (b) on the surface? Either flags (INV-1 C).

## RR7 — Independent agenda preserved
Each non-POV character must want something not about Marcus.
- **Broken:** Serra's move exists to create a moment for Marcus; no goal of her own in the beat.
- **Fixed:** Serra takes the optimal line to *win the scrim* (kill the support); Marcus inserting himself is incidental to her plan.
- **Critic check:** strip the POV character — does each other character still have a want? If a character's only function is to advance Marcus, flag (INV-3; `forbidden_drift.md` #10).

## RR8 — Standing is encoded in address (names and forms)
Who calls whom what is a relational fact, not flavor.
- **Broken:** a Realm native calls Marcus "Marcus"; "Sarah" appears anywhere; an Earth teammate calls him "Soren" before that line is crossed.
- **Fixed:** Earth circle → "Marcus"; Realm → "Soren"; Serra is "Serra" throughout; a Realm native using anything but "Brent" for Brent would itself be a *story event*.
- **Critic check:** does every term of address match the speaker's relationship and story phase? Flag leaks (cross-ref `prose_clarity_rules.md` R7, `canon_index.md` naming canon).

---

## How the agents use this

**Coder:** read with `relationship_invariants.md` before any two-character scene. After drafting, self-run The One Test and RR1–RR8.
**Critic:** run The One Test as a hard gate, then walk RR1–RR8. Any flag → CHANGES REQUESTED, with the failing line quoted, the rule number, and the invariant (INV-#) it breaks. A scene that fails The One Test cannot be APPROVED regardless of prose quality.
**Wiring:** add to the Coder's required-reads and the Critic's checklist alongside `prose_clarity_rules.md`; list under the **style** tier in `canon_index.md`. Governs *how relationships are written*; `relationship_invariants.md` governs *what is true.*

*Derived from the SCENE-001 Serra revision and the cycle-4 "equals → earned" over-correction.*
