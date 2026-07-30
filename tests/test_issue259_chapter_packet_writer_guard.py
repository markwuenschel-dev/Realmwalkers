"""#259 residual 1, the fitness half — every ChapterPacket authority writer is lock-covered.

Why this exists in this shape. The guard that was supposed to catch this drift,
`test_slice3b_torture.py:342`, is an ALLOWLIST: it holds five named function objects and greps each
for a lock token. An allowlist can only re-check functions someone remembered to add, so the four
`routers/packets.py` transitions — plus `_persist` and `hard_delete_chapter_packets`, which #259 did
not even name — were invisible to it. That guard still covers its five adoption/scene-packet
mutations and is left alone; this one owns the ChapterPacket writer set by enumeration.

HOW IT KNOWS A ROW IS A ChapterPacket. Two earlier drafts of this file were quietly blind, and both
blind spots are the reason the detector now does real (if small) type tracking:

  * draft 1 keyed rule 3 on `PacketStatus` appearing in the assigned value — so it PASSED a mutation
    that stripped the lock wrapper off `update_packet`, whose writes are `body`/`open_questions`/
    `confidence`;
  * draft 2 gated rules 3/4 on the enclosing function's own source containing the literal
    "ChapterPacket" — but at HEAD neither `update_packet` nor `approve_packet` did (they get their row
    from `_latest`), so the guard would NOT have caught the very defect #259 was filed for. Widening
    that gate to module scope over-flagged 29 unrelated ScenePacket/ProductionRun/adoption writers,
    which would have turned the exemption list into a rubber stamp.

So instead: within each function, track the local names actually bound to a ChapterPacket — from a
constructor, a `session.get(ChapterPacket, ...)`, a parameter annotated `ChapterPacket`, or a call to
a module function whose RETURN ANNOTATION is ChapterPacket (this repo is fully annotated and pyright
runs in CI, so annotations are trustworthy). A store is a write only when its receiver is one of those
names. `test_the_guard_catches_the_original_defect` runs the detector against `PREFIX_PACKETS`, the
pre-fix source checked in below, and requires it to flag `update_packet` and `approve_packet` — the
regression test for the guard itself, and the thing draft 2 failed.

What it detects:
  1. `ChapterPacket(...)` construction;
  2. Core bulk statements — `delete/update/insert(ChapterPacket)`;
  3. a store to an authority field (`AUTHORITY_FIELDS`) on a ChapterPacket-bound name;
  4. `session.delete(x)` where `x` is a ChapterPacket-bound name.

HONEST LIMITS — enumerate them, because a guard that overclaims is worse than none. Each was probed
and confirmed still invisible:
  * a ChapterPacket obtained from a helper in ANOTHER module (`_returns_chapter_packet` is
    per-module), e.g. `row = await packet_pipeline.latest_approved(s, c)`;
  * a write through a subscript or attribute receiver — `rows[0].status = …`, `self.row.status = …`;
  * `setattr(row, "status", …)`, `session.merge(…)`, raw `text("UPDATE chapter_packets …")`;
  * `sa.update(ChapterPacket)` written as an attribute call rather than a bare name;
  * tuple-unpacking assignment.
Walrus binding (`if (row := await _latest(...))`) IS tracked — it is this repo's own idiom, and
"supersede" (#261) is named in `chapter_lock.py:5-6` as in scope, so that shape had to be closed.
The acceptance suite (`test_issue259_chapter_packet_lock_coverage.py`) is what proves runtime
behaviour end to end; this file proves shape.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "dominion"

#: A function is lock-covered if its source contains one of these. `ensure_import_adoption(` matches
#: only the lock-acquiring seam, never `ensure_import_adoption_locked(`, which assumes the lock.
LOCK_TOKENS = (
    "run_under_chapter_workflow(",
    "acquire_chapter_workflow_lock(",
    "ensure_import_adoption(",
)

#: ChapterPacket columns whose value decides authority: the lifecycle status, the contract body, the
#: approval-gating inputs `can_approve` reads, and (#261) the amendment lineage/provenance fields.
#:
#: The amendment additions are not cosmetic. `supersedes_packet_id` / `superseded_by_packet_id` are what
#: the two lineage CHECKs test, so a write to either can move a chapter between "has an authority" and
#: "has none"; `approval_source` is the invariant-8 record of who approved; `origin_mode` selects which
#: partial unique index a row falls under, so flipping it can free or occupy the single active slot.
#: Each therefore decides authority as surely as `status` does, and an unlocked write to any of them is
#: the same defect #259 was filed for.
AUTHORITY_FIELDS = frozenset(
    {
        "status",
        "body",
        "open_questions",
        "confidence",
        "qa_verdict",
        "qa_warnings",
        "supersedes_packet_id",
        "superseded_by_packet_id",
        "superseded_at",
        "origin_mode",
        "approval_source",
        "approved_at",
        "amendment_scope",
    }
)

#: Real ChapterPacket writers that carry no lock token because a NAMED caller holds the lock for them.
#: The caller is re-verified by `test_every_exemption_still_points_at_a_locked_caller`, so an
#: exemption cannot become a hole when its caller is refactored.
EXEMPT_LOCKED_BY_CALLER: dict[str, tuple[str, str]] = {
    "api/routers/packets.py::_update_packet_locked": (
        "api/routers/packets.py::update_packet",
        "The update transition's body. update_packet runs it inside run_under_chapter_workflow and "
        "owns the commit; this helper must not commit.",
    ),
    "workers/packet/__init__.py::_blocked_row": (
        "workers/packet/__init__.py::_persist",
        "Pure factory — builds a fail-closed ChapterPacket in memory and touches no session.",
    ),
    "workers/packet/__init__.py::_qa_and_persist": (
        "workers/packet/__init__.py::_persist",
        "Constructs the row and hands it to _persist, the single locked writer, on the next line. "
        "Holding the lock here instead would span the QA model call (chapter_lock.py:20-22).",
    ),
    "api/packet_delete.py::hard_delete_chapter_packets": (
        "api/routers/packets.py::delete_packet",
        "Reached only from delete_packet, which runs it inside run_under_chapter_workflow. Proven at "
        "runtime by test_issue259_chapter_packet_lock_coverage::"
        "test_delete_under_held_chapter_lock_is_409_and_writes_nothing.",
    ),
    "workers/import_adoption.py::_delete_pass_packet": (
        "workers/import_adoption.py::publish_adoption",
        "Called only at import_adoption.py:459 and :472, both inside publish_adoption's _body(), "
        "which run_under_chapter_workflow wraps at :490.",
    ),
    "workers/packet/amendment_author.py::_amendment_row": (
        "workers/packet/__init__.py::_persist",
        "#261 W2a. Pure factory — builds the copy-on-write amendment row in memory (proposed or "
        "fail-closed blocked) and touches no session. Both call sites hand it straight to "
        "packet._persist(..., replace=False), the single locked ChapterPacket writer; holding the lock in "
        "the author pass instead would span the author+QA model calls (chapter_lock.py:20-22). It is the "
        "ONE place the amendment lineage columns are written, which is why the construction lives here "
        "rather than as attribute stores on the row.",
    ),
    "workers/packet/amendment.py::apply_authority_locked": (
        "workers/packet/amendment.py::approve_amendment",
        "#261. THE single authority transition (approve + supersede + stale children). Its name declares "
        "the precondition and its docstring states it: the chapter workflow lock is already held and it "
        "performs no commit. approve_amendment wraps it in run_under_chapter_workflow and owns the commit, "
        "so holding the lock here too would violate the wrapper's clean-transaction precondition. "
        "test_every_exemption_still_points_at_a_locked_caller re-verifies that caller on every run.",
    ),
}


#: The two handlers exactly as they stood BEFORE this change — the defect #259 was filed for. Checked
#: in (not read from git) so this evidence survives the commit that fixes it. Reduced to the shapes
#: that matter: the row comes from `_latest`, the writes are attribute stores, no lock anywhere.
PREFIX_PACKETS = """
async def _latest(session, chapter_id) -> ChapterPacket | None:
    return (await session.execute(select(ChapterPacket))).scalar_one_or_none()


