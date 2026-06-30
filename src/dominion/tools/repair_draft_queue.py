"""Repair beat/ScenePacket links and cancel invalid draft jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from dominion.shared.db import SessionFactory
from dominion.shared.enums import JobStatus
from dominion.shared.models import Beat, Job
from dominion.tools.draft_audit import audit_chapter


async def _apply(chapter_id: uuid.UUID, dry_run: bool) -> dict:
    async with SessionFactory() as session:
        report = await audit_chapter(session, chapter_id)
        actions: dict = {"repaired_beats": [], "cancelled_jobs": [], "skipped": []}

        if dry_run:
            return {
                "chapter_id": str(chapter_id),
                "dry_run": True,
                "would_repair_beats": report.repairable_beats,
                "would_cancel_jobs": report.malformed_jobs,
                "skipped": report.unlinked_beats + report.duplicate_packets,
            }

        for item in report.repairable_beats:
            beat = await session.get(Beat, uuid.UUID(item["beat_id"]))
            if beat is not None:
                beat.scene_packet_id = uuid.UUID(item["scene_packet_id"])
                actions["repaired_beats"].append(item)

        for item in report.malformed_jobs:
            job = await session.get(Job, uuid.UUID(item["id"]))
            if job is not None and job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED):
                job.status = JobStatus.FAILED
                job.last_error = (job.last_error or "") + " [cancelled: invalid scene_packet_id]"
                actions["cancelled_jobs"].append(item["id"])

        if report.unlinked_beats or report.duplicate_packets:
            actions["skipped"] = report.unlinked_beats + report.duplicate_packets

        await session.commit()
        return {"chapter_id": str(chapter_id), "dry_run": False, **actions}


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair draft queue for a chapter")
    parser.add_argument("--chapter-id", type=uuid.UUID, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        parser.error("Specify --dry-run or --apply")
    result = asyncio.run(_apply(args.chapter_id, dry_run=not args.apply))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
