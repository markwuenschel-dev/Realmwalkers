"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "../api/client";
import type {
  ArtifactOut,
  IssueOut,
  PacketOut,
  ProductionRunActionOut,
  ProductionRunDetailOut,
  ProductionRunOut,
  RepairTaskOut,
} from "../api/types";
import { useDeskData } from "../api/data";
import { css } from "../css";
import ProseBlocks from "../components/ProseBlocks";
import { Spinner } from "../components/DraftActivity";
import {
  isNoApprovedPacketError,
  packetBlockedGuidance,
  packetDraftBlockers,
  packetRepairTasks,
} from "../lib/packetBlockers";
import type { PacketViolation } from "../lib/packetBlockers";
import { useDesk } from "../state";

const PANEL =
  "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px";
const SMALL =
  "font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)";

function latestArtifact(artifacts: ArtifactOut[], type: string): ArtifactOut | null {
  return [...artifacts].reverse().find((artifact) => artifact.artifact_type === type) ?? null;
}

function severityColor(severity: string): string {
  switch (severity) {
    case "hard":
      return "var(--bad)";
    case "warn":
      return "var(--warn)";
    default:
      return "var(--info)";
  }
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

function summaryCount(summary: Record<string, unknown> | null | undefined, key: string): string {
  const value = summary?.[key];
  return typeof value === "number" ? value.toLocaleString() : "—";
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

export default function ProductionScreen() {
  const { t } = useDesk();
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

  const loadDetail = useCallback(async (targetRunId: string | null) => {
    if (!targetRunId) {
      setDetail(null);
      return;
    }
    const out = await api.productionRun(targetRunId);
    setDetail(out);
  }, []);

  const loadRuns = useCallback(async (targetChapterId: string) => {
    setLoading(true);
    setStartBlocked(false); // re-evaluated on every refresh / chapter switch
    try {
      const out = await api.productionRuns(targetChapterId);
      setRuns(out);
      setError(null);
      setRunId((current) => {
        if (current && out.some((run) => run.id === current)) return current;
        return out[0]?.id ?? null;
      });
    } catch (e) {
      setRuns([]);
      setDetail(null);
      setRunId(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

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
    if (!runId) {
      setDetail(null);
      return;
    }
    void loadDetail(runId).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : String(e));
    });
  }, [loadDetail, runId]);

  const chapter = orderedChapters.find((row) => row.id === chapterId) ?? null;
  const finalArtifact = detail ? latestArtifact(detail.artifacts, "final_chapter") : null;
  const draftArtifact = detail ? latestArtifact(detail.artifacts, "chapter_draft") : null;
  const qaArtifact = detail ? latestArtifact(detail.artifacts, "chapter_draft_qa") : null;
  const finalText =
    typeof finalArtifact?.body.prose === "string"
      ? finalArtifact.body.prose
      : typeof draftArtifact?.body.prose === "string"
        ? draftArtifact.body.prose
        : "";

  const runAction = useCallback(
    async (
      label: string,
      fn: () => Promise<ProductionRunActionOut | RepairTaskOut>,
      opts?: { reloadRuns?: boolean; reloadDetail?: boolean },
    ) => {
      setBusy(label);
      try {
        await fn();
        setError(null);
        if (opts?.reloadRuns && chapterId) await loadRuns(chapterId);
        if (opts?.reloadDetail !== false) await loadDetail(runId);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [chapterId, loadDetail, loadRuns, runId],
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
  // the chapter packet (404 = none yet) to say WHY and list its blockers / repair tasks.
  const blockedRun = detail?.run.status === "blocked";
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
  const sequenceScenes = Array.isArray(detail?.chapter_sequence?.body?.scenes)
    ? detail?.chapter_sequence?.body?.scenes
    : [];

  return (
    <div style={css("display:flex;flex-direction:column;gap:18px")}>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap",
        )}
      >
        <div>
          <h1
            style={css(
              "margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)",
            )}
          >
            Editorial production
          </h1>
          <p style={css("margin:0;color:var(--dim);font-size:14px;max-width:760px")}>
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
          <button
            disabled={!chapterId || busy === "start"}
            onClick={() => void startRun()}
            style={css(
              `height:34px;padding:0 14px;border:none;border-radius:9px;background:${t.accent};color:${t.onAccent};font-family:var(--ui);font-size:12.5px;cursor:${!chapterId || busy === "start" ? "default" : "pointer"}`,
            )}
          >
            {busy === "start" ? "Starting…" : "Start run"}
          </button>
          <button
            disabled={!chapterId || loading}
            onClick={() => chapterId && void loadRuns(chapterId)}
            style={css(
              "height:34px;padding:0 14px;border-radius:9px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:12.5px",
            )}
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div
          style={css(
            "border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--bg2));border-radius:10px;padding:12px 14px;color:var(--bad);font-size:13px",
          )}
        >
          {error}
        </div>
      )}

      {showGate && (
        <ProductionGatePanel
          title={
            startBlocked ? "Blocked — no approved chapter packet" : "This production run is blocked"
          }
          packet={gatePacket}
          packetLoaded={gatePacketLoaded}
          accent={t.accent}
          onAccent={t.onAccent}
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
        <div style={css(`${PANEL};display:flex;flex-direction:column;gap:12px`)}>
          <div>
            <div style={css(SMALL)}>Chapter</div>
            <div
              style={css(
                "margin-top:6px;font-family:var(--display);font-size:20px;color:var(--ink)",
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
            <div style={css(SMALL)}>Runs</div>
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
                      onClick={() => setRunId(run.id)}
                      style={css(
                        `text-align:left;padding:10px 12px;border-radius:10px;border:1px solid ${active ? "var(--accent)" : "var(--line)"};background:${active ? "color-mix(in srgb,var(--accent) 8%,var(--bg3))" : "var(--bg3)"};color:var(--ink);cursor:pointer`,
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
                        <span
                          style={css(
                            `font-family:var(--mono);font-size:11px;color:${statusTone(run.status)}`,
                          )}
                        >
                          {run.status}
                        </span>
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
        </div>

        <div style={css("display:flex;flex-direction:column;gap:18px")}>
          <div style={css("display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px")}>
            <MetricCard
              label="Status"
              value={detail?.run.status ?? "—"}
              tone={statusTone(detail?.run.status ?? "")}
            />
            <MetricCard
              label="Issues"
              value={summaryCount(detail?.run.summary_json, "issue_count")}
              tone="var(--warn)"
            />
            <MetricCard
              label="Repair tasks"
              value={summaryCount(detail?.run.summary_json, "repair_task_count")}
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
              <button
                disabled={busy === "triage"}
                onClick={() =>
                  void runAction("triage", () => api.triageProductionRun(detail.run.id), {
                    reloadRuns: true,
                  })
                }
                style={css(
                  "height:32px;padding:0 14px;border-radius:9px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:12.5px",
                )}
              >
                {busy === "triage" ? "Triaging…" : "Auto-triage"}
              </button>
              <button
                disabled={busy === "assemble"}
                onClick={() =>
                  void runAction("assemble", () => api.assembleProductionRun(detail.run.id), {
                    reloadRuns: true,
                  })
                }
                style={css(
                  "height:32px;padding:0 14px;border-radius:9px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:12.5px",
                )}
              >
                {busy === "assemble" ? "Assembling…" : "Refresh assembly"}
              </button>
            </div>
          )}

          <div style={css(`${PANEL};display:grid;grid-template-columns:1.1fr .9fr;gap:18px`)}>
            <div>
              <div style={css(SMALL)}>Issue inbox</div>
              <div style={css("display:flex;flex-direction:column;gap:10px;margin-top:12px")}>
                {issues.length ? (
                  issues.map((issue) => <IssueRow key={issue.id} issue={issue} />)
                ) : (
                  <div style={css("color:var(--dim);font-size:13px")}>
                    No structured issues on this run.
                  </div>
                )}
              </div>
            </div>

            <div>
              <div style={css(SMALL)}>Repair tasks</div>
              <div style={css("display:flex;flex-direction:column;gap:10px;margin-top:12px")}>
                {repairTasks.length ? (
                  repairTasks.map((task) => (
                    <RepairRow
                      key={task.id}
                      task={task}
                      busy={busy}
                      onApply={() =>
                        void runAction(`apply:${task.id}`, () => api.applyRepairTask(task.id), {
                          reloadRuns: true,
                        })
                      }
                      onVerify={() =>
                        void runAction(
                          `verify:${task.id}`,
                          async () => {
                            await api.verifyRepairTask(task.id);
                            return api.repairTask(task.id);
                          },
                          { reloadRuns: true },
                        )
                      }
                    />
                  ))
                ) : (
                  <div style={css("color:var(--dim);font-size:13px")}>
                    No repair tasks queued yet.
                  </div>
                )}
              </div>
            </div>
          </div>

          <div style={css(`${PANEL};display:grid;grid-template-columns:1.15fr .85fr;gap:18px`)}>
            <div>
              <div style={css(SMALL)}>{finalArtifact ? "Final chapter" : "Assembled chapter"}</div>
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
                <div style={css(SMALL)}>Sequence</div>
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
                <div style={css(SMALL)}>Run QA</div>
                <pre
                  style={css(
                    "margin:12px 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--bg3);font-family:var(--mono);font-size:11.5px;white-space:pre-wrap;color:var(--ink)",
                  )}
                >
                  {JSON.stringify(qaArtifact?.body ?? detail?.run.summary_json ?? {}, null, 2)}
                </pre>
              </div>
            </div>
          </div>

          <div style={css(PANEL)}>
            <div style={css(SMALL)}>Event trail</div>
            <div style={css("margin-top:12px")}>
              {detail ? (
                <EventFeed detail={detail} />
              ) : (
                <div style={css("color:var(--dim);font-size:13px")}>
                  Pick a run to inspect its audit trail.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricCard({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div style={css(`${PANEL};padding:14px 16px`)}>
      <div style={css(SMALL)}>{label}</div>
      <div style={css(`margin-top:8px;font-family:var(--display);font-size:24px;color:${tone}`)}>
        {value}
      </div>
    </div>
  );
}

// --- structured blocked state (Workstream I) --------------------------------------------------------
// Production drafts FROM the approved chapter packet, so every precondition failure resolves in the
// Packets tab. This panel says WHY (the packet's machine-readable blockers / repair tasks) and offers
// the direct path there — never a raw exception string.

function violationTone(v: PacketViolation): string {
  if (v.blocks_drafting) return "var(--bad)";
  if (v.blocks_final_export) return "var(--warn)";
  return "var(--dim)";
}

function ViolationRow({ violation }: { violation: PacketViolation }) {
  return (
    <div
      style={css(
        "display:flex;align-items:flex-start;gap:10px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg3)",
      )}
    >
      <span
        style={css(
          `flex:none;font-family:var(--mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:${violationTone(violation)};border:1px solid color-mix(in srgb,${violationTone(violation)} 40%,var(--line));border-radius:5px;padding:2px 6px;margin-top:1px`,
        )}
      >
        {violation.severity}
      </span>
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
  accent,
  onAccent,
  onGoToPackets,
}: {
  title: string;
  packet: PacketOut | null;
  packetLoaded: boolean;
  accent: string;
  onAccent: string;
  onGoToPackets: () => void;
}) {
  const guidance = packet?.status === "blocked" ? packetBlockedGuidance(packet) : null;
  const blockers = packet ? packetDraftBlockers(packet.qa_warnings) : [];
  const repairs = packet ? packetRepairTasks(packet.qa_warnings) : [];

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
    <div
      style={css(
        `${PANEL};border-left:3px solid var(--bad);display:flex;flex-direction:column;gap:12px`,
      )}
      data-testid="production-gate"
    >
      <div>
        <div style={css(SMALL)}>{guidance ? guidance.title : title}</div>
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
          <div style={css(SMALL)}>Blocking · {blockers.length}</div>
          <div style={css("display:flex;flex-direction:column;gap:6px;margin-top:8px")}>
            {blockers.map((v, i) => (
              <ViolationRow key={i} violation={v} />
            ))}
          </div>
        </div>
      )}

      {repairs.length > 0 && (
        <div>
          <div style={css(SMALL)}>Repair tasks · {repairs.length} · fixable, not blocking</div>
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

      <div style={css("display:flex;gap:10px;align-items:center;flex-wrap:wrap")}>
        <button
          onClick={onGoToPackets}
          style={css(
            `height:32px;padding:0 14px;border:none;border-radius:9px;background:${accent};color:${onAccent};font-family:var(--ui);font-size:12.5px;cursor:pointer`,
          )}
        >
          Go to Packets
        </button>
        <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          create / repair / QA / approve the chapter packet, then start the run again
        </span>
      </div>
    </div>
  );
}

function IssueRow({ issue }: { issue: IssueOut }) {
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
        <span
          style={css(
            `font-family:var(--mono);font-size:11px;color:${severityColor(issue.severity)}`,
          )}
        >
          {issue.severity} · {issue.status}
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
    </div>
  );
}

function RepairRow({
  task,
  busy,
  onApply,
  onVerify,
}: {
  task: RepairTaskOut;
  busy: string | null;
  onApply: () => void;
  onVerify: () => void;
}) {
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
        <span
          style={css(`font-family:var(--mono);font-size:11px;color:${statusTone(task.status)}`)}
        >
          {task.status}
        </span>
      </div>
      <div style={css("margin-top:6px;font-size:13px;color:var(--ink)")}>
        {task.repair_kind} · {task.authority_level}
      </div>
      <div style={css("margin-top:6px;font-size:12px;color:var(--dim);white-space:pre-wrap")}>
        {task.instructions}
      </div>
      <div style={css("display:flex;gap:8px;margin-top:10px;flex-wrap:wrap")}>
        <button
          disabled={task.requires_human_approval || busy === `apply:${task.id}`}
          onClick={onApply}
          style={css(
            "height:30px;padding:0 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);font-size:12px",
          )}
        >
          {busy === `apply:${task.id}` ? "Applying…" : "Apply"}
        </button>
        <button
          disabled={busy === `verify:${task.id}`}
          onClick={onVerify}
          style={css(
            "height:30px;padding:0 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);font-size:12px",
          )}
        >
          {busy === `verify:${task.id}` ? "Verifying…" : "Verify"}
        </button>
      </div>
    </div>
  );
}
