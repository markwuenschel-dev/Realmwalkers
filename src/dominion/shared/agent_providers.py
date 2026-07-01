"""External LLM provider registry for the Agent Operations panel.

Anthropic, OpenAI, and xAI are wired to execution (see workers.llm's provider dispatch and
agent_registry.PROVIDER_TIERS for the model catalog). The rest remain placeholders.
"""

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
        label="Anthropic",
        status="active",
        description="claude-haiku-4-5 / claude-sonnet-5 / claude-opus-4-8",
    ),
    AgentProvider(
        id="openai",
        label="OpenAI",
        status="active",
        description="gpt-5.4-nano / gpt-5.4-mini / gpt-5.5 (requires OPENAI_API_KEY)",
    ),
    AgentProvider(
        id="xai",
        label="xAI",
        status="active",
        description="grok-4.3 (requires XAI_API_KEY)",
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
        id="cursor",
        label="Cursor",
        status="coming_soon",
        description="Placeholder — not wired to execution",
    ),
)
