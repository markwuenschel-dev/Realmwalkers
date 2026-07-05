"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { wordCount } from "../lib/format";
import { resolveAuthorName, useAuthorName } from "../lib/authorName";
import { severityLabel, severityVar } from "../lib/severity";
import { useSelection } from "../lib/useSelection";
import { useTabLoadTiming } from "../lib/useTabLoadTiming";
import BulkBar, { BulkButton } from "../components/BulkBar";
import GateDisclosure from "../components/GateDisclosure";
import { Button, Chip, Panel } from "../components/ui";
import type { ChipTone } from "../components/ui";
import type { ChaptersView } from "../types";
import type {
  ChapterOut,
  ChapterPipelineOut,
  ChapterUpdateIn,
  ManuscriptChapter,
  SceneOut,
} from "../api/types";
import type { ExportKind } from "../lib/docx";

const STATUS_COLORS: Record<string, "good" | "warn" | "bad" | "info" | "dim"> = {
  approved: "good",
  pending_review: "warn",
  revision_requested: "bad",
  draft: "info",
  superseded: "dim",
};

// Production-run status → tone (run-status vocabulary, distinct from scene status and severity).
const RUN_TONE: Record<string, ChipTone> = {
  completed: "good",
  approved: "good",
  repairing: "warn",
  waiting_for_human: "warn",
  waiting_for_scene_drafts: "warn",
  blocked: "bad",
  failed: "bad",
  cancelled: "neutral",
  running: "info",
  queued: "info",
};

// Canonical severity display order for the violation mini-chips.
const SEVERITY_ORDER = ["block", "repair", "warn", "info"];

