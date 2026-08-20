"""#223 R5 fork 3b — no authorization seam INDEPENDENTLY reads legacy JSONB or a model-derived verdict.

Fork 3a ruled (D): one canonical authorization module, two enforcing times (schedule draft, publish
prose). Fork 3b is the negative architectural test that keeps that shape from eroding — the guard that
notices when a *second* seam starts deciding authority from raw state on its own.

WHAT COUNTS AS AN AUTHORIZATION SEAM. A function that can refuse progression, by EITHER mechanism:

  * it RETURNS a decision — `GateRefusal`, `StageDecision`, `AuthorizationDecision`, or the
    `(can_draft, reason)` pair shape `tuple[bool, str | None]`; or
  * it REFUSES BY RAISING one of `REFUSAL_EXCEPTIONS` — the `assert_draft_ready` shape, which returns
    `None` and signals by exception.

Both mechanisms are in scope deliberately. An earlier draft of this file classified by return type
only, which left `assert_draft_ready` (`workers/scene_packet/approval_policy.py:224`) — a real drafting
gate — outside the scan while the module docstring claimed to cover authorization seams generally. A
guard whose stated boundary is wider than its detector is the failure mode this repo indicts at
`test_issue259_chapter_packet_writer_guard.py:3-8`; the boundary and the detector now match.

Detection is by PROPERTY, never by a remembered list of function names — an allowlist can only
re-check what someone thought to add, which is how the four `routers/packets.py` transitions stayed
invisible to the predecessor guard #259 replaced.

WHY CLAUSE 1 IS A BAN-WITH-ONE-ANCHOR, NOT A FLAT BAN. The obvious spelling — "no authorization seam
may read `open_questions`" — is red on arrival, and for a legitimate reason: the chapter approve gate
reads exactly that today, deliberately, at `workers/packet/approval_policy.py:38-42`
(`open_question_items`) via `_POLICY.extra_gate` (`:117-119`), and #277 is the open ticket to retire it
once the Escalation record exists (#223 R4). Banning it outright would either fail CI or force an
exemption so broad it rubber-stamps the thing it guards. So the rule is *singularity*, which is what
fork 3a actually ruled: ONE module may turn this state into a gate decision.

CLAUSE 1 IS RECEIVER-BOUND. It fires only on `open_questions` reached from a name bound to a
`ChapterPacket` — `packet.open_questions`, `packet.body["open_questions"]`. A bare `payload["open_
questions"]` on an unrelated dict is NOT a violation. Keying on the field name alone would have
flagged safe code reading an unrelated payload, and the pressure to clear those false positives is
what turns an exemption list into a rubber stamp.

WHY CLAUSE 2 KEYS ON THE OWNING CLASS, NOT THE FIELD NAME. `qa_verdict` is three different things in
this schema and only two of them are model output:

  * `ScenePacket.qa_verdict`   — LLM (`scene_packet/qa.py` prompt; stored `scene_packet/derive.py:776`);
  * `ChapterPacket.qa_verdict` — LLM (`workers/packet/qa.py:39-54` prompt; stored `packet/__init__.py`);
  * `ChapterSequence.qa_verdict` — **deterministic**. `evaluate_chapter_sequence`
    (`workers/production_sequence.py:582-650`) is pure arithmetic over the body dict — duplicate beats,
    duplicate scene functions, entry/exit chaining, word budgets. No model call anywhere in it.

A field-name ban would therefore have flagged `run_stages.py:194` and `production_sequence.py:822`, two
gates that are deterministic and correct, and the fix would have been to exempt them — teaching the
guard that a model-derived verdict gate is acceptable so long as you name it in a list. The class-bound
rule gets the same protection with no exemptions at all. (This is the #278 invariant generalized: that
ticket removed one model-derived field from `DraftGateInputs`; this stops a seam re-reading the row.)

HONEST LIMITS — enumerate them, because a guard that overclaims is worse than none:
  * a seam reading the state through a helper in ANOTHER module is invisible (per-function scope) — the
    same limit the #259 guard records about itself;
  * a read through a name NOT bound to a ChapterPacket in this function — a detached dict passed down,
    a repository adapter's return value, an ORM alias — is invisible to clause 1 by construction;
  * module-level `lambda`s assigned into a policy struct (this repo's `ApprovalPolicy` idiom) are not
    inside a `def`, so they are covered by the canonical-anchor test, not the per-seam scan;
  * subscript receivers (`rows[0].qa_verdict`), `getattr(row, "qa_verdict")`, and raw SQL are invisible;
  * refusal by returning a bare `bool`, by a callback, or by an exception type not in
    `REFUSAL_EXCEPTIONS` is not classified as a seam.

THE THIRD CLAUSE IS NOT HERE, AND NOT FAKED HERE. Fork 3b originally proposed a clause banning ad-hoc
Adjudication booleans. `Adjudication` has zero instances in `src/`, so a live clause cannot be proven
non-inert, and a synthetic fixture would pin an invented domain model that #223 R6 has not ruled. An
earlier draft tried a name-prefix tripwire on `Adjudication*` / `Escalation*` classes; that was removed
because it is NOT rename-proof — an R7 concept named `Ruling`, `Hold`, or `Resolution`, or one arriving
as a table plus repository functions rather than a class, passes it untouched, and a guard that looks
like coverage without being coverage is worse than a recorded prerequisite. The third clause is instead
an R7 blocking acceptance item recorded on issue #223: before any adjudication persistence or reader
merges, add the concrete clause, a live-reader canary, and a mutation proving that reader is rejected.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "dominion"

#: Return types that mark a function as deciding "may this proceed".
SEAM_RETURN_TYPES = frozenset({"GateRefusal", "StageDecision", "AuthorizationDecision"})

#: Exceptions raised to REFUSE progression. A function that raises one of these is a seam even though
#: its return annotation says nothing — `assert_draft_ready` is the canonical instance. Scoped to
#: refusals of a governed transition; plumbing failures (`LlmRateLimited`, `BudgetExceeded`) are not
#: authorization decisions and are deliberately absent.
REFUSAL_EXCEPTIONS = frozenset(
    {
        "ScenePacketRequiredError",  # workers/context/types.py:13 — drafting must not proceed
        "AmendmentNotEligible",  # workers/packet/amendment.py:84
        "AmendmentSourceDrifted",  # workers/packet/amendment.py:98 — "Invariant 4, fail-closed"
        "AmendmentPredecessorMissing",  # workers/packet/amendment.py:113 — "Invariant 3 ... fails closed"
        "ChapterNotAmendable",  # shared/adoption_entry.py:97
        "ChapterContractAlreadyApproved",  # shared/adoption_entry.py:91
        "ChapterHasContractedScenes",  # shared/adoption_entry.py:85
        "IncompatibleAdoptionEntry",  # shared/adoption_entry.py:112 — "Fail-closed ... will not guess through"
        "ApprovalBlockerError",  # workers/scene_packet/blockers.py:48
    }
)

#: Exception families whose every member is a governed-transition domain outcome. Their subclass edges
#: are the structural signal that keeps `REFUSAL_EXCEPTIONS` honest: a new member must be classified as
#: a refusal or explicitly excused, or `test_the_refusal_taxonomy_is_not_stale` fails. This is what makes
#: the taxonomy CI-maintained rather than remembered — the maintenance risk of a raise-based scan.
REFUSAL_FAMILY_BASES = frozenset({"AdoptionEntryError", "AmendmentError"})

#: Members of those families that are NOT authorization refusals — existence lookups, not decisions about
#: whether a transition may proceed. Each needs a reason, and each is re-verified to still exist.
NOT_A_REFUSAL: dict[str, str] = {
    "AdoptionChapterNotFound": "The chapter does not exist (adoption_entry.py:79). A lookup miss, not a decision.",
    "AmendmentChapterNotFound": "The chapter vanished between read and locked reload (amendment.py:80). A lookup miss.",
    "AmendmentPacketNotFound": (
        "The named amendment packet does not exist or is not this chapter's (amendment.py:94). Primarily a "
        "lookup miss; if the ownership half ever becomes a standalone authority check, move it above."
    ),
}

#: ORM classes whose `qa_verdict` column is written from LLM output. `ChapterSequence` is excluded on
#: purpose and with proof — see the module docstring.
MODEL_DERIVED_VERDICT_OWNERS = frozenset({"ScenePacket", "ChapterPacket"})

#: The class whose `open_questions` JSONB carries approval authority as formatted strings (#277).
JSONB_AUTHORITY_OWNER = frozenset({"ChapterPacket"})

#: The legacy JSONB column that carries approval authority as a list of formatted strings (#277).
LEGACY_JSONB_AUTHORITY_FIELD = "open_questions"

#: The ONE module allowed to turn `open_questions` into a gate decision (fork 3a's canonical module).
#: When #223's Escalation record lands, this moves to the new module — it does not gain a second entry.
CANONICAL_JSONB_AUTHORITY_MODULE = "workers/packet/approval_policy.py"

#: The canonical reader, re-verified so this guard notices if the anchor moves. If `open_question_items`
#: stops reading the JSONB, either #277's retire landed (good — update this) or the gate moved somewhere
#: unexempted (bad — that is the drift this file exists to catch).
CANONICAL_READER = "workers/packet/approval_policy.py::open_question_items"


def _mentions(node: ast.AST | None, names: frozenset[str] | tuple[str, ...]) -> bool:
    if node is None:
        return False
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _returns_bool_reason_tuple(returns: ast.AST | None) -> bool:
    """`tuple[bool, str | None]` — `resolve_draft_gate`'s (can_draft, disabled_reason) shape."""
    if not isinstance(returns, ast.Subscript):
        return False
    base = returns.value
    if not (isinstance(base, ast.Name) and base.id in {"tuple", "Tuple"}):
        return False
    sl = returns.slice
    elts = sl.elts if isinstance(sl, ast.Tuple) else []
    return bool(elts) and isinstance(elts[0], ast.Name) and elts[0].id == "bool"


