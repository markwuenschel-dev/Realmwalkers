"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "../api/client";
import type {
  ArtifactOut,
  DraftReadinessOut,
  IssueOut,
  PacketOut,
  ProductionRunDetailOut,
  ProductionRunOut,
  RepairTaskOut,
} from "../api/types";
import { useDeskData } from "../api/data";
import { css } from "../css";
import GateDisclosure from "../components/GateDisclosure";
import ProseBlocks from "../components/ProseBlocks";
import { Button, Chip, Eyebrow, MetricCard, Panel, Spinner, Stepper } from "../components/ui";
import type { ChipTone, Step, StepState } from "../components/ui";
import { downloadBlob } from "../lib/download";
import { severityChipTone } from "../lib/severity";
import { useTabLoadTiming } from "../lib/useTabLoadTiming";
import {
  isNoApprovedPacketError,
  packetAdvisories,
  packetBlockedGuidance,
  packetDraftBlockers,
  packetRepairTasks,
  packetSurfaceAudit,
  surfaceAuditSummary,
} from "../lib/packetBlockers";
import type { PacketViolation } from "../lib/packetBlockers";

// Atelier display-XL screen title (shared idiom with DocsScreen).
const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";

// Session cache of full run details — the heaviest Desk payload (~670KB observed: every artifact's
// prose rides inline). Selecting a run serves the cached detail instantly; the run row's updated_at
// from the (cheap) list endpoint is the staleness token, since every backend action bumps it. An
// unchanged run therefore costs ZERO detail refetch on a tab revisit, and post-action refreshes
// update the entry in place (slim merges for actions that can't touch artifacts, full reloads for
// the ones that can).
const runDetailCache = new Map<string, ProductionRunDetailOut>();

/** Test-only: module-level session state would otherwise leak between vitest cases. */
export function resetRunDetailCacheForTests(): void {
  runDetailCache.clear();
}

function latestArtifact(artifacts: ArtifactOut[], type: string): ArtifactOut | null {
  return [...artifacts].reverse().find((artifact) => artifact.artifact_type === type) ?? null;
}

function statusTone(status: string): string {
  switch (status) {
    case "completed":
    case "verified":
      return "var(--good)";
    case "repairing":
    case "running":
      return "var(--info)";
    case "waiting_for_human":
    case "queued":
      return "var(--warn)";
    case "failed":
    case "blocked":
    case "rejected":
      return "var(--bad)";
    default:
      return "var(--dim)";
  }
}

function statusChipTone(status: string): ChipTone {
  switch (status) {
    case "completed":
    case "verified":
      return "good";
    case "repairing":
    case "running":
      return "info";
    case "waiting_for_human":
    case "queued":
      return "warn";
    case "failed":
    case "blocked":
    case "rejected":
      return "bad";
    default:
      return "neutral";
  }
}

function summaryCount(summary: Record<string, unknown> | null | undefined, key: string): string {
  const value = summary?.[key];
  return typeof value === "number" ? value.toLocaleString() : "—";
}

// --- run-stage pipeline ------------------------------------------------------------------------
// The pinned five-stage production pipeline. Every modern current_stage value maps 1:1; off-path
// holds (structural repair / provider 429) render as a blocked step; legacy stage strings from the
// pre-pipeline flow map to the nearest honest position rather than pretending they never existed.

const PIPELINE: { id: string; label: string }[] = [
  { id: "waiting_for_scene_drafts", label: "awaiting drafts" },
  { id: "drafting_scenes", label: "drafting scenes" },
  { id: "scene_qa", label: "scene QA" },
  { id: "assembling_chapter", label: "assembling" },
  { id: "chapter_qa", label: "chapter QA" },
];

// Legacy repair-funnel stages: chapter QA already ran and captured issues, so the funnel renders
// as a trailing sixth step — the five pinned stages stay truthful (all done) instead of one of
// them impersonating "repair".
const LEGACY_REPAIR_NOTE: Record<string, string> = {
  contract_classification: "classifying issues",
  issue_triage: "issue triage",
  repair_queue: "repair queued",
  repair_execution: "repairing",
};

function runPipelineSteps(run: { current_stage?: string | null; status: string }): Step[] {
  const stage = run.current_stage ?? null;
  const halted = run.status === "failed" || run.status === "blocked" || run.status === "rejected";
  const activeState: StepState = halted ? "blocked" : "active";

  const at = (idx: number, note?: string): Step[] =>
    PIPELINE.map((p, i) => ({
      id: p.id,
      label: p.label,
      state: i < idx ? "done" : i === idx ? activeState : "pending",
      note: i === idx ? note : undefined,
    }));
  const allDone = (): Step[] =>
    PIPELINE.map((p) => ({ id: p.id, label: p.label, state: "done" as StepState }));

  if (run.status === "completed" || run.status === "verified") return allDone();

  // Off-path holds — the active step renders blocked with the reason.
  if (stage === "structural_repair_required") {
    // Structural issues are a chapter-QA finding: assembly happened, QA refused to pass it.
    return PIPELINE.map((p, i) => ({
      id: p.id,
      label: p.label,
      state: i < 4 ? "done" : "blocked",
      note: i === 4 ? "structural repair required" : undefined,
    }));
  }
  if (stage === "provider_rate_limited") {
    // The 429 lands on the drafting stage's provider calls; the exact scene isn't recorded.
    return PIPELINE.map((p, i) => ({
      id: p.id,
      label: p.label,
      state: i < 1 ? "done" : i === 1 ? "blocked" : "pending",
      note: i === 1 ? "provider rate limited — retryable" : undefined,
    }));
  }

  if (stage && LEGACY_REPAIR_NOTE[stage]) {
    return [
      ...allDone(),
      { id: "repair", label: "repair", state: activeState, note: LEGACY_REPAIR_NOTE[stage] },
    ];
  }
  if (stage === "chapter_assembly") return at(3, halted ? run.status : undefined);

  const idx = stage ? PIPELINE.findIndex((p) => p.id === stage) : -1;
  if (idx >= 0) return at(idx, halted ? run.status : undefined);
  // Unknown / pre-pipeline stage (packet_gate, null while queued): anchor at the first step and
  // surface the raw stage string so nothing is hidden.
  return at(0, stage ?? "queued");
}

