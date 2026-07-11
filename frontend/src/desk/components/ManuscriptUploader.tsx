"use client";

// Manuscript uploader — drop files, get a best-effort chapter/scene split, correct the boundaries in
// the annotated editor, and import into the review inbox. See GitHub #202 (parse), #203 (import),
// #204 (boundary editor).
import { useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import ManuscriptEditor from "./ManuscriptEditor";
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
          .md / .txt · multiple files ok · correct the split, then import to the inbox
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
        <ManuscriptEditor parsed={parsed} bookId={bookId} onImported={() => setParsed(null)} />
      )}
    </div>
  );
}
