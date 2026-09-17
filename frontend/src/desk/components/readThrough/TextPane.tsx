"use client";

// TextPane — the chapter snapshot rendered raw, with anchor highlights placed from the server's
// offsets (lib/anchorSpans). Deliberately not beautify/ProseBlocks: any normalization would move the
// text out from under the offsets.

import { Fragment, useEffect, useMemo, useRef } from "react";
import { css } from "../../css";
import { anchorSpans, type KeyedAnchor, type Piece } from "../../lib/anchorSpans";
import { Panel } from "../ui";

const READER =
  "max-height:72vh;overflow:auto;padding-right:6px;font-family:var(--prose,var(--ui));font-size:15px;line-height:1.8;color:var(--ink)";
const PARA = "white-space:pre-wrap;overflow-wrap:anywhere;min-height:1.8em";
const MONO = "font-family:var(--mono);font-size:11px;color:var(--dim)";

function markStyle(piece: Piece, activeAnchorId: string | null): string {
  const ids = new Set(piece.marks.map((m) => m.anchorId));
  const active = activeAnchorId !== null && ids.has(activeAnchorId);
  const candidateOnly = piece.marks.every((m) => m.kind === "candidate");
  const tone = candidateOnly ? "--warn" : "--info";
  return (
    `color:inherit;cursor:pointer;border-radius:2px;` +
    `background:color-mix(in srgb,var(${tone}) ${active ? 28 : 11}%,transparent);` +
    `border-bottom:1.5px ${candidateOnly ? "dashed" : "solid"} var(${tone})` +
    (ids.size > 1 ? ";outline:1px dotted var(--accent);outline-offset:1px" : "")
  );
}

export default function TextPane({
  label,
  text,
  anchors,
  activeAnchorId,
  scrollNonce,
  onSelectAnchor,
}: {
  label: string;
  text: string;
  anchors: readonly KeyedAnchor[];
  activeAnchorId: string | null;
  /** Bumped on every anchor click so re-clicking the same anchor scrolls again. */
  scrollNonce: number;
  onSelectAnchor: (anchorId: string) => void;
}) {
  // Placement depends on the snapshot text and the anchors only — selecting a note must not redo it.
  const layout = useMemo(() => anchorSpans(text, anchors), [text, anchors]);
  const targetsByPiece = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const [anchorId, pieceKey] of layout.firstPiece) {
      m.set(pieceKey, [...(m.get(pieceKey) ?? []), anchorId]);
    }
    return m;
  }, [layout]);
  const targets = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    if (!activeAnchorId) return;
    targets.current.get(activeAnchorId)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [activeAnchorId, scrollNonce]);

  const mismatched = new Set(layout.mismatches.map((m) => m.anchorId)).size;

  return (
    <Panel eyebrow="Chapter text · as supplied" title={label}>
      <div style={css(READER)}>
        {layout.paragraphs.map((p) => (
          <div key={p.index} style={css(PARA)}>
            {p.pieces.map((piece, i) => {
              if (piece.marks.length === 0) return <Fragment key={i}>{piece.text}</Fragment>;
              const ids = [...new Set(piece.marks.map((m) => m.anchorId))];
              const scrollIds = targetsByPiece.get(`${p.index}:${i}`);
              const candidateOnly = piece.marks.every((m) => m.kind === "candidate");
              return (
                <mark
                  key={i}
                  data-kind={candidateOnly ? "candidate" : "located"}
                  data-anchor-ids={ids.join(" ")}
                  data-active={
                    activeAnchorId !== null && ids.includes(activeAnchorId) ? "true" : undefined
                  }
                  ref={
                    scrollIds
                      ? (el) => {
                          for (const id of scrollIds) {
                            if (el) targets.current.set(id, el);
                            else targets.current.delete(id);
                          }
                        }
                      : undefined
                  }
                  onClick={() =>
                    onSelectAnchor(
                      activeAnchorId !== null && ids.includes(activeAnchorId)
                        ? activeAnchorId
                        : ids[0],
                    )
                  }
                  style={css(markStyle(piece, activeAnchorId))}
                >
                  {piece.text}
                </mark>
              );
            })}
          </div>
        ))}
      </div>
      {mismatched > 0 && (
        <p style={css("margin:10px 0 0;font-size:12.5px;color:var(--warn)")}>
          {`${mismatched} quote${mismatched === 1 ? "" : "s"} did not match this chapter's text at the saved position and ${mismatched === 1 ? "is" : "are"} not highlighted.`}
        </p>
      )}
      {layout.overlaps.length > 0 && (
        <p style={css(`${MONO};margin:6px 0 0`)}>
          {`${layout.overlaps.length} passage${layout.overlaps.length === 1 ? "" : "s"} carry more than one note's highlight (outlined).`}
        </p>
      )}
    </Panel>
  );
}
