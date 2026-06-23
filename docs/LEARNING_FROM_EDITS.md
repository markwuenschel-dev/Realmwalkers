# Learning From the Author's Edits — Design Note

**Status:** design only (not built). Captures how the Desk could learn the author's voice, dialogue,
structure, and continuity preferences from the edits they make in review. Aligns with DESIGN §11 —
*"the human's verdict = authoritative gate AND future training label."*

The goal is not a fine-tuned model on day one. It is a **layered system** where every edit you make
quietly improves the next draft, cheapest mechanism first, with fine-tuning as a much later option.

---

## What already exists (the foundation is ~70% here)

**The signal is being captured as a side effect.** Every hand-edit in review produces a recoverable
*before → after* pair:
- `Scene.agent_original` — the model's draft (set at draft time, `pipeline.py`).
- `Scene.prose` — the author's edited text (overwritten in `reviews.decide` when `edited_prose` is sent).
- `Scene.prose_source` — flips to `"agent+human_edit"` on a hand-edit.
- `Approval.feedback` — every revision note the author types (the *verbal* signal).

> ⚠ Caveat: `agent_original` holds the **marker form** (raw ```stat blocks), while `prose` is the
> rendered + edited text — so a naive diff is noisy. A clean capture (Tier 1) fixes this.

**The drafter already knows how to imitate the author** — `drafter._voice_system` injects, per POV:
- `ctx.voice_spec` ("Voice for {pov}: …"), and
- `ctx.exemplars` ("Match the voice of these passages: …").

**But one wire is cut.** `PovProfile.exemplar_scene_ids` is stored, and the drafter *consumes*
`ctx.exemplars`, yet `context.assemble_context` never loads those scene ids into `ctx.exemplars`.
So stored exemplars never reach the drafter today. Closing that gap is most of Tier 2.

**Per-draft, read-fresh knobs** that take effect on the *next* scene with no redeploy:
- `PovProfile.voice_spec` (set via `set_voice.py`).
- `novel/style/dialogue_rules.md` (re-read every draft; scoped to characters present).

---

## Mapping the four learning targets to mechanisms

| Target | Primary mechanism | Where it lives |
|---|---|---|
| **Voice / prose style** | Exemplars (in-context) + distilled `voice_spec` | `PovProfile`, `drafter._voice_system` |
| **Dialogue habits** | Distilled rules | `novel/style/dialogue_rules.md` |
| **Structure / pacing** | Distilled rules now; fine-tune later | `voice_spec` / `_CRAFT`; Tier 5 |
| **Continuity / fact fixes** | *Not the drafter* — beat/ledger + a corrections memory | beats, `character_state`, continuity reviewer, canon |

Key point on **continuity/fact fixes**: these should generally *not* be taught to the drafter as
"style." If you keep correcting the same fact, the durable fix is to update the **beat's declared
deltas**, the **ledger** (via a continuity "keep prose → fix ledger" resolution), or **canon** —
not to nudge the prose model. A recurring-correction log that proposes canon/ledger updates is the
right shape here, kept distinct from the voice pipeline.

---

## The tiers (cheapest / highest-leverage first)

### Tier 1 — Clean capture (foundation)
On a hand-edit, snapshot the model's **rendered** draft next to the author's version so we have a
faithful pair, instead of diffing against the marker-form `agent_original`.
- Add an `edit_pairs` table (or columns): `scene_id`, `version`, `pov`, `agent_text`,
  `human_text`, `created_at`. Write it in `reviews.decide` when `prose_source` becomes
  `agent+human_edit`.
- No drafting behavior changes yet — this is the dataset every later tier reads.

### Tier 2 — Exemplars (in-context voice; ~90% built)
- **Fix the cut wire:** in `assemble_context`, load `PovProfile.exemplar_scene_ids` → fetch those
  scenes' prose → `ctx.exemplars`. Cap count/length to protect the token budget.
- **Add a "use as voice exemplar" action** in the scene editor (and/or auto-suggest scenes you
  edited heavily). Stores the scene id on the POV profile.
- Effect: the drafter few-shots on *your* approved/edited prose for that POV, immediately, no
  training. Reversible (un-mark to remove). Best lever for **voice**.

### Tier 3 — Distill edits → rules
A periodic, human-in-the-loop job: a review-model pass reads the recent before→after pairs (Tier 1)
and **proposes** additions to `voice_spec` (voice/structure) and `dialogue_rules.md` (dialogue),
e.g. *"trims filter verbs (saw/felt/noticed)," "dialogue tags stay 'said'/'asked'."* The author
approves/edits before anything is written. Both files are read fresh per draft, so accepted rules
land on the next scene. Best lever for **dialogue** and durable **style/structure** preferences.

### Tier 4 — Revision-time priming
When a revision is requested, include a few of the author's recent before→after edits in the
revise prompt (`_revise_prompt`) — "here's how the author tends to fix prose" — so a redraft mirrors
their hand. Bounded, cheap, complements Tier 2/3.

### Tier 5 — Fine-tune / preference optimization (later)
Once enough pairs accrue, export Tier-1 data as a SFT/DPO dataset (model `agent_text` → preferred
`human_text`). Heavyweight (cost, eval, drift risk); deferred until volume and a stable voice
justify it. In-context tiers should carry the project a long way first.

---

## Sequencing recommendation

1. **Tier 1** (capture) — small, unblocks everything, no behavior change.
2. **Tier 2** (exemplars) — fastest visible payoff; the wire is nearly there.
3. **Tier 3** (distilled rules) — turns accumulated edits into durable voice/dialogue rules.
4. **Tier 4** as a quick follow-on to 3.
5. **Tier 5** only when the dataset is large and the voice is stable.

## Open questions / decisions
- **Per-book or global learning?** `PovProfile`, `voice_spec`, and the ledger are per-book; the
  drafter's `dialogue_rules.md` is global. Decide whether learned style is book-scoped or carries
  across books.
- **Exemplar selection:** author-curated only, or auto-propose "heavily edited" scenes as candidates?
- **Exemplar budget:** how many / how long before they crowd the context window.
- **Marker-form noise:** does Tier 1 store rendered text, marker text, or both?
- **Continuity corrections:** build the recurring-fix → canon/ledger proposer now, or defer?
- **Voice drift / staleness:** as the author's style evolves, exemplars/rules need pruning — who
  prunes, and when?

---

*Cross-refs: `docs/DESIGN.md` §11 (training label), `src/dominion/workers/specialists/drafter.py`
(`_voice_system`, exemplars), `src/dominion/workers/context.py` (`assemble_context`),
`src/dominion/workers/set_voice.py`, `src/dominion/api/routers/reviews.py` (`decide`),
`novel/style/dialogue_rules.md`.*
