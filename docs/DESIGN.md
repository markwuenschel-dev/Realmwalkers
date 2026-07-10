# Dominion Realm Writing System — Build Spec

> Status: decisions settled. This is the document to build Phase 1 from. A compact decisions log is at the end for traceability.

## 0. The one invariant everything else serves

The system does a **bounded** unit of work, **persists it**, and **halts**. Forward motion happens only on a human signal (your approve/deny) — never on the system's own initiative. There is no resident process re-reasoning itself on boot. Intelligence lives in the writers and reviewers; the seat that decides *what runs when* is plain code; the seat that owns *what is true* is plain code; **you are the only gate.**

Success metric, the only one tracked: **scenes you've approved.** Not PRs, not green tests, not agents built.

---

## 1. Topology — one deployable system

```
NOVEL SYSTEM  (one repo, ONE container on the shared box, one Postgres)
┌──────────────────────────────────────────────────────────────────┐
│  Next.js Writers' Desk ──/api/desk/*──► FastAPI ────► Postgres   │
│   (BFF, same-origin proxy)             (thin layer)  (+pgvector) │
│    • inbox (pending scenes)             read queue    SOURCE OF  │
│    • scene view + advisory flags        write decisions  TRUTH   │
│    • continuity panel (resolve)         queue draft jobs         │
│    • hand-edit prose                                             │
│    • packet approval surfaces (gates 1a/1b)                      │
└──────────────────────────────────────────────┬───────────────────┘
                                               │ claim job / write scene
                                     ┌─────────▼────────────┐
                                     │  draft worker        │
                                     │  (single-flight      │
                                     │   background task)   │
                                     │  one scene → done    │
                                     │  router + specialists│
                                     └──────────────────────┘
```

Everything ships as a **single container** on the shared AWS box: the Dockerfile builds the Next.js frontend
(standalone output) and runs it alongside FastAPI — Next serves the public port and proxies
same-origin `/api/desk/*` to FastAPI on an internal port, so there is no separate API host and no
CORS (see [`DEPLOY.md`](DEPLOY.md)). The ~20-min generation **never** runs in a request handler.
FastAPI is a thin DB-facing API; drafting runs as a browser-triggered, single-flight background
task (`POST /jobs/draft-next` drains the queue) that generates exactly one scene at a time and
stops — no separate worker service, and nothing resident between approvals.

---

## 2. Repo & deployment

