"""Audit draft jobs and beat/ScenePacket links (read-only)."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from dominion.shared.db import SessionFactory
from dominion.tools.draft_audit import audit_chapter


async def _run(chapter_id: uuid.UUID, dry_run: bool) -> None:
    async with SessionFactory() as session:
        report = await audit_chapter(session, chapter_id)
    out = {
        "chapter_id": str(chapter_id),
        "dry_run": dry_run,
        "malformed_jobs": report.malformed_jobs,
        "unlinked_beats": report.unlinked_beats,
        "repairable_beats": report.repairable_beats,
        "duplicate_packets": report.duplicate_packets,
        "beat_packet_mismatches": report.beat_packet_mismatches,
    }
    print(json.dumps(out, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit draft queue state for a chapter")
    parser.add_argument("--chapter-id", type=uuid.UUID, required=True)
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()
    asyncio.run(_run(args.chapter_id, args.dry_run))


if __name__ == "__main__":
    main()
