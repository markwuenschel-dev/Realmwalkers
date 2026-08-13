"""Prose-hash fingerprints for import-adoption source invalidation (ADR 0028).

The adoption `source_fingerprint` is PROSE-HASH based, not version-based: the inbox hand-edit path
mutates `scene.prose` in place (not every mutation is a new Scene row), so a version-only fingerprint
would miss an edit. The fingerprint is a hash over sorted (scene_no, scene_id, version, prose_sha256)
for every snapshotted scene; any source-prose mutation changes it, and the fail-closed queue seam
refuses to mint a revision Job unless the current chapter fingerprint matches the active adoption's.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def chapter_scene_rows(
    session: AsyncSession, chapter_id: uuid.UUID
) -> list[tuple[int, uuid.UUID, int, str | None]]:
    """The chapter's non-superseded scenes as `(scene_no, scene_id, version, prose)` — the SINGLE
    membership query behind EVERY chapter source fingerprint (ADR-0028 Q10).

    It lives here, beside `chapter_source_fingerprint`, because "which scenes are in the snapshot" and
    "how the snapshot is hashed" are one decision: two callers that disagree about membership produce
    fingerprints that compare unequal even when the prose is identical, which would make the adoption
    worker's drift CAS and amendment mode's drift gate silently incomparable. `import_adoption` and
    `packet.amendment` both delegate here rather than each holding a copy of the query.
    """
    from sqlalchemy import select

    from dominion.shared.enums import SceneStatus
    from dominion.shared.models import Scene

    rows = (
        await session.execute(
            select(Scene.scene_no, Scene.id, Scene.version, Scene.prose).where(
                Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED
            )
        )
    ).all()
    return [(int(r[0]), r[1], int(r[2]), r[3]) for r in rows]


def prose_sha256(prose: str | None) -> str:
    """Stable sha256 of a scene's exact prose (None == empty)."""
    return hashlib.sha256((prose or "").encode("utf-8")).hexdigest()


def chapter_source_fingerprint(rows: Iterable[tuple[int, uuid.UUID, int, str | None]]) -> str:
    """Fingerprint a chapter snapshot from `(scene_no, scene_id, version, prose)` rows.

    Deterministic regardless of input order (rows are sorted), so two callers snapshotting the same
    chapter state produce the same fingerprint. Callers pass the latest non-superseded scene per slot.
    """
    parts = sorted(
        (int(scene_no), str(scene_id), int(version), prose_sha256(prose)) for scene_no, scene_id, version, prose in rows
    )
    joined = "\n".join(
        f"{scene_no}:{scene_id}:{version}:{prose_hash}" for scene_no, scene_id, version, prose_hash in parts
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
