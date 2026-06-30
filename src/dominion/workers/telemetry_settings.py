"""Settings snapshot captured at telemetry persist time for run comparison."""

from __future__ import annotations

from typing import Any

from dominion.shared.config import settings


def telemetry_settings_snapshot() -> dict[str, Any]:
    return {
        "scene_packet_author_model": settings.scene_packet_author_model,
        "scene_packet_author_fallback_model": settings.scene_packet_author_fallback_model,
        "scene_packet_qa_model": settings.scene_packet_qa_model,
        "scene_packet_qa_fallback_model": settings.scene_packet_qa_fallback_model,
        "scene_packet_context_window_budget": settings.scene_packet_context_window_budget,
        "scene_packet_prefix_prime_token_budget": settings.scene_packet_prefix_prime_token_budget,
        "scene_packet_concurrency": settings.scene_packet_concurrency,
        "draft_model": settings.draft_model,
        "review_model": settings.review_model,
        "enrich_model": settings.enrich_model,
    }
