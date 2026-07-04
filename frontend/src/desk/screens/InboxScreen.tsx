"use client";

import { useMemo, useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { sceneLabel, wordCount } from "../lib/format";
import { buildScenesMarkdown, downloadMarkdown, type SceneExportItem } from "../lib/sceneMarkdown";
import { resolveAuthorName, useAuthorName } from "../lib/authorName";
import { useSelection } from "../lib/useSelection";
import { useTabLoadTiming } from "../lib/useTabLoadTiming";
import Planner from "../components/Planner";
import BulkBar, { BulkButton } from "../components/BulkBar";
import { ActivityFeed, DraftPanel, formatElapsed } from "../components/DraftActivity";
import ClearFailedPanel from "../components/ClearFailedPanel";
import type { SceneOut } from "../api/types";
import type { ExportKind } from "../lib/docx";

export default function InboxScreen() {
  const { t, openScene, openSceneId } = useDesk();
  const data = useDeskData();
  // Tab-switch cost, visible in the console (provider data is cached, so revisits log ~0ms).
  useTabLoadTiming("inbox", !data.loading);
  const sel = useSelection();
  const [reviseMode, setReviseMode] = useState(false);
  const [note, setNote] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [exportingAs, setExportingAs] = useState<ExportKind | null>(null);
  // Shared with every other export surface (Manuscript, Scene, Chapters, Packets) so it's typed once.
  const [author, saveAuthor] = useAuthorName();
  const clearSel = () => {
    sel.clear();
    setReviseMode(false);
    setNote("");
  };

  // Bulk export: pending scenes' detail/feedback aren't loaded (only the active scene's are), so pull
  // each one's critiques + annotations + suggestions on demand and bundle them into one Markdown file.
  const downloadSelected = async () => {
    if (downloading || sel.count === 0) return;
    setDownloading(true);
    try {
      const items: SceneExportItem[] = await Promise.all(
        sel.ids.map(async (id) => {
          const [scene, annotations, suggestions] = await Promise.all([
            api.scene(id),
            api.annotations(id),
            api.suggestions(id),
          ]);
          return {
            scene,
            chapter: data.chapters.find((c) => c.id === scene.chapter_id) ?? null,
            annotations,
            suggestions,
          };
        }),
      );
      items.sort(
        (a, b) =>
          (a.chapter?.chapter_no ?? 0) - (b.chapter?.chapter_no ?? 0) ||
          a.scene.scene_no - b.scene.scene_no,
      );
      downloadMarkdown(`proposed_scenes_${items.length}.md`, buildScenesMarkdown(items));
      clearSel();
    } catch (e) {
      window.alert(`Couldn't export: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDownloading(false);
    }
  };

  // Manuscript-style bulk export: the same Markdown / Reader-DOCX / Shunn-DOCX builders the Manuscript
  // tab uses, fed the selected pending scenes grouped back into their chapters — so the output is
  // byte-for-byte the same format no matter which screen produced it. Unlike downloadSelected above,
  // this needs no extra API calls: data.pending already carries every selected scene's prose.
  const selectedChapters = () => {
    const chosen = data.pending.filter((s) => sel.has(s.id));
    const byChapter = new Map<string, typeof chosen>();
    for (const s of chosen)
      byChapter.set(s.chapter_id, [...(byChapter.get(s.chapter_id) ?? []), s]);
    return [...byChapter.entries()].map(([chapterId, scenes]) => {
      const ch = data.chapters.find((c) => c.id === chapterId);
      return {
        chapter_no: ch?.chapter_no ?? 0,
        title: ch?.title ?? null,
        pov: ch?.pov ?? "",
        scenes: scenes.map((s) => ({ scene_no: s.scene_no, prose: s.prose })),
      };
    });
  };
  const selectedTitle = () =>
    `${data.books.find((b) => b.id === data.bookId)?.title ?? "Manuscript"} — selected scenes`;

  const exportSelectedMarkdown = async () => {
    const chapters = selectedChapters();
    if (chapters.length === 0) return;
    setExportingAs("md");
    try {
      const exp = await import("../lib/docx");
      const ms = exp.buildManuscriptFrom(selectedTitle(), chapters);
      exp.saveMarkdown(
        exp.buildManuscriptMarkdown(ms),
        exp.markdownFilename(`selected_scenes_${sel.count}`),
      );
    } finally {
      setExportingAs(null);
    }
  };

  const exportSelectedDocx = async () => {
    const chapters = selectedChapters();
    if (chapters.length === 0) return;
    setExportingAs("docx");
    try {
      const exp = await import("../lib/docx");
      const ms = exp.buildManuscriptFrom(selectedTitle(), chapters);
      await exp.saveDocx(
        exp.buildManuscriptDoc(ms, "selected scenes"),
        exp.docxFilename(`selected_scenes_${sel.count}`),
      );
    } finally {
      setExportingAs(null);
    }
  };

  const exportSelectedShunn = async () => {
    const chapters = selectedChapters();
    if (chapters.length === 0) return;
    setExportingAs("shunn");
    try {
      const exp = await import("../lib/docx");
      const ms = exp.buildManuscriptFrom(selectedTitle(), chapters);
      const name = resolveAuthorName(author, saveAuthor);
      if (!name) return;
      await exp.saveDocx(
        exp.buildShunnDoc(ms, name, exp.manuscriptWordCount(ms)),
        exp.docxFilename(`selected_scenes_${sel.count}_shunn`),
      );
    } finally {
      setExportingAs(null);
    }
  };

  const latest = data.latestScenes;
  const approved = latest.filter((s) => s.status === "approved");
  const revising = latest.filter((s) => s.status === "revision_requested");

  // Word-count the whole assembled manuscript once per manuscript change, not on every Inbox render
  // (this flatMaps + counts every approved scene's prose — costly on a long book).
  const manuscriptWords = useMemo(
    () =>
      (data.manuscript?.chapters ?? [])
        .flatMap((c) => c.scenes)
        .reduce((acc, s) => acc + wordCount(s.prose), 0),
    [data.manuscript],
  );

  const stats = [
    { label: "Manuscript", value: manuscriptWords.toLocaleString(), suffix: "words" },
    {
      label: "Scenes approved",
      value: String(approved.length),
      suffix: `/ ${latest.length || 0} drafted`,
    },
    {
      label: "Awaiting you",
      value: String(data.pending.length),
      suffix: "scenes",
      note: data.pending.length ? "ready for review" : "queue clear",
    },
    {
      label: "Drafting",
      value: data.jobs.running ? "1" : "0",
      suffix: data.jobs.running ? "in progress" : "idle",
      note: data.jobs.running
        ? [data.jobs.active_scene?.phase, formatElapsed(data.jobs.active_scene?.elapsed_s)]
            .filter(Boolean)
            .join(" · ") || undefined
        : data.jobs.queued
          ? `${data.jobs.queued} queued`
          : data.jobs.failed
            ? `${data.jobs.failed} failed`
            : undefined,
    },
  ];

  const cardBase =
    "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:16px 17px;min-height:118px;display:flex;flex-direction:column";
  const sceneCard = (
    s: SceneOut,
    color: string,
    tag: string,
    onClick?: () => void,
    select?: { checked: boolean; onToggle: () => void },
  ) => (
    <div
      key={s.id}
      onClick={onClick}
      style={css(
        `${cardBase};border-left:3px solid ${select?.checked ? color : color};${select?.checked ? "outline:2px solid " + color + ";" : ""}${onClick ? "cursor:pointer;box-shadow:var(--shadow)" : "opacity:.8"}`,
      )}
    >
      <div style={css("display:flex;align-items:center;gap:9px;margin-bottom:9px")}>
        {select && (
          <input
            type="checkbox"
            checked={select.checked}
            onClick={(e) => e.stopPropagation()}
            onChange={select.onToggle}
            style={css(
              "width:15px;height:15px;cursor:pointer;flex:none;accent-color:var(--accent)",
            )}
          />
        )}
        <span style={css("font-family:var(--display);font-size:16.5px;color:var(--ink)")}>
          Scene {s.scene_no}
        </span>
        <span
          style={css("margin-left:auto;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
        >
          v{s.version}
        </span>
      </div>
      <div style={css("font-size:13.5px;color:var(--dim);line-height:1.45;margin-bottom:12px")}>
        {sceneLabel(s)}
      </div>
      <div
        style={css(
          "margin-top:auto;display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
        )}
      >
        <span>{wordCount(s.prose)} words</span>
        <span style={css(`color:${color}`)}>{tag}</span>
      </div>
    </div>
  );

  return (
    <div>
      <div style={css("margin-bottom:24px")}>
        <h1
          style={css(
            "margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)",
          )}
        >
          Drafting desk
        </h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>
          {data.books.find((b) => b.id === data.bookId)?.title ?? "No book yet"} — plan a chapter,
          then judge what the Oracle drafts. Nothing runs until you ask it to.
        </p>
      </div>

      <Planner />

      <div
        style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px")}
      >
        {stats.map((s) => (
          <div
            key={s.label}
            style={css(
              "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px",
            )}
          >
            <div
              style={css(
                "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:10px",
              )}
            >
              {s.label}
            </div>
            <div
              style={css(
                "font-family:var(--display);font-size:27px;color:var(--ink);line-height:1",
              )}
            >
              {s.value}
              <span style={css("font-size:14px;color:var(--dim)")}> {s.suffix}</span>
            </div>
            {s.note && (
              <div
                style={css(
                  "font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:9px",
                )}
              >
                {s.note}
              </div>
            )}
          </div>
        ))}
      </div>

      <div
        style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:start")}
      >
        {/* Drafting (live worker state) */}
        <div>
          <Column title="Drafting" color={t.info} count={data.jobs.running ? 1 : 0} />
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            <DraftPanel />
            <RetryFailedBanner />
            <ActivityFeed />
          </div>
        </div>

        {/* Awaiting review (pending queue) */}
        <div>
          <Column title="Awaiting review" color={t.warn} count={data.pending.length} />
          {data.pending.length > 0 && (
            <label
              style={css(
                "display:flex;align-items:center;gap:7px;margin:0 2px 9px;font-family:var(--mono);font-size:10.5px;color:var(--dim);cursor:pointer",
              )}
            >
              <input
                type="checkbox"
                checked={data.pending.every((s) => sel.has(s.id))}
                onChange={() => sel.toggleAll(data.pending.map((s) => s.id))}
                style={css("width:14px;height:14px;cursor:pointer;accent-color:var(--accent)")}
              />
              select all
            </label>
          )}
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {data.pending.length === 0 && <Empty text="nothing to review" />}
            {data.pending.map((s, i) =>
              sceneCard(s, t.warn, "review →", () => openScene(i), {
                checked: sel.has(s.id),
                onToggle: () => sel.toggle(s.id),
              }),
            )}
          </div>
        </div>

        {/* Revising */}
        <div>
          <Column title="Revising" color={t.bad} count={revising.length} />
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {revising.length === 0 && <Empty text="—" />}
            {revising.map((s) => sceneCard(s, t.bad, "redrafting", () => openSceneId(s.id)))}
          </div>
        </div>

        {/* Approved */}
        <div>
          <Column title="Approved" color={t.good} count={approved.length} />
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {approved.length === 0 && <Empty text="—" />}
            {approved
              .sort((a, b) => a.scene_no - b.scene_no)
              .map((s) => sceneCard(s, t.good, "edit →", () => openSceneId(s.id)))}
          </div>
        </div>
      </div>

      <BulkBar count={sel.count} noun="scene" onClear={clearSel}>
        {!reviseMode ? (
          <>
            <BulkButton
              tone="good"
              onClick={() => {
                void data.runBulk(sel.ids, (id) => api.decide(id, { decision: "approve" }), {
                  drainAfter: true,
                });
                clearSel();
              }}
            >
              Approve
            </BulkButton>
            <BulkButton onClick={() => setReviseMode(true)}>Request revision</BulkButton>
            <BulkButton
              disabled={downloading}
              onClick={() => {
                void downloadSelected();
              }}
            >
              {downloading ? "Exporting…" : "Download .md + feedback"}
            </BulkButton>
            <BulkButton
              disabled={exportingAs != null}
              onClick={() => {
                void exportSelectedMarkdown();
              }}
            >
              {exportingAs === "md" ? "Exporting…" : "Export Markdown"}
            </BulkButton>
            <BulkButton
              disabled={exportingAs != null}
              onClick={() => {
                void exportSelectedDocx();
              }}
            >
              {exportingAs === "docx" ? "Exporting…" : "Export Reader DOCX"}
            </BulkButton>
            <BulkButton
              disabled={exportingAs != null}
              onClick={() => {
                void exportSelectedShunn();
              }}
            >
              {exportingAs === "shunn" ? "Exporting…" : "Export Shunn DOCX"}
            </BulkButton>
            <BulkButton
              tone="bad"
              onClick={() => {
                if (
                  confirm(
                    `Delete ${sel.count} scene${sel.count === 1 ? "" : "s"}? This removes the draft and its review history.`,
                  )
                ) {
                  void data.deleteScenes(sel.ids);
                  clearSel();
                }
              }}
            >
              Delete
            </BulkButton>
          </>
        ) : (
          <>
            <input
              autoFocus
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="one revision note for all selected…"
              style={css(
                "width:260px;max-width:46vw;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:12.5px;font-family:var(--ui)",
              )}
            />
            <BulkButton
              tone="good"
              disabled={!note.trim()}
              onClick={() => {
                void data.runBulk(
                  sel.ids,
                  (id) => api.decide(id, { decision: "revise", feedback: note.trim() }),
                  { drainAfter: true },
                );
                clearSel();
              }}
            >
              Send {sel.count} to revise
            </BulkButton>
            <BulkButton onClick={() => setReviseMode(false)}>Cancel</BulkButton>
          </>
        )}
      </BulkBar>
    </div>
  );
}

function Column({ title, color, count }: { title: string; color: string; count: number }) {
  return (
    <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:11px;padding:0 2px")}>
      <span style={css(`width:8px;height:8px;border-radius:50%;background:${color}`)} />
      <span
        style={css(
          "font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink)",
        )}
      >
        {title}
      </span>
      <span style={css("margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)")}>
        {count}
      </span>
    </div>
  );
}

// Shown in the Drafting column whenever jobs have FAILED — re-queues them to draft again. A scene
// usually fails on a transient cause (API outage, depleted credits, a one-off 5xx); a FAILED job is
// terminal, so without this it would never redraft on its own.
function RetryFailedBanner() {
  const data = useDeskData();
  return (
    <ClearFailedPanel
      failedCount={data.jobs.failed}
      failedJobs={data.failedJobs}
      onRetry={() => data.retryFailed()}
      onClear={() => data.clearFailed()}
    />
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div
      style={css(
        "border:1px dashed var(--line);border-radius:10px;padding:16px;text-align:center;font-family:var(--mono);font-size:11px;color:var(--dim)",
      )}
    >
      {text}
    </div>
  );
}
