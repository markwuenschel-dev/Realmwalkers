# Learning From the Author's Edits — Design Note

**Status:** **Tier 1 (clean capture) and Tier 2 (exemplars) are built;** Tiers 3–5 remain design.
Every hand-edit now records a faithful `agent→human` pair (`EditPair`), and the drafter few-shots on
the author's curated approved prose per POV (the once-cut exemplar wire is connected, plus a
`set_exemplars` CLI to author the list). The remaining Tier-2 nicety — an in-editor "use as voice
exemplar" button — and the distillation/fine-tune tiers are still ahead. Captures how the Desk learns
the author's voice, dialogue, structure, and continuity preferences from the edits they make in
review. Aligns with DESIGN §11 — *"the human's verdict = authoritative gate AND future training label."*

> New persistent table `EditPair` — additive `create_all`, so **rerun `scripts/init_db.py`** (matches
> the migration note in `ROADMAP.md`).

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

**The once-cut wire is now connected (Tier 2).** `PovProfile.exemplar_scene_ids` is stored and the
drafter *consumes* `ctx.exemplars`; `context.assemble_context` now loads those scene ids' prose into
`ctx.exemplars` (`_load_exemplars`, capped by `settings.exemplar_max_count` / `exemplar_max_chars`,
author order preserved, the revised scene excluded). `set_exemplars.py` authors the list from the
terminal (the eventual in-editor button writes the same field).

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

### Tier 1 — Clean capture (foundation) ✅ built
On a hand-edit, snapshot the model's **rendered** draft next to the author's version so we have a
faithful pair, instead of diffing against the marker-form `agent_original`.
- [x] `EditPair` table — `scene_id`, `version`, `pov`, `agent_text`, `human_text`, `created_at`
  (`shared/models.py`). Written by `reviews._capture_edit_pair`, called from `decide` on the hand-edit
  path **before** `scene.prose` is overwritten. `agent_text` is the rendered draft —
  `render_stat_blocks(agent_original)` — so the diff isn't noisy with ```stat``` markers (falls back to
  the pre-edit prose for older scenes with no `agent_original`).
- [x] **Re-edit safe:** upsert per `(scene_id, version)` — a re-edit refreshes only `human_text`, so we
  keep the true model draft and never record a human→human pair.
- No drafting behavior changes — this is the dataset every later tier reads.

### Tier 2 — Exemplars (in-context voice) ✅ wire + CLI built
- [x] **Cut wire fixed:** `assemble_context` loads `PovProfile.exemplar_scene_ids` → those scenes'
  prose → `ctx.exemplars` (`_load_exemplars`): capped by count/length to protect the token budget,
  author order preserved, the scene being revised excluded.
- [x] **Authoring path:** `set_exemplars.py` (mirrors `set_voice`) upserts the list from the terminal;
  `set_voice` still leaves exemplars untouched, so the two are independent.
- [ ] **In-editor "use as voice exemplar" action** (and/or auto-suggest heavily-edited scenes) — still
  to do; it writes the same `exemplar_scene_ids` field the CLI does.
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

1. ✅ **Tier 1** (capture) — small, unblocks everything, no behavior change. *Done.*
2. ✅ **Tier 2** (exemplars) — fastest visible payoff; the wire is connected + a CLI authors the list.
   *Done (bar the in-editor button).*
3. **Tier 3** (distilled rules) — turns accumulated edits into durable voice/dialogue rules. **← next**
4. **Tier 4** as a quick follow-on to 3.
5. **Tier 5** only when the dataset is large and the voice is stable.

## Open questions / decisions
- **Per-book or global learning?** `PovProfile`, `voice_spec`, and the ledger are per-book; the
  drafter's `dialogue_rules.md` is global. Decide whether learned style is book-scoped or carries
  across books.
- **Exemplar selection:** author-curated for now (`set_exemplars`); auto-proposing "heavily edited"
  scenes as candidates is still open.
- **Exemplar budget:** capped by `settings.exemplar_max_count` (3) / `exemplar_max_chars` (1500) for
  now — tune as we see real prompts.
- ~~**Marker-form noise:** does Tier 1 store rendered text, marker text, or both?~~ **Resolved:**
  `EditPair.agent_text` stores the **rendered** draft (render the stored marker form), so a diff
  against `human_text` isn't polluted by ```stat``` markers.
- **Continuity corrections:** build the recurring-fix → canon/ledger proposer now, or defer?
- **Voice drift / staleness:** as the author's style evolves, exemplars/rules need pruning — who
  prunes, and when?

---

*Cross-refs: `docs/DESIGN.md` §11 (training label), `src/dominion/workers/specialists/drafter.py`
(`_voice_system`, exemplars), `src/dominion/workers/context.py` (`assemble_context`, `_load_exemplars`),
`src/dominion/workers/set_voice.py` + `set_exemplars.py` (authoring CLIs),
`src/dominion/api/routers/reviews.py` (`decide`, `_capture_edit_pair`), `EditPair` in
`src/dominion/shared/models.py`, `novel/style/dialogue_rules.md`.*
