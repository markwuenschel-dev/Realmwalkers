#!/usr/bin/env python3
"""No-LLM deterministic debug runner for the scope-aware contract pipeline.

Usage examples (from repo root):

  python scripts/debug_validate_contract.py --internal tmp/packet.json
  python scripts/debug_validate_contract.py --surface tmp/packet.json
  python scripts/debug_validate_contract.py --full tmp/packet.json
  python scripts/debug_validate_contract.py --write-surface tmp/packet.json --out tmp/surface.json

Loads a raw ChapterPacket body JSON (the dict that would be `body` on a ChapterPacket row),
runs the internal validator, the SurfaceContractBuilder, and surface validator.
Prints stage-by-stage blockers and warnings. Writes projected surface when requested.

This is the tool for reproducing deterministic validation failures from saved JSON
without re-running any LLM or touching the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure src on path when run directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from dominion.workers.packet.surface_contract import (  # noqa: E402
    SurfaceContractResult,
    build_surface_contract,
    validate_surface_contract,
)
from dominion.workers.packet.validation import (  # noqa: E402
    ChapterPacketValidationResult,
    evaluate_chapter_packet_internal,
)


def load_body(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"File not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    # Accept either a raw body dict, or a packet-like wrapper that has "body"
    if isinstance(data, dict):
        if "body" in data and isinstance(data["body"], dict):
            return data["body"]
        return data
    raise SystemExit("Input must be a JSON object (packet body or {body: ...})")


def print_violations(label: str, blockers: list[Any], warnings: list[Any]) -> None:
    print(f"\n=== {label} ===")
    if blockers:
        print("BLOCKERS:")
        for v in blockers:
            f = v.field or "?"
            print(f"  [{v.kind}] {f}: {v.detail}")
    if warnings:
        print("WARNINGS:")
        for v in warnings:
            f = v.field or "?"
            print(f"  [{v.kind}] {f}: {v.detail}")
    if not blockers and not warnings:
        print("(none)")


def cmd_internal(body: dict[str, Any]) -> None:
    res: ChapterPacketValidationResult = evaluate_chapter_packet_internal(body)
    print("INTERNAL VALIDATION (roster + structure only)")
    print(f"draftable={res.draftable}")
    print_violations("internal", res.draft_blockers, res.warnings)


def cmd_surface(body: dict[str, Any]) -> SurfaceContractResult:
    internal = evaluate_chapter_packet_internal(body)
    print("INTERNAL (pre-surface):")
    print(f"  draftable={internal.draftable}  warnings={len(internal.warnings)}")
    surf: SurfaceContractResult = build_surface_contract(internal.normalized_body)
    print(f"\nSURFACE BUILD complete. policies={len(surf.policies)}")
    print_violations("surface builder", surf.blockers, surf.warnings)

    surf2 = validate_surface_contract(surf.surface_body, surf.policies)
    # surf2 are always blocks from the post check
    if surf2:
        print("\nPOST-PROJECTION SURFACE VALIDATION BLOCKS:")
        for v in surf2:
            print(f"  [{v.kind}] {v.field}: {v.detail}")
    else:
        print("\nPOST-PROJECTION SURFACE VALIDATION: clean")
    return surf


def cmd_full(body: dict[str, Any]) -> None:
    cmd_internal(body)
    cmd_surface(body)


def cmd_write_surface(body: dict[str, Any], out_path: str) -> None:
    surf = cmd_surface(body)
    outp = Path(out_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(surf.surface_body, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote surface contract to {outp}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Debug ChapterPacket contract stages from saved JSON.")
    ap.add_argument("packet_json", help="Path to JSON file containing packet body (or wrapper with 'body')")
    ap.add_argument("--internal", action="store_true", help="Run internal validation only")
    ap.add_argument("--surface", action="store_true", help="Run internal + surface build + surface validate")
    ap.add_argument("--full", action="store_true", help="Run all stages and print everything")
    ap.add_argument("--write-surface", dest="write_surface", action="store_true", help="Build and write surface JSON")
    ap.add_argument("--out", default="tmp/surface_contract.json", help="Output path for --write-surface")
    args = ap.parse_args()

    body = load_body(args.packet_json)

    if args.write_surface:
        cmd_write_surface(body, args.out)
    elif args.full or (not args.internal and not args.surface):
        cmd_full(body)
    elif args.internal:
        cmd_internal(body)
    elif args.surface:
        cmd_surface(body)


if __name__ == "__main__":
    main()
