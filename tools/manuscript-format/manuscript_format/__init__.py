"""Standalone manuscript formatter — a Python port of the Writers' Desk DOCX export pipeline.

Reads a Markdown (or DOCX) manuscript and emits the same three artifacts the in-app exporter does:

* **Reader DOCX** — styled book format: title page, linked Contents, draft running header,
  drop-cap chapter openings, volume/part dividers, and the full LitRPG interface-panel treatment.
* **Shunn DOCX** — plain submission format, rich blocks flattened to safe text.
* **Semantic Markdown** — YAML front matter + structural comments, prose preserved verbatim.

The pipeline mirrors the TypeScript original exactly::

    source ──ingest──▶ Manuscript ──build_spine──▶ ManuscriptSpine ──policy──▶ emitter

Every module names the TypeScript file it ports in its docstring.
"""

from __future__ import annotations

__version__ = "1.3.1"
