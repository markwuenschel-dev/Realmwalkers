"""Loaders + adapters for the bad Chapter 1 production run fixtures.

Fixture set: tests/fixtures/ch1_bad_run/ — preserved from failing run 51d635ec
(see reports/ch1_pipeline_failure_analysis.md). Pure JSON parsing, no DB, no network.

Two layers:

1. Loaders — parse the fixture files and expose the pieces the regression tests
   assert against (sequence body, scene packets, the assembled chapter draft prose
   and its per-scene rows).

2. Adapters — the recovery lanes (scene_scope / budget_reconciliation / canon_guards /
   the lane-1 entry-state chaining post-pass) are landing in parallel worktrees. Their
   module paths and issue kinds are pinned; exact callable names/signatures are not.
   `call_detector` probes a candidate-name list and binds the first plausible signature.
   If a lane module exists but no candidate matches, the tests fail LOUDLY with a
   pointer here — the integrator aligns the adapter in this one file, never the
   assertions in the test file.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ch1_bad_run"

# --- Pinned facts about the bad run (see reports/ch1_pipeline_failure_analysis.md) ---
CHAPTER_HARD_MAX_WORDS = 7_200
SCENE_HARD_MAX_SUM = 10_400  # 2200 + 2400 + 3200 + 2600
SCENE_COUNT = 4
LEAK_MARKER = "Neurochromatic Eyes flickered"
LEAK_TERM = "neurochromatic"
NO_EYES_RULING_MARKER = "No Eyes notification in Chapter 1"

# Issue kinds pinned across lanes (lane 5 triage clusters reference the same names).
KIND_SCOPE_BLEED = "scene_scope_bleed"
KIND_DUPLICATE_BEAT = "duplicate_irreversible_beat"
KIND_BUDGET_MISMATCH = "sequence_budget_mismatch"
KIND_CANON_LEAK = "canon_contract_leak"
NEW_TAXONOMY_KINDS = frozenset({KIND_SCOPE_BLEED, KIND_DUPLICATE_BEAT, KIND_BUDGET_MISMATCH, KIND_CANON_LEAK})


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


@cache
def _load(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def chapter_packet() -> dict[str, Any]:
    return _load("chapter_packet.json")


def chapter_packet_body() -> dict[str, Any]:
    return chapter_packet()["body"]


def chapter_sequence() -> dict[str, Any]:
    return _load("chapter_sequence.json")


def sequence_body() -> dict[str, Any]:
    return chapter_sequence()["body"]


def sequence_scenes() -> list[dict[str, Any]]:
    """Sequence scene entries sorted by scene_no (1..4)."""
    return sorted(sequence_body()["scenes"], key=lambda s: int(s["scene_no"]))


def scene_packets() -> list[dict[str, Any]]:
    """Approved scene packets sorted by scene_no (1..4)."""
    return sorted(_load("scene_packets.json"), key=lambda p: int(p["scene_no"]))


def production_run_detail() -> dict[str, Any]:
    return _load("production_run_detail.json")


def chapter_draft_artifact() -> dict[str, Any]:
    """The assembled chapter_draft artifact from the failing run."""
    for artifact in production_run_detail()["artifacts"]:
        if artifact.get("artifact_type") == "chapter_draft":
            return artifact
    raise AssertionError("chapter_draft artifact missing from production_run_detail fixture")


def assembled_prose() -> str:
    """Full assembled chapter prose (the REAL 9.6k-word bad draft)."""
    return chapter_draft_artifact()["body"]["prose"]


def draft_scene_rows() -> list[dict[str, Any]]:
    """Per-scene rows from the chapter_draft artifact's body.scenes, sorted by scene_no.

    Each row carries the drafted prose plus the contract the drafter was handed
    (entry_state/exit_state, owned/required/forbidden beats, word_budget, word_count).
    """
    rows = chapter_draft_artifact()["body"].get("scenes") or []
    return sorted((r for r in rows if isinstance(r, dict)), key=lambda r: int(r["scene_no"]))


def scene_prose_by_no() -> dict[int, str]:
    """scene_no -> drafted prose, from the chapter_draft artifact rows."""
    return {int(r["scene_no"]): str(r.get("prose") or "") for r in draft_scene_rows()}


def word_count(text: str) -> int:
    return len(text.split())


def count_matches(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def resolved_rulings() -> list[str]:
    """All resolved open-question ruling strings from the chapter packet (recursive)."""
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            resolved = node.get("resolved")
            if isinstance(resolved, list):
                for item in resolved:
                    if isinstance(item, dict) and isinstance(item.get("resolution"), str):
                        out.append(item["resolution"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(chapter_packet())
    return out


# ---------------------------------------------------------------------------
# Issue-result normalization (lane outputs come in unknown container shapes)
# ---------------------------------------------------------------------------

_ISSUE_LIST_KEYS = ("issues", "items", "findings", "violations", "leaks", "results")
_KIND_KEYS = ("issue_kind", "kind", "issue_type", "type")
_BLOCKING_SEVERITIES = frozenset({"blocker", "blocking", "block", "hard", "fatal", "error"})


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        return {"issue_kind": item}
    if hasattr(item, "__dict__") or hasattr(item, "__slots__"):
        data: dict[str, Any] = {}
        for key in dir(item):
            if key.startswith("_"):
                continue
            try:
                value = getattr(item, key)
            except Exception:
                continue
            if not callable(value):
                data[key] = value
        return data
    return {}


def as_issue_dicts(result: Any) -> list[dict[str, Any]]:
    """Normalize a lane detector's return value into a flat list of issue dicts."""
    if result is None:
        return []
    if isinstance(result, dict):
        for key in _ISSUE_LIST_KEYS:
            if isinstance(result.get(key), list):
                return [_as_dict(i) for i in result[key]]
        # A dict that itself looks like a single issue.
        if any(k in result for k in _KIND_KEYS):
            return [result]
        return []
    if isinstance(result, (list, tuple)):
        return [_as_dict(i) for i in result]
    single = _as_dict(result)
    if isinstance(single, dict) and any(k in single for k in _ISSUE_LIST_KEYS):
        return as_issue_dicts({k: v for k, v in single.items()})
    return [single] if single else []


