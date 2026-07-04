"""Acceptance/regression harness over the bad Chapter 1 run fixtures (lane 10).

Fixtures: tests/fixtures/ch1_bad_run/ (failing run 51d635ec — see
reports/ch1_pipeline_failure_analysis.md). Pure Python: no network, no LLM, no DB.

Two tiers:

* Canary tests (TestFixtureCanaries) always run. They pin the fixture to the bug
  it documents — if someone "fixes" the fixture data, the regression tests above
  it stop meaning anything, so the canaries fail first.

* Lane acceptance tests are gated with pytest.importorskip on the lane-owned
  modules (dominion.workers.scene_scope / budget_reconciliation / canon_guards,
  and lane 1's chaining post-pass). Green pre-integration; they bite the moment
  the lane lands. Callable names are resolved through the adapters in
  tests/ch1_bad_run_fixtures.py — a landed module with an unmatched signature
  FAILS loudly (AdapterMismatch), it never skips.
"""

from __future__ import annotations

import copy

import ch1_bad_run_fixtures as fx
import pytest


@pytest.fixture(scope="module")
def scene_scope():
    """Lane 2 module — skip (green) until it lands, bite afterwards."""
    return pytest.importorskip("dominion.workers.scene_scope")


@pytest.fixture(scope="module")
def budget_reconciliation():
    """Lane 3 module — skip (green) until it lands, bite afterwards."""
    return pytest.importorskip("dominion.workers.budget_reconciliation")


@pytest.fixture(scope="module")
def canon_guards():
    """Lane 4 module — skip (green) until it lands, bite afterwards."""
    return pytest.importorskip("dominion.workers.canon_guards")


# ---------------------------------------------------------------------------
# Tier 1 — fixture canaries (always run; document the bad run's actual failures)
# ---------------------------------------------------------------------------


