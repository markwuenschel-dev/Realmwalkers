"""Throwaway diagnostic: where does each scene's per-scene token budget actually go?

Self-contained (raw asyncpg + .env parse — no app import, no project sync). Reads the latest
scene-packet derive run from `llm_calls` and prints, per scene, every model call with its WEIGHTED
budget cost (cache writes at 1.0x, reads at 0.1x — matching budget.py), a per-scene subtotal against
the scene_token_budget ceiling, and a cache verdict: is the shared chapter/scene prefix being READ
back (cheap) or silently RE-WRITTEN at full weight (the budget blowup)?

Run from the repo root (reaches Postgres on localhost:5432):

    uv run --no-project --with asyncpg python scripts/diag_scene_budget.py

Prints only aggregate token counts — never the DB URL. Safe to delete afterwards.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg

W_WRITE = 1.0  # mirror budget.py
W_READ = 0.1
CEILING = int(os.environ.get("SCENE_TOKEN_BUDGET", "60000"))
SCENE_STAGES = ("scene_packet_author", "scene_packet_qa")


def dsn() -> str:
    """Resolve a plain asyncpg DSN. Prefers a live env var (so `railway run python …` targets prod),
    then .env, then the local default. Never returned to stdout."""
    # Prefer a PUBLIC proxy URL first so `railway run …` works from outside the cluster (the plain
    # DATABASE_URL on Railway points at the internal host, unreachable from a dev machine).
    raw = (
        os.environ.get("DATABASE_PUBLIC_URL")
        or os.environ.get("DOMINION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    env = Path(__file__).resolve().parent.parent / ".env"
    if not raw and env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("DOMINION_DATABASE_URL", "DATABASE_URL"):
                raw = v.strip().strip('"').strip("'")
                break
    raw = raw or "postgresql://dominion:dominion@localhost:5432/dominion"
    for scheme in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres://"):
        if raw.startswith(scheme):
            raw = "postgresql://" + raw[len(scheme) :]
    return raw


def weighted(r) -> float:
    return (
        r["input_tokens"] + r["output_tokens"] + r["cache_creation_tokens"] * W_WRITE + r["cache_read_tokens"] * W_READ
    )


def m(r, key, default=None):
    md = r["metadata"]
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except Exception:
            md = {}
    return md.get(key, default) if isinstance(md, dict) else default


async def main() -> None:
    conn = await asyncpg.connect(dsn())
    try:
        latest = await conn.fetchrow(
            "SELECT run_id FROM llm_calls WHERE stage LIKE 'scene_packet%' AND run_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        if latest is None:
            print("No scene-packet telemetry found (run a derive first).")
            return
        run_id = latest["run_id"]
        rows = await conn.fetch(
            "SELECT scene_no, stage, model, input_tokens, output_tokens, cache_creation_tokens, "
            "cache_read_tokens, truncated, error, metadata, created_at "
            "FROM llm_calls WHERE run_id = $1 ORDER BY created_at",
            run_id,
        )
    finally:
        await conn.close()

    print(f"run_id={run_id}  calls={len(rows)}  scene_token_budget={CEILING}")
    snap = m(rows[0], "settings_snapshot", {}) or {}
    if snap:
        print(
            "models: author={} qa={} fallback={} sectioned={}".format(
                snap.get("scene_packet_author_model"),
                snap.get("scene_packet_qa_model"),
                snap.get("scene_packet_author_fallback_model"),
                snap.get("scene_packet_author_sectioned"),
            )
        )

    t0 = min(r["created_at"] for r in rows)

    primes = [r for r in rows if r["scene_no"] is None]
    if primes:
        print("\n-- prefix primes (separate budget) --")
        for r in primes:
            dt = (r["created_at"] - t0).total_seconds()
            print(
                f"  +{dt:5.0f}s {r['stage']:32s} {r['model']:18s} "
                f"out={r['output_tokens']:5d} cc={r['cache_creation_tokens']:6d} cr={r['cache_read_tokens']:6d}"
            )

    by_scene: dict[int, list] = {}
    for r in rows:
        if r["scene_no"] is not None and r["stage"] in SCENE_STAGES:
            by_scene.setdefault(r["scene_no"], []).append(r)

    for scene_no in sorted(by_scene):
        calls = by_scene[scene_no]
        print(f"\n== Scene {scene_no} == (ceiling {CEILING})")
        author_w = qa_w = 0.0
        cc_author = cr_author = cc_qa = cr_qa = 0
        for r in calls:
            dt = (r["created_at"] - t0).total_seconds()
            w = weighted(r)
            sect = m(r, "section_name", "") or ""
            fb = m(r, "fallback_attempt", False)
            tag = r["stage"].replace("scene_packet_", "") + (f":{sect}" if sect else "") + (" [FB]" if fb else "")
            flags = ("TRUNC " if r["truncated"] else "") + ("ERR" if r["error"] else "")
            print(
                f"  +{dt:5.0f}s {tag:22s} {r['model']:16s} "
                f"out={r['output_tokens']:5d} cc={r['cache_creation_tokens']:6d} cr={r['cache_read_tokens']:6d} "
                f"=> w={w:8.0f}  {flags}"
            )
            if r["stage"] == "scene_packet_qa":
                qa_w += w
                cc_qa += r["cache_creation_tokens"]
                cr_qa += r["cache_read_tokens"]
            else:
                author_w += w
                cc_author += r["cache_creation_tokens"]
                cr_author += r["cache_read_tokens"]
        total = author_w + qa_w
        over = " <<< OVER CEILING" if total > CEILING else ""
        print(f"  subtotal: author_w={author_w:.0f}  qa_w={qa_w:.0f}  TOTAL={total:.0f}/{CEILING}{over}")
        print(f"  cache: author cc(write)={cc_author} cr(read)={cr_author} | qa cc(write)={cc_qa} cr(read)={cr_qa}")

    span = (max(r["created_at"] for r in rows) - t0).total_seconds()
    print(f"\nrun wall-clock span = {span:.0f}s (Anthropic ephemeral cache TTL = 300s)")
    print("VERDICT cues:")
    print("  - TOTAL just over ceiling + large cc(write) on QA or non-primer author => primed prefix")
    print("    EXPIRED mid-run -> full-weight rewrites (a TTL problem, not a sizing problem).")
    print("  - cr(read) dominates but TOTAL still high => raw OUTPUT cost of 5 sections (a sizing problem).")


if __name__ == "__main__":
    asyncio.run(main())
