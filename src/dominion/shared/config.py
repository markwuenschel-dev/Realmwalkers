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

    # Bounded execution
    scene_token_budget: int = 40_000
    scene_time_budget_s: int = 300

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
