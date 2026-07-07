# Learning From the Author's Edits — Design Note

**Status:** **Tier 1 (clean capture), Tier 2 (exemplars), and Tier 3 (distilled rules) are built;**
Tiers 4–5 remain design. Every hand-edit records a faithful `agent→human` pair (`EditPair`), the
drafter few-shots on the author's curated approved prose per POV, and a human-gated distillation job
now turns recent edits into proposed voice/dialogue rules the author accepts into `voice_spec`. The
remaining Tier-2 nicety — an in-editor "use as voice exemplar" button — and the fine-tune tier are
still ahead. Captures how the Desk learns
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
author order preserved, the revised scene excluded). The exemplar list is authored via the
`POST /scenes/{id}/exemplar` endpoint (`set_exemplar`, `api/routers/scenes.py`), which writes the same
`exemplar_scene_ids` field (the former `legacy/set_exemplars.py` terminal CLI has since been removed).

**Per-draft, read-fresh knobs** that take effect on the *next* scene with no redeploy:
- `PovProfile.voice_spec` (now populated by accepting distilled rules through the learning router —
  `POST /rule-proposals/{id}/decision`; the former `legacy/set_voice.py` CLI has since been removed).
- `series/style/dialogue_rules.md` (re-read every draft; scoped to characters present).

---

## Mapping the four learning targets to mechanisms

| Target | Primary mechanism | Where it lives |
|---|---|---|
| **Voice / prose style** | Exemplars (in-context) + distilled `voice_spec` | `PovProfile`, `drafter._voice_system` |
| **Dialogue habits** | Distilled rules | `series/style/dialogue_rules.md` |
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
- [x] **Authoring path:** the `POST /scenes/{id}/exemplar` endpoint (`api/routers/scenes.py`) upserts the
  list; it touches only `exemplar_scene_ids`, leaving `voice_spec` untouched, so the two are independent.
  (The former `legacy/set_exemplars.py` / `set_voice.py` terminal CLIs have since been removed.)
- [x] **In-editor "use as voice exemplar" action** — `api.setExemplar` (`client.ts`) wired via
  `useDeskSceneActions`, writing the same `exemplar_scene_ids` field. Auto-suggesting heavily-edited
  scenes as exemplars is still to do.
- Effect: the drafter few-shots on *your* approved/edited prose for that POV, immediately, no
  training. Reversible (un-mark to remove). Best lever for **voice**.

### Tier 3 — Distill edits → rules ✅ built
A periodic, human-in-the-loop job: a review-model pass reads the recent before→after pairs (Tier 1)
and **proposes** durable voice/dialogue rules, e.g. *"trims filter verbs (saw/felt/noticed)," "dialogue
tags stay 'said'/'asked'."* The author approves/edits/rejects before anything is written; accepted
rules land on the next scene because `voice_spec` is read fresh per draft. Best lever for **dialogue**
and durable **style/structure** preferences.
- [x] **Distiller** — `workers/learning/distill.py`: `load_recent_pairs` (the POV's most-recent
  `EditPair`s, joined through scene→chapter; no-op/empty pairs dropped), `candidate_povs`, and
  `propose_rules` (one bounded `review_model` call, tolerant JSON parse via `reviewers/base.py`, a
  `TimeoutError`→504 on a hung call, mirroring the planner). Capped by `settings.distill_max_pairs` /
  `distill_pair_max_chars` / `distill_time_budget_s`.
- [x] **Model + API** — `RuleProposal` (`shared/models.py`: book/pov/kind/rule_text/rationale/
  source_pair_ids/status). `learning` router: `POST /books/{id}/distill` (per-POV or all POVs with
  edits; deduped against existing non-rejected proposals), `GET /books/{id}/rule-proposals`,
  `POST /rule-proposals/{id}/decision`. Accept appends the rule to the POV's `PovProfile.voice_spec`
  (find-or-create), applied only on the pending→accepted transition so re-accepting can't double-write.
- [x] **Desk** — Ledger → "Voice rules": a "Distill from edits" button, then accept (editable text) /
  reject per proposal. New `RuleProposal` table → **rerun `scripts/init_db.py`** (additive `create_all`).
- **Deviation:** distilled *dialogue* rules are stored in the per-POV `voice_spec` (DB-backed, read
  fresh per draft) rather than appended to the global `series/style/dialogue_rules.md` — the deploy
  filesystem is ephemeral, so a file append wouldn't persist. `dialogue_rules.md` stays the
  hand-authored, authoritative source; distilled rules are per-POV learned *preferences*.
- [ ] **Continuity-correction proposer** (recurring fact fixes → canon/ledger, kept distinct from the
  voice pipeline) — still design (see "Mapping" above).

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
3. ✅ **Tier 3** (distilled rules) — turns accumulated edits into durable voice/dialogue rules.
   *Done (bar the continuity-correction proposer).*
4. **Tier 4** (revision-time priming) as a quick follow-on to 3. **← next**
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
`src/dominion/api/routers/scenes.py` (`set_exemplar` — exemplar authoring; the former
`legacy/set_voice.py` + `legacy/set_exemplars.py` CLIs since removed),
`src/dominion/api/routers/reviews.py` (`decide`, `_capture_edit_pair`), `EditPair` +
`RuleProposal` in `src/dominion/shared/models.py`, `src/dominion/workers/learning/distill.py` +
`src/dominion/api/routers/learning.py` (Tier 3), `frontend/src/desk/screens/LedgerScreen.tsx`
(Voice rules surface), `series/style/dialogue_rules.md`.*
