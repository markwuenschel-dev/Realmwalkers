"""Fitness check for the C1 greenlet class: no ORM-attribute reads on a loop row inside `_sweep_one_run`'s
except handlers.

Every `except` in `_sweep_one_run` follows a `session.begin_nested()` savepoint. On a mid-stage failure the
savepoint rolls back and expires the row's flushed attributes, so reading `task.<attr>` / `run.<attr>` there
(even `task.id`) is a sync lazy-load on the async session -> MissingGreenlet. The fix captures primitives
(`tid`, `task_id`, `authority`) before the savepoint; this check keeps a future edit from reintroducing the
class. Pure static analysis — no DB, always runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import dominion.workers.sweeper as sweeper_mod

# Loop rows whose attributes must never be read inside an except handler in the guarded function.
_ORM_ROW_NAMES = {"task", "run"}
_GUARDED_FUNC = "_sweep_one_run"


def _orm_reads_in_except(source: str, func_name: str) -> list[tuple[int, str]]:
    """Return (lineno, "name.attr") for every ORM-row attribute read inside an except handler of the
    named function. Scoped to func_name because every except there sits after a begin_nested savepoint."""
    tree = ast.parse(source)
    target = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == func_name),
        None,
    )
    assert target is not None, f"{func_name} not found in source"
    violations: list[tuple[int, str]] = []
    for handler in (n for n in ast.walk(target) if isinstance(n, ast.ExceptHandler)):
        for node in ast.walk(handler):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in _ORM_ROW_NAMES:
                violations.append((node.lineno, f"{node.value.id}.{node.attr}"))
    return violations


def test_sweeper_sweep_one_run_has_no_orm_reads_in_except():
    source = Path(sweeper_mod.__file__).read_text(encoding="utf-8")
    violations = _orm_reads_in_except(source, _GUARDED_FUNC)
    assert not violations, (
        f"{_GUARDED_FUNC} reads ORM-row attributes inside an except handler (post-savepoint lazy-load / "
        f"MissingGreenlet risk): {violations}. Capture a primitive before the savepoint instead."
    )


def test_guard_flags_a_post_savepoint_orm_read():
    # Negative fixture: the checker must go red on an except that reads an expired ORM attribute.
    bad = (
        "async def _sweep_one_run(session, run_id, cfg):\n"
        "    for task in tasks:\n"
        "        try:\n"
        "            async with session.begin_nested():\n"
        "                await apply(session, task.id)\n"
        "        except Exception as exc:\n"
        "            log.error('boom', authority=task.authority_level)\n"
    )
    violations = _orm_reads_in_except(bad, _GUARDED_FUNC)
    assert (7, "task.authority_level") in violations
