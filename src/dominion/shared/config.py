"""Runtime settings, loaded from environment / .env (DESIGN §9, §10)."""
from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOMINION_", env_file=".env", extra="ignore")

    # Also accept the bare DATABASE_URL a host like Railway injects, and normalize whatever scheme it
    # uses (postgres:// or postgresql://) to the async driver the app actually connects with.
    database_url: str = Field(
        default="postgresql+asyncpg://dominion:dominion@localhost:5432/dominion",
        validation_alias=AliasChoices("DOMINION_DATABASE_URL", "DATABASE_URL"),
    )

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        for scheme in ("postgresql+asyncpg://",):
            if v.startswith(scheme):
                return v
        for scheme in ("postgresql://", "postgres://"):
            if v.startswith(scheme):
                return "postgresql+asyncpg://" + v[len(scheme):]
        return v

    # Anthropic (the key uses its own conventional env var, not the DOMINION_ prefix)
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    draft_model: str = "claude-sonnet-4-6"
    review_model: str = "claude-haiku-4-5-20251001"
    # Enrichment passes are targeted rewrites layered on the Sonnet-drafted spine, so they run on Haiku
    # by default — roughly a third the cost and ~2-3x faster per pass, with little prose impact since
    # the spine already carries the voice. Override DOMINION_ENRICH_MODEL to put them back on Sonnet if
    # a chapter needs richer enrichment (DESIGN §5-6).
    enrich_model: str = "claude-haiku-4-5"

    # Contract-first drafting — Phase 1 (chapter packets). The packet agents run ONCE per chapter, so
    # a strong reasoner is cheap (amortized over ~12+ scenes) — they decide the guardrails every later
    # writer obeys, so default them to Sonnet. (Per-scene stage models — preflight/compression/QA —
    # arrive with their phases.)
    packet_author_model: str = "claude-sonnet-4-6"
    # QA only ATTACKS the author's packet (a checker, not a creator), so it rides Haiku like the
    # other checker/enrichment stages (review_model, enrich_model) — meaningfully faster second call.
    packet_qa_model: str = "claude-haiku-4-5"
    # The packet author/QA calls run synchronously inside the propose-packet request; bound them so a
    # hung call surfaces as a clean failure instead of a spinning browser (mirrors plan_time_budget_s).
    packet_time_budget_s: int = 180
    # No auto-approve during early tuning: even a green packet needs a human fast-approve until we
    # have several chapters of observed packet quality. Flip True later to let green auto-proceed.
    packet_auto_approve_green: bool = False

    # Scene-packet contract system. The ScenePacket builder localizes the approved ChapterPacket into
    # one scene's reader/POV/reveal/word contract; QA attacks each one. Builder rides Sonnet (it shapes
    # the contract every later writer obeys), QA rides Haiku (a checker, like packet_qa_model).
    scene_packet_author_model: str = "claude-sonnet-4-6"
    scene_packet_qa_model: str = "claude-haiku-4-5"
    # Length guard rewrites (compress/expand) are targeted edits on an existing draft, so they ride the
    # cheap/fast Haiku tier like the enrichment passes — never the main draft model.
    length_compress_model: str = "claude-haiku-4-5"
    length_expand_model: str = "claude-haiku-4-5"

    # Length guard policy (DESIGN: word budgeting). Default to NOT auto-rewriting: an over-max draft
    # lands with a WARN critique for the human, an under-min draft lands with INFO. Only a hard-max
    # overflow is auto-compressed, and if still over hard_max it is quarantined as a DRAFT.
    length_auto_compress_over_max: bool = False
    length_auto_expand_under_min: bool = False
    length_hard_fail_over_hard_max: bool = True

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
    rag_semantic_k: int = 12
    rag_keyword_k: int = 12
    rag_final_k: int = 8
    rag_owner_file_boost: int = 100

    # Authoring source-of-truth docs loaded into the drafter. Relative paths resolve from the
    # project root (falling back to CWD). dialogue_rules.md is authoritative for ALL dialogue —
    # it wins over the per-POV voice spec where they disagree (see drafter._voice_system).
    dialogue_rules_path: str = "series/style/dialogue_rules.md"

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

    # Bounded execution
    scene_token_budget: int = 40_000
    scene_time_budget_s: int = 300
    # The gate-1 plan-call runs synchronously inside the POST /runs request, so an unbounded LLM
    # call leaves the browser spinning forever. Bound it: on timeout the request fails cleanly
    # (the author retries) instead of hanging with no feedback.
    plan_time_budget_s: int = 90

    # LLM transient-error retry (DESIGN §10): retry rate-limit / 5xx / overloaded / connection errors
    # with exponential backoff (base * 2**attempt). Non-transient errors (auth, 400/403/404) never retry.
    llm_max_retries: int = 3
    llm_retry_base_delay_s: float = 1.0

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
