"""Export the FastAPI OpenAPI schema to openapi.json at the repo root.

Used by the frontend codegen pipeline (pnpm codegen) and CI drift checks.
Does not start uvicorn or require a live database.
"""
from __future__ import annotations

import json
from pathlib import Path

from dominion.api.main import app

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openapi.json"


def main() -> None:
    OUT.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