def _raises_refusal(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """The `assert_draft_ready` shape: refusal signalled by raising, not returning."""
    for stmt in fn.body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            exc = node.exc
            name = exc.func if isinstance(exc, ast.Call) else exc
            if isinstance(name, ast.Name) and name.id in REFUSAL_EXCEPTIONS:
                return True
    return False


def _outermost_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out.append(node)
        elif isinstance(node, ast.ClassDef):
            out.extend(n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef))
    return out


def _is_seam(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return _mentions(fn.returns, SEAM_RETURN_TYPES) or _returns_bool_reason_tuple(fn.returns) or _raises_refusal(fn)


def _bound_names(fn: ast.FunctionDef | ast.AsyncFunctionDef, classes: frozenset[str]) -> set[str]:
    """Local names bound to an instance of one of `classes` — annotated parameter, constructor,
    `session.get(Cls, ...)`, or `select(Cls)`. Flow-INSENSITIVE, like the #259 guard: once a name is
    seen bound it stays tracked, because missing a read is the failure mode this exists to prevent."""
    names: set[str] = set()
    for arg in [*fn.args.args, *fn.args.kwonlyargs, *fn.args.posonlyargs]:
        if _mentions(arg.annotation, classes):
            names.add(arg.arg)

    def _binds(value: ast.expr) -> bool:
        expr = value.value if isinstance(value, ast.Await) else value
        if isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Name) and expr.func.id in classes:
                return True  # ChapterPacket(...)
            if (
                getattr(expr.func, "attr", None) == "get"
                and expr.args
                and isinstance(expr.args[0], ast.Name)
                and expr.args[0].id in classes
            ):
                return True  # await session.get(Cls, pid)
        return any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "select"
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id in classes
            for n in ast.walk(expr)
        )

    for _ in range(3):
        before = len(names)
        for node in ast.walk(fn):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
                if _mentions(node.annotation, classes) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            elif isinstance(node, ast.NamedExpr):
                targets, value = [node.target], node.value
            elif isinstance(node, ast.For | ast.AsyncFor):
                if isinstance(node.iter, ast.Name) and node.iter.id in names and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                continue
            if value is not None and _binds(value):
                names.update(t.id for t in targets if isinstance(t, ast.Name))
        if len(names) == before:
            break
    return names