class TestFixtureCanaries:
    def test_all_entry_states_identical_to_global_entry(self):
        """§1 of the failure analysis: every scene starts from the global chapter
        entry even though scenes 2-4 declare dependencies. This is the bug lane 1
        fixes — the fixture must keep exhibiting it."""
        body = fx.sequence_body()
        scenes = fx.sequence_scenes()
        assert len(scenes) == fx.SCENE_COUNT
        global_entry = body["global_entry_state"]
        assert global_entry.startswith("Marcus is late at work")
        for scene in scenes:
            assert scene["entry_state"] == global_entry, (
                f"fixture drifted: scene {scene['scene_no']} entry_state no longer "
                "matches the global entry — the ch1_bad_run fixture must stay bad"
            )

    def test_exit_states_distinct_and_dependency_chain_declared(self):
        """The chain INPUTS exist (distinct exit states, declared deps,
        independent_draft_allowed=false) — derivation just never consumed them."""
        scenes = fx.sequence_scenes()
        exits = [s["exit_state"] for s in scenes]
        assert len(set(exits)) == fx.SCENE_COUNT, "exit states should be distinct"
        for scene in scenes:
            assert scene["independent_draft_allowed"] is False
        assert [s["depends_on_scene_no"] for s in scenes] == [None, 1, 2, 3]

    def test_scene_budgets_contradict_chapter_envelope(self):
        """§3: per-scene hard_max already sums to 10,400 against a 7,200-word
        chapter envelope — the overrun was arithmetic before any drafting."""
        seq = fx.chapter_sequence()
        assert seq["hard_max_words"] == fx.CHAPTER_HARD_MAX_WORDS
        assert fx.sequence_body()["hard_max_words"] == fx.CHAPTER_HARD_MAX_WORDS

        packet_sum = sum(p["body"]["word_budget"]["hard_max"] for p in fx.scene_packets())
        sequence_sum = sum(s["word_budget"]["hard_max"] for s in fx.sequence_scenes())
        assert packet_sum == fx.SCENE_HARD_MAX_SUM
        assert sequence_sum == fx.SCENE_HARD_MAX_SUM
        assert packet_sum > fx.CHAPTER_HARD_MAX_WORDS

    def test_assembled_draft_busts_chapter_hard_max(self):
        """The assembled draft (~9.6k words) exceeds the 7,200 chapter hard max,
        and every per-scene row exceeds its own soft max."""
        prose = fx.assembled_prose()
        assert fx.word_count(prose) > fx.CHAPTER_HARD_MAX_WORDS
        rows = fx.draft_scene_rows()
        assert len(rows) == fx.SCENE_COUNT
        assert sum(r["word_count"] for r in rows) > fx.CHAPTER_HARD_MAX_WORDS
        for row in rows:
            assert row["word_count"] > row["word_budget"]["max"], (
                f"scene {row['scene_no']} no longer overruns its soft max"
            )

    def test_scene2_prose_contains_scene3_recognition_markers(self):
        """§2: the hood-tear / red-hair recognition beat is OWNED by scene 3
        (sequence beat_ownership) yet scene 2's prose stages it — the scope bleed
        lane 2 must detect."""
        ownership = fx.sequence_body()["beat_ownership"]
        hood_beats = [b for b, owner in ownership.items() if "hood" in b.lower()]
        assert hood_beats and all(ownership[b] == 3 for b in hood_beats), (
            "hood/red-hair recognition beat should be owned by scene 3"
        )
        prose = fx.scene_prose_by_no()
        assert fx.count_matches(r"\bhood\b", prose[2]) >= 2
        assert fx.count_matches(r"\bred hair\b", prose[2]) >= 2
        assert "Serra" in prose[2], "scene 2 already performs the named recognition"
        # Scene 1 (work scene) is clean — the markers are scene-specific, not noise.
        assert fx.count_matches(r"\bhood\b|\bred hair\b", prose[1]) == 0

    def test_recognition_beat_replayed_in_scenes_3_and_4(self):
        """§2: recognition is re-performed in scenes 3 AND 4 (after scene 2 already
        staged it) — the duplicate irreversible beat lane 2 must detect."""
        prose = fx.scene_prose_by_no()
        assert fx.count_matches(r"recogni\w*", prose[3]) >= 1
        assert fx.count_matches(r"recogni\w*", prose[4]) >= 1
        # Scene 4 replays the full named recognition beat, not a mere callback.
        assert "Serra" in prose[4]
        total = sum(fx.count_matches(r"recogni\w*", p) for p in prose.values())
        assert total >= 6, "recognition should be staged repeatedly across the draft"

    def test_canon_leak_present_in_draft_and_forbidden_by_packet(self):
        """§4: the 'Neurochromatic Eyes flickered' passage is in the REAL assembled
        prose (scene 2's row), the chapter packet's resolved ruling forbids it,
        and none of the run's 24 issues flagged it."""
        assert fx.LEAK_MARKER in fx.assembled_prose()
        assert fx.LEAK_MARKER in fx.scene_prose_by_no()[2]

        rulings = [r for r in fx.resolved_rulings() if fx.NO_EYES_RULING_MARKER in r]
        assert rulings, "chapter packet lost its 'No Eyes notification in Chapter 1' ruling"
        assert any(fx.LEAK_TERM in r.lower() for r in rulings)

        issues = fx.production_run_detail()["issues"]
        assert len(issues) == 24
        assert not any(fx.LEAK_TERM in fx.issue_text(i) for i in issues), (
            "fixture drifted: an issue now flags the Eyes leak, but this run's QA missed it"
        )

    def test_bad_run_predates_new_issue_taxonomy(self):
        """§5: the run produced a 24-issue symptom swarm; none of them use the
        recovery taxonomy the lanes introduce. Keeps kind-name collisions honest."""
        run = fx.production_run_detail()
        kinds = {i.get("issue_kind") for i in run["issues"]}
        assert not (kinds & fx.NEW_TAXONOMY_KINDS)
        assert len(run["repair_tasks"]) == 10


# ---------------------------------------------------------------------------
# Tier 2 — lane 1: entry-state chaining (post-pass over the fixture sequence)
# ---------------------------------------------------------------------------


