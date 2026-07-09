"""Fitness check for the greenlet class: no ORM-row attribute reads inside the except handler of ANY
savepoint-bearing function in sweeper.py.

Every `session.begin_nested()` savepoint that rolls back on a mid-stage failure expires the flushed
row's attributes, so reading `task.<attr>` / `run.<attr>` in the following except (even `task.id`) is a
sync lazy-load on the async session -> MissingGreenlet (the N1/C1 class). The fix captures primitives
before the savepoint. This scans every function that opens a savepoint — not just `_sweep_one_run` — so a
new savepoint helper can't silently reintroduce the class. Pure static analysis; no DB, always runs.

Scope/known bound: keyed to the literal loop-row names below and to `sweeper.py` (the only module that
uses `begin_nested`). A renamed loop var or a savepoint added in another module would slip past — widen
`_ORM_ROW_NAMES` / `_GUARDED_MODULES` when that happens.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import dominion.workers.sweeper as sweeper_mod

# Loop rows whose attributes must never be read inside an except handler after a savepoint.
_ORM_ROW_NAMES = {"task", "run"}


def _savepoint_functions(source: str) -> Iterator[ast.AsyncFunctionDef | ast.FunctionDef]:
    """Every function/coroutine whose body opens a `.begin_nested()` savepoint."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "begin_nested"
            for c in ast.walk(node)
        ):
            yield node


def _orm_reads_in_except(func: ast.AsyncFunctionDef | ast.FunctionDef) -> list[tuple[int, str]]:
    """(lineno, "name.attr") for every ORM-row attribute read inside an except handler of `func`."""
    violations: list[tuple[int, str]] = []
    for handler in (n for n in ast.walk(func) if isinstance(n, ast.ExceptHandler)):
        for node in ast.walk(handler):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in _ORM_ROW_NAMES:
                violations.append((node.lineno, f"{node.value.id}.{node.attr}"))
    return violations


def test_sweeper_savepoint_functions_have_no_orm_reads_in_except():
    source = Path(sweeper_mod.__file__).read_text(encoding="utf-8")
    funcs = list(_savepoint_functions(source))
    assert funcs, "guard is inert: no begin_nested() savepoint found in sweeper.py"
    problems = {f.name: v for f in funcs if (v := _orm_reads_in_except(f))}
    assert not problems, (
        f"savepoint function(s) read ORM-row attributes inside an except handler (post-savepoint "
        f"lazy-load / MissingGreenlet risk): {problems}. Capture a primitive before the savepoint instead."
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
    funcs = list(_savepoint_functions(bad))
    assert len(funcs) == 1
    assert (7, "task.authority_level") in _orm_reads_in_except(funcs[0])