def _root_name(expr: ast.AST) -> str | None:
    """The base name of an attribute/subscript chain: `packet.body["x"]["y"]` -> `packet`."""
    while isinstance(expr, ast.Attribute | ast.Subscript):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _reads_legacy_jsonb(node: ast.AST, *, cp_names: set[str]) -> bool:
    """A READ of `open_questions` reached from a ChapterPacket-bound name.

    Receiver-bound on purpose: `packet.open_questions` and `packet.body["open_questions"]` are the
    authority read; an unrelated `payload["open_questions"]` is not. Stores are #259's guard."""
    if isinstance(node, ast.Attribute) and node.attr == LEGACY_JSONB_AUTHORITY_FIELD:
        return isinstance(node.ctx, ast.Load) and _root_name(node.value) in cp_names
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value == LEGACY_JSONB_AUTHORITY_FIELD:
            return _root_name(node.value) in cp_names
    return False


def _reads_model_verdict(node: ast.AST, *, tracked: set[str]) -> bool:
    """`packet.qa_verdict` where `packet` is bound to a model-derived-verdict class."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "qa_verdict"
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id in tracked
    )


def _scan_source(source: str, rel: str) -> list[tuple[str, str, int]]:
    """[(key, clause, lineno)] for every fork-3b violation in one module."""
    out: list[tuple[str, str, int]] = []
    tree = ast.parse(source)
    for fn in _outermost_functions(tree):
        if not _is_seam(fn):
            continue
        key = f"{rel}::{fn.name}"
        verdict_names = _bound_names(fn, MODEL_DERIVED_VERDICT_OWNERS)
        jsonb_names = _bound_names(fn, JSONB_AUTHORITY_OWNER)
        for stmt in fn.body:
            for node in ast.walk(stmt):
                if rel != CANONICAL_JSONB_AUTHORITY_MODULE and _reads_legacy_jsonb(node, cp_names=jsonb_names):
                    out.append((key, "legacy-jsonb-authority-read", node.lineno))
                if _reads_model_verdict(node, tracked=verdict_names):
                    out.append((key, "model-derived-verdict-read", node.lineno))
    return out


def _all_violations() -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        out.extend(_scan_source(path.read_text(encoding="utf-8"), path.relative_to(SRC).as_posix()))
    return out


def _all_seams() -> set[str]:
    seams: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()
        seams.update(f"{rel}::{fn.name}" for fn in _outermost_functions(tree) if _is_seam(fn))
    return seams


# ---------- the enforced clauses


def test_no_authorization_seam_independently_reads_legacy_jsonb_or_a_model_verdict():
    """#223 R5 fork 3a (D): ONE canonical module decides, and it decides from typed state. A second
    seam reading `ChapterPacket.open_questions` or a model-written `qa_verdict` on its own is the drift
    that made #277 and #278 possible in the first place."""
    bad = [f"  {key} — {clause} (line {line})" for key, clause, line in _all_violations()]
    assert not bad, "authorization seams reading raw authority state independently:\n" + "\n".join(bad)