async def update_packet(chapter_id, body, session) -> PacketOut:
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")
    row.qa_warnings = {}
    row.body = canonical
    row.open_questions = canonical["chapter_contract"]["open_questions"]
    row.confidence = PacketConfidence(body.confidence)
    await session.commit()
    return enrich_packet_out(row)


async def approve_packet(chapter_id, session) -> PacketOut:
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")
    row.status = PacketStatus.APPROVED
    row.body = {**row.body, "status": PacketStatus.APPROVED.value}
    await session.commit()
    return enrich_packet_out(row)
"""


def _mentions_chapter_packet(node: ast.AST | None) -> bool:
    """True if an annotation/expression names ChapterPacket (handles `ChapterPacket | None`)."""
    if node is None:
        return False
    return any(isinstance(n, ast.Name) and n.id == "ChapterPacket" for n in ast.walk(node))


def _called_name(expr: ast.AST) -> str | None:
    """The function name of a (possibly awaited) call expression."""
    if isinstance(expr, ast.Await):
        expr = expr.value
    if isinstance(expr, ast.Call):
        f = expr.func
        return f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
    return None


def _returns_chapter_packet(tree: ast.Module) -> set[str]:
    """Module functions whose return annotation is a ChapterPacket — e.g. `_latest`, `_persist`."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _mentions_chapter_packet(node.returns):
            out.add(node.name)
    return out


