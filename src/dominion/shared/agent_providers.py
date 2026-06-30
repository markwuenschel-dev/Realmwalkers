"""External LLM provider registry for the Agent Operations panel (scaffold only — Anthropic is active)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderStatus = Literal["active", "coming_soon"]


@dataclass(frozen=True)
class AgentProvider:
    id: str
    label: str
    status: ProviderStatus
    description: str = ""


PROVIDERS: tuple[AgentProvider, ...] = (
    AgentProvider(
        id="anthropic",
        label="Anthropic (Claude)",
        status="active",
        description="Current agent models and tiers",
    ),
    AgentProvider(
        id="openai_codex",
        label="OpenAI Codex",
        status="coming_soon",
        description="Placeholder — not wired to execution",
    ),
    AgentProvider(
        id="google_gemini",
        label="Google Gemini",
        status="coming_soon",
        description="Placeholder — not wired to execution",
    ),
    AgentProvider(
        id="antigravity",
        label="Antigravity",
        status="coming_soon",
        description="Placeholder — not wired to execution",
    ),
    AgentProvider(
        id="xai_grok",
        label="xAI Grok",
        status="coming_soon",
        description="Placeholder — not wired to execution",
    ),
    AgentProvider(
        id="cursor",
        label="Cursor",
        status="coming_soon",
        description="Placeholder — not wired to execution",
    ),
)