def test_the_scan_surface_covers_both_refusal_mechanisms():
    """If the classifier stops matching, every clause above passes vacuously. Both mechanisms are
    pinned: return-signalled seams AND the raise-based `assert_draft_ready` shape."""
    seams = _all_seams()
    for expected in (
        # return-signalled
        "workers/packet/approval_policy.py::can_approve",
        "workers/scene_packet/approval_policy.py::can_approve",
        "workers/draft_readiness.py::resolve_draft_gate",
        "workers/run_stages.py::evaluate_drafting_readiness",
        "workers/run_stages.py::evaluate_assembly_readiness",
        "shared/authorization.py::authorize_repair",
        # raise-based — outside the return-type rule entirely
        "workers/scene_packet/approval_policy.py::assert_draft_ready",
    ):
        assert expected in seams, f"guard is inert: {expected} no longer classified as an authorization seam"


def test_the_refusal_taxonomy_is_not_stale():
    """The maintenance risk of classifying seams by the exception they raise: a new refusal type is
    added, nobody updates `REFUSAL_EXCEPTIONS`, and that seam silently leaves the scan. This makes the
    update a CI condition instead of a thing someone has to remember.

    Structural, not name-based: it follows the SUBCLASS EDGES of the two closed domain-refusal families.
    Adding a member to either family fails here until it is classified as a refusal or excused with a
    reason. (Refusal types outside those families — `ScenePacketRequiredError`, `ApprovalBlockerError` —
    are covered only by the existence half below; a brand-new standalone refusal base is invisible until
    someone adds it to REFUSAL_FAMILY_BASES. That limit is why the R7 acceptance item on #223 requires a
    live-reader canary rather than trusting this scan to notice the next boundary on its own.)"""
    classified = REFUSAL_EXCEPTIONS | NOT_A_REFUSAL.keys()
    unclassified: list[str] = []
    defined: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()
        for n in ast.walk(tree):
            if not isinstance(n, ast.ClassDef):
                continue
            defined.add(n.name)
            bases = {b.id for b in n.bases if isinstance(b, ast.Name)}
            if bases & REFUSAL_FAMILY_BASES and n.name not in classified:
                unclassified.append(
                    f"  {rel}::{n.name} (subclass of {', '.join(sorted(bases & REFUSAL_FAMILY_BASES))})"
                )
    assert not unclassified, (
        "new domain-refusal exception(s) are unclassified, so any seam that refuses by raising them is "
        "outside this guard's scan:\n" + "\n".join(unclassified) + "\nAdd each to REFUSAL_EXCEPTIONS, or "
        "to NOT_A_REFUSAL with the reason it is a lookup miss rather than an authorization decision."
    )
    for name in sorted(REFUSAL_EXCEPTIONS | NOT_A_REFUSAL.keys()):
        assert name in defined, f"refusal taxonomy is stale: {name} no longer exists in src/"


