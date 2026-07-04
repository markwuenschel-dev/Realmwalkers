"""Deterministic canon_contract_leak guard (workers.canon_guards) against the REAL Ch1 bad-run
chapter packet fixture.

The regression this pins: run 51d635ec's assembled draft contained "Neurochromatic Eyes flickered
at the edge of his perception…" although the packet's resolved ruling says "No Eyes notification
in Chapter 1 … no Neurochromatic Eyes, no Meszkhal item signal" — and 0 of the run's 24 issues
flagged it. The prohibition lived only in ruling free text while canon_locks/surface_terms listed
the term as the ALLOWED name, so nothing deterministic ever scanned prose against it.

Pure-Python: no network, no LLM, no Postgres.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from dominion.workers.canon_guards import (
    ISSUE_KIND,
    derive_prohibited_terms,
    format_prohibited_terms_block,
    scan_packet_prose,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ch1_bad_run" / "chapter_packet.json"

# The exact leak sentence from the bad run's assembled chapter_draft artifact (curly apostrophe
# and all), with its surrounding sentences.
LEAK_PROSE = (
    "Marcus flexed his fingers. The cool pull of the glove against his knuckles grounded him. "
    "Neurochromatic Eyes flickered at the edge of his perception, turning the field into layered "
    "probability and emphasis. He didn’t need them fully open to feel the pattern."
)

# Ordinary game-UI language the contract explicitly allows — must never flag.
BENIGN_PROSE = (
    "The scoreboard updated as the round clock ran down. His health bar dipped under the "
    "rogue’s pressure, and the lobby chat scrolled past unread. Marcus kept his eyes on "
    "the sand and the match countdown."
)


@pytest.fixture(scope="module")
def packet() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _leaks(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["kind"] == ISSUE_KIND]


def _eyes_findings(findings: list[dict]) -> list[dict]:
    return [f for f in _leaks(findings) if "eyes" in str(f["term"]).lower()]


# --- derivation: the prohibition comes from the packet, not from hardcoded terms ---------------


def test_ruling_prohibition_is_derived_from_the_packet(packet):
    terms = derive_prohibited_terms(packet["body"], packet["open_questions"])
    by_lower = {t.term.lower(): t for t in terms}

    eyes = by_lower.get("neurochromatic eyes")
    assert eyes is not None, "the ruling's 'no Neurochromatic Eyes' must become a prohibited term"
    assert eyes.source == "resolved_ruling"
    assert eyes.severity == "block"
    assert "open_questions.resolved" in eyes.contract_reference

    # The same ruling also bans the bare interface tag and the item signal.
    assert "[interface]" in by_lower
    assert "meszkhal" in by_lower

    # A ruling mentioning a present character ("No Serra interiority yet") must NOT ban the
    # character's on-page name, and the POV name is never a prohibited term.
    assert "serra" not in by_lower
    assert "marcus" not in by_lower


def test_explicit_forbidden_surface_terms_are_derived(packet):
    by_lower = {t.term.lower(): t for t in derive_prohibited_terms(packet["body"], packet["open_questions"])}
    # Unconditional rename/legacy bans from surface_terms / entity_bindings / characters_forbidden.
    for legacy in ("sarah", "chad", "angelic fortitude", "xylorane"):
        assert legacy in by_lower, legacy
        assert by_lower[legacy].source == "forbidden_surface_term"
    # "Roth" is deferred BEYOND chapter 1 ("after chapter 1 on-page reveal sequence…") — still a
    # hard whole-chapter prohibition.
    assert by_lower["roth"].severity == "block"


# --- (a) the missed leak now flags -------------------------------------------------------------


def test_leak_prose_flags_canon_contract_leak(packet):
    findings = scan_packet_prose(LEAK_PROSE, packet["body"], packet["open_questions"])
    eyes = _eyes_findings(findings)
    assert eyes, f"expected a canon_contract_leak for the Eyes activation, got: {findings}"
    leak = eyes[0]
    assert leak["term"].lower() == "neurochromatic eyes"
    assert leak["severity"] == "block"
    assert leak["blocks_drafting"] is True
    assert leak["blocks_final_export"] is True
    assert "flickered" in leak["excerpt"]
    # Subsumption: the single leak span reports once — not once for "Neurochromatic Eyes" and
    # again for the ruling's bare "Eyes".
    assert len(eyes) == 1


def test_leak_is_case_insensitive_for_multiword_terms(packet):
    prose = LEAK_PROSE.replace("Neurochromatic Eyes", "neurochromatic eyes")
    assert _eyes_findings(scan_packet_prose(prose, packet["body"], packet["open_questions"]))


def test_other_ruling_prohibitions_flag(packet):
    body, oq = packet["body"], packet["open_questions"]
    assert _leaks(scan_packet_prose("A pale [ interface ] tag hovered over the sand.", body, oq))
    assert _leaks(scan_packet_prose("Somewhere behind his sight the Eyes of Meszkhal stirred.", body, oq))


# --- (b) ordinary game-UI language never flags -------------------------------------------------


def test_benign_game_ui_language_does_not_flag(packet):
    assert scan_packet_prose(BENIGN_PROSE, packet["body"], packet["open_questions"]) == []


def test_lowercase_eyes_and_word_boundaries_do_not_flag(packet):
    # "his eyes narrowed" (common noun) and "eyeshadow" (substring) must not match the ruling's
    # single-word "Eyes" term.
    prose = "Her eyes narrowed. Marcus studied the eyeshadow smudge on the visor rim."
    assert scan_packet_prose(prose, packet["body"], packet["open_questions"]) == []


def test_present_character_name_on_page_does_not_flag(packet):
    # Serra IS on-page this chapter (post-reveal); her name must not be a leak even though her
    # surface_terms entry is timed ("until visual identity is revealed mid-duel").
    prose = "Serra lunged, and Marcus gave ground across the sand."
    assert scan_packet_prose(prose, packet["body"], packet["open_questions"]) == []


# --- (c) a contract WITHOUT the prohibition does not flag the same prose ------------------------


def _packet_permitting_eyes(packet: dict) -> dict:
    """A synthetic later-chapter contract: same canon (locks still name Neurochromatic Eyes as
    the correct interface name) but no on-page prohibition — the Eyes ruling is gone and the
    concept is an allowed UI concept."""
    later = copy.deepcopy(packet)
    later["open_questions"]["resolved"] = [
        r for r in later["open_questions"]["resolved"] if "neurochromatic" not in str(r.get("resolution", "")).lower()
    ]
    later["body"]["allowed_ui_concepts"].append("Neurochromatic Eyes")
    return later


def test_packet_without_prohibition_does_not_flag_same_prose(packet):
    later = _packet_permitting_eyes(packet)
    # The canon lock naming the Eyes is still present — a lock alone must never create a
    # prohibition (it states background truth, not on-page banning).
    assert any("Neurochromatic Eyes" in lock for lock in later["body"]["canon_locks"])
    assert scan_packet_prose(LEAK_PROSE, later["body"], later["open_questions"]) == []


# --- chapter QA wiring (run_chapter_draft_qa) ---------------------------------------------------


def test_chapter_draft_qa_blocks_on_the_leak(packet):
    from dominion.workers.production import run_chapter_draft_qa

    rows = [{"scene_no": 1, "prose": LEAK_PROSE, "word_count": len(LEAK_PROSE.split()), "scene_function": "duel"}]
    qa = run_chapter_draft_qa(
        None, rows, LEAK_PROSE, packet_body=packet["body"], open_questions=packet["open_questions"]
    )
    leaks = _eyes_findings(qa["findings"])
    assert leaks and leaks[0]["severity"] == "block"
    assert qa["verdict"] == "block"


def test_chapter_draft_qa_without_prohibition_does_not_block(packet):
    from dominion.workers.production import run_chapter_draft_qa

    later = _packet_permitting_eyes(packet)
    rows = [{"scene_no": 1, "prose": LEAK_PROSE, "word_count": len(LEAK_PROSE.split()), "scene_function": "duel"}]
    qa = run_chapter_draft_qa(None, rows, LEAK_PROSE, packet_body=later["body"], open_questions=later["open_questions"])
    assert not _leaks(qa["findings"])
    assert qa["verdict"] != "block"


# --- scene-level QA prompt carries the prohibition explicitly ----------------------------------


def test_scene_qa_prefix_carries_prohibited_terms(packet):
    from dominion.workers.scene_packet.qa import build_prefix

    prefix = build_prefix(packet["body"], chapter_open_questions=packet["open_questions"])
    assert prefix is not None
    assert "ON-PAGE PROHIBITED TERMS" in prefix
    assert "Neurochromatic Eyes" in prefix.split("ON-PAGE PROHIBITED TERMS", 1)[1]


def test_prohibited_terms_block_omitted_when_contract_permits(packet):
    later = _packet_permitting_eyes(packet)
    block = format_prohibited_terms_block(later["body"], later["open_questions"])
    # Other hard bans (Sarah/Chad/Roth…) still exist, but the Eyes must no longer be a listed
    # TERM. (The string may legitimately appear inside a contract_reference label — e.g. the
    # surface_terms entry banning the legacy rename "Angelic Fortitude" is labeled
    # "(Neurochromatic Eyes)" — so compare listed terms, not raw substrings.)
    assert block is not None
    listed_terms = [line[2:].split(" [", 1)[0] for line in block.splitlines() if line.startswith("- ")]
    assert "Neurochromatic Eyes" not in listed_terms
    assert "Sarah" in listed_terms and "Roth" in listed_terms
