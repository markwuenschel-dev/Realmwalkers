"""Fitness check for the greenlet class: no ORM-row attribute reads inside the except handler of ANY
savepoint-bearing function in sweeper.py.

Every `session.begin_nested()` savepoint that rolls back on a mid-stage failure expires the flushed
row's attributes, so reading `task.<attr>` / `run.<attr>` in the following except (even `task.id`) is a
sync lazy-load on the async session -> MissingGreenlet (the N1/C1 class). The fix captures primitives
before the savepoint. This scans every function that opens a savepoint — not just `_sweep_one_run` — so a
new savepoint helper can't silently reintroduce the class. Pure static analysis; no DB, always runs.

Scope/known bound: keyed to the literal loop-row names below and to `sweeper.py`. A renamed loop var
would still slip past — widen `_ORM_ROW_NAMES` when that happens. The cross-module bound is now
enforced rather than trusted: `test_begin_nested_is_confined_to_the_guarded_module` fails the moment
any other module introduces a `begin_nested` savepoint this scan does not cover.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import dominion.shared.adoption_entry as adoption_entry_mod
import dominion.workers.evidence_store as evidence_store_mod
import dominion.workers.sweeper as sweeper_mod

# Loop / working rows whose attributes must never be read inside an except handler after a savepoint.
# `adoption` is the row the ADR-0032 W1 seam (adoption_entry.py) flushes INSIDE its collision savepoint —
# expired on the savepoint rollback, so its attributes must not be read in the ensuing except handler.
_ORM_ROW_NAMES = {"task", "run", "scene", "adoption"}

# Every module that legitimately opens a savepoint. Each is scanned by the greenlet check below; the
# confinement test fails the moment `begin_nested` appears in a module NOT listed here — so a new
# savepoint can't dodge the scan. Add a module here ONLY together with wiring it into the scan.
_GUARDED_MODULES = (sweeper_mod, evidence_store_mod, adoption_entry_mod)


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


def test_savepoint_functions_have_no_orm_reads_in_except():
    for mod in _GUARDED_MODULES:
        name = Path(mod.__file__).name
        funcs = list(_savepoint_functions(Path(mod.__file__).read_text(encoding="utf-8")))
        assert funcs, f"guard is inert: no begin_nested() savepoint found in {name}"
        problems = {f.name: v for f in funcs if (v := _orm_reads_in_except(f))}
        assert not problems, (
            f"savepoint function(s) in {name} read ORM-row attributes inside an except handler "
            f"(post-savepoint lazy-load / MissingGreenlet risk): {problems}. Capture a primitive "
            f"before the savepoint instead."
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


def test_begin_nested_is_confined_to_the_guarded_module():
    """Cross-module coverage: the scan above only reads sweeper.py. If `begin_nested` appears in any
    other dominion module, that savepoint is unguarded — fail loudly so the guard is widened to cover
    it (extend `_savepoint_functions`' scan), not so this allowlist is quietly grown."""
    pkg_root = Path(sweeper_mod.__file__).resolve().parents[1]  # src/dominion
    guarded = {Path(m.__file__).name for m in _GUARDED_MODULES}
    offenders = sorted(
        str(py.relative_to(pkg_root))
        for py in pkg_root.rglob("*.py")
        if py.name not in guarded and "begin_nested" in py.read_text(encoding="utf-8")
    )
    assert not offenders, (
        f"begin_nested() found outside the guarded module(s) {sorted(guarded)}: {offenders}. "
        "The greenlet guard only scans sweeper.py — extend _savepoint_functions to cover these modules."
    )
