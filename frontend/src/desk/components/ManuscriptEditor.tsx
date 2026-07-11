"use client";

// Annotated boundary editor (Slice 3, GitHub #204). The parsed manuscript is flattened into
// paragraph BLOCKS; between/at each block sits a boundary the user can change — none (mid-scene),
// scene break, or chapter start. This is the "move a divider" model without literal drag: correct a
// false scene break (→ none), add a missed one (→ scene), or insert a missing chapter header
// (→ chapter) for the headerless chapters the splitter can't see. Chapter boundaries carry an
// editable number / title / POV (+ overwrite when the number collides). On import the blocks are
// resolved back into chapters → scenes.
import { useMemo, useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { useDeskData } from "../api/data";
import { Button, Chip } from "./ui";
import type { ManuscriptImportIn, ParsedManuscriptOut } from "../api/types";

type Brk = "chapter" | "scene" | "none";

interface Block {
  key: string;
  text: string;
  brk: Brk;
  chapterNo: string; // editable string; only meaningful when brk === "chapter"
  title: string;
  pov: string;
  overwrite: boolean;
}

function buildBlocks(parsed: ParsedManuscriptOut, defaultPov: string): Block[] {
  const blocks: Block[] = [];
  let k = 0;
  for (const ch of parsed.chapters) {
    ch.scenes.forEach((sc, si) => {
      const paras = sc.prose
        .split(/\n\s*\n/)
        .map((p) => p.trim())
        .filter(Boolean);
      if (paras.length === 0) paras.push(sc.prose.trim());
      paras.forEach((text, pi) => {
        const chapterStart = si === 0 && pi === 0;
        const sceneStart = si > 0 && pi === 0;
        blocks.push({
          key: `b${k++}`,
          text,
          brk: chapterStart ? "chapter" : sceneStart ? "scene" : "none",
          chapterNo: chapterStart ? String(ch.chapter_no) : "",
          title: chapterStart ? (ch.title ?? "") : "",
          pov: chapterStart ? defaultPov : "",
          overwrite: false,
        });
      });
    });
  }
  if (blocks.length && blocks[0].brk !== "chapter") {
    blocks[0] = { ...blocks[0], brk: "chapter", chapterNo: "1", pov: defaultPov };
  }
  return blocks;
}

function toImport(blocks: Block[], approveDirectly: boolean): ManuscriptImportIn {
  const chapters: ManuscriptImportIn["chapters"] = [];
  let cur: ManuscriptImportIn["chapters"][number] | null = null;
  let paras: string[] = [];
  let sceneNo = 0;
  const flushScene = () => {
    if (cur && paras.length) {
      sceneNo += 1;
      cur.scenes.push({ scene_no: sceneNo, prose: paras.join("\n\n") });
    }
    paras = [];
  };
  blocks.forEach((b, i) => {
    if (b.brk === "chapter" || i === 0) {
      flushScene();
      cur = {
        chapter_no: Number(b.chapterNo) || chapters.length + 1,
        title: b.title.trim() || null,
        pov: b.pov.trim(),
        overwrite: b.overwrite,
        scenes: [],
      };
      chapters.push(cur);
      sceneNo = 0;
      paras = [b.text];
    } else if (b.brk === "scene") {
      flushScene();
      paras = [b.text];
    } else {
      paras.push(b.text);
    }
  });
  flushScene();
  return { approve_directly: approveDirectly, chapters };
}

export default function ManuscriptEditor({
  parsed,
  bookId,
  onImported,
}: {
  parsed: ParsedManuscriptOut;
  bookId: string;
  onImported: () => void;
}) {
  const data = useDeskData();
  const [blocks, setBlocks] = useState<Block[]>(() => buildBlocks(parsed, ""));
  const [bulkPov, setBulkPov] = useState("");
  const [importing, setImporting] = useState(false);
  const existing = new Set(parsed.existing_chapter_nos);

  const set = (i: number, patch: Partial<Block>) =>
    setBlocks((bs) => bs.map((b, j) => (j === i ? { ...b, ...patch } : b)));

  const setBrk = (i: number, brk: Brk) =>
    set(
      i,
      brk === "chapter"
        ? { brk, chapterNo: blocks[i].chapterNo || "", pov: blocks[i].pov || bulkPov }
        : { brk },
    );

  const applyPovToAll = () =>
    setBlocks((bs) => bs.map((b) => (b.brk === "chapter" ? { ...b, pov: bulkPov.trim() } : b)));

  const counts = useMemo(() => {
    const chapters = blocks.filter((b) => b.brk === "chapter").length;
    const scenes = blocks.filter((b) => b.brk === "chapter" || b.brk === "scene").length;
    return { chapters, scenes };
  }, [blocks]);

  const doImport = async () => {
    setImporting(true);
    try {
      const report = await api.importManuscript(bookId, toImport(blocks, false));
      const skipped = report.skipped_conflicts.length
        ? `; skipped ch ${report.skipped_conflicts.join(", ")} (tick overwrite to replace)`
        : "";
      data.pushToast({
        tone: report.scenes_imported > 0 ? "success" : "warn",
        message: `Imported ${report.scenes_imported} scene${report.scenes_imported === 1 ? "" : "s"} into review${skipped}`,
      });
      await data.refreshAll();
      onImported();
    } catch (e) {
      data.pushToast({ tone: "error", message: e instanceof Error ? e.message : "import failed" });
    } finally {
      setImporting(false);
    }
  };

  const label = css("font-family:var(--mono);font-size:10px;color:var(--dim)");
  const input = css(
    "background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:12.5px;font-family:var(--ui)",
  );

  return (
    <div style={css("display:flex;flex-direction:column;gap:8px")}>
      <div style={css("display:flex;gap:10px;align-items:center;flex-wrap:wrap")}>
        <span style={css("font-size:12.5px;color:var(--ink)")}>
          {counts.chapters} chapter{counts.chapters === 1 ? "" : "s"} · {counts.scenes} scene
          {counts.scenes === 1 ? "" : "s"}
        </span>
        <span style={label}>click a divider to change where chapters and scenes split</span>
        <span style={css("flex:1")} />
        <input
          aria-label="default POV"
          value={bulkPov}
          onChange={(e) => setBulkPov(e.target.value)}
          placeholder="default POV"
          style={css(`${input};width:130px`)}
        />
        <Button size="sm" variant="ghost" onClick={applyPovToAll}>
          apply POV to all
        </Button>
      </div>

      {parsed.warnings.map((w, i) => (
        <span key={i} style={css("font-size:11px;color:var(--warn)")}>
          ⚠ {w}
        </span>
      ))}

      <div
        style={css(
          "display:flex;flex-direction:column;border:1px solid var(--line);border-radius:9px;background:var(--bg2);max-height:460px;overflow-y:auto",
        )}
      >
        {blocks.map((b, i) => {
          const conflict = b.brk === "chapter" && existing.has(Number(b.chapterNo));
          return (
            <div key={b.key}>
              {/* boundary control */}
              {b.brk === "chapter" ? (
                <div
                  style={css(
                    "display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:9px 11px;background:var(--bg2b);border-top:1px solid var(--accentLine);border-bottom:1px solid var(--accentLine)",
                  )}
                >
                  <strong style={css("font-size:11px;color:var(--accent);letter-spacing:.04em")}>
                    CHAPTER
                  </strong>
                  <input
                    aria-label="chapter number"
                    value={b.chapterNo}
                    onChange={(e) => set(i, { chapterNo: e.target.value.replace(/[^0-9]/g, "") })}
                    style={css(`${input};width:52px`)}
                  />
                  <input
                    aria-label="chapter title"
                    value={b.title}
                    onChange={(e) => set(i, { title: e.target.value })}
                    placeholder="title (optional)"
                    style={css(`${input};flex:1 1 140px`)}
                  />
                  <input
                    aria-label="chapter POV"
                    value={b.pov}
                    onChange={(e) => set(i, { pov: e.target.value })}
                    placeholder="POV (optional)"
                    style={css(`${input};width:120px`)}
                  />
                  {conflict && (
                    <label
                      style={css(
                        "display:flex;gap:4px;align-items:center;font-family:var(--mono);font-size:10px;color:var(--bad);cursor:pointer",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={b.overwrite}
                        onChange={(e) => set(i, { overwrite: e.target.checked })}
                      />
                      overwrite existing
                    </label>
                  )}
                  {i > 0 && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => setBrk(i, "scene")}>
                        → scene
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setBrk(i, "none")}>
                        merge ↑
                      </Button>
                    </>
                  )}
                </div>
              ) : b.brk === "scene" ? (
                <div style={css("display:flex;gap:6px;align-items:center;padding:5px 11px")}>
                  <span style={css("flex:1;height:1px;background:var(--line)")} />
                  <span style={label}>scene break</span>
                  <Button size="sm" variant="ghost" onClick={() => setBrk(i, "chapter")}>
                    → chapter
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setBrk(i, "none")}>
                    remove
                  </Button>
                  <span style={css("flex:1;height:1px;background:var(--line)")} />
                </div>
              ) : (
                <div
                  style={css(
                    "display:flex;gap:6px;align-items:center;justify-content:center;padding:2px 11px;opacity:.6",
                  )}
                >
                  <Button size="sm" variant="ghost" onClick={() => setBrk(i, "scene")}>
                    + scene break
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setBrk(i, "chapter")}>
                    + chapter
                  </Button>
                </div>
              )}

              {/* paragraph text */}
              <p
                style={css(
                  "margin:0;padding:4px 12px 8px;font-size:12px;line-height:1.55;color:var(--ink2)",
                )}
              >
                {b.text.length > 260 ? `${b.text.slice(0, 260)}…` : b.text}
              </p>
            </div>
          );
        })}
      </div>

      <div style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}>
        <Button
          size="sm"
          variant="primary"
          disabled={importing || counts.scenes === 0}
          onClick={() => void doImport()}
        >
          {importing
            ? "Importing…"
            : `Import ${counts.scenes} scene${counts.scenes === 1 ? "" : "s"} for review`}
        </Button>
        {blocks.some(
          (b) => b.brk === "chapter" && existing.has(Number(b.chapterNo)) && !b.overwrite,
        ) && (
          <Chip tone="bad" label="colliding chapters will be skipped — tick overwrite to replace" />
        )}
      </div>
    </div>
  );
}
