"""Deterministic owner-file routing (RAG upgrade).

Owner-file precedence beats semantic retrieval: relationship invariants, the cast index, character
dossiers, and mechanics docs must be injected deterministically when a query touches their topic —
vector search never decides canon precedence. This module maps a query (+ the characters in the scene)
to the owner files that MUST be present, plus the owner topics that should be boosted in reranking.

Rules are intentionally simple keyword/character triggers; add a rule rather than widening one. A doc
path that doesn't exist in the corpus yet is harmless — retrieval just finds nothing to force.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OwnerRule:
    owner_topic: str
    doc_paths: tuple[str, ...]
    keywords: tuple[str, ...] = ()
    all_characters: tuple[str, ...] = ()   # every name must appear (e.g. Marcus AND Serra)


# Order matters only for readability; all matching rules contribute.
_RULES: tuple[OwnerRule, ...] = (
    OwnerRule(
        owner_topic="relationship_invariants",
        doc_paths=("relationship_invariants.md", "marcus_serra_relationship.md", "mc.md",
                   "serra_hawthorne.md"),
        keywords=("recognition", "romance", "duel", "relationship"),
        all_characters=("marcus", "serra"),
    ),
    OwnerRule(
        owner_topic="cast_index",
        doc_paths=("cast_index.md",),
        keywords=("roster", "who appears", "who is present", "cast", "present"),
    ),
    OwnerRule(
        owner_topic="classes",
        doc_paths=("classes.md",),
        keywords=("class", "rarity", "class rarity"),
    ),
    OwnerRule(
        owner_topic="mechanics",
        doc_paths=("mechanics.md",),
        keywords=("tier", "soul", "spell strength", "mana", "stat"),
    ),
    OwnerRule(
        owner_topic="cosmology",
        doc_paths=("cosmology.md",),
        keywords=("cosmic", "cosmology", "god", "pantheon"),
    ),
    OwnerRule(
        owner_topic="character_power_architecture",
        doc_paths=("character_power_architecture.md",),
        keywords=("interface", "power architecture", "system window", "ui"),
    ),
)


@dataclass
class OwnerRouting:
    doc_paths: list[str] = field(default_factory=list)
    owner_topics: list[str] = field(default_factory=list)


def route(query: str, *, characters: list[str] | None = None) -> OwnerRouting:
    """Return the owner files to force-inject and the owner topics to boost for this query/cast."""
    q = (query or "").lower()
    present = {c.strip().lower() for c in (characters or []) if c and c.strip()}
    # cast names may also be named directly in the query text
    haystack_names = present | set(present)
    routing = OwnerRouting()
    seen_docs: set[str] = set()

    def _kw_in(keyword: str) -> bool:
        # word-boundary match so short keywords ("ui", "tier") don't hit inside other words ("quiet").
        return re.search(rf"\b{re.escape(keyword)}\b", q) is not None

    for rule in _RULES:
        kw_hit = any(_kw_in(k) for k in rule.keywords) if rule.keywords else False
        char_hit = bool(rule.all_characters) and all(
            c in haystack_names or c in q for c in rule.all_characters
        )
        if not (kw_hit or char_hit):
            continue
        routing.owner_topics.append(rule.owner_topic)
        for path in rule.doc_paths:
            if path not in seen_docs:
                seen_docs.add(path)
                routing.doc_paths.append(path)
    return routing