def test_the_canonical_jsonb_reader_still_exists_and_still_reads_it():
    """The anchor. Clause 1 is a ban everywhere EXCEPT one module, so it is only meaningful while that
    module is genuinely the reader. If this fails, either #277's retire landed (update the constant) or
    the gate moved to a module this guard does not exempt (the drift)."""
    rel, fn_name = CANONICAL_READER.split("::")
    path = SRC / rel
    assert path.exists(), f"canonical authority module is gone: {rel}"
    source = path.read_text(encoding="utf-8")
    fn = next((f for f in _outermost_functions(ast.parse(source)) if f.name == fn_name), None)
    assert fn is not None, f"canonical reader no longer exists: {CANONICAL_READER}"
    cp_names = _bound_names(fn, JSONB_AUTHORITY_OWNER)
    assert any(_reads_legacy_jsonb(n, cp_names=cp_names) for stmt in fn.body for n in ast.walk(stmt)), (
        f"{CANONICAL_READER} no longer reads {LEGACY_JSONB_AUTHORITY_FIELD} — the anchor moved; "
        f"re-point CANONICAL_JSONB_AUTHORITY_MODULE or retire clause 1."
    )


# ---------- detector self-tests
#
# The ruled non-inertness proof: the detector is fed synthetic source and must fire. No production
# symbol is named in the forbidden-read fixtures below, so nothing here pins an unruled domain model.


def _labels(src: str, rel: str = "workers/somewhere.py") -> list[str]:
    return [clause for _key, clause, _line in _scan_source(src, rel)]


