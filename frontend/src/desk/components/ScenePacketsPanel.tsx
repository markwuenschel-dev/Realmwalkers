"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { formatElapsed } from "./DraftActivity";
import { Button, Chip, Panel, Spinner, StatusPill } from "./ui";
import ClearFailedPanel from "./ClearFailedPanel";
import { ChapterTelemetryPanel } from "./Telemetry";
import { TelemetryDrawer, useTelemetryDrawer } from "./telemetry/TelemetryDrawer";
import type { TelemetryDrawerView } from "./telemetry/types";
import { useDeskData } from "../api/data";
import { resolveAuthorName, useAuthorName } from "../lib/authorName";
import { severityVar } from "../lib/severity";
import type {
  ScenePacketBody,
  ScenePacketOut,
  ScenePacketSummaryOut,
  ScenePacketDeriveStatusOut,
  DraftReadinessOut,
  QaIssue,
  SceneOut,
  SceneSource,
} from "../api/types";
import type { ExportKind } from "../lib/docx";

// Scene packets are the scene-local contract derived from an APPROVED chapter packet: per scene, what
// the reader/POV know before it, what may be revealed, what stays hidden, the intentional mysteries,
// and the planned word budget. Drafting is fail-closed on an approved, non-stale scene packet — so the
// flow is: derive (Author + QA per scene, in the background) → review → approve. Approving derives the
// chapter's beats. Mirrors the chapter-packet panel's background-poll pattern.

const STATUS_VAR: Record<string, string> = {
  approved: "--good",
  blocked: "--bad",
  stale: "--warn",
  proposed: "--info",
  // Transient provider refusal (429 past retries) — infrastructure, not a contract failure.
  rate_limited: "--warn",
};

const BLOCKER_SOURCE_LABEL: Record<string, string> = {
  author: "author",
  derive: "derive",
  qa: "QA",
  validation: "deterministic validation",
  rate_limit: "provider rate limit",
  unknown: "gate",
};

// QA verdict and prose state render through the shared StatusPill (axis "qa" / "prose") — the three
// status axes stay independent: contract approval ≠ QA opinion ≠ drafted prose.

// Every editable field path the editor anchors an issue to. A QA issue whose `field` matches one of
// these renders inline under that control; anything else (null, parent-level, or an unknown key) falls
// through to the editor's "general" callout so no issue is ever silently dropped.
const ANCHORED_FIELDS: ReadonlySet<string> = new Set([
  "scene_job",
  "scene_type",
  "chapter_position",
  "required_beats",
  "forbidden_beats",
  "exit_state",
  "tone_pressure",
  "reviewer_false_positive_traps",
  "phrases_to_avoid_echoing",
  "known_before_scene.reader",
  "known_before_scene.pov",
  "known_before_scene.omniscient_author",
  "learned_during_scene.reader_must_learn",
  "learned_during_scene.reader_may_learn",
  "learned_during_scene.reader_may_infer_only",
  "must_remain_hidden.reader",
  "must_remain_hidden.pov",
  "must_remain_hidden.all_surface_prose",
  "pov_permissions.may_notice",
  "pov_permissions.may_infer",
  "pov_permissions.must_not_know",
  "pov_permissions.may_be_wrong_about",
  "intentional_mysteries",
  "reviewer_instructions.continuity",
  "reviewer_instructions.pacing",
  "reviewer_instructions.dialogue",
  "reviewer_instructions.combat",
  "reviewer_instructions.sensory",
  "reviewer_instructions.voice",
]);

const REVIEWER_LANES = ["continuity", "pacing", "dialogue", "combat", "sensory", "voice"] as const;

function issuesFor(issues: QaIssue[], path: string): QaIssue[] {
  return issues.filter((it) => (it.field ?? "") === path);
}

