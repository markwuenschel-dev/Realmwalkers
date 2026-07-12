"""Static fitness guard for the book-ownership invariant (ADR 0027).

A Job belongs to a *book* via `book_id`; `run_id` is provenance only. This test fails if any module
reintroduces the legacy `Job`->`Run` routing scope — the exact defect class that stranded run-less
revision jobs. It bans only the two semantic regressions, via `ast` (so it survives reformatting and
multiline SQLAlchemy chains), and reports file+line:

  1. a `Job` query joining `Run` through `Job.run_id`  (`Job.run_id == Run.id`)
  2. `Job.run_id.in_(select(Run.id) ...)`             (selecting jobs by a Run's book)

It does NOT ban direct `Job.book_id == book_id` (correct after the migration). Instance attribute reads
like `job.run_id` (telemetry provenance, token-budget carry-forward) are untouched — the detector keys
on the CLASS names `Job`/`Run`, i.e. ORM query expressions, not instance access.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "dominion"

# Modules allowed to reference Run for ownership *resolution* (not per-book scoping). In practice these
# use raw SQL, so they don't even produce the AST patterns below — the exemption is defensive intent.
_EXEMPT = {"job_policy.py", "job_integrity.py", "migrations.py"}


def _is_class_attr(node: ast.AST, cls: str, attr: str) -> bool:
    """True for `Cls.attr` where Cls is a bare Name (the ORM class), e.g. `Job.run_id`, `Run.id`."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == cls
    )


def _refs_run(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id == "Run" for n in ast.walk(node))


def find_violations(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # (1) Job.run_id == Run.id  (join predicate / where clause)
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            has_job_run = any(_is_class_attr(s, "Job", "run_id") for s in sides)
            has_run_id = any(_is_class_attr(s, "Run", "id") for s in sides)
            if has_job_run and has_run_id:
                out.append((node.lineno, "Job.run_id == Run.id (Job->Run routing scope)"))
        # (2) Job.run_id.in_(select(Run.id) ...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "in_":
            if _is_class_attr(node.func.value, "Job", "run_id") and any(_refs_run(a) for a in node.args):
                out.append((node.lineno, "Job.run_id.in_(select(Run.id) ...) (Job->Run routing scope)"))
    return out


def test_no_job_to_run_routing_scope_in_src() -> None:
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        if path.name in _EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, kind in find_violations(tree):
            offenders.append(f"{path.relative_to(_SRC.parent.parent)}:{lineno}: {kind}")
    assert not offenders, "Book scoping must use scope_jobs_to_book (ADR 0027). Offenders:\n" + "\n".join(offenders)


# --- Detector self-checks: the guard itself must not silently become ineffective. ----------------

_POSITIVE = [
    "stmt.join(Run, Job.run_id == Run.id).where(Run.book_id == book_id)",
    "q.where(Job.run_id.in_(select(Run.id).where(Run.book_id == book_id)))",
]
_NEGATIVE = [
    "stmt.where(Job.book_id == book_id)",  # the correct single-key scope
    "telemetry_db.persist_sink(session, sink, run_id=job.run_id)",  # instance read, not a scope
    "run = await session.get(Run, old.run_id)",  # instance read, not a scope
    "q.where(Job.run_id.in_([a, b, c]))",  # in_ over literals, no Run reference
]


def test_detector_flags_the_banned_patterns() -> None:
    for src in _POSITIVE:
        assert find_violations(ast.parse(src)), f"detector missed a violation: {src}"


def test_detector_ignores_the_allowed_patterns() -> None:
    for src in _NEGATIVE:
        assert not find_violations(ast.parse(src)), f"detector false-positived: {src}"