def _is_session_get_chapter_packet(expr: ast.AST) -> bool:
    """`await session.get(ChapterPacket, ...)`."""
    if isinstance(expr, ast.Await):
        expr = expr.value
    return (
        isinstance(expr, ast.Call)
        and getattr(expr.func, "attr", None) == "get"
        and bool(expr.args)
        and isinstance(expr.args[0], ast.Name)
        and expr.args[0].id == "ChapterPacket"
    )


def _selects_chapter_packet(expr: ast.AST) -> bool:
    """`select(ChapterPacket)` anywhere in the expression — the `rows = (...).scalars().all()` shape."""
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "select"
        and n.args
        and isinstance(n.args[0], ast.Name)
        and n.args[0].id == "ChapterPacket"
        for n in ast.walk(expr)
    )


def _chapter_packet_names(fn: ast.FunctionDef | ast.AsyncFunctionDef, cp_returning: set[str]) -> set[str]:
    """Local names bound to a ChapterPacket (or a collection of them) inside `fn`.

    Flow-INSENSITIVE by design: once a name is seen bound to a ChapterPacket anywhere in the function it
    stays tracked. Re-using one loop variable for two entity types therefore over-flags — acceptable,
    because the alternative (missing a write) is the failure mode this guard exists to prevent."""
    names: set[str] = set()
    for arg in [*fn.args.args, *fn.args.kwonlyargs, *fn.args.posonlyargs]:
        if _mentions_chapter_packet(arg.annotation):
            names.add(arg.arg)

    # Two hops (`rows = select(...)` then `for row in rows`) need a small fixed point.
    for _ in range(3):
        before = len(names)
        for node in ast.walk(fn):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
                if _mentions_chapter_packet(node.annotation) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            elif isinstance(node, ast.For | ast.AsyncFor):
                # `for row in rows:` where `rows` is already known to hold ChapterPackets.
                if isinstance(node.iter, ast.Name) and node.iter.id in names and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                continue
            elif isinstance(node, ast.NamedExpr):
                # `if (row := await _latest(...)) is not None:` — this repo's own idiom (see
                # `packets.py`'s `if refusal := packet_approval.can_approve(row)`), so a supersede
                # written that way must not be invisible.
                targets, value = [node.target], node.value
            if value is None:
                continue
            binds = (
                _called_name(value) == "ChapterPacket"
                or _is_session_get_chapter_packet(value)
                or _called_name(value) in cp_returning
                or _selects_chapter_packet(value)
            )
            if binds:
                names.update(t.id for t in targets if isinstance(t, ast.Name))
        if len(names) == before:
            break
    return names


def _writes(node: ast.AST, *, cp_names: set[str]) -> str | None:
    """A short label for the ChapterPacket write this node performs, or None."""
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "ChapterPacket":
            return "ChapterPacket(...)"
        if (
            isinstance(func, ast.Name)
            and func.id in {"delete", "update", "insert"}
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "ChapterPacket"
        ):
            return f"{func.id}(ChapterPacket)"
        if (
            name == "delete"
            and isinstance(func, ast.Attribute)
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in cp_names
        ):
            return f"session.delete({node.args[0].id})"
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr in AUTHORITY_FIELDS
                and isinstance(target.value, ast.Name)
                and target.value.id in cp_names
            ):
                return f"{target.value.id}.{target.attr} = ..."
    return None


def _is_hard_write(label: str) -> bool:
    """A rule-1/2 write — unambiguously a ChapterPacket mutation, no inference involved."""
    return label == "ChapterPacket(...)" or label.endswith("(ChapterPacket)")


