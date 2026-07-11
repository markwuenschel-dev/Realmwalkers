"use client";

// Manuscript uploader — drop files, see the best-effort split, and import it into the review inbox.
// The preview is not yet editable (boundary-correcting arrives in a later slice), but you can set a
// default POV and import as-is; colliding chapters are skipped. See GitHub #202 (parse), #203 (import).
import { useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { useDeskData } from "../api/data";
import { Button, Chip } from "./ui";
import type { ManuscriptImportIn, ParsedManuscriptOut } from "../api/types";

export default function ManuscriptUploader({ bookId }: { bookId: string }) {
  const data = useDeskData();
  const [parsed, setParsed] = useState<ParsedManuscriptOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [defaultPov, setDefaultPov] = useState("");
  const [importing, setImporting] = useState(false);

  const doImport = async () => {
    if (!parsed) return;
    setImporting(true);
    try {
      const payload: ManuscriptImportIn = {
        approve_directly: false, // Slice 2: always land in review; the toggle arrives in a later slice
        chapters: parsed.chapters.map((c) => ({
          chapter_no: c.chapter_no,
          title: c.title,
          pov: defaultPov.trim(),
          overwrite: false, // conflicts are refused-and-reported; per-chapter overwrite comes later
          scenes: c.scenes.map((s) => ({ scene_no: s.scene_no, prose: s.prose })),
        })),
      };
      const report = await api.importManuscript(bookId, payload);
      const skipped = report.skipped_conflicts.length
        ? `; skipped ch ${report.skipped_conflicts.join(", ")} (already exist)`
        : "";
      data.pushToast({
        tone: report.scenes_imported > 0 ? "success" : "warn",
        message: `Imported ${report.scenes_imported} scene${report.scenes_imported === 1 ? "" : "s"} into review${skipped}`,
      });
      await data.refreshAll();
      setParsed(null);
    } catch (e) {
      data.pushToast({ tone: "error", message: e instanceof Error ? e.message : "import failed" });
    } finally {
      setImporting(false);
    }
  };

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const files = await Promise.all(
        Array.from(fileList).map(async (f) => ({ filename: f.name, text: await f.text() })),
      );
      setParsed(await api.parseManuscript(bookId, files));
    } catch (e) {
      setError(e instanceof Error ? e.message : "parse failed");
    } finally {
      setBusy(false);
    }
  };

  const sceneCount = (parsed?.chapters ?? []).reduce((n, c) => n + c.scenes.length, 0);

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
        style={css(
          `display:flex;flex-direction:column;align-items:center;gap:8px;padding:22px;border-radius:10px;text-align:center;` +
            `border:1.5px dashed ${dragging ? "var(--accent)" : "var(--line)"};background:${dragging ? "var(--bg2b)" : "var(--bg2)"}`,
        )}
      >
        <span style={css("font-size:13px;color:var(--ink)")}>
          Drop manuscript files here — whole-chapter or multi-scene
        </span>
        <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
          .md / .txt · multiple files ok · review the split, then import to the inbox
        </span>
        <label
          style={css(
            "font-family:var(--mono);font-size:11px;color:var(--accent);cursor:pointer;padding:2px 0",
          )}
        >
          <input
            type="file"
            multiple
            accept=".md,.txt,text/markdown,text/plain"
            style={css("display:none")}
            onChange={(e) => void handleFiles(e.target.files)}
          />
          or choose files…
        </label>
      </div>

      {busy && <span style={css("font-size:12px;color:var(--dim)")}>Parsing…</span>}
      {error && <span style={css("font-size:12px;color:var(--bad)")}>Parse failed: {error}</span>}

      {parsed && (
        <div style={css("display:flex;flex-direction:column;gap:10px")}>
          <div style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}>
            <span style={css("font-size:12.5px;color:var(--ink)")}>
              Detected {parsed.chapters.length} chapter{parsed.chapters.length === 1 ? "" : "s"} ·{" "}
              {sceneCount} scene{sceneCount === 1 ? "" : "s"}
            </span>
            {parsed.existing_chapter_nos.length > 0 && (
              <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                book already has ch {parsed.existing_chapter_nos.join(", ")}
              </span>
            )}
          </div>

          {parsed.warnings.map((w, i) => (
            <span key={i} style={css("font-size:11.5px;color:var(--warn)")}>
              ⚠ {w}
            </span>
          ))}

          {parsed.chapters.map((c, ci) => (
            <div
              key={ci}
              style={css(
                "display:flex;flex-direction:column;gap:6px;border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--bg2)",
              )}
            >
              <div style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}>
                <strong style={css("font-size:12.5px;color:var(--ink)")}>
                  Chapter {c.chapter_no}
                  {c.title ? ` — ${c.title}` : ""}
                </strong>
                <Chip
                  tone={c.detected ? "neutral" : "warn"}
                  label={c.detected ? "header found" : "no header — inferred"}
                />
                {c.conflict && (
                  <Chip tone="bad" label={`collides with existing ch ${c.chapter_no}`} />
                )}
                <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                  {c.scenes.length} scene{c.scenes.length === 1 ? "" : "s"}
                </span>
              </div>

              {c.warnings.map((w, wi) => (
                <span key={wi} style={css("font-size:11px;color:var(--warn)")}>
                  ⚠ {w}
                </span>
              ))}

              {c.scenes.map((s) => (
                <div
                  key={s.scene_no}
                  style={css(
                    "display:flex;flex-direction:column;gap:2px;border-left:2px solid var(--accentLine);padding:2px 0 2px 10px",
                  )}
                >
                  <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                    Scene {s.scene_no} · {s.word_count} words
                  </span>
                  <span style={css("font-size:11.5px;color:var(--ink2);line-height:1.5")}>
                    {s.prose.slice(0, 180)}
                    {s.prose.length > 180 ? "…" : ""}
                  </span>
                </div>
              ))}
            </div>
          ))}

          <div
            style={css("display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:4px")}
          >
            <label style={css("display:flex;flex-direction:column;gap:3px")}>
              <span style={css("font-family:var(--mono);font-size:10px;color:var(--dim)")}>
                default POV (optional)
              </span>
              <input
                value={defaultPov}
                onChange={(e) => setDefaultPov(e.target.value)}
                placeholder="Character name"
                style={css(
                  "width:160px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12.5px;font-family:var(--ui)",
                )}
              />
            </label>
            <Button
              size="sm"
              variant="primary"
              disabled={importing || sceneCount === 0}
              onClick={() => void doImport()}
            >
              {importing
                ? "Importing…"
                : `Import ${sceneCount} scene${sceneCount === 1 ? "" : "s"} for review`}
            </Button>
            {parsed.chapters.some((c) => c.conflict) && (
              <span style={css("font-family:var(--mono);font-size:10px;color:var(--warn)")}>
                colliding chapters will be skipped (overwrite comes in a later step)
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