def issue_kinds(result: Any) -> list[str]:
    kinds: list[str] = []
    for issue in as_issue_dicts(result):
        for key in _KIND_KEYS:
            value = issue.get(key)
            if isinstance(value, str) and value:
                kinds.append(value)
                break
    return kinds


def issues_of_kind(result: Any, kind: str) -> list[dict[str, Any]]:
    matched = []
    for issue in as_issue_dicts(result):
        for key in _KIND_KEYS:
            if issue.get(key) == kind:
                matched.append(issue)
                break
    return matched


def is_blocking(issue: dict[str, Any], result: Any = None) -> bool:
    """True when a normalized issue carries any recognizable blocking marker."""
    if issue.get("blocking") is True or issue.get("is_blocking") is True:
        return True
    severity = issue.get("severity")
    if isinstance(severity, str) and severity.lower() in _BLOCKING_SEVERITIES:
        return True
    # Container-level marker: result dict exposes a blocking_issues list naming the kind.
    if isinstance(result, dict):
        blocking = result.get("blocking_issues")
        if isinstance(blocking, list):
            blob = json.dumps(blocking, default=str)
            for key in _KIND_KEYS:
                kind = issue.get(key)
                if isinstance(kind, str) and kind and kind in blob:
                    return True
    return False


def issue_text(issue: dict[str, Any]) -> str:
    """Whole-issue serialized text, for substring assertions on quotes/claims."""
    return json.dumps(issue, default=str, ensure_ascii=False).lower()


# ---------------------------------------------------------------------------
# Lane detector adapters
# ---------------------------------------------------------------------------


class AdapterMismatch(AssertionError):
    """Lane module landed but the harness adapter found no matching callable/signature."""


def _resolve_callable(module: Any, names: tuple[str, ...]):
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return name, fn
    return None, None


def call_detector(
    module: Any,
    names: tuple[str, ...],
    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]],
) -> Any:
    """Find the lane's entry point by candidate name and call it with the first
    argument shape its signature accepts. Raises AdapterMismatch (=> loud test
    failure, not a skip) when the module exists but nothing lines up — update the
    candidate lists HERE, not the assertions in the test file."""
    name, fn = _resolve_callable(module, names)
    if fn is None:
        raise AdapterMismatch(
            f"{module.__name__} landed but exposes none of {names}; "
            "extend the candidate names in tests/ch1_bad_run_fixtures.py"
        )
    sig = inspect.signature(fn)
    for args, kwargs in attempts:
        try:
            sig.bind(*args, **kwargs)
        except TypeError:
            continue
        return fn(*args, **kwargs)
    raise AdapterMismatch(
        f"{module.__name__}.{name}{sig} did not accept any known argument shape; "
        "extend the attempts list in tests/ch1_bad_run_fixtures.py"
    )


# Lane 1: entry-state chaining post-pass. Module path is not pinned — probe the
# plausible homes (sequence derivation lives in dominion.workers.production today;
# scene_packet.derive is the coordinator-suggested landing spot).
LANE1_MODULES = (
    "dominion.workers.scene_packet.derive",
    "dominion.workers.production",
    "dominion.workers.sequence_chaining",
    "dominion.workers.scene_packet.chaining",
    "dominion.workers.packet.sequence_chaining",
)
LANE1_FUNCS = (
    "chain_entry_states",
    "apply_entry_state_chain",
    "chain_scene_entry_states",
    "chain_sequence_entry_states",
    "rechain_entry_states",
    "chain_sequence",
)


def resolve_lane1_postpass():
    """(module_name, func) for lane 1's chaining post-pass, or (None, None) pre-landing."""
    for module_name in LANE1_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        name, fn = _resolve_callable(module, LANE1_FUNCS)
        if fn is not None:
            return f"{module_name}.{name}", fn
    return None, None


def apply_lane1_postpass(fn, body: dict[str, Any]) -> dict[str, Any]:
    """Run the chaining post-pass over a sequence body copy; normalize the result
    back to a sequence-body dict whether fn returns a body, a scenes list, or
    mutates in place."""
    sig = inspect.signature(fn)
    for args in ((body,), (body["scenes"],)):
        try:
            sig.bind(*args)
        except TypeError:
            continue
        result = fn(*args)
        if isinstance(result, dict) and isinstance(result.get("scenes"), list):
            return result
        if isinstance(result, list):
            return {**body, "scenes": result}
        return body  # mutated in place
    raise AdapterMismatch(
        f"lane-1 post-pass {fn.__name__}{sig} accepts neither (sequence_body) nor (scenes); "
        "extend apply_lane1_postpass in tests/ch1_bad_run_fixtures.py"
    )
