"use client";

// Composer — the chapters a read-through will read, exactly as the author supplies them.
//
// Files come in through `readTextFiles` (extension-filtered on drop AND pick, since `accept` only
// filters the picker), pasted chapters through a label + textarea. The estimate is local arithmetic,
// not a quote: one model call per chapter plus one cross-chapter call when there are two or more, and
// at most one fallback attempt per call.

import { useState } from "react";
import { css } from "../../css";
import { LABEL_MAX, labelFor, readTextFiles } from "../../lib/readTextFiles";
import { formatCount } from "../../lib/readThroughMarkdown";
import { Button, Eyebrow, Panel } from "../ui";

export interface DraftChapter {
  key: string;
  label: string;
  text: string;
  /** Where the text came from: the filename, or "pasted". */
  source: string;
}

let keySeq = 0;
const nextKey = (): string => `draft-${++keySeq}`;

export const countWords = (text: string): number => {
  const t = text.trim();
  return t ? t.split(/\s+/).length : 0;
};

const plural = (n: number, one: string): string => `${formatCount(n)} ${n === 1 ? one : `${one}s`}`;

export function estimateLine(chapters: readonly DraftChapter[]): string {
  const n = chapters.length;
  const words = chapters.reduce((sum, c) => sum + countWords(c.text), 0);
  const calls = n + (n >= 2 ? 1 : 0);
  const fallbacks = calls; // one fallback attempt per call, at most
  return (
    `${plural(n, "chapter")} · ${plural(words, "word")} · baseline ${plural(calls, "model call")} ` +
    `(+ up to ${plural(fallbacks, "fallback attempt")})`
  );
}

export function chapterProblem(c: DraftChapter): string | null {
  if (!c.label.trim()) return "needs a label";
  if (c.label.trim().length > LABEL_MAX) return `label is over ${LABEL_MAX} characters`;
  if (!c.text.trim()) return "has no text";
  return null;
}

export type DraftUpdate = (prev: DraftChapter[]) => DraftChapter[];

const FIELD =
  "width:100%;background:var(--bg3);border:1px solid var(--line);border-radius:var(--r);" +
  "padding:7px 10px;color:var(--ink);font-family:var(--ui);font-size:13.5px";
const MONO = "font-family:var(--mono);font-size:11px;color:var(--dim)";

