"""Claude Code CLI runner — an alternative backend for `llm.complete`.

When a role's policy sets `backend="agent_cli"`, `llm.complete` routes that role's generation here
instead of the Anthropic/OpenAI HTTP API. We shell out to
`claude -p <prompt> --output-format json --model <model> --max-turns <n> --append-system-prompt <sys>`
in an ISOLATED temp cwd — so the CLI never picks up the repo's `CLAUDE.md`, runs project hooks, or
edits files — inheriting the process env so `CLAUDE_CODE_OAUTH_TOKEN` (subscription auth) or
`ANTHROPIC_API_KEY` (metered) authenticates the call; when the OAuth token is set, the API key is
dropped from the subprocess env (the CLI would otherwise prefer it). The CLI's JSON result maps back into the exact
`(text, Usage)` tuple the rest of the pipeline already consumes, so every cross-cutting concern wrapped
AROUND `complete` (budget.charge, telemetry, escalation, the tolerant JSON parsers) works unchanged.

Failure mapping mirrors the HTTP path's classification contract:
- a usage/rate-limit message (subscription cap or provider 429) -> `LlmRateLimited`, so orchestrators
  classify it as transient infrastructure, never an author/QA quality failure;
- a timeout or transient subprocess exit -> `AgentCliError(transient=True)`, which the shared retry
  loop retries;
- a missing binary or non-JSON output -> `AgentCliError(transient=False)`, surfaced as a clear,
  non-retryable job failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from collections.abc import Sequence
from typing import Any, Literal

import structlog

from dominion.shared.config import settings
from dominion.workers.budget import Usage
from dominion.workers.llm import CachedPrefixBlock, LlmRateLimited, estimate_tokens

log = structlog.get_logger()


class AgentCliError(Exception):
    """A Claude Code CLI subprocess failure. `transient` marks whether the shared retry loop should
    retry it (timeout / transient exit) or propagate immediately (missing binary, malformed output)."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def is_transient_error(exc: BaseException) -> bool:
    """Retry classifier for `_call_with_retries` on the agent_cli path: only a transient AgentCliError
    retries. `LlmRateLimited` is intentionally NOT transient here — it carries its own classification
    and must propagate so callers treat it as infrastructure, never a generation failure."""
    return isinstance(exc, AgentCliError) and exc.transient


# Free-text substrings that mean the subscription/API usage cap was hit — mapped to LlmRateLimited so
# orchestrators classify it as transient infrastructure (the same path as a 429). This is the FALLBACK
# signal, consulted only when the CLI's JSON envelope carries no structured error type (see below): free
# text is fragile, so we anchor to distinctive phrases. Note the absence of a bare "429" — on its own it
# false-positives on request IDs, byte counts, timestamps, or generated prose that merely contains "429";
# a real HTTP rate-limit surfaces it as one of the anchored forms here (or, better, structurally).
_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "too many requests",
    "limit reached",
    "overloaded",
    "429 too many requests",
    "http 429",
    "status 429",
    "status code 429",
    "error 429",
    "(429)",
)

# Structured (non-free-text) rate-limit signals from the CLI's JSON envelope. These take PRECEDENCE over
# the substring scan: the Anthropic API's error object exposes a machine-readable `type`, and a 429 / 529
# surfaces as `rate_limit_error` / `overloaded_error` (see the API error-codes reference). When the CLI
# propagates that shape in its result envelope's `error` field, we trust it verbatim rather than sniffing
# text. Kept as a small allowlist so an unrelated error type never masquerades as a rate limit.
_STRUCTURED_RATE_LIMIT_ERROR_TYPES: frozenset[str] = frozenset({"rate_limit_error", "overloaded_error"})

# CLI result `subtype` values that are DEFINITIVELY hard failures, never a transient rate limit — a
# max-turns abort is the agent running out of budget, not the provider throttling us. When the envelope
# carries one of these, we classify "hard" regardless of free text, so a "429" (or "limit") appearing in
# the model's own generated `result` text cannot flip a real hard failure into a spurious retry.
_HARD_ERROR_SUBTYPES: frozenset[str] = frozenset({"error_max_turns"})