class TestLane1EntryStateChaining:
    def test_postpass_chains_fixture_entry_states(self):
        """Acceptance (a): after lane 1's post-pass, scene 1 entry ==
        global_entry_state and scene N entry == scene N-1 exit_state (scenes 2-4
        all declare independent_draft_allowed=false)."""
        name, fn = fx.resolve_lane1_postpass()
        if fn is None:
            pytest.skip(
                "lane 1 chaining post-pass not landed yet "
                f"(probed {', '.join(fx.LANE1_MODULES)} for {', '.join(fx.LANE1_FUNCS)})"
            )
        body = fx.apply_lane1_postpass(fn, copy.deepcopy(fx.sequence_body()))
        scenes = sorted(body["scenes"], key=lambda s: int(s["scene_no"]))
        assert len(scenes) == fx.SCENE_COUNT

        assert scenes[0]["entry_state"] == fx.sequence_body()["global_entry_state"], (
            f"{name}: scene 1 must enter from the global chapter entry state"
        )
        original = {int(s["scene_no"]): s for s in fx.sequence_scenes()}
        for prev, scene in zip(scenes, scenes[1:], strict=False):
            n = int(scene["scene_no"])
            assert scene["entry_state"] == prev["exit_state"], (
                f"{name}: scene {n} entry_state must equal scene {n - 1} exit_state"
            )
            # And the chain must consume the REAL distinct exits, not rewrite them.
            assert prev["exit_state"] == original[n - 1]["exit_state"]

    def test_derivation_itself_chains_when_lane1_lands_in_place(self):
        """Complementary check at the derivation seam: if lane 1 patched
        derive_chapter_sequence directly, re-deriving from the REAL chapter packet
        body must yield chained entry states. Skips while derivation still emits
        the bug AND no post-pass exists (pre-integration)."""
        production = pytest.importorskip("dominion.workers.production")
        derived = production.derive_chapter_sequence(fx.chapter_packet_body())
        scenes = sorted(derived["scenes"], key=lambda s: int(s["scene_no"]))
        chained = all(
            scene["entry_state"] == prev["exit_state"] for prev, scene in zip(scenes, scenes[1:], strict=False)
        )
        if not chained and fx.resolve_lane1_postpass()[1] is None:
            pytest.skip("lane 1 not landed: derivation still emits unchained entry states")
        if not chained:
            # A post-pass exists, so derivation output must chain after applying it.
            _, fn = fx.resolve_lane1_postpass()
            derived = fx.apply_lane1_postpass(fn, derived)
            scenes = sorted(derived["scenes"], key=lambda s: int(s["scene_no"]))
        for prev, scene in zip(scenes, scenes[1:], strict=False):
            if scene.get("independent_draft_allowed") is False:
                assert scene["entry_state"] == prev["exit_state"]


# ---------------------------------------------------------------------------
# Tier 2 — lane 2: scene scope bleed + duplicate irreversible beats
# ---------------------------------------------------------------------------

_SCENE_SCOPE_FUNCS = (
    "detect_scene_scope_issues",
    "detect_scope_bleed",
    "scan_scene_scope",
    "check_scene_scope",
    "analyze_scene_scope",
    "scene_scope_issues",
    "detect_issues",
    "detect",
    "scan",
    "review",
    "run",
    "evaluate",
)


def _scene_scope_result(module):
    body = fx.sequence_body()
    scenes = fx.sequence_scenes()
    prose = fx.scene_prose_by_no()
    rows = fx.draft_scene_rows()
    return fx.call_detector(
        module,
        _SCENE_SCOPE_FUNCS,
        attempts=[
            ((), {"sequence_body": body, "scene_prose": prose}),
            ((), {"sequence_body": body, "prose_by_scene": prose}),
            ((), {"scenes": scenes, "scene_prose": prose}),
            ((), {"sequence": body, "scene_prose": prose}),
            ((), {"scenes": rows}),
            ((body, prose), {}),
            ((scenes, prose), {}),
            ((rows,), {}),
        ],
    )