function EventFeed({ detail }: { detail: ProductionRunDetailOut }) {
  const rows = detail.events.slice(-8).reverse();
  if (!rows.length) {
    return <div style={css("color:var(--dim);font-size:13px")}>No production events yet.</div>;
  }
  return (
    <div style={css("display:flex;flex-direction:column;gap:10px")}>
      {rows.map((event) => (
        <div
          key={event.id}
          style={css(
            "display:grid;grid-template-columns:110px 1fr;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg3)",
          )}
        >
          <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
            {event.stage ?? event.event_type}
          </div>
          <div>
            <div style={css("font-size:13px;color:var(--ink)")}>
              {event.message ?? event.event_type}
            </div>
            <div style={css("margin-top:4px;font-size:12px;color:var(--dim)")}>
              {new Date(event.created_at).toLocaleString()}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// "Why is this disabled?" for the Assemble-chapter action. Assembly stitches EXISTING prose only
// (it never drafts scenes), so its authoritative gate is prose coverage (prose.assembly_ready);
// active draft jobs and provider holds render as context so a half-drafted chapter explains
// itself. Axis labels mirror the packets screen — Contract / Prose draft stay distinct.
function AssemblyGateDiagnostics({ readiness }: { readiness: DraftReadinessOut }) {
  const prose = readiness.prose;
  const missing = readiness.missing_scene_drafts;
  const gates: { label: string; pass: boolean; detail: string }[] = [
    {
      label: "Chapter packet · contract",
      pass: readiness.chapter_packet_approved,
      detail: readiness.chapter_packet_approved ? "approved" : "not approved",
    },
    {
      label: "Prose coverage · prose draft",
      pass: prose?.assembly_ready === true,
      detail:
        `${prose?.scenes_with_prose ?? 0}/${prose?.expected_scenes ?? 0} scenes have prose` +
        (missing.length > 0 ? ` · missing: ${missing.join(", ")}` : ""),
    },
    {
      label: "Draft jobs",
      pass: readiness.active_draft_jobs === 0,
      detail:
        readiness.active_draft_jobs === 0
          ? "idle"
          : `${readiness.active_draft_jobs} active — scenes may still be arriving`,
    },
    {
      label: "Provider",
      pass: !readiness.provider_rate_limited,
      detail: readiness.provider_rate_limited
        ? "rate limited (429) — transient; retry shortly"
        : "ok",
    },
  ];
  return <GateDisclosure rows={gates} testId="assembly-gate-diagnostics" />;
}

export default function ProductionScreen() {
  const { chapters } = useDeskData();
  const router = useRouter();
  const searchParams = useSearchParams();
  const orderedChapters = useMemo(
    () => [...chapters].sort((a, b) => a.chapter_no - b.chapter_no),
    [chapters],
  );

  const [chapterId, setChapterId] = useState<string | null>(null);
  const [runs, setRuns] = useState<ProductionRunOut[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProductionRunDetailOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Structured blocked state (never a raw exception dump): set when the start precondition fails —
  // the API refused because this chapter has no approved chapter packet. A blocked selected run is
  // derived from `detail` below; both render the same remediation panel.
  const [startBlocked, setStartBlocked] = useState(false);
  const [gatePacket, setGatePacket] = useState<PacketOut | null>(null);
  const [gatePacketLoaded, setGatePacketLoaded] = useState(false);
  // Prose coverage for the assembly gate: a run only concatenates EXISTING scene prose, so starting
  // one with missing scenes just manufactures missing_scene issues. Disable until coverage is full.
  const [readiness, setReadiness] = useState<DraftReadinessOut | null>(null);
  // Raw run JSON inspector: `detail` refreshed after every action — issues/repair tasks/events via
  // the slim endpoints on every step, artifacts via a full reload on the steps that can change them
  // (assemble/apply/start). repair_attempts/verifications refresh on full reloads only.
  const [jsonOpen, setJsonOpen] = useState(false);
  // Info-toned outcome line (never an error): triage is a deterministic no-op when no issue is still
  // `proposed`, which used to read as "the button did nothing". Cleared on any run/chapter switch.
  const [notice, setNotice] = useState<string | null>(null);
  // First runs-list arrival — the screen's "first data render" moment for tab-load timing.
  const [runsLoaded, setRunsLoaded] = useState(false);
  // Mirror of `runs` for effects that need the latest rows without re-arming on every list refresh.
  const runsRef = useRef<ProductionRunOut[]>([]);

  const loadDetail = useCallback(async (targetRunId: string | null) => {
    if (!targetRunId) {
      setDetail(null);
      return;
    }
    const out = await api.productionRun(targetRunId);
    runDetailCache.set(targetRunId, out);
    setDetail(out);
  }, []);

  // Slim post-action reconciliation: refetch only what the action could have changed — issues,
  // repair tasks, events (small sub-resource endpoints) plus the fresh run row from the already
  // reloaded list — and keep the cached artifacts/prose (the ~670KB bulk) untouched.
  const refreshDetailSlim = useCallback(
    async (targetRunId: string | null, freshRun?: ProductionRunOut) => {
      if (!targetRunId) return;
      const [issues, repairTasks, events] = await Promise.all([
        api.productionRunIssues(targetRunId),
        api.productionRunRepairTasks(targetRunId),
        api.productionRunEvents(targetRunId),
      ]);
      setDetail((cur) => {
        if (!cur || cur.run.id !== targetRunId) return cur;
        const next = {
          ...cur,
          run: freshRun ?? cur.run,
          issues,
          repair_tasks: repairTasks,
          events,
        };
        runDetailCache.set(targetRunId, next);
        return next;
      });
    },
    [],
  );

  const loadRuns = useCallback(
    async (targetChapterId: string): Promise<ProductionRunOut[] | null> => {
      setLoading(true);
      setStartBlocked(false); // re-evaluated on every refresh / chapter switch
      try {
        const out = await api.productionRuns(targetChapterId);
        runsRef.current = out;
        setRuns(out);
        setError(null);
        setRunId((current) => {
          if (current && out.some((run) => run.id === current)) return current;
          return out[0]?.id ?? null;
        });
        return out;
      } catch (e) {
        runsRef.current = [];
        setRuns([]);
        setDetail(null);
        setRunId(null);
        setError(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        setLoading(false);
        setRunsLoaded(true);
      }
    },
    [],
  );

  useEffect(() => {
    const fromUrl = searchParams.get("chapter");
    if (fromUrl && orderedChapters.some((chapter) => chapter.id === fromUrl)) {
      setChapterId(fromUrl);
      return;
    }
    if (chapterId === null && orderedChapters.length) setChapterId(orderedChapters[0].id);
  }, [chapterId, orderedChapters, searchParams]);

  useEffect(() => {
    if (!chapterId) return;
    void loadRuns(chapterId);
  }, [chapterId, loadRuns]);

  useEffect(() => {
    if (!chapterId) {
      setReadiness(null);
      return;
    }
    let alive = true;
    api
      .draftReadiness(chapterId)
      .then((out) => {
        if (alive) setReadiness(out);
      })
      .catch(() => {
        if (alive) setReadiness(null); // unknown coverage: don't dead-lock the button on a fetch blip
      });
    return () => {
      alive = false;
    };
  }, [chapterId, runs]); // re-check after run refreshes so newly drafted scenes unlock the button

  useEffect(() => {
    if (!runId) {
      setDetail(null);
      return;
    }
    setNotice(null);
    const cached = runDetailCache.get(runId);
    if (cached) {
      setDetail(cached); // instant paint from the session cache
      const row = runsRef.current.find((r) => r.id === runId);
      // updated_at unchanged since we cached → nothing on the run moved → skip the ~670KB refetch
      // entirely. Any backend action bumps updated_at, which falls through to the reload below.
      if (row && row.updated_at === cached.run.updated_at) return;
    }
    void loadDetail(runId).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : String(e));
    });
  }, [loadDetail, runId]);

  // Tab-switch cost, visible in the console: cached revisits log ~list-fetch time only.
  useTabLoadTiming("production", runsLoaded);

  const runJson = useMemo(() => (detail ? JSON.stringify(detail, null, 2) : ""), [detail]);
  const downloadRunJson = () => {
    if (!detail) return;
    // Status + stage in the filename so successive downloads (one per step) sort meaningfully.
    downloadBlob(
      `production_run_${detail.run.id.slice(0, 8)}_${detail.run.current_stage ?? detail.run.status}.json`,
      runJson,
      "application/json",
    );
  };

  const chapter = orderedChapters.find((row) => row.id === chapterId) ?? null;
  // Header metrics come straight off the (cheap) list row when the heavy detail hasn't landed yet —
  // status + issue/repair counts paint immediately from ProductionRunOut.summary_json.
  const selectedRun = runs.find((row) => row.id === runId) ?? null;
  const headerRun = detail?.run ?? selectedRun;
  const finalArtifact = detail ? latestArtifact(detail.artifacts, "final_chapter") : null;
  const draftArtifact = detail ? latestArtifact(detail.artifacts, "chapter_draft") : null;
  const qaArtifact = detail ? latestArtifact(detail.artifacts, "chapter_draft_qa") : null;
  const finalText =
    typeof finalArtifact?.body.prose === "string"
      ? finalArtifact.body.prose
      : typeof draftArtifact?.body.prose === "string"
        ? draftArtifact.body.prose
        : "";

  // Returns the action's result on success (so callers can report what happened) or null on failure
  // (the error banner already carries the message). `refresh` picks the post-action reload: "slim"
  // for actions that cannot change artifacts/prose (triage, verify — they only move issues/tasks),
  // "full" (default) for the ones that can (assemble, apply). Slim keeps the ~670KB artifact payload
  // out of the request entirely.
  const runAction = useCallback(
    async <T,>(
      label: string,
      fn: () => Promise<T>,
      opts?: { reloadRuns?: boolean; refresh?: "full" | "slim" },
    ): Promise<T | null> => {
      setBusy(label);
      try {
        const out = await fn();
        setError(null);
        const freshRuns = opts?.reloadRuns && chapterId ? await loadRuns(chapterId) : null;
        if (opts?.refresh === "slim") {
          await refreshDetailSlim(
            runId,
            freshRuns?.find((r) => r.id === runId),
          );
        } else {
          await loadDetail(runId);
        }
        return out;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        setBusy(null);
      }
    },
    [chapterId, loadDetail, loadRuns, refreshDetailSlim, runId],
  );

  const startRun = async () => {
    if (!chapterId) return;
    setBusy("start");
    try {
      const out = await api.startProductionRun({ chapter_id: chapterId, auto_triage: true });
      setError(null);
      await loadRuns(chapterId);
      setRunId(out.run.id);
      await loadDetail(out.run.id);
    } catch (e) {
      if (isNoApprovedPacketError(e)) {
        // Known precondition, not an error: production drafts FROM the approved chapter packet.
        // Render the structured remediation panel instead of dumping the exception.
        setError(null);
        setStartBlocked(true);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(null);
    }
  };

  // The remediation panel shows for a failed start precondition or a blocked selected run; it needs
  // the chapter packet (404 = none yet) to say WHY and list its blockers / repair tasks. headerRun:
  // the list row already knows the status, so the gate shows before the heavy detail lands.
  const blockedRun = headerRun?.status === "blocked";
  const showGate = startBlocked || blockedRun;
  useEffect(() => {
    if (!showGate || !chapterId) return;
    let alive = true;
    setGatePacketLoaded(false);
    api
      .packet(chapterId)
      .then((pkt) => {
        if (alive) setGatePacket(pkt);
      })
      .catch(() => {
        if (alive) setGatePacket(null); // 404: no chapter packet exists yet
      })
      .finally(() => {
        if (alive) setGatePacketLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, [showGate, chapterId]);

  const issues = detail?.issues ?? [];
  const repairTasks = detail?.repair_tasks ?? [];
  // "Apply all queued" covers exactly what the background drain would: queued, no approval gate.
  const queuedEligibleCount = repairTasks.filter(
    (t) => t.status === "queued" && !t.requires_human_approval,
  ).length;
  const sequenceScenes = Array.isArray(detail?.chapter_sequence?.body?.scenes)
    ? detail?.chapter_sequence?.body?.scenes
    : [];

  // Assembly gate: "Assemble chapter" concatenates EXISTING prose only (it never drafts scenes), so
  // it stays disabled until every expected scene has prose. When readiness is unknown (fetch blip)
  // the button stays usable — the backend still fails safe with missing_scene issues.
  const prose = readiness?.prose;
  const missingProse = prose?.missing_scene_numbers ?? [];
  const assemblyBlocked = prose != null && prose.assembly_ready !== true;

  return (
    <div style={css("display:flex;flex-direction:column;gap:18px")}>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap",
        )}
      >
        <div>
          <Eyebrow style="margin-bottom:6px">Ops · production runs</Eyebrow>
          <h1 style={css(TITLE_XL)}>Editorial production</h1>
          <p style={css("margin:8px 0 0;color:var(--dim);font-size:14px;max-width:760px")}>
            Durable chapter production runs: issue capture, repair tasks, verification, and final
            chapter assembly.
          </p>
        </div>
        <div style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}>
          <select
            aria-label="Chapter"
            value={chapterId ?? ""}
            onChange={(e) => setChapterId(e.target.value || null)}
            style={css(
              "height:34px;padding:0 12px;border-radius:9px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui)",
            )}
          >
            {orderedChapters.map((item) => (
              <option key={item.id} value={item.id}>
                Ch {item.chapter_no}
                {item.title ? ` · ${item.title}` : ""}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
            disabled={!chapterId || busy === "start" || assemblyBlocked}
            title={
              assemblyBlocked
                ? `Draft scenes first — ${missingProse.length} scene(s) have no prose draft yet`
                : "Assemble existing scene prose into a chapter draft and run chapter QA"
            }
            onClick={() => void startRun()}
          >
            {busy === "start" ? "Assembling…" : "Assemble chapter"}
          </Button>
          <Button
            disabled={!chapterId || loading}
            onClick={() => chapterId && void loadRuns(chapterId)}
          >
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <div
          style={css(
            "border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--bg2));border-radius:var(--r);padding:12px 14px;color:var(--bad);font-size:13px",
          )}
        >
          {error}
        </div>
      )}

      {notice && (
        <div
          data-testid="production-notice"
          style={css(
            "border:1px solid color-mix(in srgb,var(--info) 40%,var(--line));background:color-mix(in srgb,var(--info) 8%,var(--bg2));border-radius:var(--r);padding:12px 14px;color:var(--ink);font-size:13px;display:flex;justify-content:space-between;align-items:flex-start;gap:10px",
          )}
        >
          <span>{notice}</span>
          <button
            onClick={() => setNotice(null)}
            style={css(
              "background:none;border:none;color:var(--dim);cursor:pointer;font-size:12px;font-family:var(--ui);padding:0",
            )}
          >
            Dismiss
          </button>
        </div>
      )}

      {assemblyBlocked && (
        <div
          style={css(
            "border:1px solid color-mix(in srgb,var(--warn) 40%,var(--line));background:color-mix(in srgb,var(--warn) 8%,var(--bg2));border-radius:var(--r);padding:12px 14px;display:flex;flex-direction:column;gap:8px",
          )}
          data-testid="assembly-gate"
        >
          <div style={css("font-size:13px;color:var(--ink)")}>
            Draft scenes first — assembly only stitches existing scene prose.{" "}
            {prose?.scenes_with_prose ?? 0}/{prose?.expected_scenes ?? 0} scenes have prose
            {missingProse.length > 0 ? ` (missing: ${missingProse.join(", ")})` : ""}.
          </div>
          {readiness && <AssemblyGateDiagnostics readiness={readiness} />}
          <div style={css("display:flex;gap:10px;align-items:center;flex-wrap:wrap")}>
            <Button
              variant="primary"
              size="sm"
              onClick={() => router.push(chapterId ? `/packets?chapter=${chapterId}` : "/packets")}
            >
              Draft scenes
            </Button>
            <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
              opens the scene packets panel — draft the missing scenes, then assemble
            </span>
          </div>
        </div>
      )}

      {showGate && (
        <ProductionGatePanel
          title={
            startBlocked ? "Blocked — no approved chapter packet" : "This production run is blocked"
          }
          packet={gatePacket}
          packetLoaded={gatePacketLoaded}
          onGoToPackets={() =>
            router.push(chapterId ? `/packets?chapter=${chapterId}` : "/packets")
          }
        />
      )}

      <div
        style={css(
          "display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px;align-items:start",
        )}
      >
        <Panel pad="18px 20px" style="display:flex;flex-direction:column;gap:16px">
          <div>
            <Eyebrow>Chapter</Eyebrow>
            <div
              style={css(
                "margin-top:6px;font-family:var(--display);font-weight:500;font-size:20px;color:var(--ink)",
              )}
            >
              {chapter
                ? `Ch ${chapter.chapter_no}${chapter.title ? ` · ${chapter.title}` : ""}`
                : "No chapter selected"}
            </div>
            {chapter?.outline && (
              <p style={css("margin:8px 0 0;color:var(--dim);font-size:13px;line-height:1.55")}>
                {chapter.outline}
              </p>
            )}
          </div>

          <div>
            <Eyebrow>Runs</Eyebrow>
            {loading ? (
              <div
                style={css(
                  "display:flex;align-items:center;gap:8px;margin-top:10px;font-size:12px;color:var(--dim)",
                )}
              >
                <Spinner /> loading…
              </div>
            ) : runs.length ? (
              <div style={css("display:flex;flex-direction:column;gap:8px;margin-top:10px")}>
                {runs.map((run) => {
                  const active = run.id === runId;
                  return (
                    <button
                      key={run.id}
                      className="dk-card"
                      onClick={() => setRunId(run.id)}
                      style={css(
                        `text-align:left;padding:10px 12px;border-radius:10px;border:1px solid ${active ? "var(--accent)" : "var(--line)"};background:${active ? "var(--accentSoft)" : "var(--bg3)"};color:var(--ink);cursor:pointer`,
                      )}
                    >
                      <div
                        style={css(
                          "display:flex;justify-content:space-between;gap:10px;align-items:center",
                        )}
                      >
                        <span
                          style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}
                        >
                          {run.id.slice(0, 8)}
                        </span>
                        <Chip label={run.status} tone={statusChipTone(run.status)} size="sm" />
                      </div>
                      <div style={css("margin-top:6px;font-size:12.5px;color:var(--ink)")}>
                        {run.current_stage ?? "queued"}
                      </div>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div style={css("margin-top:10px;color:var(--dim);font-size:13px")}>
                No production runs for this chapter yet.
              </div>
            )}
          </div>
        </Panel>

        <div style={css("display:flex;flex-direction:column;gap:18px")}>
          {headerRun && (
            <Panel eyebrow={`Run pipeline · ${headerRun.status}`} pad="18px 20px">
              <Stepper steps={runPipelineSteps(headerRun)} />
            </Panel>
          )}

          <div style={css("display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px")}>
            <MetricCard
              label="Status"
              value={headerRun?.status ?? "—"}
              tone={statusTone(headerRun?.status ?? "")}
            />
            <MetricCard
              label="Issues"
              value={summaryCount(headerRun?.summary_json, "issue_count")}
              tone="var(--warn)"
            />
            <MetricCard
              label="Repair tasks"
              value={summaryCount(headerRun?.summary_json, "repair_task_count")}
              tone="var(--info)"
            />
            <MetricCard
              label="Expected scenes"
              value={String(sequenceScenes?.length ?? 0)}
              tone="var(--good)"
            />
          </div>

          {detail && (
            <div style={css("display:flex;gap:8px;flex-wrap:wrap")}>
              <Button
                size="sm"
                disabled={busy != null}
                onClick={() => {
                  // Triage only converts `proposed` issues into repair tasks; with none left it is a
                  // deterministic no-op that used to look like a dead button. Say so instead.
                  const proposed = issues.filter((i) => i.status === "proposed").length;
                  if (!proposed) {
                    setNotice(
                      issues.length
                        ? `Nothing to triage — all ${issues.length} issue${issues.length === 1 ? " is" : "s are"} already triaged (${repairTasks.length} repair task${repairTasks.length === 1 ? "" : "s"} queued). Work the Apply / Verify buttons on the repair tasks instead.`
                        : "Nothing to triage — this run has no captured issues. Assemble first if you expected some.",
                    );
                    return;
                  }
                  setNotice(null);
                  // Triage only moves issues → repair tasks; artifacts/prose can't change, so the
                  // slim refresh skips the ~670KB detail payload.
                  void runAction("triage", () => api.triageProductionRun(detail.run.id), {
                    reloadRuns: true,
                    refresh: "slim",
                  }).then((ok) => {
                    if (ok)
                      setNotice(
                        `Triaged ${proposed} proposed issue${proposed === 1 ? "" : "s"} — statuses and repair tasks updated below.`,
                      );
                  });
                }}
              >
                {busy === "triage" ? "Triaging…" : "Auto-triage"}
              </Button>
              <Button
                size="sm"
                disabled={busy != null}
                onClick={() =>
                  void runAction("assemble", () => api.assembleProductionRun(detail.run.id), {
                    reloadRuns: true,
                  })
                }
              >
                {busy === "assemble" ? "Assembling…" : "Refresh assembly"}
              </Button>
              <Button size="sm" onClick={() => setJsonOpen((v) => !v)}>
                {jsonOpen ? "Hide run JSON" : "Run JSON"}
              </Button>
              <Button
                size="sm"
                onClick={downloadRunJson}
                title="Download the full run state (issues, repair tasks, artifacts, events) as JSON"
              >
                Download JSON
              </Button>
            </div>
          )}

          {detail && jsonOpen && (
            <div data-testid="run-json">
              <Panel
                inset
                eyebrow={`Run JSON · full state after the latest step · ${detail.run.current_stage ?? "queued"}`}
                pad="14px 16px"
              >
                <pre
                  style={css(
                    "margin:0;font-family:var(--mono);font-size:11.5px;white-space:pre-wrap;color:var(--ink);max-height:440px;overflow:auto",
                  )}
                >
                  {runJson}
                </pre>
              </Panel>
            </div>
          )}

          <div style={css("display:grid;grid-template-columns:1.1fr .9fr;gap:18px")}>
            <Panel eyebrow="Issue inbox" pad="18px 20px">
              <div style={css("display:flex;flex-direction:column;gap:10px")}>
                {issues.length ? (
                  issues.map((issue) => (
                    <IssueRow
                      key={issue.id}
                      issue={issue}
                      onOpenPacket={
                        chapterId
                          ? () =>
                              router.push(
                                issue.scene_no != null
                                  ? `/packets?chapter=${chapterId}&scene=${issue.scene_no}`
                                  : `/packets?chapter=${chapterId}`,
                              )
                          : undefined
                      }
                    />
                  ))
                ) : (
                  <div style={css("color:var(--dim);font-size:13px")}>
                    No structured issues on this run.
                  </div>
                )}
              </div>
            </Panel>

            <Panel eyebrow="Repair tasks" pad="18px 20px">
              <div style={css("display:flex;flex-direction:column;gap:10px")}>
                {queuedEligibleCount > 0 && runId && (
                  <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
                    <Button
                      size="sm"
                      disabled={busy != null}
                      onClick={() =>
                        void runAction("apply-all", () => api.applyAllRepairTasks(runId), {
                          reloadRuns: true,
                        }).then((out) => {
                          if (!out) return;
                          setNotice(
                            out.queue_paused
                              ? `Queue is paused — ${out.queued} queued repair task${out.queued === 1 ? "" : "s"} will apply after you resume the queue.`
                              : out.scheduled
                                ? `Applying ${out.queued} queued repair task${out.queued === 1 ? "" : "s"} in the background${out.requires_approval ? `; ${out.requires_approval} still need${out.requires_approval === 1 ? "s" : ""} your explicit Approve & apply` : ""}.`
                                : `Nothing to apply${out.requires_approval ? ` — ${out.requires_approval} task${out.requires_approval === 1 ? " needs" : "s need"} your explicit Approve & apply` : ""}.`,
                          );
                        })
                      }
                    >
                      {busy === "apply-all"
                        ? "Applying…"
                        : `Apply all queued (${queuedEligibleCount})`}
                    </Button>
                    <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                      same drain the auto-triggers use — approval-gated tasks are never included
                    </span>
                  </div>
                )}
                {repairTasks.length ? (
                  repairTasks.map((task) => (
                    <RepairRow
                      key={task.id}
                      task={task}
                      busy={busy}
                      onApply={() =>
                        void runAction(`apply:${task.id}`, () => api.applyRepairTask(task.id), {
                          reloadRuns: true,
                        }).then((updated) => {
                          if (!updated) return;
                          const where =
                            task.scene_no != null ? `Scene ${task.scene_no}` : "chapter";
                          setNotice(
                            updated.status === "waiting_for_human"
                              ? updated.requires_human_approval && !updated.human_approved_at
                                ? `Repair task for ${where} needs your explicit approval — use Approve & apply on the task.`
                                : `Repair task for ${where} is held: it conflicts with another repair on the same span, or its last auto-apply failed. Resolve the overlap (or Reject one task), then apply again.`
                              : `Repair applied for ${where} (status: ${updated.status}). Span patches land immediately; instruction repairs queue a scene revision that drafts in the background — watch the queue indicator, then Verify.`,
                          );
                        })
                      }
                      onApproveApply={() => {
                        if (
                          !window.confirm(
                            `Approve & apply this ${task.authority_level.replace(/_/g, " ")} repair? It touches more than one scene, queueing a revision for every affected scene.`,
                          )
                        )
                          return;
                        void runAction(
                          `apply:${task.id}`,
                          () => api.approveApplyRepairTask(task.id),
                          { reloadRuns: true },
                        ).then((updated) => {
                          if (!updated) return;
                          setNotice(
                            `Approved — repair is running (status: ${updated.status}). Revisions draft in the background; watch the queue indicator, then Verify.`,
                          );
                        });
                      }}
                      onVerify={() =>
                        // Verification records a verdict and moves task/issue statuses — it never
                        // touches artifacts/prose, so the slim refresh is enough.
                        void runAction(
                          `verify:${task.id}`,
                          async () => {
                            await api.verifyRepairTask(task.id);
                            return api.repairTask(task.id);
                          },
                          { reloadRuns: true, refresh: "slim" },
                        ).then((updated) => {
                          if (updated)
                            setNotice(
                              `Verification recorded for ${task.scene_no != null ? `Scene ${task.scene_no}` : "the chapter"} — task status: ${updated.status}.`,
                            );
                        })
                      }
                    />
                  ))
                ) : (
                  <div style={css("color:var(--dim);font-size:13px")}>
                    No repair tasks queued yet.
                  </div>
                )}
              </div>
            </Panel>
          </div>

          <Panel pad="18px 20px">
            <div style={css("display:grid;grid-template-columns:1.15fr .85fr;gap:18px")}>
              <div>
                <Eyebrow>{finalArtifact ? "Final chapter" : "Assembled chapter"}</Eyebrow>
                <div style={css("margin-top:14px")}>
                  {finalText ? (
                    <ProseBlocks text={finalText} proseSize="16px" />
                  ) : (
                    <div style={css("color:var(--dim);font-size:13px")}>
                      No assembled chapter prose is available on this run yet.
                    </div>
                  )}
                </div>
              </div>

              <div style={css("display:flex;flex-direction:column;gap:18px")}>
                <div>
                  <Eyebrow>Sequence</Eyebrow>
                  <div style={css("display:flex;flex-direction:column;gap:8px;margin-top:12px")}>
                    {sequenceScenes.length ? (
                      sequenceScenes.map((scene) => {
                        const row = scene as { scene_no?: number; scene_function?: string };
                        return (
                          <div
                            key={row.scene_no}
                            style={css(
                              "padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg3)",
                            )}
                          >
                            <div
                              style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}
                            >
                              Scene {row.scene_no ?? "—"}
                            </div>
                            <div style={css("margin-top:4px;font-size:13px;color:var(--ink)")}>
                              {row.scene_function || "No scene function"}
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <div style={css("color:var(--dim);font-size:13px")}>
                        No chapter sequence is available yet.
                      </div>
                    )}
                  </div>
                </div>

                <div>
                  <Eyebrow>Run QA</Eyebrow>
                  <pre
                    style={css(
                      "margin:12px 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:var(--r);background:var(--boxbg);font-family:var(--mono);font-size:11.5px;white-space:pre-wrap;color:var(--ink)",
                    )}
                  >
                    {JSON.stringify(qaArtifact?.body ?? detail?.run.summary_json ?? {}, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          </Panel>

          <Panel eyebrow="Event trail" pad="18px 20px">
            {detail ? (
              <EventFeed detail={detail} />
            ) : (
              <div style={css("color:var(--dim);font-size:13px")}>
                Pick a run to inspect its audit trail.
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

// --- structured blocked state (Workstream I) --------------------------------------------------------
// Production drafts FROM the approved chapter packet, so every precondition failure resolves in the
// Packets tab. This panel says WHY (the packet's machine-readable blockers / repair tasks) and offers
// the direct path there — never a raw exception string.

function violationChipTone(v: PacketViolation): ChipTone {
  if (v.blocks_drafting) return "bad";
  if (v.blocks_final_export) return "warn";
  return "neutral";
}

function ViolationRow({ violation }: { violation: PacketViolation }) {
  return (
    <div
      style={css(
        "display:flex;align-items:flex-start;gap:10px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg3)",
      )}
    >
      <Chip label={violation.severity} tone={violationChipTone(violation)} size="sm" />
      <div style={css("min-width:0;flex:1")}>
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          {violation.kind}
          {violation.field ? ` · ${violation.field}` : ""}
        </div>
        <div style={css("margin-top:2px;font-size:12.5px;color:var(--ink);line-height:1.4")}>
          {violation.detail}
        </div>
      </div>
    </div>
  );
}

function ProductionGatePanel({
  title,
  packet,
  packetLoaded,
  onGoToPackets,
}: {
  title: string;
  packet: PacketOut | null;
  packetLoaded: boolean;
  onGoToPackets: () => void;
}) {
  const guidance = packet?.status === "blocked" ? packetBlockedGuidance(packet) : null;
  const blockers = packet ? packetDraftBlockers(packet.qa_warnings) : [];
  const repairs = packet ? packetRepairTasks(packet.qa_warnings) : [];
  const advisories = packet ? packetAdvisories(packet.qa_warnings) : [];
  const audit = packet ? packetSurfaceAudit(packet.qa_warnings) : [];
  const [auditOpen, setAuditOpen] = useState(false);

  let lead: string;
  if (!packetLoaded) {
    lead = "Checking this chapter's packet…";
  } else if (!packet) {
    lead =
      "This chapter has no chapter packet yet. Production drafts from an approved chapter packet — create, QA, and approve one in the Packets tab first.";
  } else if (packet.status === "blocked") {
    lead =
      guidance?.reason ??
      "The chapter packet is blocked, so production cannot start until the blocking gate is resolved.";
  } else if (packet.status === "approved") {
    lead =
      "The chapter packet is approved. If this run was blocked by an earlier packet, refresh and start a new run.";
  } else {
    lead = `A chapter packet exists but is not approved yet (status: ${packet.status}). ${
      packet.approval_blockers[0] ?? "Review and approve it in the Packets tab."
    }`;
  }

  return (
    <div data-testid="production-gate">
      <Panel
        pad="18px 20px"
        style="border-left:3px solid var(--bad);display:flex;flex-direction:column;gap:12px"
      >
        <div>
          <Eyebrow>{guidance ? guidance.title : title}</Eyebrow>
          <div style={css("margin-top:8px;font-size:13.5px;color:var(--ink);line-height:1.5")}>
            {lead}
          </div>
        </div>

        {guidance && guidance.actions.length > 0 && (
          <div style={css("display:flex;flex-direction:column;gap:4px")}>
            {guidance.actions.map((action, i) => (
              <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
                · {action}
              </div>
            ))}
          </div>
        )}

        {blockers.length > 0 && (
          <div>
            <Eyebrow>Blocking · {blockers.length}</Eyebrow>
            <div style={css("display:flex;flex-direction:column;gap:6px;margin-top:8px")}>
              {blockers.map((v, i) => (
                <ViolationRow key={i} violation={v} />
              ))}
            </div>
          </div>
        )}

        {repairs.length > 0 && (
          <div>
            <Eyebrow>Repair tasks · {repairs.length} · fixable, not blocking</Eyebrow>
            <div style={css("display:flex;flex-direction:column;gap:6px;margin-top:8px")}>
              {repairs.map((v, i) => (
                <ViolationRow key={i} violation={v} />
              ))}
            </div>
            <div style={css("margin-top:8px;font-size:12px;color:var(--dim);line-height:1.45")}>
              Repair tasks never block approval or drafting — approve the packet and drafting can
              proceed. Final export waits until they are resolved.
            </div>
          </div>
        )}

        {advisories.length > 0 && (
          <div>
            <Eyebrow>Advisory · {advisories.length}</Eyebrow>
            <div style={css("display:flex;flex-direction:column;gap:6px;margin-top:8px")}>
              {advisories.map((v, i) => (
                <ViolationRow key={i} violation={v} />
              ))}
            </div>
          </div>
        )}

        {audit.length > 0 && (
          <div>
            <button
              onClick={() => setAuditOpen((v) => !v)}
              style={css(
                "display:flex;align-items:center;gap:8px;background:none;border:none;padding:0;cursor:pointer;text-align:left",
              )}
            >
              <Eyebrow>
                {auditOpen ? "▾" : "▸"} Surface projection audit · {audit.length}
              </Eyebrow>
            </button>
            <div style={css("margin-top:4px;font-size:12.5px;color:var(--dim)")}>
              {surfaceAuditSummary(audit)}
            </div>
            {auditOpen && (
              <div style={css("display:flex;flex-direction:column;gap:6px;margin-top:8px")}>
                {audit.map((v, i) => (
                  <ViolationRow key={i} violation={v} />
                ))}
              </div>
            )}
          </div>
        )}

        <div style={css("display:flex;gap:10px;align-items:center;flex-wrap:wrap")}>
          <Button variant="primary" size="sm" onClick={onGoToPackets}>
            Go to Packets
          </Button>
          <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
            create / repair / QA / approve the chapter packet, then start the run again
          </span>
        </div>
      </Panel>
    </div>
  );
}

function IssueRow({ issue, onOpenPacket }: { issue: IssueOut; onOpenPacket?: () => void }) {
  return (
    <div
      style={css(
        "padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--bg3)",
      )}
    >
      <div style={css("display:flex;justify-content:space-between;gap:10px;align-items:center")}>
        <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          {issue.scene_no != null ? `Scene ${issue.scene_no}` : "Chapter"}
        </span>
        <span style={css("display:inline-flex;gap:5px;align-items:center")}>
          <Chip label={issue.severity} tone={severityChipTone(issue.severity)} size="sm" />
          <Chip label={issue.status.replace(/_/g, " ")} tone="neutral" size="sm" />
        </span>
      </div>
      <div style={css("margin-top:6px;font-size:13px;color:var(--ink)")}>{issue.claim}</div>
      <div style={css("margin-top:6px;font-size:12px;color:var(--dim)")}>
        {issue.validator} · {issue.issue_kind}
      </div>
      {issue.quote && (
        <div
          style={css(
            "margin-top:8px;padding:8px 10px;border-left:2px solid var(--line);background:var(--bg2);font-size:12px;color:var(--dim)",
          )}
        >
          “{issue.quote}”
        </div>
      )}
      {/* The inbox is display-only by design, but "where do I act on this?" must not be a scavenger
          hunt — editing/approval live on the Packets tab, one click away. */}
      {onOpenPacket && (
        <div style={css("margin-top:8px")}>
          <Button size="sm" variant="ghost" onClick={onOpenPacket}>
            {issue.scene_no != null ? "Open scene packet →" : "Open chapter packet →"}
          </Button>
        </div>
      )}
    </div>
  );
}

function RepairRow({
  task,
  busy,
  onApply,
  onApproveApply,
  onVerify,
}: {
  task: RepairTaskOut;
  busy: string | null;
  onApply: () => void;
  onApproveApply: () => void;
  onVerify: () => void;
}) {
  // Not yet human-approved: the ONLY way this task ever executes is the explicit Approve & apply
  // (the background drain skips it; plain Apply just re-parks it). Once approved, the stamp covers
  // the task's whole repair loop, so re-queued attempts go back to plain Apply.
  const needsApproval = task.requires_human_approval && !task.human_approved_at;
  return (
    <div
      style={css(
        "padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--bg3)",
      )}
    >
      <div style={css("display:flex;justify-content:space-between;gap:10px;align-items:center")}>
        <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          {task.scene_no != null ? `Scene ${task.scene_no}` : "Chapter"}
        </span>
        <span style={css("display:inline-flex;gap:5px;align-items:center")}>
          {needsApproval && <Chip label="needs approval" tone="warn" size="sm" />}
          <Chip
            label={task.status.replace(/_/g, " ")}
            tone={statusChipTone(task.status)}
            size="sm"
          />
        </span>
      </div>
      <div style={css("margin-top:6px;font-size:13px;color:var(--ink)")}>
        {task.repair_kind} · {task.authority_level}
      </div>
      <div style={css("margin-top:6px;font-size:12px;color:var(--dim);white-space:pre-wrap")}>
        {task.instructions}
      </div>
      {needsApproval && (
        <div style={css("margin-top:8px;font-family:var(--mono);font-size:11px;color:var(--warn)")}>
          {task.authority_level.replace(/_/g, " ")} repair — touches more than one scene; applies
          only with your explicit approval.
        </div>
      )}
      {/* Disabled while ANY action runs (busy != null), not just this task's own: `busy` is a single
          slot, so per-label disabling let a second click flip it and re-enable the first button while
          its request was still in flight — a double-submit race. */}
      <div style={css("display:flex;gap:8px;margin-top:10px;flex-wrap:wrap")}>
        {needsApproval ? (
          <Button
            size="sm"
            variant="primary"
            disabled={busy != null}
            title="Runs the repair after your explicit approval — the auto-drain never picks this task up"
            onClick={onApproveApply}
          >
            {busy === `apply:${task.id}` ? "Applying…" : "Approve & apply"}
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={busy != null}
            title={busy != null ? "Another action is still running" : undefined}
            onClick={onApply}
          >
            {busy === `apply:${task.id}` ? "Applying…" : "Apply"}
          </Button>
        )}
        <Button
          size="sm"
          disabled={busy != null}
          title={busy != null ? "Another action is still running" : undefined}
          onClick={onVerify}
        >
          {busy === `verify:${task.id}` ? "Verifying…" : "Verify"}
        </Button>
      </div>
    </div>
  );
}
