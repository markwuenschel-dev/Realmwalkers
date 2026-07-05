"""Claude Code CLI backend: the `workers/agent_cli.py` runner, the per-role `backend` policy flag,
and the `llm.complete` dispatch that routes a role flipped to `backend="agent_cli"` through the
subprocess runner instead of the HTTP API — while the provider-neutral shared tail (budget.charge +
telemetry) still runs.

Fully deterministic: `asyncio.create_subprocess_exec` is monkeypatched with a fake `claude` process,
and the dispatch test monkeypatches `agent_cli.run`. No real CLI, no network, no DB.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dominion.shared import agent_ops
from dominion.shared.agent_policy import (
    agent_backend,
    load_runtime_policies,
    resolve_policy,
)
from dominion.shared.agent_registry import AGENT_BY_KEY
from dominion.shared.config import settings
from dominion.shared.models import AgentPolicyOverride
from dominion.workers import agent_cli, llm, telemetry
from dominion.workers.agent_cli import AgentCliError
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.llm import LlmRateLimited
from dominion.workers.telemetry import CallContext, TelemetrySink


@pytest.fixture(autouse=True)
def _default_policies() -> None:
    """Reset the in-process policy cache to registry defaults (backend="llm") before each test."""
    load_runtime_policies({})


# --- a fake `claude` subprocess -------------------------------------------------------------------


class _FakeProc:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def _patch_exec(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc, capture: dict[str, Any] | None = None) -> None:
    async def _fake_exec(*argv: str, **kwargs: Any) -> _FakeProc:
        if capture is not None:
            capture["argv"] = list(argv)
            capture["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(agent_cli.asyncio, "create_subprocess_exec", _fake_exec)


_SUCCESS_PAYLOAD: dict[str, Any] = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Hello from the CLI",
    "total_cost_usd": 0.012,
    "usage": {
        "input_tokens": 42,
        "output_tokens": 7,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 0,
    },
}


# --- runner: (text, Usage) mapping ----------------------------------------------------------------


async def test_runner_maps_json_payload_to_text_and_usage(monkeypatch: pytest.MonkeyPatch):
    capture: dict[str, Any] = {}
    _patch_exec(monkeypatch, _FakeProc(stdout=json.dumps(_SUCCESS_PAYLOAD).encode()), capture)

    text, usage = await agent_cli.run(
        model="claude-sonnet-5",
        system="You are a test author.",
        user="Write a line.",
        max_tokens=256,
    )

    assert text == "Hello from the CLI"
    assert (usage.input_tokens, usage.output_tokens) == (42, 7)
    assert usage.cache_read_tokens == 3
    assert usage.cache_creation_tokens == 0
    assert usage.truncated is False

    # The invocation is the single-shot JSON shape, on the isolated cwd, with the system prompt appended.
    argv = capture["argv"]
    assert argv[0] == settings.agent_cli_bin
    assert "-p" in argv and "Write a line." in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert argv[argv.index("--append-system-prompt") + 1] == "You are a test author."
    assert capture["kwargs"]["cwd"]  # a temp dir, not the repo root


async def test_runner_falls_back_to_estimate_when_usage_absent(monkeypatch: pytest.MonkeyPatch):
    payload = {"is_error": False, "result": "some prose output"}  # no usage block
    _patch_exec(monkeypatch, _FakeProc(stdout=json.dumps(payload).encode()))

    _text, usage = await agent_cli.run(model="claude-sonnet-5", system="sys", user="usr", max_tokens=64)
    # Never silently charge zero for real work: both counts fall back to the local estimate.
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0


async def test_runner_prepends_prefix_blocks_to_prompt(monkeypatch: pytest.MonkeyPatch):
    capture: dict[str, Any] = {}
    _patch_exec(monkeypatch, _FakeProc(stdout=json.dumps(_SUCCESS_PAYLOAD).encode()), capture)

    await agent_cli.run(
        model="claude-sonnet-5",
        system="sys",
        user="the dynamic tail",
        prefix_blocks=(llm.CachedPrefixBlock(name="canon", text="STABLE CANON"),),
        max_tokens=64,
    )
    prompt = capture["argv"][capture["argv"].index("-p") + 1]
    assert prompt == "STABLE CANON\n\nthe dynamic tail"


# --- runner: error classification -----------------------------------------------------------------


async def test_runner_nonzero_exit_raises_transient_agent_cli_error(monkeypatch: pytest.MonkeyPatch):
    _patch_exec(monkeypatch, _FakeProc(stderr=b"boom: internal error", returncode=1))
    with pytest.raises(AgentCliError) as exc_info:
        await agent_cli.run(model="claude-sonnet-5", system="s", user="u", max_tokens=64)
    assert exc_info.value.transient is True
    assert agent_cli.is_transient_error(exc_info.value) is True


async def test_runner_usage_limit_message_raises_rate_limited(monkeypatch: pytest.MonkeyPatch):
    _patch_exec(monkeypatch, _FakeProc(stderr=b"Claude usage limit reached. Try again later.", returncode=1))
    with pytest.raises(LlmRateLimited):
        await agent_cli.run(model="claude-sonnet-5", system="s", user="u", max_tokens=64)


async def test_runner_error_envelope_rate_limit_raises_rate_limited(monkeypatch: pytest.MonkeyPatch):
    # Exit 0 but the JSON envelope reports an error subtype carrying a rate-limit message.
    payload = {"is_error": True, "subtype": "error_during_execution", "result": "429 too many requests"}
    _patch_exec(monkeypatch, _FakeProc(stdout=json.dumps(payload).encode()))
    with pytest.raises(LlmRateLimited):
        await agent_cli.run(model="claude-sonnet-5", system="s", user="u", max_tokens=64)


async def test_runner_missing_binary_raises_non_transient(monkeypatch: pytest.MonkeyPatch):
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(agent_cli.asyncio, "create_subprocess_exec", _boom)
    with pytest.raises(AgentCliError) as exc_info:
        await agent_cli.run(model="claude-sonnet-5", system="s", user="u", max_tokens=64)
    assert exc_info.value.transient is False  # a missing binary is a clear job failure, never retried


async def test_runner_non_json_stdout_raises_non_transient(monkeypatch: pytest.MonkeyPatch):
    _patch_exec(monkeypatch, _FakeProc(stdout=b"not json at all", returncode=0))
    with pytest.raises(AgentCliError) as exc_info:
        await agent_cli.run(model="claude-sonnet-5", system="s", user="u", max_tokens=64)
    assert exc_info.value.transient is False


def test_is_transient_error_only_true_for_transient_agent_cli_error():
    assert agent_cli.is_transient_error(AgentCliError("x", transient=True)) is True
    assert agent_cli.is_transient_error(AgentCliError("x", transient=False)) is False
    assert agent_cli.is_transient_error(LlmRateLimited("rl")) is False  # carries its own classification
    assert agent_cli.is_transient_error(ValueError("nope")) is False


# --- policy round-trip: the `backend` flag --------------------------------------------------------


def test_resolve_policy_round_trips_backend():
    agent = AGENT_BY_KEY["draft_model"]
    assert resolve_policy(agent, {}).backend == "llm"  # default
    assert resolve_policy(agent, {"backend": "agent_cli"}).backend == "agent_cli"
    assert resolve_policy(agent, {"backend": "bogus"}).backend == "llm"  # invalid -> safe default


def test_policy_out_surfaces_backend():
    agent = AGENT_BY_KEY["draft_model"]
    override = AgentPolicyOverride(setting_name="draft_model", policy_json={"backend": "agent_cli"})
    out = agent_ops._policy_from_live(agent, override)
    assert out.backend == "agent_cli"
    # Absent override -> the default surfaces as "llm".
    assert agent_ops._policy_from_live(agent, None).backend == "llm"


def test_agent_backend_accessor():
    load_runtime_policies({"draft_model": {"backend": "agent_cli"}})
    assert agent_backend("draft_model") == "agent_cli"
    assert agent_backend("review_model") == "llm"  # untouched role
    assert agent_backend("does_not_exist") == "llm"  # unknown key -> safe default, never raises


# --- dispatch: complete routes to the runner and still runs the shared tail ------------------------


async def test_complete_routes_to_agent_cli_and_runs_shared_tail(monkeypatch: pytest.MonkeyPatch):
    load_runtime_policies({"draft_model": {"backend": "agent_cli"}})

    ran: dict[str, Any] = {}

    async def _fake_run(**kwargs: Any) -> tuple[str, Usage]:
        ran["kwargs"] = kwargs
        return "routed via cli", Usage(input_tokens=42, output_tokens=7)

    monkeypatch.setattr(agent_cli, "run", _fake_run)

    # If the HTTP path were taken instead, this would blow up (no client / model traffic).
    def _boom_client() -> Any:
        raise AssertionError("HTTP Anthropic client must not be constructed on the agent_cli path")

    monkeypatch.setattr(llm, "_client", _boom_client)

    budget = TokenBudget(max_tokens=10_000)
    sink = TelemetrySink()
    with telemetry.call_context(CallContext(sink=sink, stage="draft")):
        text, usage = await llm.complete(
            model="claude-sonnet-5",
            system="sys",
            user="usr",
            max_tokens=256,
            budget=budget,
            setting_key="draft_model",
        )

    assert text == "routed via cli"
    assert ran["kwargs"]["model"] == "claude-sonnet-5"  # runner actually invoked
    # Shared tail ran: budget charged from the runner's Usage, and one telemetry record landed.
    assert budget.used == usage.budget_cost > 0
    assert len(sink.records) == 1
    assert sink.records[0].model == "claude-sonnet-5"
    assert sink.records[0].output_tokens == 7


async def test_complete_default_setting_key_stays_on_llm_backend(monkeypatch: pytest.MonkeyPatch):
    # No setting_key -> backend "llm": the runner must never be consulted (full backward-compat).
    load_runtime_policies({"draft_model": {"backend": "agent_cli"}})

    async def _must_not_run(**_kwargs: Any) -> tuple[str, Usage]:
        raise AssertionError("agent_cli.run called without a setting_key")

    monkeypatch.setattr(agent_cli, "run", _must_not_run)

    captured: dict[str, Any] = {}

    async def _fake_retries(make_coro: Any, **_kw: Any) -> Any:
        captured["hit_http"] = True

        class _Resp:
            stop_reason = "end_turn"

            class usage:  # noqa: N801 — mimics the Anthropic SDK response.usage shape
                input_tokens = 10
                output_tokens = 4
                cache_creation_input_tokens = 0
                cache_read_input_tokens = 0

            content: list[Any] = []

        return _Resp()

    monkeypatch.setattr(llm, "_call_with_retries", _fake_retries)
    monkeypatch.setattr(llm, "_client", lambda: object())

    text, _usage = await llm.complete(
        model="claude-sonnet-5",
        system="sys",
        user="usr",
        max_tokens=64,
        budget=TokenBudget(max_tokens=10_000),
    )
    assert captured.get("hit_http") is True  # took the HTTP branch, not the runner
    assert text == ""  # empty content list -> empty text (fine; we only assert the routing)
