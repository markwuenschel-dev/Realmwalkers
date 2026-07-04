"""The length guard's compress/expand model must follow the drafter's provider.

length_compress_model / length_expand_model are NOT in the Settings-screen agent registry, so when a
user moves the drafter to another provider these two stayed pinned to their Anthropic default and a
scene that tripped the length guard hit Anthropic anyway (the reported 400 "credit balance too low"
while every configurable agent was on OpenAI). No DB, no LLM — pure resolver behavior."""

from __future__ import annotations

import pytest

from dominion.shared.config import settings
from dominion.workers.length.guard import _length_model


def test_length_model_follows_drafter_provider(monkeypatch: pytest.MonkeyPatch):
    # Drafter on OpenAI + length model on its Anthropic default → remap to the same TIER on OpenAI.
    monkeypatch.setattr(settings, "draft_model", "gpt-5.4-mini")
    assert _length_model("claude-haiku-4-5") == "gpt-5.4-nano"  # haiku tier on openai


def test_length_model_unchanged_when_providers_match(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "draft_model", "claude-sonnet-5")
    assert _length_model("claude-haiku-4-5") == "claude-haiku-4-5"  # both anthropic → untouched
    monkeypatch.setattr(settings, "draft_model", "gpt-5.4-mini")
    assert _length_model("gpt-5.4-nano") == "gpt-5.4-nano"  # both openai → untouched


def test_length_model_falls_back_to_configured_when_unmappable(monkeypatch: pytest.MonkeyPatch):
    # An unknown drafter provider can't host the tier → keep the configured model rather than error.
    monkeypatch.setattr(settings, "draft_model", "some-unknown-model")
    assert _length_model("claude-haiku-4-5") == "claude-haiku-4-5"
