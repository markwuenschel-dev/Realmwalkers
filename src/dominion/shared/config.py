"""Runtime settings, loaded from environment / .env (DESIGN §9, §10)."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOMINION_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dominion:dominion@localhost:5432/dominion"

    # Anthropic (the key uses its own conventional env var, not the DOMINION_ prefix)
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    draft_model: str = "claude-sonnet-4-6"
    review_model: str = "claude-haiku-4-5-20251001"
    # Enrichment passes are generative like the drafter, so they default to the draft model; kept as a
    # separate knob so the enrichment tier can be tuned without a code change (DESIGN §5-6).
    enrich_model: str = "claude-sonnet-4-6"

    # Authoring source-of-truth docs loaded into the drafter. Relative paths resolve from the
    # project root (falling back to CWD). dialogue_rules.md is authoritative for ALL dialogue —
    # it wins over the per-POV voice spec where they disagree (see drafter._voice_system).
    dialogue_rules_path: str = "novel/style/dialogue_rules.md"

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