export default function Composer({
  chapters,
  onChange,
  onRun,
  submitting,
  blocked,
  error,
}: {
  chapters: DraftChapter[];
  /** Every composition change goes through here — the screen mints a new request id for each. */
  onChange: (update: DraftUpdate) => void;
  onRun: () => void;
  submitting: boolean;
  /** Why a run cannot start right now (e.g. one is already active), or null. */
  blocked: string | null;
  error: string | null;
}) {
  const [dragging, setDragging] = useState(false);
  const [reading, setReading] = useState(false);
  const [rejected, setRejected] = useState<string[]>([]);
  const [readError, setReadError] = useState<string | null>(null);
  const [pasteLabel, setPasteLabel] = useState("");
  const [pasteText, setPasteText] = useState("");

  const problems = chapters.map(chapterProblem);
  const canRun =
    chapters.length > 0 && problems.every((p) => p === null) && !submitting && !blocked;

  async function addFiles(files: File[]) {
    if (files.length === 0) return;
    setReading(true);
    setReadError(null);
    try {
      const { accepted, rejected: skipped } = await readTextFiles(files);
      setRejected(skipped);
      if (accepted.length > 0) {
        onChange((prev) => [
          ...prev,
          ...accepted.map((f) => ({
            key: nextKey(),
            label: f.label,
            text: f.text,
            source: f.filename,
          })),
        ]);
      }
    } catch (e) {
      setReadError(e instanceof Error ? e.message : String(e));
    } finally {
      setReading(false);
    }
  }

  function addPasted() {
    const text = pasteText;
    if (!text.trim()) return;
    const typed = pasteLabel.trim();
    onChange((prev) => [
      ...prev,
      {
        key: nextKey(),
        label: (typed || labelFor(`Pasted chapter ${prev.length + 1}`, text)).slice(0, LABEL_MAX),
        text,
        source: "pasted",
      },
    ]);
    setPasteLabel("");
    setPasteText("");
  }

  const move = (index: number, delta: number) =>
    onChange((prev) => {
      const to = index + delta;
      if (to < 0 || to >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });

  return (
    <Panel eyebrow="New read-through" title="Chapters to read">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void addFiles(Array.from(e.dataTransfer.files));
        }}
        style={css(
          "display:flex;flex-direction:column;align-items:center;gap:6px;padding:18px;border-radius:10px;text-align:center;" +
            `border:1.5px dashed ${dragging ? "var(--accent)" : "var(--line)"};background:${dragging ? "var(--bg2b)" : "var(--bg3)"}`,
        )}
      >
        <span style={css("font-size:13px;color:var(--ink)")}>
          Drop chapter files here — one chapter per file
        </span>
        <span style={css(MONO)}>.md / .txt · the first # heading becomes the label</span>
        <label style={css(`${MONO};color:var(--accent);cursor:pointer;padding:2px 0`)}>
          <input
            type="file"
            multiple
            accept=".md,.txt,text/markdown,text/plain"
            style={css("display:none")}
            onChange={(e) => {
              // Copy before clearing: resetting the value lets the same file be picked again.
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              void addFiles(files);
            }}
          />
          or choose files…
        </label>
      </div>

      {reading && <p style={css(`${MONO};margin:8px 0 0`)}>Reading files…</p>}
      {rejected.length > 0 && (
        <p style={css("margin:8px 0 0;font-size:12.5px;color:var(--warn)")}>
          {`Skipped — not .md or .txt: ${rejected.join(", ")}`}
        </p>
      )}
      {readError && (
        <p style={css("margin:8px 0 0;font-size:12.5px;color:var(--bad)")}>
          {`Couldn't read a file: ${readError}`}
        </p>
      )}

      <div style={css("display:flex;flex-direction:column;gap:8px;margin-top:14px")}>
        <Eyebrow>Or paste a chapter</Eyebrow>
        <input
          value={pasteLabel}
          maxLength={LABEL_MAX}
          onChange={(e) => setPasteLabel(e.target.value)}
          placeholder="Chapter label (optional)"
          style={css(FIELD)}
        />
        <textarea
          value={pasteText}
          onChange={(e) => setPasteText(e.target.value)}
          placeholder="Paste a chapter here"
          rows={5}
          spellCheck={false}
          style={css(
            `${FIELD};resize:vertical;font-family:var(--prose,var(--ui));font-size:14px;line-height:1.6`,
          )}
        />
        <div>
          <Button size="sm" disabled={!pasteText.trim()} onClick={addPasted}>
            Add pasted chapter
          </Button>
        </div>
      </div>

      {chapters.length > 0 && (
        <ol
          style={css(
            "list-style:none;margin:16px 0 0;padding:0;display:flex;flex-direction:column;gap:6px",
          )}
        >
          {chapters.map((c, i) => (
            <li
              key={c.key}
              style={css(
                "display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 8px;" +
                  "border:1px solid var(--line);border-radius:var(--r);background:var(--bg3)",
              )}
            >
              <span style={css(`${MONO};width:22px;text-align:right`)}>{i + 1}</span>
              <input
                aria-label={`Label for chapter ${i + 1}`}
                value={c.label}
                maxLength={LABEL_MAX}
                onChange={(e) => {
                  const label = e.target.value;
                  onChange((prev) => prev.map((x) => (x.key === c.key ? { ...x, label } : x)));
                }}
                style={css(`${FIELD};flex:1;min-width:180px;width:auto`)}
              />
              <span style={css(MONO)}>{`${c.source} · ${plural(countWords(c.text), "word")}`}</span>
              {problems[i] && (
                <span style={css("font-size:12px;color:var(--warn)")}>{problems[i]}</span>
              )}
              <Button
                size="sm"
                variant="ghost"
                title="Move up"
                disabled={i === 0}
                onClick={() => move(i, -1)}
              >
                ↑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                title="Move down"
                disabled={i === chapters.length - 1}
                onClick={() => move(i, 1)}
              >
                ↓
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onChange((prev) => prev.filter((x) => x.key !== c.key))}
              >
                Remove
              </Button>
            </li>
          ))}
        </ol>
      )}

      <div
        style={css(
          "display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:14px",
        )}
      >
        <span style={css(MONO)}>
          {chapters.length > 0 ? estimateLine(chapters) : "No chapters yet"}
        </span>
        <Button variant="primary" disabled={!canRun} onClick={onRun}>
          {submitting ? "Starting…" : "Run read-through"}
        </Button>
      </div>
      {blocked && <p style={css(`${MONO};margin:8px 0 0`)}>{blocked}</p>}
      {error && (
        <div
          role="alert"
          style={css(
            "margin-top:10px;border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));border-radius:var(--r);" +
              "background:color-mix(in srgb,var(--bad) 8%,transparent);padding:10px 14px;font-size:13px",
          )}
        >
          {error}
        </div>
      )}
    </Panel>
  );
}