class TestLane2SceneScope:
    def test_scene2_bleed_into_scene3_beats_flagged(self, scene_scope):
        """Acceptance (b): scene 2's prose stages scene-3-owned irreversible beats
        (hood tear / red hair / named recognition) -> scene_scope_bleed."""
        result = _scene_scope_result(scene_scope)
        kinds = fx.issue_kinds(result)
        assert fx.KIND_SCOPE_BLEED in kinds, (
            f"expected {fx.KIND_SCOPE_BLEED} on the fixture draft, got kinds={sorted(set(kinds))}"
        )

    def test_duplicate_recognition_beat_flagged(self, scene_scope):
        """Acceptance (c): the recognition beat re-performed in scenes 3 AND 4
        -> duplicate_irreversible_beat."""
        result = _scene_scope_result(scene_scope)
        kinds = fx.issue_kinds(result)
        assert fx.KIND_DUPLICATE_BEAT in kinds, (
            f"expected {fx.KIND_DUPLICATE_BEAT} on the fixture draft, got kinds={sorted(set(kinds))}"
        )


# ---------------------------------------------------------------------------
# Tier 2 — lane 3: budget reconciliation
# ---------------------------------------------------------------------------

_BUDGET_FUNCS = (
    "reconcile_budgets",
    "reconcile_sequence_budgets",
    "reconcile_word_budgets",
    "reconcile_envelope",
    "reconcile",
    "check_budget_envelope",
    "validate_budgets",
    "detect",
    "run",
    "evaluate",
)