| Repo | Contents | Deploy |
|---|---|---|
| `Realmwalkers` (this monorepo) | `frontend/` (Next.js BFF + Writers' Desk), `src/dominion/` (`api/` FastAPI, `workers/` Python, `shared/` schema + Pydantic models used by both), `series/` + `book1/` (authored canon) | one service in the shared-box Docker Compose stack, built from the `Dockerfile` (Next standalone + FastAPI in a single container), behind Caddy; Postgres → shared `pgvector/pgvector` container, private `realmwalkers` db (persistent volume, internal Docker network only) |

`api/` and `workers/` co-locate deliberately: they share one Postgres schema and one set of Python models, so there's nothing to keep in sync across languages. There is no separate frontend host and no separate worker box — one container, one URL (see [`DEPLOY.md`](DEPLOY.md)).

---

## 3. Data model (Postgres + pgvector)

Skeletal DDL — the shape to code from, not the final column set.

```sql
create table books (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  premise text,
  created_at timestamptz default now()
);

-- Chapters own POV (Game-of-Thrones model: one POV per whole chapter) and the outline.
create table chapters (
  id uuid primary key default gen_random_uuid(),
  book_id uuid references books(id),
  chapter_no int not null,
  pov text not null,                 -- the single narrating character for this chapter
  outline text,                      -- your few-sentence outline; input to beat-proposal
  status text default 'planned',     -- planned|beats_proposed|beats_approved|drafting|done
  unique (book_id, chapter_no)
);

-- Voice + few-shot exemplars per narrating character (6 of them).
create table pov_profiles (
  id uuid primary key default gen_random_uuid(),
  book_id uuid references books(id),
  character text not null,
  voice_spec text,                   -- style fingerprint for THIS character's narration
  exemplar_scene_ids uuid[],         -- this character's best approved scenes (few-shot)
  unique (book_id, character)
);

-- A generation request: "draft chapter 4" / "draft scene 12".
create table runs (
  id uuid primary key default gen_random_uuid(),
  book_id uuid references books(id),
  scope_json jsonb not null,
  gate_mode text not null,           -- 'pause_each' | 'draft_ahead'
  token_budget int not null,
  status text not null default 'active', -- active|paused|done|aborted
  created_at timestamptz default now()
);

-- One unit of worker work = draft one scene OR one revision.
create table jobs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references runs(id),
  kind text not null,                -- 'draft' | 'revise_full' | 'revise_pass'
  target_scene_id uuid,              -- set for revisions
  target_pass text,                  -- set for revise_pass (e.g. 'dialogue')
  chapter_no int, scene_no int,
  token_budget int not null,
  status text not null default 'queued', -- queued|running|done|failed
  claimed_by text, claimed_at timestamptz,
  created_at timestamptz default now()
);

-- The plan for a scene. Proposed by the plan-call, approved/edited by you (gate 1).
create table beats (
  id uuid primary key default gen_random_uuid(),
  chapter_id uuid references chapters(id),   -- POV inherited from chapter
  scene_no int,
  characters_present text[],
  tags text[],                       -- {combat, sensory, dialogue, ...}
  expected_state_changes jsonb,      -- DECLARED stat/inventory deltas, structured
                                     --   e.g. {"Marcus":{"STR":"+3"},"gain":["Sundered Blade"]}
  knowledge_injections text[],       -- author-supplied: "Serra now knows X (told ch.12)"
  beat_text text,
  status text default 'proposed',    -- proposed|approved
  unique (chapter_id, scene_no)
);

-- Prose. A revision is a NEW row, never a mutation.
create table scenes (
  id uuid primary key default gen_random_uuid(),
  chapter_id uuid references chapters(id),
  scene_no int,
  version int not null default 1,
  parent_scene_id uuid references scenes(id),
  status text not null default 'draft',  -- draft|pending_review|approved|revision_requested|superseded
  prose text,                        -- SINGLE SOURCE OF TRUTH for all downstream derivation
  prose_source text,                 -- 'agent' | 'agent+human_edit'
  agent_original text,               -- agent's draft preserved for training-capture; NEVER fed as context
  passes_run text[],
  published boolean default false,   -- cleared for public showcase (distinct from approved)
  token_count int, model text,
  created_at timestamptz default now()
);

-- Story bible / canon, retrievable.
create table canon_entities (
  id uuid primary key default gen_random_uuid(),
  book_id uuid references books(id),
  kind text,                         -- character|location|faction|lore|item
  name text,
  body text,
  published boolean default false,   -- cleared for public showcase
  embedding vector(1536)
);

-- Hard numbers. The Oracle's backing store. NEVER fuzzy-retrieved.
create table character_state (
  id uuid primary key default gen_random_uuid(),
  book_id uuid references books(id),
  character text not null,
  as_of_scene_id uuid references scenes(id),
  provisional boolean default false, -- true if derived from an unapproved (draft_ahead) scene
  stats_json jsonb not null          -- {STR:22, skills:[...], inventory:[...], cooldowns:{...}}
);

-- Memory. Two scopes.
create table summaries (
  id uuid primary key default gen_random_uuid(),
  book_id uuid references books(id),
  scope text not null,               -- 'pov' (feeds drafter) | 'omniscient' (feeds planner+reviewer)
  pov text,                          -- null when scope='omniscient'
  up_to_scene_id uuid references scenes(id),
  rolling_summary text
);

-- Advisory ONLY. Never changes scene.status. Never blocks your inbox.
create table critiques (
  id uuid primary key default gen_random_uuid(),
  scene_id uuid references scenes(id),
  version int,
  reviewer text,                     -- continuity|combat|sensory|dialogue|pacing|voice
  severity text,                     -- info|warn|hard
  note text,
  payload jsonb                      -- for continuity mismatches:
                                     --   {character, prose_value, ledger_value, context_sentence, span}
);

-- Your verdicts = authoritative gate AND future training labels.
create table approvals (
  id uuid primary key default gen_random_uuid(),
  scene_id uuid references scenes(id),
  version int,
  decision text not null,            -- approve|deny|revise
  target_pass text,                  -- if you scoped the revision to one pass
  feedback text,
  decided_at timestamptz default now()
);
```

**Versioning = rows, not Git branches.** A revision inserts a new `scenes` row (`version+1`, `parent_scene_id` set); the old one flips to `superseded`. Full history via `order by version`. No worktrees, no branches, no dirty tree — runtime exhaust (logs, job status) lives in tables/stdout, never in a repo.

---

## 4. The loop — two gates

```
START RUN: "draft chapter 4"
   │
   ▼
┌─ GATE 1: CONTRACTS (chapter packet → scene packets) ────────┐
│ you write the chapter outline (+ assign its POV)             │
│   → a bounded call PROPOSES the ChapterPacket (the chapter's │
│     knowledge contract); agents QA it; YOU edit/approve      │
│   → ScenePackets are derived per scene (reader/POV/reveal/   │
│     word contracts) and QA'd; YOU edit/approve each          │
│   → beats (expected_state_changes, knowledge_injections)     │
│     derive from the approved ScenePackets; only then does    │
│     Draft Chapter queue scene jobs, each stamped with its    │
│     scene_packet_id — see contract_first_drafting.md         │
│ (bounded calls per request; not a resident planner)          │
└───────────────────────────────────────────────────────────┘
   │
   ▼  per scene (in pause_each or draft_ahead)
┌─ GENERATE ONE SCENE ────────────────────────────────────────┐
│ load context (POV-scoped):                                    │
│   POV voice_spec + exemplars · POV rolling summary ·          │
│   approved ScenePacket contract + its derived beat            │
│   (tags, declared deltas, chars) · beat-scoped canon ·        │
│   current ledger (Oracle read) · prior scene tail (in-chapter)│
│ Drafter writes the whole scene  ── one continuous spine       │
│   → if 'combat':               Combat pass sharpens fight     │
│   → if 'sensory':              Sensory pass adds concrete detail│
│   → if 'dialogue':              Dialogue pass punches it up    │
│ persist scene (status=pending_review, passes_run=[...])       │
│   → async REVIEW lane (read-only): Continuity always +         │
│     enabled domain reviewers → advisory critique rows.        │
│     A partial pass failure LANDS the spine + flags it;         │
│     it never fails the whole job or blocks the inbox.         │
│ EXIT.  Zero processes running.                                │
└───────────────────────────────────────────────────────────┘
   │
   ▼
┌─ GATE 2: SCENE (your inbox) ────────────────────────────────┐
│ prose + advisory flags + continuity panel + hand-edit +       │
│ approve / deny / revise(full | targeted-pass)                 │
└───────────────────────────────────────────────────────────┘
   │
   ▼  on APPROVE
┌─ COMMIT ────────────────────────────────────────────────────┐
│ derive POV summary + omniscient summary FROM FINAL TEXT       │
│   (your hand-edits are already in it — nothing to sync)       │
│ commit the beat's declared deltas → Oracle's ledger advances  │
│ preserve agent_original vs final for training capture         │
└───────────────────────────────────────────────────────────┘
```

Each specialist is a **stateless function**: fixed craft prompt + a few KB of scoped context + the *current* scene text. Every pass reads "current scene text," agnostic to who wrote it last — which is what makes hand-edit safe.

---

## 5. Coordination, the Oracle, and what "deterministic" means

Two seats are plain code, never an LLM:

- **The router** decides which specialist runs: a lookup table + a for-loop. It cannot spiral, because it doesn't reason — it executes.
- **The Oracle** answers "what is true right now?" — it reads `character_state` and is the single authority other components query for hard numbers. It owns truth; it never reasons and never judges conformance.

The advisory **continuity reviewer** is the opposite job: an LLM that only *reports* whether the prose matches the Oracle's truth. It never writes to the ledger. Truth-ownership and conformance-judgment are kept apart on purpose — fusing them is one step from the system "correcting" things on its own, which is the autonomy this design eliminates.

**Deliberate exception — the repair-task drain (Desk Control Round P14).** Autonomy stays eliminated for canon/ledger writes, plan changes, and anything an LLM *judges*; but the *execution* of already-triaged, bounded repair tasks (span patches and human-verifiable scene revision jobs) now auto-drains after triage. What runs was decided by deterministic triage the human can inspect — the drain adds no new judgment. The human keeps the same controls as drafting: the one queue pause switch stops it between tasks, `requires_human_approval` tasks (cross-scene / chapter-structural / human-required authority) are never claimed and execute only through an explicit **Approve & apply**, and every application remains verifiable, rejectable, and rollbackable after the fact.

```python
DRAFT_PASSES = {                      # enrichment: modify prose during drafting
    "combat":               combat_pass,
    "sensory":              sensory_pass,
    "dialogue":             dialogue_pass,
}
REVIEWERS = {                         # read-only, advisory
    "always":               [continuity_reviewer],
    "combat":               [combat_reviewer],
    "sensory":              [sensory_reviewer],
    "dialogue":             [dialogue_reviewer],
}

def generate_one_scene(job, beat, ctx):
    prose = drafter(beat, ctx)                       # the spine, in the chapter's POV voice
    for tag in beat.tags:
        if tag in DRAFT_PASSES:
            try:
                prose = DRAFT_PASSES[tag](prose, ctx)
            except PassError as e:
                flag_partial(job, tag, e)            # land partial + flag; never hard-fail
    scene = persist(prose, status="pending_review")
    enqueue_reviews(scene, beat.tags)                # async, advisory
    return                                           # process exits
```

The only LLMs that touch the *plan* are the bounded gate-1 packet calls — ChapterPacket author/QA, then ScenePacket author/QA (beats are derived deterministically from the approved ScenePackets; see [`contract_first_drafting.md`](contract_first_drafting.md)). Each runs per request and is gone. None is a standing process.

---

## 6. Specialist roster (novel defaults)

**Enrichment** specialists *modify* prose during drafting. **Reviewers** *judge* prose and emit advisory flags; they never edit.

| Domain | Enrichment pass | Reviewer | Phase 1 active? |
|---|---|---|---|
| Drafter (spine, POV-voiced) | yes | — | yes |
| Continuity | — | yes, always | yes |
| Combat | yes | yes | later |
| Sensory / Physical desc | yes | yes | later |
| Dialogue | yes | yes | later |
| Pacing | — | yes | later |
| Voice | — | yes | later |

For the **novel**, combat/sensory/dialogue get **both** lanes (improve during drafting *and* flag on review); continuity/pacing/voice are review-only. (If this architecture is ever pointed at the *game*, the defaults flip review-heavy — generated game content is mostly reviewed, not enriched.) Phase 1 *runs* only Drafter + Continuity; the router/tag seams exist from day one, so activating the rest is config, not re-architecture.

---

## 7. Memory — three kinds, POV-aware

| Need | Example | Mechanism | Who reads it |
|---|---|---|---|
| Semantic canon | "who is Serra, what's this city" | pgvector RAG, beat-scoped top-k | each specialist, its slice only |
| Recent plot (per character) | "what does Serra know" | per-POV rolling summary | the Drafter |
| Recent plot (truth) | "what's actually happened across threads" | omniscient summary | the plan-call + continuity reviewer |
| Hard numbers | "current STR, inventory, cooldowns" | Oracle over `character_state` | Drafter + continuity reviewer |

**Knowledge-asymmetry boundary (deliberate):** the per-POV summary accumulates only from that character's own chapters, so a narrator won't reference an event they were never present for. The system does **not** auto-model knowledge *transfer* between characters (Marcus telling Serra offscreen) — that would require a theory-of-mind knowledge graph, which is out of scope as speculative over-engineering. Transfers are author-injected via `beats.knowledge_injections`; the continuity reviewer flags a POV character referencing anything absent from their knowledge-summary. Strong approximation; the rare untracked transfer is yours to handle. Within a chapter the drafter also gets the literal prior-scene tail; across a chapter boundary (POV changes) it relies on the POV summary, not the previous chapter's other-POV text — which is what prevents knowledge leak at POV seams.

---

## 8. Runs — two independent knobs

- **Scope:** how many scenes this run drafts. Atomic unit is always one scene; "3 chapters" = queue N scene jobs after their beats are approved.
- **Gate mode:**
  - `pause_each` (**default, safe**): draft next scene → `pending_review` → **stop**. Your approval enqueues the next job.
  - `draft_ahead`: draft the whole scope to `pending_review` against *provisional* prior scenes, then the run completes; you review the batch.

**Provisional ledger (chosen):** in `draft_ahead`, the ledger advances per drafted scene marked `provisional=true`, so scene 4 sees the sword scene 2 gave it and batches stay internally coherent. On any rejection in the batch, downstream provisional rows and the scenes derived from them are auto-invalidated and flagged for redraft. (`pause_each` never hits this — the ledger is always solid there.) Use `draft_ahead` for short, confident runs. Approving is **always** per-scene and always you.

---

## 9. The scene review surface (gate 2)

The inbox scene view offers:

- **The prose**, with advisory flags from the review lane shown alongside (never blocking).
- **Continuity panel** — each hard mismatch listed as `{context sentence · prose says X · ledger says Y · [use prose] [use ledger] [neither/edit]}`. *Use prose* writes the corrected number to the Oracle's ledger; *use ledger* enqueues a targeted-pass fix on the prose span. Every contradiction is listed; nothing auto-resolves; nothing blocks.
- **Hand-edit** — edit prose freely while `pending_review` (consequence-free: no summary/ledger derived yet). On approve, all derived memory is computed from your final text; the scene is stamped `prose_source='agent+human_edit'` and the agent's `agent_original` is preserved for training capture but never fed back as context. Editing an *already-approved* scene triggers a flagged re-derive of summaries/ledger from that scene forward.
- **Approve / Deny / Revise** — revise supports **full redraft** (regenerate the spine) or **targeted pass** (re-run one specialist against current text; default when you name a pass).

---

## 10. Bounded, interruptible execution

- **Token budget per job** (`jobs.token_budget`): track running total from `usage`; exceed → abort, persist partial as `draft` + flag. Fail-closed.
- **Wall-clock budget:** `asyncio.wait_for` timeout; hung job killed, scene stays `failed`/`queued`, safe to re-run (no Git state to clean).
- **Clean kill:** one job = one fresh process/task. `Stop-Process` leaves Postgres consistent.
- **Cheap startup by construction:** worker loads only the scoped context above — a few KB. Nothing to "prove." Startup is milliseconds.

```powershell
# run worker for one cycle (one queued scene, then exits)
python -m dominion.workers.worker --once
# kill a hung generation
Get-Process python | Where-Object { $_.CommandLine -like '*dominion*' } | Stop-Process
# tail structured logs
Get-Content .\logs\worker.jsonl -Wait -Tail 50
```

> **Tripwire:** if a job ever spends more than ~5 min of *startup* before prose, the boot-verification trap is back — rip out whatever was just added.

---

## 11. Decision capture (build now, ML later)

Every verdict is a labeled example for free: `(final prose, agent_original, reviewer scores, your decision, your feedback)`. Stored in `approvals` + `critiques` + `scenes.agent_original`. Add an export view.

- **Now (no ML):** feed your best `approved` scenes back as per-POV few-shot exemplars (`pov_profiles.exemplar_scene_ids`); fold recurring rejection reasons into reviewer rubrics. Reviewers = Layer 1 (advisory machine scores), you = Layer 2 (authoritative). Quality converges via better prompts grounded in your real taste.
- **Later (separate project, defer until ~hundreds of decisions):** fine-tune a drafter on your approved corpus, or train a preference model predicting your verdict. Do not start until usage has produced the dataset.

---

## 12. Auth

Single user. No user management. Host-level password, Cloudflare Access, or a managed provider with one account. An afternoon.

---

## 12b. Agent operations panel

The Desk `/settings` screen is an **agent operations panel**, not a bare model picker. Authors choose **presets** (built-in or **user-saved custom** snapshots of tiers + policies), configure **global scene token and wall-clock budgets**, and per-agent **fallback chains**, **quality sliders**, **semantic escalation**, and **autonomy** (`auto_run` gates enrichment/review). **Smoke tests** run offline against fixtures or optionally **live** with a cost warning. **Per-reviewer telemetry** stages (`reviewer_continuity`, `reviewer_voice`, …) split the coarse `reviewers` bucket. Primary models live in `model_overrides`; policies in `agent_policy_overrides`; globals in `agent_ops_state.globals_json`.

---

## 13. Showcase site

Not built — and no longer a separate system. The original plan (Astro, own repo, GH Pages + Netlify) was dropped; the only deployment is the single shared-box container (§1/§2). If a public showcase ever happens, it pulls `published = true` canon/scenes through a read-only endpoint. `published` is distinct from `approved` (approving for the manuscript ≠ clearing spoilers for the public); the flag exists in the schema now, unused.

---

## 14. Phased build

| Phase | Active machinery | Done when |
|---|---|---|
| **1** ✅ | schema + chapters/pov_profiles + Drafter (POV-voiced) + Continuity reviewer + Oracle + review-app inbox (incl. continuity panel + hand-edit) + gate-1 beat proposal/approval + manual `--once` start. Seams present (router, tags, runs/gate, budgets, capture). | you approve **one** scene through the app and the next job appears |
| **2** ✅ | worker auto-advances (`pause_each` loop); RAG + per-POV & omniscient summaries + ledger commit-on-approval wired; import existing chapters as `approved` seed + extract canon | 3–5 consecutive scenes stay continuous with prior canon and stay in-POV |
| **3** ✅ | Combat / Sensory / Dialogue enrichment passes (transform-only, stat-safe, soft-fail) + their tag-gated review lanes; pacing/voice reviewers already live (all advisory) | enrichment measurably reduces your revision requests |
| **4** | `draft_ahead` + provisional-ledger invalidation; parallel workers across runs. *(deferred: preference model / fine-tune; showcase published-canon endpoint)* | only if scale/throughput actually hurts |

---

## 15. Decisions log

- **OPEN-1 — ledger updates:** beat-declared `expected_state_changes` commit on approval (deterministic), **plus** an advisory extraction reviewer that flags prose implying undeclared changes. Oracle owns truth (read-authority, code); reviewer only reports drift (LLM); you adjudicate. Kept separate to prevent self-correction.
- **OPEN-2 — beats:** the gate-1 plan-call *proposes* per-scene beats from your chapter outline; you edit/approve before any scene drafts. Beats are a review surface.
- **OPEN-3 — draft_ahead ledger:** provisional advance per drafted scene, auto-invalidated downstream on any batch rejection.
- **OPEN-4 — hand-edit:** allowed; `prose` is the single source of truth; all memory derives from final text on approval; `agent_original` preserved for capture but never used as context; editing approved scenes triggers flagged re-derive forward.
- **OPEN-5 — revision:** both full-redraft and targeted-single-pass; targeted is default when a pass is named.
- **OPEN-6 — continuity:** flag every contradiction, block nothing; resolve per-mismatch in the scene's continuity panel (context sentence + pick prose/ledger). No auto-fix.
- **OPEN-7 — published flag:** added now (scenes + canon), unused until the showcase pulls live canon.
- **OPEN-8 — lanes:** novel defaults combat/sensory/dialogue to both lanes; continuity/pacing/voice review-only. (Game would flip review-heavy.)
- **OPEN-9 — POV:** multi-POV, chapter-level (GoT model); ~70% Marcus, then Serra, then four others as arcs demand. POV owned by `chapters`; per-POV voice profiles + per-POV summaries; knowledge-transfer not auto-modeled (author-injected + reviewer-flagged).
- **OPEN-10 — partial pass failure:** land the spine + flag the failed pass; never hard-fail the job or block the inbox.