export default function ChaptersScreen() {
  const desk = useDesk();
  const data = useDeskData();
  const router = useRouter();
  // Tab-switch cost, visible in the console (provider data is cached, so revisits log ~0ms).
  useTabLoadTiming("chapters", !data.loading);

  // Pipeline overview — one batched request for the whole book (packet state, contract/prose
  // coverage, violations, draft gate, latest run). NOT wired into the provider poll loop: it loads
  // on mount and refreshes after actions that change it, or via the ⟳ button. A fetch failure keeps
  // whatever rendered before; strips show a placeholder instead of blocking the panels.
  const [overview, setOverview] = useState<Map<string, ChapterPipelineOut> | null>(null);
  const loadOverview = useCallback(async () => {
    if (!data.bookId) return;
    try {
      const rows = await api.chaptersOverview(data.bookId);
      setOverview(new Map(rows.map((r) => [r.chapter_id, r])));
    } catch {
      setOverview((prev) => prev ?? new Map());
    }
  }, [data.bookId]);
  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  // current state of each (chapter, scene) — derived once in the data layer. A latest row can only be
  // `superseded` when the scene was rejected (revisions create a newer version, so a superseded row is
  // never the latest), so dropping them here is what makes a rejected scene vanish from the board.
  const latest = useMemo(
    () => data.latestScenes.filter((s) => s.status !== "superseded"),
    [data.latestScenes],
  );
  const scenesByChapter = (chapterId: string): SceneOut[] =>
    latest.filter((s) => s.chapter_id === chapterId).sort((a, b) => a.scene_no - b.scene_no);

  // Status → theme token (CSS var) and Chip tone. Colors stay tokenized so both Atelier variants
  // retint them for free.
  const colorOf = (status: string): string => `var(--${STATUS_COLORS[status] ?? "dim"})`;
  const toneOf = (status: string): ChipTone => {
    const c = STATUS_COLORS[status] ?? "dim";
    return c === "dim" ? "neutral" : c;
  };

  // Bulk re-draft: tick scenes on the board, re-queue a fresh draft for each (supersedes the version).
  const sel = useSelection();
  const redraftSelected = async () => {
    const byChapter = new Map<string, string[]>();
    for (const id of sel.ids) {
      const sc = latest.find((s) => s.id === id);
      if (sc) byChapter.set(sc.chapter_id, [...(byChapter.get(sc.chapter_id) ?? []), id]);
    }
    const queued = sel.count;
    sel.clear();
    await Promise.allSettled([...byChapter].map(([cid, ids]) => api.redraftScenes(cid, ids)));
    await data.draftNext();
    if (data.jobs.queue_paused) {
      data.pushToast({
        tone: "warn",
        message: `Queued ${queued} redraft${queued === 1 ? "" : "s"} — the queue is paused; they draft after you resume`,
      });
    }
    void loadOverview();
  };

  // Per-chapter export: the same Markdown / Reader-DOCX / Shunn-DOCX builders the Manuscript tab uses,
  // scoped to one chapter's approved scenes (data.manuscript is already the approved compile) — so the
  // output is byte-for-byte the same format no matter which screen produced it.
  const [author, saveAuthor] = useAuthorName();
  const [exportingChapter, setExportingChapter] = useState<{
    id: string;
    kind: ExportKind;
  } | null>(null);
  const manuscriptChapterFor = (chapterNo: number): ManuscriptChapter | null =>
    data.manuscript?.chapters.find((mc) => mc.chapter_no === chapterNo) ?? null;
  const exportChapter = async (c: ChapterOut, kind: ExportKind) => {
    const mc = manuscriptChapterFor(c.chapter_no);
    if (!mc) return;
    setExportingChapter({ id: c.id, kind });
    try {
      const exp = await import("../lib/docx");
      const title = `Chapter ${c.chapter_no}${c.title ? `: ${c.title}` : ""}`;
      const ms = exp.buildManuscriptFrom(title, [mc]);
      const stem = `chapter_${c.chapter_no}${c.title ? `_${c.title}` : ""}`;
      if (kind === "md") {
        exp.saveMarkdown(exp.buildManuscriptMarkdown(ms), exp.markdownFilename(stem));
      } else if (kind === "docx") {
        const bookTitle = data.books.find((b) => b.id === data.bookId)?.title;
        await exp.saveDocx(
          exp.buildManuscriptDoc(ms, bookTitle ? `from ${bookTitle}` : undefined),
          exp.docxFilename(stem),
        );
      } else {
        const name = resolveAuthorName(author, saveAuthor);
        if (!name) return;
        await exp.saveDocx(
          exp.buildShunnDoc(ms, name, exp.manuscriptWordCount(ms)),
          exp.docxFilename(`${stem}_shunn`),
        );
      }
    } finally {
      setExportingChapter(null);
    }
  };

  // Write a section by hand → an approved human-authored scene (flows into summaries + prior context).
  const [writeFor, setWriteFor] = useState<string | null>(null); // chapter id whose form is open
  const [sceneNo, setSceneNo] = useState("");
  const [prose, setProse] = useState("");
  const [busy, setBusy] = useState(false);
  const saveSection = async (chapterId: string) => {
    const n = Number(sceneNo);
    if (!Number.isFinite(n) || n < 1 || !prose.trim()) return;
    setBusy(true);
    try {
      await api.createHumanScene(chapterId, { scene_no: n, prose: prose.trim() });
      await data.refreshAll();
      setWriteFor(null);
      setSceneNo("");
      setProse("");
      void loadOverview();
    } finally {
      setBusy(false);
    }
  };

  const manuscriptWords = (data.manuscript?.chapters ?? [])
    .flatMap((c) => c.scenes)
    .reduce((acc, s) => acc + wordCount(s.prose), 0);
  const approvedScenes = latest.filter((s) => s.status === "approved").length;

  const chViewItems: { id: ChaptersView; label: string }[] = [
    { id: "board", label: "Board" },
    { id: "timeline", label: "Timeline" },
  ];

  // Timeline geometry: lanes (distinct POVs), the flattened scene order, and the grid template. Runs
  // on every chapter/scene change only — not on each poll tick or unrelated context update.
  const { lanes, ordered, tCols, tlGridStyle } = useMemo(() => {
    const lanes: string[] = [];
    for (const c of data.chapters) if (!lanes.includes(c.pov)) lanes.push(c.pov);
    const ordered = [...data.chapters]
      .sort((a, b) => a.chapter_no - b.chapter_no)
      .flatMap((c) =>
        latest
          .filter((s) => s.chapter_id === c.id)
          .sort((a, b) => a.scene_no - b.scene_no)
          .map((s) => ({ scene: s, chapter: c })),
      );
    const tCols = Math.max(1, ordered.length);
    const tlGridStyle = `display:grid;grid-template-columns:96px repeat(${tCols},minmax(56px,1fr));grid-template-rows:auto ${lanes.map(() => "70px").join(" ")};gap:0 8px;align-items:stretch`;
    return { lanes, ordered, tCols, tlGridStyle };
  }, [data.chapters, latest]);

  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:24px",
        )}
      >
        <div>
          <h1
            style={css(
              "margin:0 0 6px;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)",
            )}
          >
            Chapters & progress
          </h1>
          <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>
            Where each scene stands, and the order they compile in.
          </p>
        </div>
        <div style={css("display:flex;gap:6px")}>
          <Button
            size="sm"
            variant="ghost"
            title="Re-fetch the per-chapter pipeline facts (packet, contracts, production, gate)"
            onClick={() => void loadOverview()}
          >
            ⟳ Refresh pipeline
          </Button>
          {chViewItems.map((v) => {
            const active = desk.chaptersView === v.id;
            return (
              <Button
                key={v.id}
                size="sm"
                variant={active ? "primary" : "ghost"}
                onClick={() => desk.setChaptersView(v.id)}
              >
                {v.label}
              </Button>
            );
          })}
        </div>
      </div>

      <div
        style={css(
          "display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px;align-items:start;margin-bottom:30px",
        )}
      >
        <Panel eyebrow="Manuscript">
          <div
            style={css("font-family:var(--display);font-size:40px;line-height:1;color:var(--ink)")}
          >
            {manuscriptWords.toLocaleString()}
          </div>
          <div
            style={css("font-family:var(--mono);font-size:12px;color:var(--dim);margin-top:6px")}
          >
            words approved
          </div>
          <div style={css("height:1px;background:var(--line);margin:16px 0")} />
          <Row k="scenes approved" v={`${approvedScenes} / ${latest.length}`} />
          <Row k="chapters" v={`${data.chapters.length}`} />
          <Row
            k="awaiting you"
            v={`${data.pending.length} scenes`}
            accent={data.pending.length > 0}
          />
        </Panel>

        <Panel eyebrow="Pacing · per chapter" pad="20px 22px">
          <div style={css("display:flex;flex-direction:column;gap:18px")}>
            {data.chapters.length === 0 && (
              <div style={css("display:flex;align-items:center;gap:12px;flex-wrap:wrap")}>
                <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>
                  No chapters yet — plan one from the Inbox.
                </span>
                <Button size="sm" onClick={() => router.push("/inbox")}>
                  Go to Inbox
                </Button>
              </div>
            )}
            {[...data.chapters]
              .sort((a, b) => a.chapter_no - b.chapter_no)
              .map((c) => {
                const scs = scenesByChapter(c.id);
                const words = scs.reduce((acc, s) => acc + wordCount(s.prose), 0);
                const appr = scs.filter((s) => s.status === "approved").length;
                const frac = scs.length ? Math.round((appr / scs.length) * 100) : 0;
                return (
                  <div key={c.id}>
                    <div
                      style={css(
                        "display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:8px",
                      )}
                    >
                      <span
                        style={css(
                          "font-family:var(--display);font-size:15px;color:var(--ink);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap",
                        )}
                      >
                        Ch {c.chapter_no}
                        {c.title ? (
                          <span style={css("color:var(--ink)")}> · {c.title}</span>
                        ) : null}{" "}
                        <span
                          style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
                        >
                          {c.pov}
                        </span>
                      </span>
                      <span
                        style={css(
                          "font-family:var(--mono);font-size:11.5px;color:var(--dim);flex:none",
                        )}
                      >
                        {words.toLocaleString()} words · {appr}/{scs.length} approved
                      </span>
                    </div>
                    <div
                      style={css(
                        "position:relative;height:9px;border-radius:5px;background:var(--bg3);overflow:hidden",
                      )}
                    >
                      <div
                        style={css(
                          `position:absolute;inset:0;width:${frac}%;background:var(--good)`,
                        )}
                      />
                    </div>
                    {overview !== null && (
                      <ChapterPipelineStrip
                        row={overview.get(c.id) ?? null}
                        onOpenPackets={() => router.push(`/packets?chapter=${c.id}`)}
                        onOpenProduction={() => router.push(`/production?chapter=${c.id}`)}
                      />
                    )}
                    <ChapterMetaControls
                      chapter={c}
                      onSave={(patch) => void data.updateChapter(c.id, patch)}
                    />
                    <div style={css("margin-top:8px")}>
                      {writeFor === c.id ? (
                        <div
                          style={css(
                            "display:flex;flex-direction:column;gap:7px;border:1px solid var(--accentLine);border-radius:9px;padding:11px 12px;background:var(--bg2b)",
                          )}
                        >
                          <div
                            style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}
                          >
                            <input
                              type="number"
                              min={1}
                              value={sceneNo}
                              onChange={(e) => setSceneNo(e.target.value)}
                              placeholder="scene #"
                              style={css(
                                "width:90px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12.5px;font-family:var(--ui)",
                              )}
                            />
                            <span
                              style={css(
                                "font-family:var(--mono);font-size:10.5px;color:var(--dim)",
                              )}
                            >
                              approved on save; supersedes any existing version at this scene number
                            </span>
                          </div>
                          <textarea
                            value={prose}
                            onChange={(e) => setProse(e.target.value)}
                            placeholder="write the prose for this section…"
                            style={css(
                              "width:100%;min-height:120px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:9px 11px;font-size:13px;line-height:1.55;resize:vertical;font-family:var(--ui)",
                            )}
                          />
                          <div style={css("display:flex;gap:8px")}>
                            <Button
                              size="sm"
                              variant="primary"
                              disabled={busy || !sceneNo.trim() || !prose.trim()}
                              title={
                                busy
                                  ? "Saving…"
                                  : !sceneNo.trim()
                                    ? "Enter a scene number first"
                                    : !prose.trim()
                                      ? "Write the prose first"
                                      : "Save as an approved human-authored section"
                              }
                              onClick={() => void saveSection(c.id)}
                            >
                              {busy ? "Saving…" : "Save section"}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                setWriteFor(null);
                                setSceneNo("");
                                setProse("");
                              }}
                            >
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setWriteFor(c.id);
                            setSceneNo(String(scs.length + 1));
                            setProse("");
                          }}
                          style={css(
                            "font-family:var(--mono);font-size:11px;color:var(--dim);background:none;border:none;cursor:pointer;padding:2px 0",
                          )}
                        >
                          + Write section by hand
                        </button>
                      )}
                    </div>
                    <div style={css("margin-top:8px")}>
                      <ChapterExportLinks
                        chapter={c}
                        manuscriptChapter={manuscriptChapterFor(c.chapter_no)}
                        busy={exportingChapter?.id === c.id ? exportingChapter.kind : null}
                        onExport={(kind) => void exportChapter(c, kind)}
                      />
                    </div>
                  </div>
                );
              })}
          </div>
        </Panel>
      </div>

      {desk.chaptersView === "board" && (
        <>
          <div
            style={css(
              "display:flex;align-items:center;gap:14px;margin-bottom:14px;font-family:var(--mono);font-size:10.5px;color:var(--dim);flex-wrap:wrap",
            )}
          >
            <h2
              style={css(
                "margin:0;font-family:var(--display);font-weight:500;font-size:19px;color:var(--ink)",
              )}
            >
              Scene board
            </h2>
            <span style={css("display:flex;align-items:center;gap:5px")}>
              <span style={css("width:9px;height:9px;border-radius:2px;background:var(--good)")} />
              approved
            </span>
            <span style={css("display:flex;align-items:center;gap:5px")}>
              <span style={css("width:9px;height:9px;border-radius:2px;background:var(--warn)")} />
              awaiting
            </span>
            <span style={css("display:flex;align-items:center;gap:5px")}>
              <span style={css("width:9px;height:9px;border-radius:2px;background:var(--bad)")} />
              revising
            </span>
          </div>
          <div style={css("display:flex;flex-wrap:wrap;gap:12px")}>
            {latest.length === 0 && (
              <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>
                No scenes drafted yet.
              </span>
            )}
            {ordered.map(({ scene: s, chapter: c }) => (
              <div
                key={s.id}
                onClick={() => desk.openSceneId(s.id)}
                style={css("flex:1 1 168px;min-width:160px;cursor:pointer")}
              >
                <Panel interactive pad="13px 14px">
                  <div
                    style={css(
                      "display:flex;align-items:center;gap:6px;margin-bottom:9px;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={sel.has(s.id)}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => sel.toggle(s.id)}
                      title="select to re-draft"
                      style={css(
                        "width:13px;height:13px;cursor:pointer;accent-color:var(--accent)",
                      )}
                    />
                    <span>
                      Ch {c.chapter_no} · Scene {s.scene_no}
                    </span>
                    <span style={css("margin-left:auto")}>v{s.version}</span>
                  </div>
                  <div
                    style={css(
                      "display:flex;align-items:center;justify-content:space-between;gap:8px",
                    )}
                  >
                    <Chip label={s.status.replace(/_/g, " ")} tone={toneOf(s.status)} size="sm" />
                    <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                      {wordCount(s.prose)} words
                    </span>
                  </div>
                </Panel>
              </div>
            ))}
          </div>
        </>
      )}

      {desk.chaptersView === "timeline" && (
        <Panel pad="18px 20px" style="overflow-x:auto">
          {ordered.length === 0 ? (
            <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>
              No scenes to chart yet.
            </span>
          ) : (
            <div style={css(`${tlGridStyle};min-width:680px`)}>
              {lanes.map((ln, li) => (
                <div
                  key={`ll${li}`}
                  style={css(
                    `grid-column:1;grid-row:${2 + li};display:flex;align-items:center;font-family:var(--display);font-size:14px;color:var(--ink)`,
                  )}
                >
                  {ln}
                </div>
              ))}
              {lanes.map((_, li) => (
                <div
                  key={`lt${li}`}
                  style={css(
                    `grid-column:2 / span ${tCols};grid-row:${2 + li};align-self:center;height:2px;background:var(--line)`,
                  )}
                />
              ))}
              {ordered.map(({ scene: s, chapter: c }, i) => {
                const li = Math.max(0, lanes.indexOf(c.pov));
                const color = colorOf(s.status);
                return (
                  <div
                    key={s.id}
                    onClick={() => desk.openSceneId(s.id)}
                    style={css(
                      `grid-column:${2 + i};grid-row:${2 + li};align-self:center;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;height:50px;border-radius:9px;border:1px solid var(--line);border-top:3px solid ${color};background:var(--bg2);box-shadow:var(--shadow);cursor:pointer`,
                    )}
                  >
                    <span style={css("font-family:var(--mono);font-size:12px;color:var(--ink)")}>
                      C{c.chapter_no}·S{s.scene_no}
                    </span>
                    <span
                      style={css(`width:5px;height:5px;border-radius:50%;background:${color}`)}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </Panel>
      )}

      <BulkBar count={sel.count} noun="scene" onClear={sel.clear}>
        <BulkButton
          onClick={() => void redraftSelected()}
          title={
            data.jobs.queue_paused
              ? "Queue is paused — redrafts queue now and draft after you resume"
              : "Queue a fresh draft for each selected scene"
          }
        >
          Re-draft selected
        </BulkButton>
      </BulkBar>
    </div>
  );
}

// The per-chapter pipeline strip: packet → contracts+prose → production → draft gate, each segment
// a click away from the tab that acts on it. Renders from ONE batched overview row; `row` is null
// when the overview fetch failed — show a quiet placeholder, never block the chapter panels.
function ChapterPipelineStrip({
  row,
  onOpenPackets,
  onOpenProduction,
}: {
  row: ChapterPipelineOut | null;
  onOpenPackets: () => void;
  onOpenProduction: () => void;
}) {
  if (!row) {
    return (
      <div style={css("margin-top:8px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
        pipeline — (unavailable; use ⟳ Refresh pipeline)
      </div>
    );
  }

  const packetChip: { label: string; tone: ChipTone } =
    row.packet_approval_state == null
      ? { label: "no packet", tone: "neutral" }
      : row.packet_approval_state === "blocked"
        ? { label: "packet blocked", tone: "bad" }
        : row.packet_approval_state === "open_questions"
          ? { label: "open questions", tone: "warn" }
          : row.packet_approval_state === "already_approved"
            ? { label: "packet approved", tone: "good" }
            : { label: "packet proposed", tone: "info" };

  // Fold raw severity tokens (legacy "hard" → block) and sum, in canonical severity order.
  const folded = new Map<string, number>();
  for (const [sev, n] of Object.entries(row.violation_counts ?? {})) {
    const label = severityLabel(sev);
    folded.set(label, (folded.get(label) ?? 0) + (n ?? 0));
  }
  const severityRank = (label: string) => {
    const i = SEVERITY_ORDER.indexOf(label);
    return i === -1 ? SEVERITY_ORDER.length : i; // unknown tokens sort last
  };
  const violations = [...folded.entries()].sort((a, b) => severityRank(a[0]) - severityRank(b[0]));

  const run = row.latest_run;
  const gateRows = [
    {
      label: "Chapter packet · contract",
      pass: row.packet_approval_state === "already_approved",
      detail: row.packet_approval_state?.replace(/_/g, " ") ?? "no packet yet",
    },
    {
      label: "Scene contracts",
      pass: row.scene_packets_total > 0 && row.scene_packets_approved === row.scene_packets_total,
      detail:
        `${row.scene_packets_approved}/${row.scene_packets_total} approved` +
        (row.scene_packets_stale ? ` · ${row.scene_packets_stale} stale` : "") +
        (row.scene_packets_rate_limited ? ` · ${row.scene_packets_rate_limited} rate-limited` : ""),
    },
    {
      label: "Prose coverage · prose draft",
      pass: row.assembly_ready,
      detail: `${row.scenes_with_prose}/${row.expected_scenes} scenes have prose`,
    },
    {
      label: "Draft jobs",
      pass: row.active_draft_jobs === 0,
      detail:
        row.active_draft_jobs === 0
          ? "idle"
          : `${row.active_draft_jobs} active — scenes may still be arriving`,
    },
    {
      label: "Provider",
      pass: !row.provider_rate_limited,
      detail: row.provider_rate_limited ? "rate limited (429) — transient; retry shortly" : "ok",
    },
  ];

  const arrow = <span style={css("color:var(--dim);opacity:.5;flex:none")}>→</span>;
  return (
    <div style={css("margin-top:8px;display:flex;flex-direction:column;gap:6px")}>
      <div style={css("display:flex;align-items:center;gap:8px;flex-wrap:wrap")}>
        <Chip
          label={packetChip.label}
          tone={packetChip.tone}
          size="sm"
          onClick={onOpenPackets}
          title={
            row.packet_approval_blockers[0] ?? "Chapter packet — the contract drafting works from"
          }
        />
        {arrow}
        <span
          onClick={onOpenPackets}
          title="Scene contracts + prose coverage — opens the Packets tab"
          style={css(
            "cursor:pointer;font-family:var(--mono);font-size:10.5px;color:var(--dim);display:inline-flex;align-items:center;gap:6px",
          )}
        >
          contracts {row.scene_packets_approved}/{row.scene_packets_total} · prose{" "}
          {row.scenes_with_prose}/{row.expected_scenes}
          {violations.map(([label, count]) =>
            count > 0 ? (
              <Chip
                key={label}
                label={`${count} ${label}`}
                colorVar={severityVar(label)}
                size="sm"
                title="Contract QA findings across this chapter's scene packets — block gates drafting, repair gates final export"
              />
            ) : null,
          )}
        </span>
        {arrow}
        {run ? (
          <span
            onClick={onOpenProduction}
            title={run.current_stage ?? "queued"}
            style={css(
              "cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
            )}
          >
            <Chip
              label={run.status.replace(/_/g, " ")}
              tone={RUN_TONE[run.status] ?? "neutral"}
              size="sm"
            />
            {run.issue_count} issues · {run.repair_task_count} repairs
          </span>
        ) : (
          <span
            onClick={onOpenProduction}
            title="No production runs yet — opens the Production tab"
            style={css("cursor:pointer;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
          >
            no runs
          </span>
        )}
        {arrow}
        <Chip
          label={row.can_draft ? "ready to draft" : "not ready"}
          tone={row.can_draft ? "good" : "warn"}
          size="sm"
        />
      </div>
      {!row.can_draft && <GateDisclosure lead={row.disabled_reason} rows={gateRows} />}
    </div>
  );
}

function Row({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div
      style={css(
        "display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;color:var(--dim);line-height:2.1",
      )}
    >
      <span>{k}</span>
      <span style={css(accent ? "color:var(--warn)" : "color:var(--ink)")}>{v}</span>
    </div>
  );
}

const KIND_OPTIONS: { value: string; label: string }[] = [
  { value: "chapter", label: "Chapter" },
  { value: "prologue", label: "Prologue" },
  { value: "interlude", label: "Interlude" },
  { value: "epilogue", label: "Epilogue" },
  { value: "front_matter", label: "Front matter" },
  { value: "back_matter", label: "Back matter" },
];

// Per-chapter structural metadata: reader-facing kind (Prologue/Interlude/…) + an optional epigraph.
// Kind saves immediately on select; the epigraph saves on blur. Each PATCHes only its changed field,
// so neither re-runs the planner nor touches prose. Display-only downstream — ordering stays chapter_no.
function ChapterMetaControls({
  chapter,
  onSave,
}: {
  chapter: ChapterOut;
  onSave: (patch: ChapterUpdateIn) => void;
}) {
  const [editingEpigraph, setEditingEpigraph] = useState(false);
  const [draft, setDraft] = useState(chapter.epigraph ?? "");
  useEffect(() => setDraft(chapter.epigraph ?? ""), [chapter.epigraph]);

  const saveEpigraph = () => {
    const next = draft.trim();
    if (next !== (chapter.epigraph ?? "").trim()) onSave({ epigraph: next || null });
    setEditingEpigraph(false);
  };

  return (
    <div style={css("display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:8px")}>
      <label
        style={css(
          "display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)",
        )}
      >
        kind
        <select
          value={chapter.kind ?? "chapter"}
          onChange={(e) => onSave({ kind: e.target.value as ChapterUpdateIn["kind"] })}
          style={css(
            "background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 7px;font-size:11.5px;font-family:var(--ui);cursor:pointer",
          )}
        >
          {KIND_OPTIONS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      {editingEpigraph ? (
        <textarea
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={saveEpigraph}
          placeholder="epigraph — a short quote shown at the chapter opening"
          style={css(
            "flex:1 1 260px;min-width:220px;min-height:44px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12px;line-height:1.5;resize:vertical;font-family:var(--ui)",
          )}
        />
      ) : (
        <button
          onClick={() => setEditingEpigraph(true)}
          title={chapter.epigraph?.trim() ? "Edit epigraph" : "Add an epigraph"}
          style={css(
            "font-family:var(--mono);font-size:10.5px;color:var(--dim);background:none;border:none;cursor:pointer;padding:2px 0;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left",
          )}
        >
          {chapter.epigraph?.trim() ? `epigraph: “${chapter.epigraph.trim()}”` : "+ epigraph"}
        </button>
      )}
    </div>
  );
}

// Same three exports the Manuscript tab offers (Markdown / Reader DOCX / Shunn DOCX), scoped to one
// chapter's approved scenes. Hidden behind a note until the chapter has any approved prose to export.
function ChapterExportLinks({
  chapter,
  manuscriptChapter,
  busy,
  onExport,
}: {
  chapter: ChapterOut;
  manuscriptChapter: ManuscriptChapter | null;
  busy: ExportKind | null;
  onExport: (kind: ExportKind) => void;
}) {
  const hasProse = !!manuscriptChapter?.scenes.some((s) => (s.prose ?? "").trim());
  if (!hasProse) {
    return (
      <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim);opacity:.55")}>
        export: no approved prose yet
      </span>
    );
  }
  const link = (kind: ExportKind, label: string) => (
    <span
      key={kind}
      onClick={() => onExport(kind)}
      title={`Export Chapter ${chapter.chapter_no} — same format the Manuscript tab uses`}
      style={css(
        `font-family:var(--mono);font-size:11px;color:var(--dim);cursor:pointer;opacity:${busy ? 0.6 : 1}`,
      )}
    >
      {busy === kind ? "Exporting…" : label}
    </span>
  );
  return (
    <div style={css("display:flex;align-items:center;gap:9px;flex-wrap:wrap")}>
      {link("md", "Export Markdown")}
      <span style={css("color:var(--dim);opacity:.4")}>·</span>
      {link("docx", "Export Reader DOCX")}
      <span style={css("color:var(--dim);opacity:.4")}>·</span>
      {link("shunn", "Export Shunn DOCX")}
    </div>
  );
}
