"use client";

// Manuscript uploader — Slice 1: drop files, see the best-effort split. READ-ONLY: this previews the
// detected chapter/scene structure and flags collisions, but does not import anything yet (boundary
// editing + import land in later slices). See GitHub #202.
import { useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { Chip } from "./ui";
import type { ParsedManuscriptOut } from "../api/types";

export default function ManuscriptUploader({ bookId }: { bookId: string }) {
  const [parsed, setParsed] = useState<ParsedManuscriptOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

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
          .md / .txt · multiple files ok · read-only preview (nothing is imported yet)
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
        </div>
      )}
    </div>
  );
}