def _collect_scene_hard_maxes(node, out):
    """Recursively collect hard_max values from dicts that look like per-scene
    budget entries (a hard_max alongside scene identity or a target)."""
    if isinstance(node, dict):
        hard_max = node.get("hard_max")
        if isinstance(hard_max, int) and ("scene_no" in node or "target" in node or "seed_id" in node):
            out.append(hard_max)
        for value in node.values():
            _collect_scene_hard_maxes(value, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _collect_scene_hard_maxes(value, out)


class TestLane3BudgetReconciliation:
    def test_budget_contradiction_cannot_pass_silently(self, budget_reconciliation):
        """Acceptance (d): scene hard_max sum (10,400) vs chapter envelope (7,200).
        Reconciliation must EITHER shrink the per-scene envelopes so they fit,
        OR raise a blocking sequence_budget_mismatch. Silence == regression."""
        body = fx.sequence_body()
        scenes = fx.sequence_scenes()
        budgets = [s["word_budget"] for s in scenes]
        result = fx.call_detector(
            budget_reconciliation,
            _BUDGET_FUNCS,
            attempts=[
                ((), {"sequence_body": body}),
                ((), {"sequence": body}),
                ((), {"scenes": scenes, "hard_max_words": fx.CHAPTER_HARD_MAX_WORDS}),
                ((), {"scene_budgets": budgets, "chapter_hard_max": fx.CHAPTER_HARD_MAX_WORDS}),
                ((), {"scene_budgets": budgets, "hard_max_words": fx.CHAPTER_HARD_MAX_WORDS}),
                ((body,), {}),
                ((scenes, fx.CHAPTER_HARD_MAX_WORDS), {}),
                ((budgets, fx.CHAPTER_HARD_MAX_WORDS), {}),
            ],
        )

        mismatch_issues = fx.issues_of_kind(result, fx.KIND_BUDGET_MISMATCH)
        reconciled: list[int] = []
        _collect_scene_hard_maxes(result, reconciled)
        reconciled_fits = (
            len(reconciled) >= fx.SCENE_COUNT and sum(sorted(reconciled)[: fx.SCENE_COUNT]) <= fx.CHAPTER_HARD_MAX_WORDS
        ) or (0 < len(reconciled) < fx.SCENE_COUNT and sum(reconciled) <= fx.CHAPTER_HARD_MAX_WORDS)

        assert mismatch_issues or reconciled_fits, (
            "10,400 of scene hard_max inside a 7,200-word chapter envelope passed "
            f"silently: no {fx.KIND_BUDGET_MISMATCH} issue and no reconciled budgets "
            f"(result={type(result).__name__})"
        )
        if mismatch_issues and not reconciled_fits:
            assert any(fx.is_blocking(i, result) for i in mismatch_issues), (
                f"{fx.KIND_BUDGET_MISMATCH} was raised but nothing marks it blocking "
                "(blocking/is_blocking flag, blocker-class severity, or a "
                "blocking_issues container) — an advisory warn re-creates this bad run"
            )


# ---------------------------------------------------------------------------
# Tier 2 — lane 4: canon guards (prohibition scanner)
# ---------------------------------------------------------------------------

_CANON_FUNCS = (
    "scan_packet_prose",
    "scan_prohibitions",
    "detect_canon_leaks",
    "scan_canon",
    "check_canon",
    "scan_prose",
    "find_leaks",
    "detect_leaks",
    "guard",
    "scan",
    "detect",
    "check",
    "run",
    "evaluate",
)

# Benign control prose: built exclusively from the chapter packet's
# allowed_ui_concepts, including the ruling's explicitly sanctioned UI echo
# ("voluntary status confirmed", not Eyes).
_BENIGN_UI_PROSE = (
    "The match countdown ticked down over the lobby while the guild channel "
    "scrolled with warmups. Marcus checked the boundary integrity log out of "
    "habit: no flagged events, no recommendation pushes, readiness profile "
    "nominal for the active candidate population. A procedural acknowledgment "
    "blinked once in the corner - voluntary status confirmed - and he flexed "
    "his fingers inside the gloves, watching the sealed control cohort numbers "
    "he was not supposed to be thinking about during a scrim. Logout sat where "
    "it always sat. Match authority belonged to the arena, the way it should."
)


def _canon_scan(module, prose):
    packet_body = fx.chapter_packet_body()
    packet = fx.chapter_packet()
    # The No-Eyes prohibition lives in the packet's resolved rulings, not the body —
    # the landed lane-4 API takes open_questions alongside the body.
    open_questions = packet.get("open_questions") if isinstance(packet, dict) else None
    return fx.call_detector(
        module,
        _CANON_FUNCS,
        attempts=[
            ((), {"prose": prose, "packet_body": packet_body, "open_questions": open_questions}),
            ((prose, packet_body, open_questions), {}),
            ((), {"prose": prose, "chapter_packet_body": packet_body}),
            ((), {"prose": prose, "packet_body": packet_body}),
            ((), {"prose": prose, "chapter_packet": packet}),
            ((), {"prose": prose, "packet": packet_body}),
            ((), {"text": prose, "chapter_packet_body": packet_body}),
            ((prose, packet_body), {}),
            ((packet_body, prose), {}),
        ],
    )


class TestLane4CanonGuards:
    def test_real_draft_eyes_leak_flagged(self, canon_guards):
        """Acceptance (e): the REAL assembled draft + the REAL chapter packet
        prohibitions -> canon_contract_leak on the 'Neurochromatic Eyes flickered'
        passage the shipped QA missed entirely."""
        result = _canon_scan(canon_guards, fx.assembled_prose())
        leaks = fx.issues_of_kind(result, fx.KIND_CANON_LEAK)
        assert leaks, (
            f"expected {fx.KIND_CANON_LEAK} on the assembled bad draft, got kinds={sorted(set(fx.issue_kinds(result)))}"
        )
        assert any(fx.LEAK_TERM in fx.issue_text(leak) for leak in leaks), (
            "canon_contract_leak fired but none of the leak issues reference the "
            "'Neurochromatic Eyes flickered' passage"
        )

    def test_ordinary_ui_prose_does_not_flag(self, canon_guards):
        """Acceptance (f): prose built purely from allowed_ui_concepts (including
        the ruling-sanctioned 'voluntary status confirmed') must NOT leak-flag —
        the guard is a prohibition scanner, not a UI-word allergy."""
        allowed = {c.lower() for c in fx.chapter_packet_body()["allowed_ui_concepts"]}
        assert {"match countdown", "lobby", "guild channel", "logout"} <= allowed
        result = _canon_scan(canon_guards, _BENIGN_UI_PROSE)
        leaks = fx.issues_of_kind(result, fx.KIND_CANON_LEAK)
        assert not leaks, f"benign allowed-UI prose false-positived: {[fx.issue_text(leak)[:160] for leak in leaks]}"