export function ScenePacketsPanel({ chapterId }: { chapterId: string }) {
  const desk = useDeskData();
  const drawer = useTelemetryDrawer();
  // The LIST renders from slim summaries (statuses/counters — no bodies), so switching to this tab
  // fetches kilobytes, not the full contract JSON of every scene. Full packets load lazily per card
  // (expand/edit) into this cache, which is invalidated on every list reload.
  const [packets, setPackets] = useState<ScenePacketSummaryOut[]>([]);
  const [fullPackets, setFullPackets] = useState<Record<string, ScenePacketOut>>({});
  const [loading, setLoading] = useState(false);
  const [deriving, setDeriving] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped whenever packets reload (chapter change, derive finish) so the telemetry panel re-pulls.
  const [telemetryKey, setTelemetryKey] = useState(0);
  const [readiness, setReadiness] = useState<DraftReadinessOut | null>(null);
  const [drafting, setDrafting] = useState(false);
  // The last completed derive's counts — so a re-derive that (correctly) skips every approved,
  // unchanged packet says so instead of looking like the button did nothing.
  const [deriveResult, setDeriveResult] = useState<NonNullable<
    ScenePacketDeriveStatusOut["result"]
  > | null>(null);
  const [copied, setCopied] = useState(false);
  // Per-scene export (Markdown / Reader-DOCX / Shunn-DOCX) once a scene packet's scene has been
  // drafted — same builders the Manuscript tab uses. Author name shared with every export surface.
  const [author, saveAuthor] = useAuthorName();
  const [exportingScene, setExportingScene] = useState<{
    packetId: string;
    kind: ExportKind;
  } | null>(null);
  const pollRef = useRef<number | null>(null);

  const openTelemetry = useCallback(
    (view: TelemetryDrawerView) => {
      drawer.open(view);
    },
    [drawer],
  );

  const loadReadiness = useCallback(async () => {
    try {
      setReadiness(await api.draftReadiness(chapterId));
    } catch {
      setReadiness(null);
    }
  }, [chapterId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Summaries + readiness in parallel — readiness is the slower call and serializing them
      // added its full latency to every list paint.
      const [summaries] = await Promise.all([api.scenePacketSummaries(chapterId), loadReadiness()]);
      setPackets(summaries);
      // Statuses/bodies may have changed server-side — drop the per-card cache so open cards refetch.
      setFullPackets({});
      setTelemetryKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [chapterId, loadReadiness]);

  const loadFull = useCallback(async (packetId: string) => {
    try {
      const full = await api.scenePacket(packetId);
      setFullPackets((m) => ({ ...m, [packetId]: full }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // On chapter change: load the list + rejoin any in-flight derive (it runs server-side).
  useEffect(() => {
    setError(null);
    setPackets([]);
    setDeriveResult(null);
    void load();
    let alive = true;
    api
      .deriveStatus(chapterId)
      .then((st) => {
        if (alive && st.running) setDeriving(true);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [chapterId, load]);

  // Poll while a derive is running; refetch the list when it finishes.
  useEffect(() => {
    if (!deriving) return;
    let alive = true;
    const tick = async () => {
      try {
        const st = await api.deriveStatus(chapterId);
        if (!alive) return;
        if (st.running) {
          setElapsed(st.elapsed_s ?? null);
        } else {
          setDeriving(false);
          setElapsed(null);
          if (st.result) setDeriveResult(st.result);
          await load();
        }
      } catch {
        /* transient — keep polling */
      }
    };
    void tick();
    pollRef.current = window.setInterval(tick, 1500);
    return () => {
      alive = false;
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [deriving, chapterId, load]);

  const derive = async () => {
    setError(null);
    try {
      const st = await api.deriveScenePackets(chapterId);
      setDeriving(true);
      setElapsed(st.elapsed_s ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const approvable = packets.filter((p) => p.status === "proposed");
  const approvedCount = packets.filter((p) => p.status === "approved").length;
  const chapterMeta = desk.chapters.find((c) => c.id === chapterId);
  // The drafted scene for each scene packet, if any exists yet — keyed by scene_no so each card can
  // offer to export its own scene once it's been drafted (a scene packet has no prose of its own).
  const sceneByNo = useMemo(
    () =>
      new Map(
        desk.latestScenes.filter((s) => s.chapter_id === chapterId).map((s) => [s.scene_no, s]),
      ),
    [desk.latestScenes, chapterId],
  );
  const exportScene = async (packetId: string, scene: SceneOut, kind: ExportKind) => {
    setExportingScene({ packetId, kind });
    try {
      const exp = await import("../lib/docx");
      const title = `Chapter ${chapterMeta?.chapter_no ?? "?"} · Scene ${scene.scene_no}`;
      const ms = exp.buildManuscriptFrom(title, [
        {
          chapter_no: chapterMeta?.chapter_no ?? 0,
          title: chapterMeta?.title ?? null,
          pov: chapterMeta?.pov ?? "",
          scenes: [{ scene_no: scene.scene_no, prose: scene.prose }],
        },
      ]);
      const stem = `scene_ch${chapterMeta?.chapter_no ?? "x"}_s${scene.scene_no}_v${scene.version}`;
      if (kind === "md") {
        exp.saveMarkdown(exp.buildManuscriptMarkdown(ms), exp.markdownFilename(stem));
      } else if (kind === "docx") {
        await exp.saveDocx(exp.buildManuscriptDoc(ms, title), exp.docxFilename(stem));
      } else {
        const name = resolveAuthorName(author, saveAuthor);
        if (!name) return;
        await exp.saveDocx(
          exp.buildShunnDoc(ms, name, exp.manuscriptWordCount(ms)),
          exp.docxFilename(`${stem}_shunn`),
        );
      }
    } finally {
      setExportingScene(null);
    }
  };
  const chapterFailedJobs = desk.failedJobs.filter(
    (f) => chapterMeta != null && f.chapter_no === chapterMeta.chapter_no,
  );
  const readinessJobIssues = readiness
    ? ((readiness.jobs.malformed as number) ?? 0) + ((readiness.jobs.failed as number) ?? 0)
    : 0;
  const failedBannerCount =
    chapterFailedJobs.length > 0
      ? chapterFailedJobs.length
      : readinessJobIssues > 0
        ? readinessJobIssues
        : desk.jobs.failed;
  const showFailedBanner =
    chapterFailedJobs.length > 0 || readinessJobIssues > 0 || desk.jobs.failed > 0;

  return (
    <div style={css("margin-top:26px;border-top:1px solid var(--line);padding-top:22px")}>
      {showFailedBanner && (
        <div style={css("margin-bottom:14px")}>
          <ClearFailedPanel
            failedCount={failedBannerCount}
            failedJobs={chapterFailedJobs.length > 0 ? chapterFailedJobs : desk.failedJobs}
            onClear={() => desk.clearFailed(chapterId)}
            scopeLabel="this chapter"
            compact={chapterFailedJobs.length === 0}
          />
        </div>
      )}
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:16px",
        )}
      >
        <div>
          <h2
            style={css(
              "margin:0 0 4px;font-family:var(--display);font-weight:500;font-size:22px;line-height:28px;letter-spacing:-.01em;color:var(--ink)",
            )}
          >
            Scene packets
          </h2>
          <p style={css("margin:0;color:var(--dim);font-size:13.5px;max-width:620px")}>
            The scene-local contract every draft obeys — reader/POV knowledge, allowed vs hidden
            reveals, intentional mysteries, and the planned word budget. Derive them, review,
            approve. Drafting requires an approved, non-stale scene packet.
          </p>
        </div>
        <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
          <Button
            title="Copy the readiness gate, per-scene statuses, and last derive counts as JSON — paste it when reporting a pipeline problem."
            onClick={() => {
              const diagnostics = {
                chapter_id: chapterId,
                copied_at: new Date().toISOString(),
                readiness,
                scene_packets: packets,
                last_derive: deriveResult,
              };
              void navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2)).then(() => {
                setCopied(true);
                window.setTimeout(() => setCopied(false), 2000);
              });
            }}
          >
            {copied ? "Copied ✓" : "Copy diagnostics"}
          </Button>
          {packets.length > 0 && (
            <Button
              disabled={busy != null || deriving}
              style="color:var(--warn)"
              title="Soft retire: mark every scene packet stale (kept for audit; re-derive or re-approve before drafting) instead of deleting them."
              onClick={() => void run("retire-soft", () => api.markScenePacketsStale(chapterId))}
            >
              {busy === "retire-soft" ? "Retiring…" : "Retire (soft)"}
            </Button>
          )}
          {packets.length > 0 && (
            <Button
              disabled={busy != null || deriving}
              style="color:var(--warn)"
              onClick={() => {
                if (
                  !confirm(
                    `Clear all ${packets.length} scene packet${packets.length === 1 ? "" : "s"} for this chapter? This hard-deletes them — Retire (soft) keeps them as stale instead. Re-derive before drafting.`,
                  )
                )
                  return;
                void run("clear-all", () => api.deleteScenePackets(chapterId));
              }}
            >
              {busy === "clear-all" ? "Clearing…" : "Clear scene packets"}
            </Button>
          )}
          {approvable.length > 0 && (
            <Button
              variant="primary"
              style="background:var(--good);border-color:transparent"
              disabled={busy != null || deriving}
              onClick={() => void run("approve-all", () => api.approveScenePackets(chapterId))}
            >
              {busy === "approve-all" ? "Approving…" : `Approve all (${approvable.length})`}
            </Button>
          )}
          <Button variant="primary" disabled={deriving} onClick={() => void derive()}>
            {deriving
              ? `Deriving…${formatElapsed(elapsed) ? ` ${formatElapsed(elapsed)}` : ""}`
              : packets.length
                ? "Re-derive"
                : "Derive scene packets"}
          </Button>
        </div>
      </div>

      {error && (
        <div
          style={css(
            "margin-bottom:14px;border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--bg2));border-radius:var(--r);padding:10px 12px;color:var(--bad);font-size:12.5px",
          )}
        >
          {error}
        </div>
      )}

      {deriving && (
        <div
          style={css(
            "display:flex;align-items:center;gap:11px;margin-bottom:14px;border:1px solid color-mix(in srgb,var(--info) 35%,var(--line));background:color-mix(in srgb,var(--info) 7%,var(--bg2));border-radius:9px;padding:10px 13px",
          )}
        >
          <Spinner />
          <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
            ScenePacket Author + QA running per scene
            {formatElapsed(elapsed) ? ` · ${formatElapsed(elapsed)} elapsed` : ""} · runs
            server-side — switch tabs freely.
          </span>
        </div>
      )}

      {deriveResult && !deriving && (
        <div
          style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-bottom:12px")}
        >
          last derive: {deriveResult.created ?? 0} created · {deriveResult.updated ?? 0} updated ·{" "}
          {deriveResult.skipped ?? 0} skipped (approved, unchanged) · {deriveResult.blocked ?? 0}{" "}
          blocked · {deriveResult.rate_limited ?? 0} rate-limited
        </div>
      )}

      {approvedCount > 0 && (
        <div style={css("margin-bottom:14px")}>
          {/* Readiness strip — the headline claim and the button obey the SAME authoritative server
              gate (readiness.can_draft): the page must never say "ready to draft" while the button
              below it is disabled, and never disable it without naming the failing gate. */}
          <div
            style={css(
              "display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin-bottom:10px",
            )}
          >
            <Chip
              label={
                readiness?.can_draft
                  ? "ready to draft"
                  : readiness
                    ? "not ready to draft"
                    : "checking readiness"
              }
              tone={readiness?.can_draft ? "good" : readiness ? "warn" : "neutral"}
            />
            <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
              {approvedCount} approved
            </span>
            {readiness && !readiness.can_draft && readiness.disabled_reason && (
              <span style={css("font-size:12.5px;color:var(--ink);line-height:1.4")}>
                {readiness.disabled_reason}
              </span>
            )}
          </div>
          {readiness && !readiness.can_draft && (
            <DraftGateDiagnostics
              readiness={readiness}
              busy={busy != null}
              relinkBusy={busy === "relink-beats"}
              alignBusy={busy === "align-scene-count"}
              onAlign={(sequenceId) =>
                void (async () => {
                  setBusy("align-scene-count");
                  setError(null);
                  try {
                    await api.alignSequenceSceneCount(sequenceId);
                    setReadiness(await api.draftReadiness(chapterId));
                    desk.pushToast({
                      tone: "success",
                      message: "Sequence plan aligned to the seeded scenes — readiness re-checked",
                    });
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(null);
                  }
                })()
              }
              onRelink={() =>
                void (async () => {
                  setBusy("relink-beats");
                  setError(null);
                  try {
                    setReadiness(await api.deriveBeats(chapterId));
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(null);
                  }
                })()
              }
            />
          )}
          <Button
            variant="primary"
            disabled={drafting || !readiness?.can_draft}
            title={
              readiness?.can_draft
                ? "Queue prose drafting jobs for every approved, undrafted scene"
                : (readiness?.disabled_reason ?? "Checking draft readiness…")
            }
            onClick={() => {
              void (async () => {
                setDrafting(true);
                setError(null);
                try {
                  await api.draftChapter(chapterId);
                  await api.draftNext();
                  await load();
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setDrafting(false);
                }
              })();
            }}
          >
            {drafting ? "Queuing…" : "Draft scenes"}
          </Button>
          {readiness && !readiness.can_draft && readiness.blockers.length > 0 && (
            <div style={css("margin-top:8px;font-size:11.5px;color:var(--warn);line-height:1.45")}>
              {readiness.blockers.slice(0, 3).map((b, i) => (
                <div key={i}>
                  Sc{b.scene_no ?? "?"}: {b.required_action}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {loading && packets.length === 0 ? (
        <Muted text="Loading scene packets…" />
      ) : packets.length === 0 && !deriving ? (
        <div
          style={css(
            "border:1px dashed var(--line);border-radius:var(--rLg);padding:30px 22px;text-align:center;color:var(--dim)",
          )}
        >
          <div aria-hidden style={css("font-size:16px;color:var(--accent);margin-bottom:8px")}>
            ✦
          </div>
          <div style={css("font-family:var(--display);font-style:italic;font-size:15px")}>
            No scene packets yet. Derive them from the approved chapter packet above.
          </div>
        </div>
      ) : (
        <div style={css("display:flex;flex-direction:column;gap:12px")}>
          {packets.map((p) => (
            <ScenePacketCard
              key={p.id}
              summary={p}
              full={fullPackets[p.id] ?? null}
              onLoadFull={() => void loadFull(p.id)}
              busy={busy}
              scene={sceneByNo.get(p.scene_no) ?? null}
              exportingKind={exportingScene?.packetId === p.id ? exportingScene.kind : null}
              onExport={(scene, kind) => void exportScene(p.id, scene, kind)}
              onApprove={() => run(`approve:${p.id}`, () => api.approveScenePacket(p.id))}
              onReQa={() => run(`qa:${p.id}`, () => api.qaScenePacket(p.id))}
              onSave={(body) => run(`save:${p.id}`, () => api.updateScenePacket(p.id, { body }))}
              onDelete={() => {
                if (
                  !confirm(
                    `Delete scene packet for scene ${p.scene_no}? Re-derive before drafting this scene.`,
                  )
                )
                  return;
                void run(`delete:${p.id}`, () => api.deleteScenePacket(p.id));
              }}
            />
          ))}
        </div>
      )}

      <ChapterTelemetryPanel
        chapterId={chapterId}
        refreshKey={telemetryKey}
        bookId={desk.bookId ?? undefined}
        onOpen={desk.bookId ? openTelemetry : undefined}
      />
      {drawer.isOpen && desk.bookId && drawer.view && (
        <TelemetryDrawer nav={drawer.nav} bookId={desk.bookId} />
      )}
    </div>
  );
}

// "Why is this disabled?" — rendered ONLY while the Draft-scenes action is disabled
// (readiness.can_draft === false). Collapsed it shows the server's one-sentence disabled_reason
// (the FIRST failing gate in pipeline order); expanded it lists every gate with a pass/fail chip,
// in the exact order the backend's resolve_draft_gate checks them. Axis labels stay distinct —
// contract lifecycle ≠ Scene QA opinion ≠ prose-draft state — mirroring the per-card chips.
function DraftGateDiagnostics({
  readiness,
  busy,
  relinkBusy,
  alignBusy,
  onRelink,
  onAlign,
}: {
  readiness: DraftReadinessOut;
  busy: boolean;
  relinkBusy: boolean;
  alignBusy: boolean;
  onRelink: () => void;
  onAlign: (sequenceId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const sp = readiness.scene_packets;
  const spApproved = (sp.approved as number) ?? 0;
  const spExpected = (sp.expected as number) ?? 0;
  const spMissing = (sp.missing_scene_numbers as number[] | undefined) ?? [];
  const spRateLimited = (sp.rate_limited as number) ?? 0;
  const beatsApproved = (readiness.beats.approved as number) ?? 0;
  const beatsLinked = (readiness.beats.linked as number) ?? 0;
  const unlinkedCount = (readiness.beats.unlinked as string[] | undefined)?.length ?? 0;
  const proseHave = readiness.prose?.scenes_with_prose ?? 0;
  const proseExpected = readiness.prose?.expected_scenes ?? 0;
  const missingDrafts = readiness.missing_scene_drafts;
  const structural = readiness.structural_blockers;
  // Pipeline order — identical to the backend gate: packet → sequence/budget → scene packets
  // (contract, then Scene QA) → beats → jobs → prose coverage → provider rate limit.
  const gates: { label: string; pass: boolean; detail: string }[] = [
    {
      label: "Chapter packet · contract",
      pass: readiness.chapter_packet_approved,
      detail: readiness.chapter_packet_approved ? "approved" : "not approved",
    },
    {
      label: "Sequence / budget · structure",
      pass: structural.length === 0,
      detail:
        structural.length === 0
          ? "no structural faults"
          : `${structural.length} structural fault(s) — see below`,
    },
    {
      label: "Scene packets · contract",
      pass: spApproved > 0 && spMissing.length === 0 && readiness.scene_packets_stale === 0,
      detail:
        `${spApproved}/${spExpected || "?"} approved` +
        (spMissing.length > 0 ? ` · missing: ${spMissing.join(", ")}` : "") +
        (readiness.scene_packets_stale > 0 ? ` · ${readiness.scene_packets_stale} stale` : ""),
    },
    {
      label: "Scene packets · Scene QA",
      pass: readiness.scene_packet_qa_blocking === 0,
      detail:
        readiness.scene_packet_qa_blocking === 0
          ? "no QA blocks"
          : `${readiness.scene_packet_qa_blocking} packet(s) with verdict block_drafting`,
    },
    {
      label: "Beats",
      pass: beatsApproved > 0 && unlinkedCount === 0 && readiness.blockers.length === 0,
      detail:
        beatsApproved === 0
          ? "no approved beats yet — approving scene packets derives them"
          : `${beatsLinked}/${beatsApproved} linked` +
            (unlinkedCount > 0 ? ` · ${unlinkedCount} unlinked` : "") +
            (readiness.blockers.length > 0
              ? ` · ${readiness.blockers.length} queue blocker(s)`
              : ""),
    },
    {
      label: "Draft jobs",
      pass: readiness.active_draft_jobs === 0,
      detail: readiness.active_draft_jobs === 0 ? "idle" : `${readiness.active_draft_jobs} active`,
    },
    {
      label: "Prose drafts · prose",
      pass: missingDrafts.length > 0,
      detail:
        `${proseHave}/${proseExpected || "?"} scenes have prose` +
        (missingDrafts.length > 0
          ? ` · to draft: ${missingDrafts.join(", ")}`
          : " — every scene drafted; use redraft"),
    },
    {
      label: "Provider",
      pass: !readiness.provider_rate_limited,
      detail: readiness.provider_rate_limited
        ? `rate limited (429)${spRateLimited > 0 ? ` · ${spRateLimited} packet(s) held` : ""} — transient; retry shortly`
        : "ok",
    },
  ];
  return (
    <div
      style={css(
        "border:1px solid var(--line);border-radius:var(--r);background:var(--bg2b);padding:11px 13px;margin-bottom:10px",
      )}
      data-testid="draft-gate-diagnostics"
    >
      <div
        className="dk-row"
        style={css(
          "display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;cursor:pointer;border-radius:7px",
        )}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--warn)")}>
          Why is this disabled?
        </span>
        <span style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
          {readiness.disabled_reason ?? "The draft gate is not satisfied."}
        </span>
        <span
          style={css("margin-left:auto;font-family:var(--mono);font-size:13px;color:var(--dim)")}
        >
          {open ? "▾" : "▸"}
        </span>
      </div>
      {open && (
        <div style={css("margin-top:10px;display:flex;flex-direction:column;gap:6px")}>
          {gates.map((g) => (
            <div
              key={g.label}
              style={css("display:flex;align-items:baseline;gap:8px;flex-wrap:wrap")}
            >
              <Chip label={g.pass ? "pass" : "fail"} tone={g.pass ? "good" : "bad"} />
              <span style={css("font-family:var(--mono);font-size:11px;color:var(--ink)")}>
                {g.label}
              </span>
              <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                {g.detail}
              </span>
            </div>
          ))}
          {structural.length > 0 && (
            <div style={css("margin-top:4px;display:flex;flex-direction:column;gap:4px")}>
              {structural.slice(0, 4).map((b, i) => (
                <div key={i} style={css("font-size:11.5px;color:var(--warn);line-height:1.4")}>
                  · [{b.kind.replace(/_/g, " ")}] {b.message}
                  {/* One-click reconcile: the scene-count mismatch is target-vs-actual only (the
                      sequence's scenes[] already matches the seeds), so aligning the plan number
                      is a safe, reversible human call — no re-derive round trip. */}
                  {b.kind === "sequence_scene_count_mismatch" && b.sequence_id != null && (
                    <span style={css("margin-left:8px")}>
                      <Button
                        size="sm"
                        disabled={busy || alignBusy}
                        onClick={() => onAlign(String(b.sequence_id))}
                      >
                        {alignBusy
                          ? "Aligning…"
                          : `Align plan to ${b.seed_count ?? "the"} seeded scenes`}
                      </Button>
                    </span>
                  )}
                </div>
              ))}
              {structural.length > 4 && (
                <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                  … {structural.length - 4} more
                </div>
              )}
            </div>
          )}
          {unlinkedCount > 0 && (
            <div style={css("margin-top:4px")}>
              <Button
                variant="primary"
                disabled={busy}
                title="Reconcile beats with the current approved scene packets — prunes orphaned/legacy beats that hold the gate as 'unlinked'. Changes no approvals."
                onClick={onRelink}
              >
                {relinkBusy ? "Re-linking…" : "Re-link beats"}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ScenePacketCard({
  summary,
  full,
  onLoadFull,
  busy,
  scene,
  exportingKind,
  onExport,
  onApprove,
  onReQa,
  onSave,
  onDelete,
}: {
  summary: ScenePacketSummaryOut;
  full: ScenePacketOut | null;
  onLoadFull: () => void;
  busy: string | null;
  scene: SceneOut | null;
  exportingKind: ExportKind | null;
  onExport: (scene: SceneOut, kind: ExportKind) => void;
  onApprove: () => void;
  onReQa: () => void;
  onSave: (body: ScenePacketBody) => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  // The collapsed row renders entirely from the slim summary; the full contract (body, QA report,
  // sources) loads lazily on first expand/edit and is cached panel-side until the list reloads.
  useEffect(() => {
    if ((open || editing) && !full) onLoadFull();
  }, [open, editing, full, onLoadFull]);

  const b: ScenePacketBody = full?.body ?? {};
  const wb = b.word_budget ?? {};
  const known = b.known_before_scene ?? {};
  const learned = b.learned_during_scene ?? {};
  const hidden = b.must_remain_hidden ?? {};
  const statusVar = STATUS_VAR[summary.status] ?? "--dim";
  const isBlocked = summary.status === "blocked";
  const isRateLimited = summary.status === "rate_limited";
  const blockedReason =
    summary.blocked_reason ??
    summary.approval_blockers?.[0] ??
    "Blocked, but no reason was recorded. Re-run derive or inspect telemetry.";
  const blockerLabel = summary.blocker_source
    ? (BLOCKER_SOURCE_LABEL[summary.blocker_source] ?? summary.blocker_source)
    : null;
  const qaApprovedWhileBlocked = isBlocked && summary.qa_verdict === "approve";
  // Server-computed: the collapsed row has no body to test locally, and the gate must match the
  // backend's own valid_scene_packet_body decision anyway.
  const bodyValid = summary.body_valid;
  const residual = full?.qa_warnings?.residual_risks ?? [];
  const issues = full?.qa_warnings?.issues ?? [];
  // Deterministic-validation channel (distinct from QA `issues`). The backend collapses invalid
  // provenance into a single warn violation, so this is normally short and advisory.
  const violations = full?.qa_warnings?.violations ?? [];
  const reasons = summary.approval_blockers ?? [];
  const canApprove = summary.can_approve;
  const showBlockers = reasons.length > 0 && (summary.status === "proposed" || isBlocked);

  // Per-action busy flags (the panel keys busy as "<action>:<id>"). cardBusy disables every action on
  // this card while any one of them is in flight.
  const mine = (action: string) => busy === `${action}:${summary.id}`;
  const cardBusy = mine("approve") || mine("qa") || mine("save") || mine("delete");

  return (
    <Panel pad="13px 15px" style={`border-left:3px solid var(${statusVar})`}>
      <div
        style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap;cursor:pointer")}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>
          Scene {summary.scene_no}
          {b.scene_type ? (
            <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
              {" "}
              · {b.scene_type}
            </span>
          ) : null}
        </span>
        {/* Three independent status axes, never merged: contract lifecycle, advisory QA verdict,
            prose-draft state. "approved + QA: revise required" is a legitimate combination (QA never
            gates approval) and must read as two facts, not one contradiction. */}
        <StatusPill axis="contract" state={summary.status} />
        <StatusPill axis="qa" state={summary.qa_verdict} />
        <StatusPill axis="prose" state={summary.prose_state} />
        {wb.target ? (
          <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
            ~{wb.target}w{wb.min || wb.max ? ` (${wb.min ?? "?"}–${wb.max ?? "?"})` : ""}
            {wb.hard_max ? ` · ≤${wb.hard_max}` : ""}
          </span>
        ) : null}
        <span style={css("margin-left:auto;display:flex;align-items:center;gap:8px")}>
          {/* Action cluster — clicks here must not toggle the card open/closed. */}
          <span
            onClick={(e) => e.stopPropagation()}
            style={css("display:flex;align-items:center;gap:8px")}
          >
            {!editing && canApprove && (
              <Button
                size="sm"
                variant="primary"
                style="background:var(--good);border-color:transparent"
                disabled={cardBusy}
                onClick={onApprove}
                title={
                  summary.status === "stale"
                    ? "Re-assert this contract as-is despite the upstream change (free); re-derive instead if the upstream change was meaningful"
                    : undefined
                }
              >
                {mine("approve")
                  ? "Approving…"
                  : summary.status === "stale"
                    ? "Re-approve"
                    : "Approve"}
              </Button>
            )}
            {!editing && summary.status !== "approved" && (
              <Button
                size="sm"
                disabled={cardBusy || !bodyValid}
                onClick={onReQa}
                title={
                  bodyValid
                    ? isRateLimited
                      ? "The contract body is valid — re-running QA clears the transient rate-limit hold."
                      : undefined
                    : isRateLimited
                      ? "Rate limited by provider; retry pending. Re-run derive to retry this scene — the contract was never generated."
                      : "Cannot rerun QA: this packet failed during author/derive and has no valid scene contract. Re-run derive instead."
                }
              >
                {mine("qa") ? "Re-running QA…" : "Re-run QA"}
              </Button>
            )}
            {!editing && summary.status !== "approved" && (
              <Button
                size="sm"
                disabled={cardBusy}
                onClick={() => {
                  setOpen(true);
                  setEditing(true);
                }}
              >
                Edit
              </Button>
            )}
            {!editing && (
              <Button size="sm" style="color:var(--warn)" disabled={cardBusy} onClick={onDelete}>
                {mine("delete") ? "Deleting…" : "Delete"}
              </Button>
            )}
          </span>
          <span style={css("font-family:var(--mono);font-size:14px;color:var(--dim)")}>
            {open ? "▾" : "▸"}
          </span>
        </span>
      </div>

      {summary.status === "stale" && summary.stale_reason && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--warn);margin-top:7px")}>
          stale: {summary.stale_reason} — re-derive or re-approve before drafting.
        </div>
      )}
      {!editing && !bodyValid && !isRateLimited && summary.status !== "approved" && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:7px")}>
          Cannot rerun QA: this packet failed during author/derive and has no valid scene contract.
          Re-run derive instead.
        </div>
      )}
      {isRateLimited && (
        <div
          style={css(
            "margin-top:9px;border:1px solid color-mix(in srgb,var(--warn) 40%,var(--line));background:color-mix(in srgb,var(--warn) 7%,var(--bg2));border-radius:8px;padding:9px 11px;display:flex;flex-direction:column;gap:4px",
          )}
        >
          <div style={css("font-family:var(--mono);font-size:11px;color:var(--warn)")}>
            Rate limited by provider — transient infrastructure failure, not an author/QA failure:
          </div>
          <div style={css("font-size:12px;color:var(--ink);line-height:1.4")}>{blockedReason}</div>
          <div style={css("font-size:12px;color:var(--dim);line-height:1.4")}>
            {bodyValid
              ? "The scene contract is valid — Re-run QA to clear this hold."
              : "Re-derive to retry this scene (approved scenes are skipped automatically)."}
          </div>
        </div>
      )}
      {isBlocked && (
        <div
          style={css(
            "margin-top:9px;border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 7%,var(--bg2));border-radius:8px;padding:9px 11px;display:flex;flex-direction:column;gap:4px",
          )}
        >
          <div style={css("font-family:var(--mono);font-size:11px;color:var(--bad)")}>
            {blockerLabel ? `Blocked by ${blockerLabel}:` : "Blocked:"}
          </div>
          <div style={css("font-size:12px;color:var(--ink);line-height:1.4")}>{blockedReason}</div>
          {qaApprovedWhileBlocked && (
            <div style={css("font-size:12px;color:var(--ink);line-height:1.4;margin-top:4px")}>
              Blocked by derive/author gate, not by QA. QA approved the current body, but the packet
              status remains blocked. Re-run derive to reconcile.
            </div>
          )}
        </div>
      )}

      {/* Why approval is refused — the data that used to be hidden behind a bare 409. */}
      {showBlockers && !isBlocked && (
        <div
          style={css(
            "margin-top:9px;border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 7%,var(--bg2));border-radius:8px;padding:9px 11px;display:flex;flex-direction:column;gap:4px",
          )}
        >
          <div style={css("font-family:var(--mono);font-size:11px;color:var(--bad)")}>
            {summary.blocker_source === "validation"
              ? "Deterministic validation blocks approval — fix the contract below (Edit) and Re-run QA, or Re-derive:"
              : summary.blocker_source == null || summary.blocker_source === "qa"
                ? "QA blocks approval — fix the contract below (Edit) and Re-run QA, or Re-derive:"
                : "Approval blocked — fix the contract below (Edit) and Re-run QA, or Re-derive:"}
          </div>
          {reasons.map((r, i) => (
            <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
              · {r}
            </div>
          ))}
        </div>
      )}

      {editing ? (
        full ? (
          <ScenePacketEditor
            body={b}
            issues={issues}
            sources={full.sources}
            busy={mine("save")}
            onSave={(body) => {
              onSave(body);
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <div style={css("margin-top:12px")}>
            <Muted text="Loading contract…" />
          </div>
        )
      ) : (
        open &&
        (full == null ? (
          <div style={css("margin-top:12px")}>
            <Muted text="Loading contract…" />
          </div>
        ) : (
          <div style={css("margin-top:12px;display:flex;flex-direction:column;gap:10px")}>
            {b.scene_job && (
              <div style={css("font-size:13px;color:var(--ink);line-height:1.45")}>
                {b.scene_job}
              </div>
            )}
            <Pills label="Reader knows before" items={known.reader} tone="dim" />
            <Pills label="POV knows before" items={known.pov} tone="dim" />
            <Pills label="Reader must learn" items={learned.reader_must_learn} tone="info" />
            <Pills
              label="Reader may infer only"
              items={learned.reader_may_infer_only}
              tone="warn"
            />
            <Pills label="Must stay hidden (reader)" items={hidden.reader} tone="bad" />
            <Pills label="Required beats" items={b.required_beats} tone="good" />
            <Pills label="Forbidden beats" items={b.forbidden_beats} tone="bad" />
            {(b.intentional_mysteries?.length ?? 0) > 0 && (
              <div>
                <Label text="Intentional mysteries (don't explain)" />
                <div style={css("display:flex;flex-direction:column;gap:5px")}>
                  {b.intentional_mysteries!.map((m, i) => (
                    <div key={i} style={css("font-size:12.5px;color:var(--ink)")}>
                      {m.mystery}
                      {m.desired_reader_effect ? (
                        <span style={css("color:var(--dim)")}> — {m.desired_reader_effect}</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <Pills
              label="Reviewer false-positive traps"
              items={b.reviewer_false_positive_traps}
              tone="dim"
            />
            {b.exit_state && (
              <div style={css("font-size:12px;color:var(--dim)")}>Exit: {b.exit_state}</div>
            )}

            {/* QA detail — every residual risk and issue, with severity. Previously rendered nowhere,
                which is why a gated packet looked identical to a clean one. */}
            {(residual.length > 0 || issues.length > 0) && (
              <div
                style={css(
                  "margin-top:4px;border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:8px",
                )}
              >
                <Label text="QA report" />
                {issues.map((it, i) => {
                  const sev = it.severity ?? "info";
                  const sevVar = severityVar(sev);
                  return (
                    <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
                      <Chip label={sev} colorVar={sevVar} />{" "}
                      {it.field ? (
                        <>
                          <Chip label={it.field} colorVar="--info" />{" "}
                        </>
                      ) : null}
                      {it.kind ? <strong>{it.kind}: </strong> : null}
                      {it.detail}
                    </div>
                  );
                })}
                <Pills label="Residual risks (non-blocking)" items={residual} tone="warn" />
              </div>
            )}

            {/* Deterministic-validation channel — advisory contract violations (warn), distinct from QA.
                Provenance issues arrive pre-collapsed to a single line, so this list stays short. */}
            {violations.length > 0 && (
              <div
                style={css(
                  "margin-top:4px;border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:8px",
                )}
              >
                <Label text="Deterministic validation (non-blocking)" />
                {violations.slice(0, 6).map((it, i) => {
                  const sev = it.severity ?? "warn";
                  const sevVar = severityVar(sev, "--warn");
                  return (
                    <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
                      <Chip label={sev} colorVar={sevVar} />{" "}
                      {it.field ? (
                        <>
                          <Chip label={it.field} colorVar="--info" />{" "}
                        </>
                      ) : null}
                      {it.kind ? <strong>{it.kind}: </strong> : null}
                      {it.detail}
                    </div>
                  );
                })}
                {violations.length > 6 && (
                  <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                    … {violations.length - 6} more
                  </div>
                )}
              </div>
            )}

            {/* Same three exports the Manuscript tab offers, once this scene has actually been
                drafted — a scene packet is pre-prose planning JSON, so there's nothing to export
                until drafting produces prose for it. */}
            {summary.status === "approved" && (
              <div
                style={css(
                  "margin-top:4px;border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:6px",
                )}
              >
                <Label text="Export drafted scene" />
                {scene && (scene.prose ?? "").trim() ? (
                  <div style={css("display:flex;align-items:center;gap:9px;flex-wrap:wrap")}>
                    <ExportLink
                      kind="md"
                      label="Export Markdown"
                      busy={exportingKind}
                      onClick={() => onExport(scene, "md")}
                    />
                    <span style={css("color:var(--dim);opacity:.4")}>·</span>
                    <ExportLink
                      kind="docx"
                      label="Export Reader DOCX"
                      busy={exportingKind}
                      onClick={() => onExport(scene, "docx")}
                    />
                    <span style={css("color:var(--dim);opacity:.4")}>·</span>
                    <ExportLink
                      kind="shunn"
                      label="Export Shunn DOCX"
                      busy={exportingKind}
                      onClick={() => onExport(scene, "shunn")}
                    />
                  </div>
                ) : (
                  <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                    Not drafted yet — export options appear once this scene has prose.
                  </div>
                )}
              </div>
            )}

            <SourcesPanel sources={full.sources} />
          </div>
        ))
      )}
    </Panel>
  );
}

// Edit the full scene contract, grouped into the same five sections the author emits (knowledge,
// mysteries, shape, reviewer instructions, phrases), and PUT the whole body. QA issues render inline
// under the field they name (issue.field) so "fix this" points at a control, not a wall of text. Works
// on a local draft (Save persists, Cancel discards); saving returns an approved packet to `proposed`
// server-side, after which the human re-runs QA then approves. Fields the editor doesn't surface still
// round-trip untouched — the draft is a clone of the whole body.
function ScenePacketEditor({
  body,
  issues,
  sources,
  busy,
  onSave,
  onCancel,
}: {
  body: ScenePacketBody;
  issues: QaIssue[];
  sources?: SceneSource[] | null;
  busy: boolean;
  onSave: (body: ScenePacketBody) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<ScenePacketBody>(() => structuredClone(body));

  const setField = (k: keyof ScenePacketBody, v: unknown) => setDraft((d) => ({ ...d, [k]: v }));
  // Immutably set a nested list field, e.g. known_before_scene.reader.
  const setNested = (group: keyof ScenePacketBody, key: string, v: string[]) =>
    setDraft((d) => ({ ...d, [group]: { ...(d[group] as Record<string, unknown>), [key]: v } }));

  const fi = (path: string) => issuesFor(issues, path);
  const known = draft.known_before_scene ?? {};
  const learned = draft.learned_during_scene ?? {};
  const hidden = draft.must_remain_hidden ?? {};
  const perms = draft.pov_permissions ?? {};
  const wb = draft.word_budget ?? {};
  // Issues the editor can't anchor to a specific control (null field, a parent-level path, or an
  // unrecognized key) — surfaced up top so none is lost in the gaps between fields.
  const general = issues.filter((it) => !it.field || !ANCHORED_FIELDS.has(it.field));

  return (
    <div style={css("margin-top:12px;display:flex;flex-direction:column;gap:12px")}>
      <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
        <Chip label="editing scene packet" colorVar="--info" />
        <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          Save replaces the contract and returns it to proposed — Re-run QA, then Approve.
        </span>
      </div>

      {general.length > 0 && (
        <div
          style={css(
            "border:1px solid color-mix(in srgb,var(--warn) 40%,var(--line));background:color-mix(in srgb,var(--warn) 7%,var(--bg2));border-radius:9px;padding:9px 11px",
          )}
        >
          <Label text="QA issues — whole packet" />
          <FieldIssues issues={general} />
        </div>
      )}

      {(wb.target || wb.min || wb.max) && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          Word budget (planner-set, read-only): ~{wb.target ?? "?"}w · {wb.min ?? "?"}–
          {wb.max ?? "?"}
          {wb.hard_max ? ` · hard ≤${wb.hard_max}` : ""}
        </div>
      )}

      <Section
        title="Knowledge"
        hint="who knows what before the scene · what's learned · what stays hidden"
      >
        <EditList
          label="Reader knows before"
          value={known.reader}
          issues={fi("known_before_scene.reader")}
          onChange={(v) => setNested("known_before_scene", "reader", v)}
        />
        <EditList
          label="POV knows before"
          value={known.pov}
          issues={fi("known_before_scene.pov")}
          onChange={(v) => setNested("known_before_scene", "pov", v)}
        />
        <EditList
          label="Author-only (omniscient)"
          value={known.omniscient_author}
          issues={fi("known_before_scene.omniscient_author")}
          onChange={(v) => setNested("known_before_scene", "omniscient_author", v)}
        />
        <EditList
          label="Reader must learn"
          value={learned.reader_must_learn}
          issues={fi("learned_during_scene.reader_must_learn")}
          onChange={(v) => setNested("learned_during_scene", "reader_must_learn", v)}
        />
        <EditList
          label="Reader may learn"
          value={learned.reader_may_learn}
          issues={fi("learned_during_scene.reader_may_learn")}
          onChange={(v) => setNested("learned_during_scene", "reader_may_learn", v)}
        />
        <EditList
          label="Reader may infer only"
          value={learned.reader_may_infer_only}
          issues={fi("learned_during_scene.reader_may_infer_only")}
          onChange={(v) => setNested("learned_during_scene", "reader_may_infer_only", v)}
        />
        <EditList
          label="Hidden from reader"
          value={hidden.reader}
          issues={fi("must_remain_hidden.reader")}
          onChange={(v) => setNested("must_remain_hidden", "reader", v)}
        />
        <EditList
          label="Hidden from POV"
          value={hidden.pov}
          issues={fi("must_remain_hidden.pov")}
          onChange={(v) => setNested("must_remain_hidden", "pov", v)}
        />
        <EditList
          label="Hidden from all surface prose"
          value={hidden.all_surface_prose}
          issues={fi("must_remain_hidden.all_surface_prose")}
          onChange={(v) => setNested("must_remain_hidden", "all_surface_prose", v)}
        />
        <EditList
          label="POV may notice"
          value={perms.may_notice}
          issues={fi("pov_permissions.may_notice")}
          onChange={(v) => setNested("pov_permissions", "may_notice", v)}
        />
        <EditList
          label="POV may infer"
          value={perms.may_infer}
          issues={fi("pov_permissions.may_infer")}
          onChange={(v) => setNested("pov_permissions", "may_infer", v)}
        />
        <EditList
          label="POV must not know"
          value={perms.must_not_know}
          issues={fi("pov_permissions.must_not_know")}
          onChange={(v) => setNested("pov_permissions", "must_not_know", v)}
        />
        <EditList
          label="POV may be wrong about"
          value={perms.may_be_wrong_about}
          issues={fi("pov_permissions.may_be_wrong_about")}
          onChange={(v) => setNested("pov_permissions", "may_be_wrong_about", v)}
        />
        <ClaimSources claims={draft.claim_sources} sources={sources} />
      </Section>

      <Section
        title="Mysteries & reviewer traps"
        hint="what to leave unexplained · what reviewers may wrongly flag"
      >
        <MysteryEditor
          value={draft.intentional_mysteries}
          issues={fi("intentional_mysteries")}
          onChange={(v) => setField("intentional_mysteries", v)}
        />
        <EditList
          label="Reviewer false-positive traps"
          value={draft.reviewer_false_positive_traps}
          issues={fi("reviewer_false_positive_traps")}
          onChange={(v) => setField("reviewer_false_positive_traps", v)}
        />
      </Section>

      <Section title="Shape" hint="what the scene is and where it lands">
        <EditText
          label="Scene job"
          value={draft.scene_job}
          multiline
          issues={fi("scene_job")}
          onChange={(v) => setField("scene_job", v)}
        />
        <EditText
          label="Scene type"
          value={draft.scene_type}
          issues={fi("scene_type")}
          onChange={(v) => setField("scene_type", v)}
        />
        <EditText
          label="Chapter position"
          value={draft.chapter_position}
          issues={fi("chapter_position")}
          onChange={(v) => setField("chapter_position", v)}
        />
        <EditList
          label="Required beats"
          value={draft.required_beats}
          issues={fi("required_beats")}
          onChange={(v) => setField("required_beats", v)}
        />
        <EditList
          label="Forbidden beats"
          value={draft.forbidden_beats}
          issues={fi("forbidden_beats")}
          onChange={(v) => setField("forbidden_beats", v)}
        />
        <EditText
          label="Exit state"
          value={draft.exit_state}
          multiline
          issues={fi("exit_state")}
          onChange={(v) => setField("exit_state", v)}
        />
        <EditText
          label="Tone pressure"
          value={draft.tone_pressure}
          multiline
          issues={fi("tone_pressure")}
          onChange={(v) => setField("tone_pressure", v)}
        />
      </Section>

      <Section title="Reviewer instructions" hint="per-lane guidance for the draft reviewers">
        {REVIEWER_LANES.map((lane) => (
          <EditList
            key={lane}
            label={lane[0].toUpperCase() + lane.slice(1)}
            value={draft.reviewer_instructions?.[lane]}
            issues={fi(`reviewer_instructions.${lane}`)}
            onChange={(v) => setNested("reviewer_instructions", lane, v)}
          />
        ))}
      </Section>

      <Section
        title="Phrases to avoid echoing"
        hint="contract language the drafter shouldn't parrot"
      >
        <EditList
          label="Phrases to avoid echoing"
          value={draft.phrases_to_avoid_echoing}
          issues={fi("phrases_to_avoid_echoing")}
          onChange={(v) => setField("phrases_to_avoid_echoing", v)}
        />
      </Section>

      <SourcesPanel sources={sources} />

      <div style={css("display:flex;gap:9px")}>
        <Button
          variant="primary"
          style="background:var(--good);border-color:transparent"
          disabled={busy}
          onClick={() => onSave(draft)}
        >
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// A titled group of related fields, so the contract reads as five sections (matching the author's own
// section split) instead of one flat stack of ~25 controls.
function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div
      style={css(
        "border:1px solid var(--line);border-radius:10px;background:var(--bg2b);padding:11px 12px;display:flex;flex-direction:column;gap:11px",
      )}
    >
      <div style={css("display:flex;align-items:baseline;gap:9px;flex-wrap:wrap")}>
        <span
          style={css(
            "font-family:var(--display);font-size:13.5px;font-weight:600;color:var(--ink)",
          )}
        >
          {title}
        </span>
        {hint ? (
          <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
            {hint}
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

// QA issues anchored to one field, rendered immediately under that control so the human sees exactly
// what's wrong and where, instead of cross-referencing a separate QA-report panel.
function FieldIssues({ issues }: { issues?: QaIssue[] }) {
  if (!issues || issues.length === 0) return null;
  return (
    <div style={css("margin-top:5px;display:flex;flex-direction:column;gap:4px")}>
      {issues.map((it, i) => {
        const sev = it.severity ?? "info";
        const v = severityVar(sev);
        return (
          <div
            key={i}
            style={css(
              `font-size:11.5px;color:var(--ink);line-height:1.4;border-left:2px solid var(${v});background:color-mix(in srgb,var(${v}) 8%,var(--bg2));padding:4px 8px;border-radius:0 6px 6px 0`,
            )}
          >
            <Chip label={sev} colorVar={v} /> {it.kind ? <strong>{it.kind}: </strong> : null}
            {it.detail}
          </div>
        );
      })}
    </div>
  );
}

// "Built from these sources" — the canon/owner chunks this packet was derived from. The handles ([C1])
// match the author's claim_sources, so a wrong fact in the contract is traceable to a real file.
function SourcesPanel({ sources }: { sources?: SceneSource[] | null }) {
  const [open, setOpen] = useState(false);
  if (!sources || sources.length === 0) return null;
  return (
    <div
      style={css(
        "border:1px solid var(--line);border-radius:10px;background:var(--bg2b);padding:11px 12px",
      )}
    >
      <div
        style={css("display:flex;align-items:center;gap:8px;cursor:pointer")}
        onClick={() => setOpen((v) => !v)}
      >
        <span
          style={css(
            "font-family:var(--display);font-size:13.5px;font-weight:600;color:var(--ink)",
          )}
        >
          Built from these sources
        </span>
        <Chip label={String(sources.length)} colorVar="--dim" />
        <span
          style={css("margin-left:auto;font-family:var(--mono);font-size:13px;color:var(--dim)")}
        >
          {open ? "▾" : "▸"}
        </span>
      </div>
      {open && (
        <div style={css("margin-top:9px;display:flex;flex-direction:column;gap:6px")}>
          <div style={css("font-size:11px;color:var(--dim);line-height:1.4")}>
            A wrong claim usually traces to one of these — fix the canon file and re-derive.
          </div>
          {sources.map((s, i) => {
            const owner = s.retrieval_reason === "owner_forced";
            return (
              <div
                key={i}
                style={css(
                  "font-family:var(--mono);font-size:11px;color:var(--dim);line-height:1.45",
                )}
              >
                <span style={css("color:var(--ink)")}>[{s.handle ?? "?"}]</span> {s.doc_path || "?"}
                {s.heading_path ? ` › ${s.heading_path}` : ""}{" "}
                <span style={css(`color:var(${owner ? "--good" : "--dim"})`)}>
                  {owner ? "owner" : (s.retrieval_reason ?? "")}
                </span>
                {typeof s.score === "number" ? ` · ${s.score.toFixed(2)}` : ""}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Read-only provenance the author emitted: each knowledge claim mapped to the canon handle it came from
// (resolved to a file via `sources`). This is what answers "where is this pulling its info from".
function ClaimSources({
  claims,
  sources,
}: {
  claims?: { claim?: string; source_id?: string | null }[];
  sources?: SceneSource[] | null;
}) {
  if (!claims || claims.length === 0) return null;
  const byHandle = new Map((sources ?? []).map((s) => [s.handle, s] as const));
  return (
    <div>
      <Label text="Claim provenance · read-only, from derive" />
      <div style={css("display:flex;flex-direction:column;gap:5px")}>
        {claims.map((c, i) => {
          const src = c.source_id ? byHandle.get(c.source_id) : undefined;
          return (
            <div key={i} style={css("font-size:11.5px;color:var(--ink);line-height:1.4")}>
              {c.claim}
              {c.source_id ? (
                <span style={css("font-family:var(--mono);color:var(--dim)")}>
                  {"  ← "}
                  {c.source_id}
                  {src
                    ? ` (${src.doc_path}${src.heading_path ? " › " + src.heading_path : ""})`
                    : ""}
                </span>
              ) : (
                <span style={css("font-family:var(--mono);color:var(--dim)")}> ← inference</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Intentional mysteries are objects, not strings — edit each as (mystery, desired effect); do_not_explain
// stays true. Add/remove rows. Any QA issue anchored to the group renders below the rows.
function MysteryEditor({
  value,
  issues,
  onChange,
}: {
  value?: ScenePacketBody["intentional_mysteries"];
  issues?: QaIssue[];
  onChange: (v: NonNullable<ScenePacketBody["intentional_mysteries"]>) => void;
}) {
  const items = value ?? [];
  const update = (i: number, patch: { mystery?: string; desired_reader_effect?: string }) =>
    onChange(items.map((m, j) => (j === i ? { do_not_explain: true, ...m, ...patch } : m)));
  const remove = (i: number) => onChange(items.filter((_, j) => j !== i));
  const add = () =>
    onChange([...items, { mystery: "", desired_reader_effect: "", do_not_explain: true }]);
  return (
    <div>
      <Label text="Intentional mysteries · don't explain" />
      <div style={css("display:flex;flex-direction:column;gap:8px")}>
        {items.map((m, i) => (
          <div
            key={i}
            style={css(
              "display:flex;flex-direction:column;gap:5px;border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--bg)",
            )}
          >
            <input
              value={m.mystery ?? ""}
              placeholder="mystery"
              onChange={(e) => update(i, { mystery: e.target.value })}
              style={inputStyle()}
            />
            <input
              value={m.desired_reader_effect ?? ""}
              placeholder="desired reader effect"
              onChange={(e) => update(i, { desired_reader_effect: e.target.value })}
              style={inputStyle()}
            />
            <div>
              <Button size="sm" style="color:var(--warn)" onClick={() => remove(i)}>
                Remove
              </Button>
            </div>
          </div>
        ))}
        <div>
          <Button size="sm" onClick={add}>
            + Add mystery
          </Button>
        </div>
      </div>
      <FieldIssues issues={issues} />
    </div>
  );
}

// --- small local helpers (kept self-contained; PacketsScreen's are not exported) ---

function Pills({
  label,
  items,
  tone,
}: {
  label: string;
  items?: string[];
  tone: "good" | "bad" | "info" | "warn" | "dim";
}) {
  if (!items || items.length === 0) return null;
  const v = `--${tone}`;
  return (
    <div>
      <Label text={label} />
      <div style={css("display:flex;flex-wrap:wrap;gap:6px")}>
        {items.map((it, i) => (
          <span
            key={i}
            style={css(
              `font-size:12px;color:var(--ink);background:color-mix(in srgb,var(${v}) 10%,var(--bg3));border:1px solid color-mix(in srgb,var(${v}) 30%,var(--line));border-radius:6px;padding:3px 8px`,
            )}
          >
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}

function Label({ text }: { text: string }) {
  return (
    <div
      style={css(
        "font-family:var(--mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim);margin-bottom:5px",
      )}
    >
      {text}
    </div>
  );
}

function ExportLink({
  kind,
  label,
  busy,
  onClick,
}: {
  kind: ExportKind;
  label: string;
  busy: ExportKind | null;
  onClick: () => void;
}) {
  return (
    <span
      onClick={onClick}
      title="Same format the Manuscript tab exports"
      style={css(
        `font-family:var(--mono);font-size:11px;color:var(--dim);cursor:pointer;opacity:${busy ? 0.6 : 1}`,
      )}
    >
      {busy === kind ? "Exporting…" : label}
    </span>
  );
}

function Muted({ text }: { text: string }) {
  return (
    <div style={css("font-family:var(--mono);font-size:12px;color:var(--dim);padding:14px 2px")}>
      {text}
    </div>
  );
}

function inputStyle(): CSSProperties {
  return css(
    "width:100%;box-sizing:border-box;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-family:var(--ui);font-size:13px;color:var(--ink);resize:vertical",
  );
}

function EditText({
  label,
  value,
  onChange,
  multiline,
  rows,
  issues,
}: {
  label: string;
  value?: string;
  onChange: (v: string) => void;
  multiline?: boolean;
  rows?: number;
  issues?: QaIssue[];
}) {
  return (
    <div>
      <Label text={label} />
      {multiline ? (
        <textarea
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          rows={rows ?? 2}
          style={inputStyle()}
        />
      ) : (
        <input
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          style={inputStyle()}
        />
      )}
      <FieldIssues issues={issues} />
    </div>
  );
}

// A string-array field edited as one item per line. Local text buffer so blank/intermediate lines
// don't fight the cursor; the trimmed, non-empty list is pushed up on every change. (Mirrors the
// chapter-packet editor's EditList, which isn't exported from PacketsScreen.)
function EditList({
  label,
  value,
  onChange,
  issues,
}: {
  label: string;
  value?: string[];
  onChange: (v: string[]) => void;
  issues?: QaIssue[];
}) {
  const [text, setText] = useState(() => (value ?? []).join("\n"));
  return (
    <div>
      <Label text={`${label} · one per line`} />
      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          onChange(
            e.target.value
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean),
          );
        }}
        rows={Math.max(2, value?.length ?? 0)}
        style={inputStyle()}
      />
      <FieldIssues issues={issues} />
    </div>
  );
}
