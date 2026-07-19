"""SECTION-MIRROR: the frontend SECTION_ORDER + ChapterKind lists (desk/manuscript/labels.ts) mirror the
backend chapter_order._SECTION_ORDER + enums.ChapterKind. They are hand-maintained parallel lists kept
aligned only by a 'KEEP IN SYNC' comment; this pins them so a one-sided edit fails in CI."""

from __future__ import annotations

import re
from pathlib import Path

from dominion.shared.chapter_order import _SECTION_ORDER
from dominion.shared.enums import ChapterKind

_LABELS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "desk" / "manuscript" / "labels.ts"


def _ts_string_array(source: str, const_name: str) -> list[str]:
    m = re.search(rf"const {re.escape(const_name)}[^=]*=\s*\[(.*?)\]", source, re.S)
    assert m, f"const {const_name} array not found in labels.ts"
    return re.findall(r'"([a-z_]+)"', m.group(1))


def test_section_order_mirrors_backend():
    ts = _LABELS.read_text(encoding="utf-8")
    assert _ts_string_array(ts, "SECTION_ORDER") == list(_SECTION_ORDER)


def test_chapter_kinds_mirror_backend():
    ts = _LABELS.read_text(encoding="utf-8")
    assert _ts_string_array(ts, "KNOWN_CHAPTER_KINDS") == [k.value for k in ChapterKind]