_POSITIVE = [
    # a seam reading the legacy JSONB off a ChapterPacket-bound parameter
    "def can_x(row: ChapterPacket) -> GateRefusal | None:\n"
    "    if row.open_questions:\n        return GateRefusal('x')\n    return None\n",
    # the nested-subscript spelling, rooted at the same bound name
    "def can_x(row: ChapterPacket) -> GateRefusal | None:\n"
    "    if row.body['open_questions']:\n        return GateRefusal('x')\n    return None\n",
    # a seam reading a model-written verdict off an annotated parameter
    "def can_x(p: ScenePacket) -> GateRefusal | None:\n"
    "    if p.qa_verdict == 'block_drafting':\n        return GateRefusal('x')\n    return None\n",
    # the (can_draft, reason) tuple seam shape
    "def gate(p: ChapterPacket) -> tuple[bool, str | None]:\n"
    "    if p.qa_verdict:\n        return False, 'blocked'\n    return True, None\n",
    # bound via session.get rather than an annotation
    "async def decide(s, pid) -> StageDecision:\n"
    "    row = await s.get(ChapterPacket, pid)\n    return StageDecision(ok=not row.qa_verdict)\n",
    # RAISE-BASED seam — no decision return type at all, classified by the refusal it raises
    "def assert_ok(p: ChapterPacket) -> None:\n"
    "    if p.open_questions:\n        raise ScenePacketRequiredError('not ready')\n",
    # raise-based seam reading the model verdict
    "def assert_ok(p: ScenePacket) -> None:\n"
    "    if p.qa_verdict == 'block_drafting':\n        raise ScenePacketRequiredError('qa')\n",
]
_NEGATIVE = [
    # NOT a seam — a projection/classifier may read the advisory verdict freely
    "def infer_source(p: ScenePacket, reason) -> BlockerSource:\n"
    "    return BlockerSource.QA if p.qa_verdict == 'block_drafting' else BlockerSource.NONE\n",
    # a seam reading the DETERMINISTIC sequence verdict — the field-name trap this guard must not fall into
    "def decide(seq: ChapterSequence) -> StageDecision:\n"
    "    return StageDecision(ok=seq.qa_verdict != 'block_drafting')\n",
    # a seam taking a plain string parameter (run_stages' shape) — deterministic, not a row read
    "def decide(*, sequence_qa_verdict: str | None) -> StageDecision:\n"
    "    return StageDecision(ok=sequence_qa_verdict != 'block_drafting')\n",
    # RECEIVER-BOUND: an unrelated payload that happens to carry the same key is not an authority read
    "def decide(payload: dict) -> StageDecision:\n    return StageDecision(ok=not payload['open_questions'])\n",
    # same, attribute spelling on an unbound name
    "def decide(form) -> StageDecision:\n    return StageDecision(ok=not form.open_questions)\n",
    # a seam that STORES rather than reads (that is #259's guard, not this one)
    "def decide(p: ScenePacket) -> StageDecision:\n    p.qa_verdict = 'approve'\n    return StageDecision(ok=True)\n",
    # a non-seam reading the JSONB — authoring/projection code is untouched
    "def render(row: ChapterPacket) -> dict:\n    return {'items': row.open_questions}\n",
    # raises, but not a refusal exception — plumbing failure, not an authorization decision
    "def fetch(p: ChapterPacket) -> None:\n    if p.open_questions:\n        raise LlmRateLimited('429')\n",
]


def test_detector_flags_the_banned_patterns():
    for src in _POSITIVE:
        assert _labels(src), f"detector missed a forbidden authority read: {src!r}"


def test_detector_ignores_the_allowed_patterns():
    for src in _NEGATIVE:
        assert not _labels(src), f"detector false-positived on: {src!r} -> {_labels(src)}"


def test_the_canonical_module_is_exempt_from_clause_1_only():
    """The exemption is per-module AND per-clause: the canonical module may read the JSONB, and still
    may not decide from a model-written verdict."""
    jsonb = (
        "def can_x(row: ChapterPacket) -> GateRefusal | None:\n"
        "    return GateRefusal('x') if row.open_questions else None\n"
    )
    verdict = (
        "def can_x(p: ScenePacket) -> GateRefusal | None:\n    return GateRefusal('x') if p.qa_verdict else None\n"
    )
    assert not _labels(jsonb, CANONICAL_JSONB_AUTHORITY_MODULE), "canonical module should be exempt from clause 1"
    assert _labels(verdict, CANONICAL_JSONB_AUTHORITY_MODULE) == ["model-derived-verdict-read"], (
        "canonical module must NOT be exempt from clause 2"
    )