def _outermost_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Top-level defs and methods — never nested inner functions, so a `_body()` closure is checked
    against its enclosing route's full source (where the lock wrapper call lives)."""
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out.append(node)
        elif isinstance(node, ast.ClassDef):
            out.extend(n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef))
    return out


def _walk_body(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    """Every node in the function BODY, excluding its decorator list — `@router.delete("/x")` is a
    one-argument `.delete(...)` call and would otherwise match the session-delete rule."""
    for stmt in fn.body:
        yield from ast.walk(stmt)


def _code_only(fn: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> str:
    """The function's source with its docstring removed, so a lock token merely NAMED in prose cannot
    read as coverage. (A comment saying "see run_under_chapter_workflow(" would otherwise mark an
    unlocked writer as covered — the coverage side of the same text-contingency that made draft 2
    blind on the write side.)"""
    body = fn.body[1:] if _has_docstring(fn) else fn.body
    return "\n".join(seg for stmt in body if (seg := ast.get_source_segment(source, stmt)))


def _has_docstring(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return (
        bool(fn.body)
        and isinstance(fn.body[0], ast.Expr)
        and isinstance(fn.body[0].value, ast.Constant)
        and isinstance(fn.body[0].value.value, str)
    )


def _scan_source(source: str, rel: str) -> dict[str, list[tuple[str, int, bool]]]:
    sites: dict[str, list[tuple[str, int, bool]]] = {}
    tree = ast.parse(source)
    cp_returning = _returns_chapter_packet(tree)
    for fn in _outermost_functions(tree):
        is_locked = any(tok in _code_only(fn, source) for tok in LOCK_TOKENS)
        cp_names = _chapter_packet_names(fn, cp_returning)
        for node in _walk_body(fn):
            label = _writes(node, cp_names=cp_names)
            if label is not None:
                sites.setdefault(f"{rel}::{fn.name}", []).append((label, node.lineno, is_locked))
    return sites


def _all_sites() -> dict[str, list[tuple[str, int, bool]]]:
    """key -> [(write-label, lineno, enclosing-function-is-lock-covered)]. No exemptions applied."""
    sites: dict[str, list[tuple[str, int, bool]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "ChapterPacket" not in source:
            continue
        sites.update(_scan_source(source, path.relative_to(SRC).as_posix()))
    return sites


def test_every_chapter_packet_writer_is_lock_covered():
    """#259 residual 1. A ChapterPacket authority write outside the chapter workflow lock is the exact
    defect this ticket exists to close — and the thing amendment mode (#261) cannot be built on."""
    uncovered = [
        f"  {key} — {label} (line {line})"
        for key, hits in _all_sites().items()
        if key not in EXEMPT_LOCKED_BY_CALLER
        for label, line, is_locked in hits
        if not is_locked
    ]
    assert not uncovered, "ChapterPacket writes not under the chapter workflow lock:\n" + "\n".join(uncovered)


def test_the_guard_catches_the_original_defect():
    """The guard's own regression test. Run the detector against the PRE-FIX source at git HEAD: it
    must flag `update_packet` and `approve_packet` as unlocked writers. A previous draft did not —
    it gated on the function's own text mentioning ChapterPacket, which neither did — so it would
    have shipped green against the exact defect #259 was filed for.

    The pre-fix source is CHECKED IN below rather than read from `git show HEAD:` — the obvious
    spelling, which self-destructs: the moment this work is committed, HEAD becomes the FIXED source
    and both assertions invert. (It would also fail in any checkout without history.) `PREFIX_PACKETS`
    is a verbatim reduction of the two handlers as they stood before this change."""
    sites = _scan_source(PREFIX_PACKETS, "api/routers/packets.py")
    for name in ("update_packet", "approve_packet"):
        key = f"api/routers/packets.py::{name}"
        assert key in sites, f"detector is blind to the original defect: {name} not detected"
        assert all(not locked for _, _, locked in sites[key]), f"{name} should read as UNLOCKED"


def test_the_guard_is_not_inert():
    """If the detector stops finding the writers we KNOW exist, it has gone blind."""
    sites = _all_sites()
    for expected in (
        # `approve_packet` is DELIBERATELY absent now (#261): it no longer stores a status itself, it
        # delegates to `amendment.apply_authority_locked`, which is the single authority transition. That
        # function replaces it in this canary — if BOTH ever vanish from the detected set, the guard has
        # gone blind to chapter-packet approval entirely, which is the thing this test exists to notice.
        "workers/packet/amendment.py::apply_authority_locked",
        "api/routers/packets.py::_update_packet_locked",
        "workers/packet/__init__.py::_persist",
        "api/packet_delete.py::hard_delete_chapter_packets",
    ):
        assert expected in sites, f"guard is inert: {expected} no longer detected as a ChapterPacket writer"


def test_every_exemption_still_names_a_real_function():
    """A stale exemption is a hole that silently re-opens. Each key must still resolve."""
    for key in EXEMPT_LOCKED_BY_CALLER:
        rel, fn_name = key.split("::")
        path = SRC / rel
        assert path.exists(), f"exempt file no longer exists: {rel}"
        names = {fn.name for fn in _outermost_functions(ast.parse(path.read_text(encoding="utf-8")))}
        assert fn_name in names, f"exempt function no longer exists: {key}"


def test_every_exemption_still_points_at_a_locked_caller():
    """Each exemption is valid only while the caller it names still holds the lock. Without this,
    unwrapping `delete_packet` would leave its delete helper silently exempt."""
    for key, (caller, _reason) in EXEMPT_LOCKED_BY_CALLER.items():
        rel, fn_name = caller.split("::")
        path = SRC / rel
        assert path.exists(), f"{key}: locking caller's file is gone ({rel})"
        source = path.read_text(encoding="utf-8")
        fn = next((f for f in _outermost_functions(ast.parse(source)) if f.name == fn_name), None)
        assert fn is not None, f"{key}: locking caller no longer exists ({caller})"
        assert any(tok in _code_only(fn, source) for tok in LOCK_TOKENS), (
            f"{key} is exempt because {caller} was supposed to hold the chapter lock — it no longer "
            f"does, so this exemption is now a hole."
        )


# ---------- detector self-tests


def _labels(src: str) -> list[str]:
    tree = ast.parse(src)
    cp_returning = _returns_chapter_packet(tree)
    out: list[str] = []
    for fn in _outermost_functions(tree):
        cp_names = _chapter_packet_names(fn, cp_returning)
        out.extend(lab for n in _walk_body(fn) if (lab := _writes(n, cp_names=cp_names)) is not None)
    return out


_POSITIVE = [
    # constructed locally
    "async def f(s):\n    row = ChapterPacket(book_id=b)\n    row.status = 'approved'\n",
    # Core bulk
    "async def f(s):\n    await s.execute(delete(ChapterPacket).where(ChapterPacket.chapter_id == c))\n",
    # via a session.get
    "async def f(s, pid):\n    row = await s.get(ChapterPacket, pid)\n    row.body = {}\n",
    # via an annotated module helper — the `_latest` shape that draft 2 was blind to
    "async def _latest(s, c) -> ChapterPacket | None:\n    return None\n"
    "async def supersede(s, c):\n    row = await _latest(s, c)\n    row.status = 'superseded'\n",
    # parameter annotated ChapterPacket
    "async def f(s, row: ChapterPacket):\n    row.open_questions = {}\n",
    # session.delete of a tracked name
    "async def f(s, row: ChapterPacket):\n    await s.delete(row)\n",
]
_NEGATIVE = [
    # a ScenePacket row in a module that also mentions ChapterPacket
    "async def _sp(s, i) -> ScenePacket:\n    return None\n"
    "async def f(s, i):\n    row = await _sp(s, i)\n    row.status = 'stale'\n",
    # read-only
    "async def f(s, pid):\n    packet = await s.get(ChapterPacket, pid)\n    return packet.body\n",
    # the FastAPI decorator that draft 2 counted as delete_packet's only 'write'
    "@router.delete('/{chapter_id}/packet', response_model=DeleteChapterPacketOut)\n"
    "async def delete_packet(chapter_id, session):\n    return None\n",
    # unrelated entity
    "async def f(s):\n    latest = ChapterSequence()\n    latest.status = 'x'\n",
]


def test_detector_flags_the_banned_patterns():
    for src in _POSITIVE:
        assert _labels(src), f"detector missed a real write: {src!r}"


def test_detector_ignores_the_allowed_patterns():
    for src in _NEGATIVE:
        assert not _labels(src), f"detector false-positived on: {src!r} -> {_labels(src)}"
