"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
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
import { Button, Chip, Eyebrow, MetricCard, Panel, ProgressBar } from "../components/ui";
import type { ChipTone } from "../components/ui";
import type { SceneOut } from "../api/types";
import type { ExportKind } from "../lib/docx";

export default function InboxScreen() {
  const { openSceneId, toggleActivity } = useDesk();
  const data = useDeskData();
  const router = useRouter();
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

  // "Plan a chapter" collapse: open by default ONLY on an empty book (no chapters yet). Once the
  // author clicks the header the explicit toggle wins over the default.
  const [plannerToggled, setPlannerToggled] = useState<boolean | null>(null);
  const plannerOpen = plannerToggled ?? data.chapters.length === 0;

  // Chapters progress strip: latest scenes grouped per chapter (approved vs drafted).
  const chapterRows = useMemo(() => {
    const byChapter = new Map<string, { total: number; done: number }>();
    for (const s of latest) {
      const row = byChapter.get(s.chapter_id) ?? { total: 0, done: 0 };
      row.total += 1;
      if (s.status === "approved") row.done += 1;
      byChapter.set(s.chapter_id, row);
    }
    return [...data.chapters]
      .sort((a, b) => a.chapter_no - b.chapter_no)
      .map((ch) => ({ chapter: ch, ...(byChapter.get(ch.id) ?? { total: 0, done: 0 }) }));
  }, [data.chapters, latest]);

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

  const sceneCard = (
    s: SceneOut,
    tone: ChipTone,
    tag: string,
    onClick?: () => void,
    select?: { checked: boolean; onToggle: () => void },
  ) => (
    <div key={s.id} onClick={onClick} style={css(onClick ? "cursor:pointer" : "")}>
      <Panel
        interactive={!!onClick}
        pad="13px 14px"
        style={`min-height:112px;display:flex;flex-direction:column;${select?.checked ? "outline:2px solid var(--accentLine);" : ""}${onClick ? "" : "opacity:.8"}`}
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
            style={css(
              "margin-left:auto;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
            )}
          >
            v{s.version}
          </span>
        </div>
        <div style={css("font-size:13.5px;color:var(--dim);line-height:1.45;margin-bottom:12px")}>
          {sceneLabel(s)}
        </div>
        <div
          style={css(
            "margin-top:auto;display:flex;align-items:center;justify-content:space-between;gap:8px",
          )}
        >
          <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
            {wordCount(s.prose)} words
          </span>
          <Chip label={tag} tone={tone} size="sm" />
        </div>
      </Panel>
    </div>
  );

  return (
    <div>
      <div style={css("margin-bottom:24px")}>
        <h1
          style={css(
            "margin:0 0 6px;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)",
          )}
        >
          Drafting desk
        </h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>
          {data.books.find((b) => b.id === data.bookId)?.title ?? "No book yet"} — plan a chapter,
          then judge what the Oracle drafts. Nothing runs until you ask it to.
        </p>
      </div>

      <div
        style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px")}
      >
        {stats.map((s) => (
          <MetricCard
            key={s.label}
            label={s.label}
            value={s.value}
            hint={[s.suffix, s.note].filter(Boolean).join(" · ") || undefined}
          />
        ))}
      </div>

      {/* 1 — the review queue, front and center. Select-all covers EVERY pending scene, not just
          the five visible cards, so bulk approve/export always operates on the whole queue. */}
      <Panel
        eyebrow="Needs your decision"
        title={`${data.pending.length} awaiting review`}
        style="margin-bottom:26px"
        actions={
          data.pending.length > 0 ? (
            <label
              style={css(
                "display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:10.5px;color:var(--dim);cursor:pointer",
              )}
            >
              <input
                type="checkbox"
                checked={data.pending.every((s) => sel.has(s.id))}
                onChange={() => sel.toggleAll(data.pending.map((s) => s.id))}
                style={css("width:14px;height:14px;cursor:pointer;accent-color:var(--accent)")}
              />
              select all {data.pending.length}
            </label>
          ) : undefined
        }
      >
        {data.pending.length === 0 ? (
          <Empty text="nothing to review" />
        ) : (
          <>
            <div
              style={css(
                "display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px",
              )}
            >
              {data.pending.slice(0, 5).map((s) =>
                // Navigate by scene ID (focused /scene/[id]), like the Chapters board and command
                // palette — NOT by pending-queue index. The old openScene(i) queue-index path could
                // land you on pending[0] (scene 1) whenever the index/queue-length didn't line up.
                sceneCard(s, "warn", "review →", () => openSceneId(s.id), {
                  checked: sel.has(s.id),
                  onToggle: () => sel.toggle(s.id),
                }),
              )}
            </div>
            {data.pending.length > 5 && (
              <div
                style={css(
                  "margin-top:10px;font-family:var(--mono);font-size:11px;color:var(--dim)",
                )}
              >
                +{data.pending.length - 5} more awaiting
              </div>
            )}
          </>
        )}
      </Panel>

      {/* 2 — the pipeline: live worker state, failures, queue summary + quick actions. */}
      <Panel
        eyebrow="Pipeline"
        style="margin-bottom:26px"
        actions={
          <>
            <span
              style={css(
                `font-family:var(--mono);font-size:11px;white-space:nowrap;color:${data.jobs.queue_paused ? "var(--warn)" : "var(--dim)"}`,
              )}
            >
              {data.jobs.queued} queued · {data.jobs.failed} failed
              {data.jobs.queue_paused ? " · paused" : ""}
            </span>
            <Button size="sm" onClick={() => void data.draftNext()}>
              Draft next
            </Button>
            <Button
              size="sm"
              variant="ghost"
              title={data.jobs.queue_paused ? "resume the draft queue" : "pause the draft queue"}
              onClick={() => void data.setQueuePaused(!data.jobs.queue_paused)}
            >
              {data.jobs.queue_paused ? "Resume" : "Pause"}
            </Button>
            <Button size="sm" variant="ghost" onClick={toggleActivity}>
              Activity
            </Button>
          </>
        }
      >
        <div style={css("display:flex;flex-direction:column;gap:10px")}>
          <DraftPanel />
          <RetryFailedBanner />
          <ActivityFeed />
        </div>
      </Panel>

      {/* 3 — per-chapter progress strip (replaces the Revising/Approved kanban columns). */}
      <Panel
        eyebrow="Chapters"
        style="margin-bottom:26px"
        actions={
          revising.length > 0 ? (
            <Chip
              label={`Revising ${revising.length}`}
              tone="bad"
              title="scenes sent back for revision — open the chapters board"
              onClick={() => router.push("/chapters")}
            />
          ) : undefined
        }
      >
        {chapterRows.length === 0 ? (
          <Empty text="no chapters yet — plan one below" />
        ) : (
          <div style={css("display:flex;flex-direction:column;gap:2px")}>
            {chapterRows.map(({ chapter, total, done }) => (
              <button
                key={chapter.id}
                className="dk-navlink"
                onClick={() => router.push("/chapters")}
                title={`Ch ${chapter.chapter_no} — open the chapters board`}
                style={css(
                  "display:grid;grid-template-columns:minmax(150px,1fr) 2fr auto;align-items:center;gap:14px;width:100%;padding:8px 10px;border:none;border-radius:8px;background:transparent;cursor:pointer;text-align:left",
                )}
              >
                <span
                  style={css(
                    "font-size:13.5px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis",
                  )}
                >
                  Ch {chapter.chapter_no}
                  {chapter.title ? ` · ${chapter.title}` : ""}
                </span>
                <ProgressBar value={total > 0 ? done / total : 0} color="var(--good)" />
                <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                  {done}/{total}
                </span>
              </button>
            ))}
          </div>
        )}
      </Panel>

      {/* 4 — the planner, tucked away once the book has chapters. */}
      <Panel pad="12px 18px" style="margin-bottom:26px">
        <button
          onClick={() => setPlannerToggled(!plannerOpen)}
          aria-expanded={plannerOpen}
          style={css(
            "display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;background:none;border:none;padding:4px 2px;cursor:pointer;text-align:left",
          )}
        >
          <Eyebrow tone="var(--ink)">Plan a chapter</Eyebrow>
          <span aria-hidden style={css("color:var(--dim);font-size:11px")}>
            {plannerOpen ? "▾" : "▸"}
          </span>
        </button>
        {plannerOpen && (
          <div style={css("margin-top:12px")}>
            <Planner />
          </div>
        )}
      </Panel>

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

// Shown in the Pipeline panel whenever jobs have FAILED — re-queues them to draft again. A scene
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
