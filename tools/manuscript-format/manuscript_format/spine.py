"""Port of ``frontend/src/desk/manuscript/spine.ts`` + ``readerFrontMatter.ts``.

``ManuscriptSpine`` is the versioned, renderer-neutral intermediate representation every emitter
consumes::

    Manuscript (flat wire) ──build_spine──▶ ManuscriptSpine ──▶ policy ──▶ emitter

Reader DOCX, Shunn DOCX, and Markdown do NOT independently loop chapters, resolve labels, parse
prose, or invent metadata — they read this tree. Prose fidelity is a hard rule: every scene carries
``prose_raw`` (verbatim, the safe source), ``prose_render`` (beautified) and the parsed ``blocks``
SEPARATELY, so an emitter's choice of source is explicit policy, never an accidental transform.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .beautify import beautify
from .labels import (
    GENERATED_SECTION,
    ExportMetadata,
    is_known_chapter_kind,
    part_label,
    resolve_chapter_label,
    section_rank,
    volume_label,
)
from .presets import ExportPolicy
from .prose import ProseBlock, parse_blocks, word_count

#: Bump when the spine's SHAPE changes in a way emitters must notice.
SPINE_SCHEMA_VERSION = "manuscript-spine/2"


# ── The flat wire shape (mirrors the OpenAPI `ManuscriptOut` schema) ─────────────────────────────


@dataclass
class ManuscriptScene:
    scene_no: int
    prose: str | None = None


@dataclass
class ManuscriptChapter:
    pov: str = ""
    position: int | None = None
    chapter_no: int | None = None
    title: str | None = None
    kind: str = "chapter"
    section_type: str | None = None
    epigraph: str | None = None
    part_id: str | None = None
    scenes: list[ManuscriptScene] = field(default_factory=list)


@dataclass
class ManuscriptPart:
    id: str
    part_no: int
    title: str = ""
    volume_id: str | None = None
    subtitle: str | None = None
    kind: str = "part"


@dataclass
class ManuscriptVolume:
    id: str
    volume_no: int
    title: str = ""
    subtitle: str | None = None


@dataclass
class Manuscript:
    """The flat wire manuscript: volumes/parts are sibling lists, joined by id references."""

    title: str = "Untitled"
    book_id: str = ""
    series: str | None = None
    book_no: int | None = None
    subtitle: str | None = None
    volumes: list[ManuscriptVolume] = field(default_factory=list)
    parts: list[ManuscriptPart] = field(default_factory=list)
    chapters: list[ManuscriptChapter] = field(default_factory=list)


# ── Spine nodes ──────────────────────────────────────────────────────────────


@dataclass
class SpineParseIssue:
    """A low-level, severity-free anomaly found while building the spine (parse time)."""

    code: str
    message: str


@dataclass
class SpineSceneNode:
    scene_no: int
    #: Verbatim semantic prose — the safe source. Markdown exports THIS.
    prose_raw: str
    #: ``beautify(prose_raw)`` — the typographically-normalized form. Explicit, not invisible.
    prose_render: str
    #: Parsed AST of ``prose_render``. Reader/Shunn DOCX consume THIS (they never re-parse prose).
    blocks: list[ProseBlock]
    #: Counted from ``prose_raw`` (the source of truth), so counts can't diverge by beautify.
    word_count: int
    has_prose: bool
    issues: list[SpineParseIssue] = field(default_factory=list)


@dataclass
class SpineChapterNode:
    type: str = "chapter"
    position: int | None = None
    #: DISPLAY number — ``None`` for a numberless kind (prologue/interlude/epilogue/front-/back-matter).
    chapter_no: int | None = None
    #: Normalized kind. An unrecognized source kind is coerced to "chapter" and ``kind_recognized``
    #: is set False so a prologue never silently becomes "Chapter N".
    kind: str = "chapter"
    kind_recognized: bool = True
    section_type: str | None = None
    title: str | None = None
    pov: str = ""
    epigraph: str | None = None
    #: Resolved ONCE via the shared label contract. Emitters render this verbatim.
    label: str = ""
    part_id: str | None = None
    scenes: list[SpineSceneNode] = field(default_factory=list)


@dataclass
class SpinePartNode:
    type: str = "part"
    id: str = ""
    part_no: int = 0
    #: Label word only — an Act is a Part rendered as "Act I".
    kind: str = "part"
    title: str = ""
    subtitle: str | None = None
    volume_id: str | None = None
    label: str = ""
    chapters: list[SpineChapterNode] = field(default_factory=list)


@dataclass
class SpineVolumeNode:
    type: str = "volume"
    id: str = ""
    volume_no: int = 0
    title: str = ""
    subtitle: str | None = None
    label: str = ""
    parts: list[SpinePartNode] = field(default_factory=list)


SpineNode = SpineVolumeNode | SpinePartNode | SpineChapterNode


@dataclass
class ManuscriptSpine:
    metadata: ExportMetadata
    nodes: list[SpineNode] = field(default_factory=list)
    schema_version: str = SPINE_SCHEMA_VERSION


def _build_scene_node(scene: ManuscriptScene) -> SpineSceneNode:
    prose_raw = scene.prose or ""
    has_prose = len(prose_raw.strip()) > 0
    prose_render = beautify(prose_raw)
    blocks = parse_blocks(prose_render)
    issues: list[SpineParseIssue] = []
    if not has_prose:
        issues.append(SpineParseIssue("empty_scene", f"Scene {scene.scene_no} has no prose."))
    elif not blocks:
        issues.append(
            SpineParseIssue(
                "no_blocks_parsed", f"Scene {scene.scene_no} has prose but parsed to zero blocks."
            )
        )
    return SpineSceneNode(
        scene_no=scene.scene_no,
        prose_raw=prose_raw,
        prose_render=prose_render,
        blocks=blocks,
        word_count=word_count(prose_raw),
        has_prose=has_prose,
        issues=issues,
    )


def _build_chapter_node(ch: ManuscriptChapter) -> SpineChapterNode:
    raw_kind = ch.kind or "chapter"
    kind_recognized = is_known_chapter_kind(raw_kind)
    kind = raw_kind if kind_recognized else "chapter"
    return SpineChapterNode(
        position=ch.position,
        chapter_no=ch.chapter_no,
        kind=kind,
        kind_recognized=kind_recognized,
        section_type=ch.section_type,
        title=ch.title,
        pov=ch.pov,
        epigraph=ch.epigraph,
        # Label off the NORMALIZED kind (unknown → "Chapter N").
        label=resolve_chapter_label(
            kind=kind, title=ch.title, section_type=ch.section_type, chapter_no=ch.chapter_no
        ),
        part_id=ch.part_id,
        scenes=[_build_scene_node(s) for s in sorted(ch.scenes, key=lambda s: s.scene_no)],
    )


def _order_key(ch: ManuscriptChapter) -> int:
    if ch.position is not None:
        return ch.position
    return ch.chapter_no if ch.chapter_no is not None else 0


def build_spine(ms: Manuscript, metadata: ExportMetadata) -> ManuscriptSpine:
    """Tree-ify the flat wire manuscript into the ordered reading spine.

    A grouping node is emitted at the position of its FIRST member in reading order, and members
    collect under it thereafter. Ungrouped chapters, chapters with a dangling ``part_id``, and
    parts with a dangling ``volume_id`` render at their natural (higher) level.
    """
    part_by_id = {p.id: p for p in ms.parts}
    volume_by_id = {v.id: v for v in ms.volumes}
    emitted_parts: dict[str, SpinePartNode] = {}
    emitted_volumes: dict[str, SpineVolumeNode] = {}
    nodes: list[SpineNode] = []

    def volume_node_for(v: ManuscriptVolume) -> SpineVolumeNode:
        node = emitted_volumes.get(v.id)
        if node is None:
            node = SpineVolumeNode(
                id=v.id,
                volume_no=v.volume_no,
                title=v.title,
                subtitle=v.subtitle,
                label=volume_label(v.volume_no, v.title),
                parts=[],
            )
            emitted_volumes[v.id] = node
            nodes.append(node)
        return node

    def part_node_for(p: ManuscriptPart) -> SpinePartNode:
        node = emitted_parts.get(p.id)
        if node is None:
            node = SpinePartNode(
                id=p.id,
                part_no=p.part_no,
                kind="act" if p.kind == "act" else "part",
                title=p.title,
                subtitle=p.subtitle,
                volume_id=p.volume_id,
                label=part_label(p.part_no, p.title, p.kind),
                chapters=[],
            )
            emitted_parts[p.id] = node
            volume = volume_by_id.get(p.volume_id) if p.volume_id else None
            if volume is not None:
                volume_node_for(volume).parts.append(node)
            else:
                nodes.append(node)  # ungrouped part, or dangling volume_id → top-level
        return node

    for ch in sorted(ms.chapters, key=_order_key):
        ch_node = _build_chapter_node(ch)
        part = part_by_id.get(ch_node.part_id) if ch_node.part_id else None
        if part is not None:
            part_node_for(part).chapters.append(ch_node)
        else:
            nodes.append(ch_node)  # ungrouped, or dangling part_id → top-level

    return ManuscriptSpine(metadata=metadata, nodes=nodes)


# ── Derived reading-order views ──────────────────────────────────────────────


def spine_volumes(spine: ManuscriptSpine) -> list[SpineVolumeNode]:
    return [n for n in spine.nodes if isinstance(n, SpineVolumeNode)]


def spine_parts(spine: ManuscriptSpine) -> list[SpinePartNode]:
    """Every part node in reading order (volume members flattened in, top-level parts in place)."""
    out: list[SpinePartNode] = []
    for n in spine.nodes:
        if isinstance(n, SpineVolumeNode):
            out.extend(n.parts)
        elif isinstance(n, SpinePartNode):
            out.append(n)
    return out


def spine_chapters(spine: ManuscriptSpine) -> list[SpineChapterNode]:
    """Every chapter node in reading order (volumes → parts → chapters; ungrouped in place)."""
    out: list[SpineChapterNode] = []
    for n in spine.nodes:
        if isinstance(n, SpineVolumeNode):
            for p in n.parts:
                out.extend(p.chapters)
        elif isinstance(n, SpinePartNode):
            out.extend(n.chapters)
        else:
            out.append(n)
    return out


def spine_scenes(spine: ManuscriptSpine) -> list[SpineSceneNode]:
    return [s for c in spine_chapters(spine) for s in c.scenes]


@dataclass
class SpineCounts:
    volumes: int
    parts: int
    chapters: int
    scenes: int
    words: int


def spine_counts(spine: ManuscriptSpine) -> SpineCounts:
    """Counts for the manifest + page estimates. Only scenes with prose count."""
    scenes = [s for s in spine_scenes(spine) if s.has_prose]
    return SpineCounts(
        volumes=len(spine_volumes(spine)),
        parts=len(spine_parts(spine)),
        chapters=len(spine_chapters(spine)),
        scenes=len(scenes),
        words=sum(s.word_count for s in scenes),
    )


def spine_has_prose(spine: ManuscriptSpine) -> bool:
    return any(s.has_prose for s in spine_scenes(spine))


# ── Reader production plan (port of readerFrontMatter.ts) ────────────────────


@dataclass
class ReaderFrontItem:
    """One planned front-matter emission: a generated page or an authored section chapter."""

    type: str  # "half_title" | "title_page" | "toc" | "section"
    entries: list[str] = field(default_factory=list)
    node: SpineChapterNode | None = None


@dataclass
class ReaderProductionPlan:
    #: Half-title, title page, authored front-matter sections and Contents, in canonical order.
    front: list[ReaderFrontItem]
    #: Everything after the front matter, in spine order (front-matter chapters removed).
    body: list[SpineNode]


def _is_front_matter(n: SpineNode) -> bool:
    return isinstance(n, SpineChapterNode) and n.kind == "front_matter"


def _chapter_has_prose(c: SpineChapterNode) -> bool:
    return any(s.has_prose for s in c.scenes)


def plan_reader_production(spine: ManuscriptSpine, policy: ExportPolicy) -> ReaderProductionPlan:
    """Build the Reader emission plan: generated pages interleaved with authored front matter by
    canonical publishing rank, so Copyright → Dedication → CONTENTS → Preface fall in book order."""
    ranked: list[tuple[int, ReaderFrontItem]] = []

    if policy.include_half_title:
        ranked.append((section_rank(GENERATED_SECTION.half_title), ReaderFrontItem("half_title")))
    if policy.include_title_page:
        ranked.append((section_rank(GENERATED_SECTION.title_page), ReaderFrontItem("title_page")))

    for node in spine.nodes:
        if _is_front_matter(node):
            assert isinstance(node, SpineChapterNode)
            ranked.append((section_rank(node.section_type), ReaderFrontItem("section", node=node)))

    if policy.include_table_of_contents:
        # Contents lists everything after it — prologue, chapters, epilogue, back matter.
        entries = [
            f"{c.label} — {c.title}" if c.title else c.label
            for c in spine_chapters(spine)
            if c.kind != "front_matter" and _chapter_has_prose(c)
        ]
        if entries:
            ranked.append(
                (
                    section_rank(GENERATED_SECTION.table_of_contents),
                    ReaderFrontItem("toc", entries=entries),
                )
            )

    # Stable sort by rank keeps equal-rank items in their discovery order.
    ranked.sort(key=lambda r: r[0])

    return ReaderProductionPlan(
        front=[item for _, item in ranked],
        body=[n for n in spine.nodes if not _is_front_matter(n)],
    )
