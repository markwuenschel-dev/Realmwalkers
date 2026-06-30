"""Static guardrail: draft Job rows may only be constructed in approved modules."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "dominion"

ALLOWED_SUFFIXES = {
    "workers/job_routing.py",
    "workers/draft_queue.py",
    "workers/enqueue.py",
}


def test_draft_job_instantiation_only_in_whitelisted_modules():
    violations: list[str] = []
    markers = ("kind=JobKind.DRAFT", 'kind="draft"', "kind='draft'")
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(REPO / "src" / "dominion").as_posix()
        if rel in ALLOWED_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        if any(m in text for m in markers):
            if rel.startswith("tests/"):
                continue
            violations.append(rel)
    assert not violations, f"Job(kind=draft) outside whitelist: {violations}"
