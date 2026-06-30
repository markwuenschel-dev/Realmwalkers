"""Shared primary→fallback escalation for LLM agent calls.

Structural triggers (Phase 1): truncated output, unparseable response.
Semantic triggers (Phase 2): high-risk QA verdicts / reviewer HARD flags — optional per-agent policy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dominion.shared.agent_policy import get_runtime_policy
from dominion.shared.agent_registry import FALLBACK_ATTR, ROLE_KEYS, tier_of
from dominion.shared.config import settings
from dominion.workers import telemetry
from dominion.workers.budget import Usage


@dataclass(frozen=True)
class EscalationPolicy:
    never_fallback_tiers: frozenset[str] = frozenset()
    fallback_max_tokens_floor: int | None = None


def resolve_fallback_model(setting_key: str) -> str:
    """Read the live fallback model for an agent role from settings."""
    attr = FALLBACK_ATTR.get(setting_key)
    if not attr:
        return ""
    return (getattr(settings, attr, "") or "").strip()


def _blocked_fallback(fallback_model: str, never_fallback_tiers: frozenset[str]) -> bool:
    tier = tier_of(fallback_model)
    return tier is not None and tier in never_fallback_tiers


async def attempt_with_escalation(
    *,
    setting_key: str,
    primary_model: str,
    primary_max_tokens: int,
    attempt_fn: Callable[[str, int], Awaitable[tuple[Any, Usage]]],
    is_success: Callable[[Any], bool],
    policy: EscalationPolicy | None = None,
    fallback_max_tokens: int | None = None,
    semantic_escalate: Callable[[Any], bool] | None = None,
    pick_preferred: Callable[[Any, Any], Any] | None = None,
) -> tuple[Any, str, bool]:
    """Try primary model once; escalate to fallback on structural or semantic failure.

    Returns (value, model_used, escalated).
    """
    policy = policy or EscalationPolicy()
    value, usage = await attempt_fn(primary_model, primary_max_tokens)
    structural_fail = not is_success(value) or usage.truncated
    semantic_fail = not structural_fail and semantic_escalate is not None and semantic_escalate(value)
    if not structural_fail and not semantic_fail:
        return value, primary_model, False

    fallback = resolve_fallback_model(setting_key)
    if not fallback or fallback == primary_model:
        return value, primary_model, False
    if _blocked_fallback(fallback, policy.never_fallback_tiers):
        return value, primary_model, False

    fb_max = primary_max_tokens
    if usage.truncated and policy.fallback_max_tokens_floor:
        fb_max = max(primary_max_tokens, policy.fallback_max_tokens_floor)
    elif fallback_max_tokens is not None:
        fb_max = fallback_max_tokens

    async def _fallback_attempt(model: str, max_tokens: int) -> tuple[Any, Usage]:
        with telemetry.call_metadata(fallback_attempt=True, semantic_escalation=semantic_fail):
            return await attempt_fn(model, max_tokens)

    value2, _usage2 = await _fallback_attempt(fallback, fb_max)
    if pick_preferred is not None and is_success(value) and is_success(value2):
        chosen = pick_preferred(value, value2)
        return chosen, fallback if chosen is value2 else primary_model, True
    if is_success(value2):
        return value2, fallback, True
    if is_success(value) and not usage.truncated:
        return value, primary_model, False
    return value2, fallback, True


def policy_for_setting(setting_key: str) -> EscalationPolicy:
    """Build escalation policy from registry defaults + runtime overrides."""
    from dominion.shared.agent_registry import AGENT_BY_KEY

    agent = AGENT_BY_KEY.get(setting_key)
    if agent is None:
        return EscalationPolicy()
    runtime = get_runtime_policy(setting_key)
    floors = {
        "scene_packet_author_model": 12000,
        "scene_packet_qa_model": 5000,
        "packet_author_model": 16000,
        "packet_qa_model": 3000,
    }
    return EscalationPolicy(
        never_fallback_tiers=runtime.never_fallback_tiers or frozenset(agent.never_fallback_tiers),
        fallback_max_tokens_floor=floors.get(setting_key),
    )


def validate_setting_key(setting_key: str) -> None:
    if setting_key not in ROLE_KEYS:
        raise ValueError(f"unknown agent setting '{setting_key}'")
