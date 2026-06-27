"""Canonical source hashing for ScenePacket staleness (DESIGN: staleness detection).

A ScenePacket is derived from a set of upstream inputs (the chapter packet, the scene seed, the word
budget, the prior approved scenes, relevant owner/canon hashes). We hash all of them canonically so a
later change to any input is detectable: if the recomputed hash differs from the stored `source_hash`,
the packet is stale and may not create a new draft job until re-derived or re-approved.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, so equal inputs always hash equal."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def source_hash(
    *,
    chapter_packet_id: Any,
    chapter_packet_body: dict[str, Any] | None,
    scene_seed: dict[str, Any] | None,
    chapter_word_budget: Any,
    prior_scene_keys: list[Any] | None = None,
    owner_file_hashes: dict[str, str] | None = None,
    canon_chunk_hashes: list[str] | None = None,
) -> str:
    """sha256 over the canonical JSON of every input a ScenePacket is derived from.

    `prior_scene_keys` should be a list of (scene_id, version, word_count)-ish tuples for the prior
    approved scenes — anything that, when it changes, should re-open this scene's contract.
    """
    payload = {
        "chapter_packet_id": str(chapter_packet_id),
        "chapter_packet_body": chapter_packet_body or {},
        "scene_seed": scene_seed or {},
        "chapter_word_budget": chapter_word_budget,
        "prior_scene_keys": prior_scene_keys or [],
        "owner_file_hashes": owner_file_hashes or {},
        "canon_chunk_hashes": sorted(canon_chunk_hashes or []),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
