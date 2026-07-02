"""Scope-aware contract pipeline primitives for ChapterPacket / SurfaceContract / ScenePacket.

Raw AuthorPacketInternal (ChapterPacket.body) may contain hidden canonical truth.
SurfaceContract is the only drafter-safe projection passed downstream.
Field scope is explicit: no validator may hard-block text whose scope it does not know.

Generic over any hidden term (character, faction, place, power, relationship, reveal, cosmology...).
No per-name or per-book logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Any not required at runtime after cleanups.


class ContractScope(str, Enum):  # noqa: UP042 - str+Enum mixin is the intended pattern for string enums here
    """Classification of a field or value's visibility and safety constraints.

    INTERNAL_PLANNING: Backstage. May hold hidden names, future reveals, unrevealed identities,
    canon truth. Safe for author/system; never shown to drafter or reader.

    AUTHOR_ONLY_CANON: Canon/audit truth. Anything the system must know for consistency or audit.
    Never leaks to surface.

    DRAFTER_SURFACE: Instructions passed to drafting models. MUST be safe. Hidden canonical names
    must be replaced, omitted, or the packet must block before reaching here.

    READER_KNOWLEDGE: What the reader is allowed to know at this point. Hidden names/reveals block here.

    POV_KNOWLEDGE: What the POV character is allowed to know/perceive. Hidden names/reveals block here.

    MANUSCRIPT_SURFACE: Actual generated prose. Forbidden surface terms hard-block here (unless policy
    expired/reveal allowed).

    AUDIT: Debug/provenance/log. May contain internal truth for diagnostics.
    """

    INTERNAL_PLANNING = "internal_planning"
    AUTHOR_ONLY_CANON = "author_only_canon"
    DRAFTER_SURFACE = "drafter_surface"
    READER_KNOWLEDGE = "reader_knowledge"
    POV_KNOWLEDGE = "pov_knowledge"
    MANUSCRIPT_SURFACE = "manuscript_surface"
    AUDIT = "audit"


# ChapterPacket top-level (and direct) fields and their canonical scope in the RAW internal packet.
# Raw packet is AuthorPacketInternal: these classifications say where truth lives before projection.
CHAPTER_PACKET_FIELD_SCOPES: dict[str, ContractScope] = {
    "chapter_job": ContractScope.INTERNAL_PLANNING,
    "one_sentence_spine": ContractScope.INTERNAL_PLANNING,
    "entry_state": ContractScope.INTERNAL_PLANNING,
    "exit_state": ContractScope.INTERNAL_PLANNING,
    "emotional_spine": ContractScope.INTERNAL_PLANNING,
    "characters_present": ContractScope.INTERNAL_PLANNING,
    "characters_absent": ContractScope.INTERNAL_PLANNING,
    "characters_mentioned_only": ContractScope.INTERNAL_PLANNING,
    "characters_forbidden": ContractScope.INTERNAL_PLANNING,
    "allowed_knowledge": ContractScope.READER_KNOWLEDGE,
    "forbidden_knowledge": ContractScope.AUTHOR_ONLY_CANON,
    "required_reveals": ContractScope.INTERNAL_PLANNING,
    "forbidden_reveals": ContractScope.AUTHOR_ONLY_CANON,
    "canon_locks": ContractScope.AUTHOR_ONLY_CANON,
    "roster_locks": ContractScope.AUTHOR_ONLY_CANON,
    "relationship_locks": ContractScope.AUTHOR_ONLY_CANON,
    "timeline_locks": ContractScope.AUTHOR_ONLY_CANON,
    "allowed_ui_concepts": ContractScope.READER_KNOWLEDGE,
    "forbidden_ui_concepts": ContractScope.AUTHOR_ONLY_CANON,
    "required_unanswered_questions": ContractScope.INTERNAL_PLANNING,
    "known_risks": ContractScope.AUTHOR_ONLY_CANON,
    "claims": ContractScope.AUTHOR_ONLY_CANON,
    "open_questions": ContractScope.INTERNAL_PLANNING,
    # surface_terms is the explicit generic policy carrier (author-supplied safe projections).
    "surface_terms": ContractScope.INTERNAL_PLANNING,
    # Legacy transition key (entity_bindings) treated as internal planning.
    "entity_bindings": ContractScope.INTERNAL_PLANNING,
}

# Scene seed fields as they arrive in the RAW ChapterPacket.scene_seeds[].
# These are INTERNAL until projected by SurfaceContractBuilder.
RAW_SCENE_SEED_FIELD_SCOPES: dict[str, ContractScope] = {
    "scene_job": ContractScope.INTERNAL_PLANNING,
    "required_beats": ContractScope.INTERNAL_PLANNING,
    "forbidden_beats": ContractScope.INTERNAL_PLANNING,
    "exit_state": ContractScope.INTERNAL_PLANNING,
    "scene_type": ContractScope.INTERNAL_PLANNING,
    "word_budget": ContractScope.INTERNAL_PLANNING,
    "seed_id": ContractScope.INTERNAL_PLANNING,
    "scene_no": ContractScope.INTERNAL_PLANNING,
}

# After projection, these paths in the SurfaceContract are DRAFTER_SURFACE (safe for drafter models).
# ScenePacket derivation MUST consume these, not the raw packet equivalents.
SURFACE_CONTRACT_FIELD_SCOPES: dict[str, ContractScope] = {
    "scene_seeds.scene_job": ContractScope.DRAFTER_SURFACE,
    "scene_seeds.required_beats": ContractScope.DRAFTER_SURFACE,
    "scene_seeds.forbidden_beats": ContractScope.DRAFTER_SURFACE,
    "scene_seeds.exit_state": ContractScope.DRAFTER_SURFACE,
    # Top-level surface fields that are safe to hand to drafter (chapter summary level).
    "chapter_job": ContractScope.DRAFTER_SURFACE,
    "one_sentence_spine": ContractScope.DRAFTER_SURFACE,
    "entry_state": ContractScope.DRAFTER_SURFACE,
    "exit_state": ContractScope.DRAFTER_SURFACE,
}


@dataclass(frozen=True)
class ScopedTextField:
    """A text value together with its declared scope and source path.

    Validators MUST use this (or equivalent) and MUST NOT hard-block when scope is unknown
    or when the scope permits the term (e.g. INTERNAL_PLANNING may hold hidden canon names).
    """

    path: str
    scope: ContractScope
    value: str


def get_field_scope(path: str, is_scene_seed_field: bool = False) -> ContractScope | None:
    """Return the declared scope for a (possibly dotted) path, or None if unknown.

    For scene seeds inside raw packet use RAW_SCENE_SEED... ; after surface projection use SURFACE...
    Top level uses CHAPTER_PACKET...
    """
    if path.startswith("scene_seeds."):
        key = path.split(".", 1)[1] if "." in path else path
        if is_scene_seed_field or key in RAW_SCENE_SEED_FIELD_SCOPES:
            # In raw packet context the scene seed subfields are still internal.
            return RAW_SCENE_SEED_FIELD_SCOPES.get(key)
        # If caller signals post-projection dotted path
        return SURFACE_CONTRACT_FIELD_SCOPES.get(path)
    if path in SURFACE_CONTRACT_FIELD_SCOPES:
        return SURFACE_CONTRACT_FIELD_SCOPES[path]
    return CHAPTER_PACKET_FIELD_SCOPES.get(path)


def scope_allows_internal_truth(scope: ContractScope | None) -> bool:
    """True for scopes that may legitimately contain hidden canonical / author-only truth."""
    if scope is None:
        return False
    return scope in {
        ContractScope.INTERNAL_PLANNING,
        ContractScope.AUTHOR_ONLY_CANON,
        ContractScope.AUDIT,
    }


def scope_is_surface_drafter(scope: ContractScope | None) -> bool:
    """Scopes that must never contain unsafely projected forbidden surface terms."""
    if scope is None:
        return False
    return scope in {
        ContractScope.DRAFTER_SURFACE,
        ContractScope.READER_KNOWLEDGE,
        ContractScope.POV_KNOWLEDGE,
        ContractScope.MANUSCRIPT_SURFACE,
    }
