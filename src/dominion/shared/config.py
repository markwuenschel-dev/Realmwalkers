"""Runtime settings, loaded from environment / .env (DESIGN §9, §10)."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


def _to_asyncpg(v: str) -> str:
    """Normalize a postgres URL to the asyncpg driver scheme the app connects with."""
    if v.startswith("postgresql+asyncpg://"):
        return v
    for scheme in ("postgresql://", "postgres://"):
        if v.startswith(scheme):
            return "postgresql+asyncpg://" + v[len(scheme) :]
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOMINION_", env_file=_ENV_FILE, extra="ignore")

    # The shared-box Compose stack injects DOMINION_DATABASE_URL (a private, internal-only host); a bare
    # DATABASE_URL is also accepted. Normalize whatever scheme is given (postgres:// or postgresql://) to
    # the async driver the app actually connects with.
    database_url: str = Field(
        default="postgresql+asyncpg://dominion:dominion@localhost:5432/dominion",
        validation_alias=AliasChoices("DOMINION_DATABASE_URL", "DATABASE_URL"),
    )

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        return _to_asyncpg(v)

    # Anthropic (the key uses its own conventional env var, not the DOMINION_ prefix)
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    draft_model: str = "gpt-5.6-luna"
    draft_fallback_model: str = "gpt-5.6-terra"
    review_model: str = "gpt-5.6-luna"
    review_fallback_model: str = "gpt-5.6-terra"
    # Enrichment passes are targeted rewrites layered on the Sonnet-drafted spine, so they run on Haiku
    # by default — roughly a third the cost and ~2-3x faster per pass, with little prose impact since
    # the spine already carries the voice. Override DOMINION_ENRICH_MODEL to put them back on Sonnet if
    # a chapter needs richer enrichment (DESIGN §5-6).
    enrich_model: str = "gpt-5.6-luna"
    # Rate-limit fallback only. Kept on the SAME provider deliberately: the fallback exists to survive
    # a 429, and hopping to Anthropic would trade a rate limit for a hard failure whenever that account
    # is unfunded — the enrichment pass soft-fails (PassError), so that lands as a silently un-enriched
    # scene rather than a visible error. Point this back at a claude model if both accounts are funded.
    enrich_fallback_model: str = "gpt-5.6-terra"

    # Contract-first drafting — Phase 1 (chapter packets). The packet agents run ONCE per chapter, so
    # a strong reasoner is cheap (amortized over ~12+ scenes) — they decide the guardrails every later
    # writer obeys, so default them to Sonnet. (Per-scene stage models — preflight/compression/QA —
    # arrive with their phases.)
    packet_author_model: str = "gpt-5.6-luna"
    packet_author_fallback_model: str = "gpt-5.6-terra"
    # QA only ATTACKS the author's packet (a checker, not a creator), so it rides Haiku like the
    # other checker/enrichment stages (review_model, enrich_model) — meaningfully faster second call.
    packet_qa_model: str = "gpt-5.6-luna"
    packet_qa_fallback_model: str = "gpt-5.6-terra"
    # The packet author/QA calls run synchronously inside the propose-packet request; bound them so a
    # hung call surfaces as a clean failure instead of a spinning browser (mirrors plan_time_budget_s).
    packet_time_budget_s: int = 300
    # No auto-approve during early tuning: even a green packet needs a human fast-approve until we
    # have several chapters of observed packet quality. Flip True later to let green auto-proceed.
    packet_auto_approve_green: bool = False

    # Scene-packet contract system. The ScenePacket builder localizes the approved ChapterPacket into
    # one scene's reader/POV/reveal/word contract; QA attacks each one. Both default to Haiku: unlike
    # the once-per-chapter ChapterPacket author, these run ONCE PER SCENE (~12+ calls/chapter), so the
    # per-call latency/cost dominates the run. Both are exposed in the models tab (settings ROLES), so
    # bump the author to Sonnet there for a chapter that needs a richer contract.
    scene_packet_author_model: str = "gpt-5.6-luna"
    scene_packet_qa_model: str = "gpt-5.6-luna"
    # The ScenePacket body is a large JSON object; if Haiku runs out of output tokens mid-object the
    # response is truncated and the parse fails closed ("incomplete body"). Give the author generous
    # headroom, and on an invalid/truncated body retry ONCE escalated to a stronger model — which both
    # fixes a genuine truncation and covers a model that just can't emit clean JSON for this schema.
    # An empty fallback disables the escalation (the single attempt then stands or blocks).
    scene_packet_author_max_tokens: int = 8000
    scene_packet_qa_max_tokens: int = 3000
    scene_packet_author_fallback_model: str = "gpt-5.6-terra"
    scene_packet_qa_fallback_model: str = "gpt-5.6-terra"
    # Import Adoption per-scene evidence extraction (ADR 0028). Its OWN role — a distinct high-volume
    # cost/quality decision (~1 call per imported scene), defaulting to the same economical tier as the
    # ScenePacket author; coupling it to either packet-author role would make operator tuning of one
    # workflow unexpectedly affect another. Escalates to a stronger model on unparseable output.
    import_evidence_model: str = "gpt-5.6-luna"
    import_evidence_fallback_model: str = "gpt-5.6-terra"
    import_evidence_max_tokens: int = 4000
    # Manual QA re-run (POST /scene-packets/{id}/qa) runs one Author-free QA pass against the current
    # body. It is a single bounded call, so it gets its own soft/hard work budget separate from a full
    # derive — a tiny soft overage shouldn't discard a usable verdict, but a runaway call must still stop.
    scene_packet_manual_qa_token_budget: int = 20_000
    scene_packet_manual_qa_hard_token_budget: int = 30_000
    # Explicit shared-prefix priming runs before scene fan-out, so Scene 1 no longer pays the chapter-
    # level cache write under its per-scene work budget. This separate ceiling bounds those prime calls.
    scene_packet_prefix_prime_token_budget: int = 100_000
    # Hard ceiling for prefix-prime calls. The prime writes one large chapter-shared block; its soft
    # target is scene_packet_prefix_prime_token_budget, but a slightly larger chapter packet should warn,
    # not fail the whole derive before any scene runs. Hard-block only well past the soft target.
    scene_packet_prefix_prime_hard_token_budget: int = 125_000
    # Raw, unweighted context-window guard for ScenePacket calls. Cache discounts are work/cost
    # accounting only; cached tokens still occupy the model context window.
    scene_packet_context_window_budget: int = 180_000
    # Scene packets are independent per scene, so their Author+QA pairs run concurrently (DB reads and
    # writes stay serial; only the LLM calls fan out). Bounds in-flight scenes so a wide chapter can't
    # spike rate limits. Author->QA within a scene stays sequential (QA reads the author's output).
    scene_packet_concurrency: int = 5
    # Split the scene-packet author into ~5 concurrent SECTION calls (each emits a disjoint slice of the
    # contract) instead of one monolithic call. Output generation is sequential WITHIN a call but parallel
    # ACROSS calls, so this cuts author wall-clock latency ~3-4x — the contract JSON is large and the work
    # is output-bound (~12.5s per 1k output tokens, measured). The monolithic author stays as the
    # fallback-only path until the sectioned path clears its acceptance window (then it's deleted).
    scene_packet_author_sectioned: bool = True
    # Global ceiling on concurrently in-flight scene-packet author SECTION calls, across ALL scenes of a
    # derive. The sectioned author fans each scene into ~5 calls; scenes already fan out at
    # scene_packet_concurrency, so without a global cap a wide chapter (scenes x sections) spikes
    # Anthropic's RPM/TPM and the 429 backoff eats the latency win. Bounds total in-flight section calls.
    scene_packet_max_inflight_llm: int = 8
    # Writer-first draftability policy (DESIGN: the product is drafting, so optional provenance hygiene
    # must never hard-block a packet). block_on_provenance=False keeps an invalid claim_sources.source_id
    # (an outline label, a UUID, an out-of-range handle) a WARNING — the derive normalizes it to null and
    # the packet stays draftable. Flip True only to make provenance a hard gate (a safety valve, not the
    # default). The default preserves writer-first behavior and is not an excuse to skip normalization.
    scene_packet_block_on_provenance: bool = False
    # Length guard rewrites (compress/expand) are targeted edits on an existing draft, so they ride the
    # cheap/fast Haiku tier like the enrichment passes — never the main draft model.
    length_compress_model: str = "gpt-5.6-luna"
    length_expand_model: str = "gpt-5.6-luna"

    # Length guard policy (DESIGN: word budgeting). Default to NOT auto-rewriting: an over-max draft
    # lands with a WARN critique for the human, an under-min draft lands with INFO. Only a hard-max
    # overflow is auto-compressed, and if still over hard_max it is quarantined as a DRAFT.
    length_auto_compress_over_max: bool = False
    length_auto_expand_under_min: bool = False
    length_hard_fail_over_hard_max: bool = True

    # SceneFidelity model governance (ADR 0014). All mode adapters use ONE role + ONE approved fallback
    # chain (resolved via agent_registry.FALLBACK_ATTR["scene_fidelity_model"]); if no approved fallback is
    # available the adapter is incomplete and an export-required incompleteness holds Production Run
    # completion, never a prose failure. Bounded inflight caps the concurrent mode fan-out. The version
    # markers are recorded on every report as provenance: a prompt-version bump is provenance by default
    # and forces re-evaluation only when explicitly marked for recheck.
    scene_fidelity_model: str = "gpt-5.6-luna"
    scene_fidelity_fallback_model: str = "gpt-5.6-terra"
    scene_fidelity_max_inflight: int = 3
    scene_fidelity_prompt_version: int = 1
    scene_fidelity_facade_version: int = 1
    scene_fidelity_report_schema_version: int = 1

    # Hybrid canon retrieval (RAG upgrade). owner_file_boost is added to a chunk's rerank score when it
    # comes from a forced owner file, so owner precedence always beats a semantic-only hit.
    #
    # Embedding seam: `embedding_provider` selects the backend behind workers.memory.embedding.embed().
    #   "openai" — real semantic vectors via the OpenAI embeddings REST API (text-embedding-3-small,
    #              1536-dim, matching the Vector column). Used automatically once an OpenAI key is set.
    #   "hash"   — deterministic feature-hashing (no key, no network). The default + offline/CI/test
    #              fallback, and the silent fallback if "openai" is selected but no key is present.
    # The embedding_version stamped on each chunk encodes provider+model, so switching providers
    # re-embeds changed chunks on the next ingest instead of mixing incompatible vector spaces.
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_time_budget_s: float = 30.0
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    # Multi-provider draft/QA models (workers.llm dispatches by model-id prefix: claude-* stays on the
    # existing Anthropic path; gpt-* / o*-* route to OpenAI, grok-* routes to xAI, gemini-* routes to
    # Google's OpenAI-compatible Gemini endpoint). No new SDK dependency: all three are plain httpx
    # POSTs, matching the embedding provider's existing convention.
    #
    # Optional local LiteLLM gateway (OpenAI-compatible). When LITELLM_VIRTUAL_KEY is a real
    # sk-… virtual key, workers.llm routes chat through the gateway (stable aliases) instead of
    # direct provider keys. Embeddings still use OPENAI_API_KEY / hash fallback unless you add
    # a gateway embedding route later.
    litellm_virtual_key: str | None = Field(default=None, validation_alias="LITELLM_VIRTUAL_KEY")
    litellm_base_url: str = Field(
        default="http://localhost:4000/v1",
        validation_alias="LITELLM_BASE_URL",
    )
    litellm_model: str | None = Field(
        default=None,
        validation_alias="LITELLM_MODEL",
        description="Optional force-all gateway alias (e.g. llm-general).",
    )
    google_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    xai_api_key: str | None = Field(default=None, validation_alias="XAI_API_KEY")
    xai_base_url: str = "https://api.x.ai/v1"
    openai_base_url: str = "https://api.openai.com/v1"
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    rag_semantic_k: int = 12
    rag_keyword_k: int = 12
    rag_final_k: int = 8
    rag_owner_file_boost: int = 100

    # Authoring source-of-truth docs loaded into the drafter. Relative paths resolve from the
    # project root (falling back to CWD). dialogue_rules.md is authoritative for ALL dialogue —
    # it wins over the per-POV voice spec where they disagree (see drafter._voice_system).
    dialogue_rules_path: str = "series/style/dialogue_rules.md"

    # Post-split monorepo ingest source dirs (series/canon + book1/manuscript). Centralized here so the
    # worker CLIs (canon_rag.py, seed.py) read one source of truth instead of duplicating the literal
    # paths in each argparse default — a folder rename now changes one place. Relative paths resolve
    # from the project root (falling back to CWD), like dialogue_rules_path above. Env-override via the
    # DOMINION_ prefix: DOMINION_CANON_DIR, DOMINION_SCENES_DIR.
    canon_dir: str = "series/canon"
    scenes_dir: str = "book1/manuscript/scenes"

    # Voice exemplars (LEARNING_FROM_EDITS Tier 2): the drafter few-shots on the author's curated
    # approved prose, loaded from PovProfile.exemplar_scene_ids. Capped so a handful of passages can't
    # crowd the scene's token budget — count of passages, and chars kept from each.
    exemplar_max_count: int = 3
    exemplar_max_chars: int = 1500

    # Distilled rules (LEARNING_FROM_EDITS Tier 3): a review-model pass reads recent author edits and
    # proposes durable voice/dialogue rules for the author to approve. Bound the batch (most-recent
    # before→after pairs per POV) and the in-request call (mirrors plan_time_budget_s — the distill
    # endpoint runs synchronously, so a hung call must surface as a clean 504, not a spinning browser).
    distill_max_pairs: int = 40
    distill_pair_max_chars: int = 1200  # per side of each pair, to protect the token budget
    distill_time_budget_s: int = 120

    # Bounded execution. The per-scene token ceiling bounds one scene's Author+QA *work*. With caching
    # now live (cache reads discounted), the dominant remaining cost is OUTPUT — the scene-packet JSON
    # contract is large, and output is real, un-cacheable work charged at full weight. The 40k ceiling
    # was sized before that was understood and blocked legitimate scenes at ~41-47k (observed); the
    # primer scene also pays the chapter-wide prefix *write* it can't read back. 60k clears both with
    # headroom. If cost matters more than contract richness later, trim the schema (less output) rather
    # than lowering this — a smaller ceiling just re-blocks honest work.
    scene_token_budget: int = 60_000
    # The HARD ceiling for one scene's Author+QA work. scene_token_budget is the SOFT target: a scene
    # that finishes a handful of tokens over it (the recurring `60043 > 60000`) produced valid output —
    # discarding it just re-runs the same work. So the soft target only WARNS; work is blocked only past
    # this hard ceiling. Sized with headroom over the soft target so genuine overruns still fail closed.
    scene_token_hard_budget: int = 75_000
    scene_time_budget_s: int = 300
    # The gate-1 plan-call runs synchronously inside the POST /runs request, so an unbounded LLM
    # call leaves the browser spinning forever. Bound it: on timeout the request fails cleanly
    # (the author retries) instead of hanging with no feedback.
    plan_time_budget_s: int = 90

    # LLM transient-error retry (DESIGN §10): retry rate-limit / 5xx / overloaded / connection errors
    # with exponential backoff + full jitter (base * 2**attempt, scattered so throttled calls don't
    # re-fire in lockstep), floored at the provider's Retry-After hint when one is sent. Non-transient
    # errors (auth, 400/403/404) never retry. A 429 that survives every retry raises LlmRateLimited —
    # a classified infrastructure failure, never an author/QA failure.
    llm_max_retries: int = 3
    llm_retry_base_delay_s: float = 1.0
    # Ceiling on any single backoff sleep (a provider Retry-After above this still only waits this long).
    llm_retry_max_delay_s: float = 30.0
    # Per-provider ceiling on concurrently in-flight LLM calls in this process (0 = uncapped). The
    # OpenAI-compatible path (gpt-*/o*-*/grok-*/gemini-*) defaults to 1: current GPT TPM windows are
    # small enough that a concurrent scene author+QA swarm self-inflicts 429s — serialize those calls
    # until real cross-call token budgeting exists (raise to 2 only with verified TPM headroom).
    # Anthropic stays uncapped here; scene_packet_max_inflight_llm already bounds its fan-out.
    llm_openai_concurrency: int = 1
    llm_anthropic_concurrency: int = 0

    # --- Claude Code CLI backend (per-role "Agent CLI" toggle, workers/agent_cli.py) ----------------
    # When a role's policy sets backend="agent_cli", llm.complete shells out to the Claude Code CLI
    # (`claude -p ... --output-format json`) instead of the HTTP API — the model choice is unchanged,
    # only HOW it's called. Auth is inherited from the process env at runtime: CLAUDE_CODE_OAUTH_TOKEN
    # (subscription — the cost lever) or ANTHROPIC_API_KEY (metered). Defaults are a cheap single-shot
    # (one turn, no tools); loosen turns/tools only to deliberately enable agentic behavior. Env-override
    # via the DOMINION_ prefix (e.g. DOMINION_AGENT_CLI_MAX_TURNS).
    agent_cli_bin: str = "claude"
    agent_cli_max_turns: int = 1
    # Comma/space-separated tool allowlist passed to --allowedTools; empty (default) = no tools, so the
    # CLI runs a pure single-shot completion and can't touch the filesystem or network beyond the model.
    agent_cli_allowed_tools: str = ""
    # Ceiling on concurrent `claude` subprocesses (0 = uncapped). Bounds subprocess fan-out so a busy
    # queue with an agent_cli role doesn't spawn unbounded processes.
    agent_cli_concurrency: int = 2
    # Hard per-stage INPUT budgets (estimated tokens) for scene-packet author/QA prompts. A prompt over
    # its budget fails locally with PromptBudgetExceeded ("prompt_budget_exceeded") BEFORE any provider
    # call — an oversized context must never burn TPM just to get refused mid-generation.
    scene_packet_author_prompt_budget: int = 32_000
    # Import-evidence input ceiling. An oversized SCENE is chunked deterministically before extraction
    # (workers/import_evidence._deterministic_chunks), so a single extraction call stays under this.
    import_evidence_prompt_budget: int = 32_000
    # QA parity with the author: its prefix is the chapter packet (minus derived/audit sections — see
    # scene_packet.qa.build_prefix), and a legitimately rich chapter packet plus the scene body was
    # observed to overflow the old 24k guard even after the prefix slimming.
    scene_packet_qa_prompt_budget: int = 32_000

    # Context-window preflight via Anthropic's real token counter (messages.count_tokens). When enabled,
    # llm.complete counts the exact request (model+system+messages) BEFORE messages.create and blocks if
    # count + output allowance exceeds the call's context_window_budget — so an oversized request fails
    # cleanly instead of erroring mid-generation. The local ceil(len/4) estimate is kept only for section
    # attribution/reporting and as the fallback when counting is disabled or unavailable.
    #   fail_closed: if counting errors after retries, raise before generation (True) rather than guess.
    #   estimate_fallback_multiplier: when fail_closed is False, the local estimate is inflated by this
    #     factor (the estimate runs low vs. the real tokenizer) before the gate, and the fallback is
    #     recorded in telemetry — never a silent downgrade to the old heuristic.
    llm_token_counting_enabled: bool = True
    llm_token_counting_fail_closed: bool = True
    llm_token_counting_estimate_fallback_multiplier: float = 1.25

    # Same-origin single-service deploy (the Next BFF proxies to FastAPI), so the browser never calls
    # the API cross-origin and no CORS origins are needed by default. Set DOMINION_CORS_ORIGINS
    # (comma-separated) only if something hits the API directly from another origin.
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