def _looks_rate_limited(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _RATE_LIMIT_MARKERS)


def _envelope_rate_limited(envelope: dict[str, Any]) -> bool:
    """True only for a STRUCTURED rate-limit signal in the CLI's JSON envelope (no text sniffing).

    Trusts two machine-readable shapes: an Anthropic-style `error` object whose `type` is a known
    rate-limit/overload type, or a `subtype` that explicitly names rate-limiting. Returns False (defer to
    the substring fallback) for anything else."""
    err = envelope.get("error")
    if isinstance(err, dict) and str(err.get("type") or "").strip().lower() in _STRUCTURED_RATE_LIMIT_ERROR_TYPES:
        return True
    subtype = str(envelope.get("subtype") or "").strip().lower()
    return "rate_limit" in subtype or "overloaded" in subtype or "usage_limit" in subtype


def _classify_failure(
    exit_code: int | None,
    envelope: dict[str, Any] | None,
    combined_text: str,
) -> Literal["rate_limit", "non_retryable"]:
    """Decide whether a CLI failure is a transient rate limit or a non-retryable error.

    "non_retryable" (renamed from the ambiguous "hard" — SEV-ALIAS: `hard` is the legacy severity spelling
    of `block`, a different domain) is the CLI failure class, unrelated to Issue/Critique severity.

    Precedence, structured-signals-first:
      1. A definitive hard-error subtype in the envelope -> "non_retryable" (rules OUT rate-limiting even if the
         free text happens to contain "429"/"limit").
      2. A structured rate-limit signal in the envelope -> "rate_limit".
      3. Fallback: the hardened `_RATE_LIMIT_MARKERS` substring scan over the combined stdout/stderr/detail.

    When no envelope is available (e.g. a non-JSON crash on the non-zero-exit path) behavior is identical
    to the original free-text-only classification, so the change is backward compatible."""
    if envelope is not None:
        subtype = str(envelope.get("subtype") or "").strip().lower()
        if subtype in _HARD_ERROR_SUBTYPES:
            return "non_retryable"
        if _envelope_rate_limited(envelope):
            return "rate_limit"
    return "rate_limit" if _looks_rate_limited(combined_text) else "non_retryable"


def _try_parse_envelope(stdout: str) -> dict[str, Any] | None:
    """Best-effort parse of the CLI's JSON result envelope for structured error classification.

    The CLI emits its JSON envelope on stdout even on some non-zero exits, so we try to recover it there
    too (not only on the success path) — but a failed parse is fine: classification simply falls back to
    the free-text scan."""
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_prompt(user: str, prefix_blocks: Sequence[CachedPrefixBlock]) -> str:
    """The CLI has no separate cached-prefix channel, so concatenate the stable prefix blocks before
    the dynamic `user` tail (the same order `llm.complete` sends them) — the model sees identical
    context; the caching upside is simply forfeited on this path."""
    if prefix_blocks:
        return "\n\n".join([*(b.text for b in prefix_blocks), user])
    return user


async def run(
    *,
    model: str,
    system: str,
    user: str,
    prefix_blocks: Sequence[CachedPrefixBlock] = (),
    max_tokens: int,
    temperature: float | None = None,
    effort: str | None = None,
    expect_cache: bool = True,
) -> tuple[str, Usage]:
    """Run one generation through the Claude Code CLI and return `(text, Usage)`.

    `temperature`/`effort`/`max_tokens`/`expect_cache` are accepted for interface parity with the HTTP
    backend but are not per-call CLI flags (the CLI drives the model with its own defaults); they are
    still counted in the estimate-only token fallback below when the CLI omits usage.
    """
    prompt = _build_prompt(user, prefix_blocks)
    argv: list[str] = [
        settings.agent_cli_bin,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-turns",
        str(settings.agent_cli_max_turns),
    ]
    if system:
        argv += ["--append-system-prompt", system]
    allowed = (settings.agent_cli_allowed_tools or "").strip()
    if allowed:
        argv += ["--allowedTools", allowed]

    timeout_s = max(1, int(settings.scene_time_budget_s))

    # Isolated cwd: the CLI can't read the repo's CLAUDE.md, run hooks, or touch project files. Env is
    # inherited so the auth token / API key flows — but when CLAUDE_CODE_OAUTH_TOKEN (subscription
    # auth) is set, ANTHROPIC_API_KEY must be dropped: the CLI prefers the API key when both are
    # present, silently rerouting agent_cli calls back onto metered credits.
    env: dict[str, str] | None = None
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory(prefix="agent_cli_") as cwd:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AgentCliError(
                f"claude CLI not found (agent_cli_bin={settings.agent_cli_bin!r}); install "
                "@anthropic-ai/claude-code or set DOMINION_AGENT_CLI_BIN to its path",
                transient=False,
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError as exc:  # asyncio.wait_for raises TimeoutError (asyncio.TimeoutError is its alias)
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.communicate()
            raise AgentCliError(f"claude CLI timed out after {timeout_s}s", transient=True) from exc

    stdout = stdout_b.decode("utf-8", "replace")
    stderr = stderr_b.decode("utf-8", "replace")

    if proc.returncode != 0:
        combined = f"{stdout}\n{stderr}"
        # Prefer the CLI's structured error envelope (it may be emitted on stdout even on a non-zero exit)
        # over free-text sniffing; fall back to the substring scan when no envelope is recoverable.
        envelope = _try_parse_envelope(stdout)
        if _classify_failure(proc.returncode, envelope, combined) == "rate_limit":
            raise LlmRateLimited(f"claude CLI usage/rate limit: {(stderr.strip() or stdout.strip())[:400]}")
        raise AgentCliError(
            f"claude CLI exited {proc.returncode}: {(stderr.strip() or stdout.strip())[:400]}",
            transient=True,
        )

    try:
        payload: Any = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AgentCliError(f"claude CLI returned non-JSON stdout: {stdout[:400]}", transient=False) from exc

    # A successful exit can still carry an error envelope (e.g. a usage cap or max-turns abort). Classify
    # from the envelope's structured fields first (subtype / error type), then fall back to the detail text.
    if isinstance(payload, dict) and (payload.get("is_error") or str(payload.get("subtype") or "").startswith("error")):
        detail = str(payload.get("result") or payload.get("error") or payload)
        if _classify_failure(proc.returncode, payload, detail) == "rate_limit":
            raise LlmRateLimited(f"claude CLI usage/rate limit: {detail[:400]}")
        raise AgentCliError(f"claude CLI reported error: {detail[:400]}", transient=True)

    result_text = ""
    usage_obj: dict[str, Any] = {}
    if isinstance(payload, dict):
        result_text = str(payload.get("result") or "")
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            usage_obj = raw_usage

    input_tokens = int(usage_obj.get("input_tokens") or 0)
    output_tokens = int(usage_obj.get("output_tokens") or 0)
    cache_read = int(usage_obj.get("cache_read_input_tokens") or 0)
    cache_creation = int(usage_obj.get("cache_creation_input_tokens") or 0)
    # Fall back to the local estimate when the CLI omits token accounting, so budget/telemetry still
    # see a non-zero, roughly-correct cost instead of silently charging zero for real work.
    if input_tokens <= 0:
        input_tokens = estimate_tokens(system) + estimate_tokens(prompt)
    if output_tokens <= 0:
        output_tokens = estimate_tokens(result_text)

    usage = Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        # The CLI single-shot returns a complete result; there is no max_tokens truncation signal to map.
        truncated=False,
    )
    log.info(
        "agent_cli.complete",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        result_chars=len(result_text),
        cost_usd=(payload.get("total_cost_usd") if isinstance(payload, dict) else None),
    )
    return result_text, usage
