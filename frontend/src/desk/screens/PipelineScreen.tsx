"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { api } from "../api/client";
import { useDeskData } from "../api/data";
import { useDeskPipeline } from "../api/hooks/useDeskPipeline";
import { css } from "../css";
import { formatElapsed } from "../components/DraftActivity";
import { Button, Chip, Eyebrow, MetricCard, Panel, Spinner } from "../components/ui";
import type { ChipTone } from "../components/ui";
import type {
  ActivityOut,
  PipelineIssueRef,
  PipelineJobOut,
  PipelineRepairTaskRef,
  PipelineRunRef,
  PipelineStatusOut,
  SweeperStatusOut,
} from "../api/types";

// The live Pipeline dashboard — everything the production pipeline is doing at a glance, honestly:
// what's running NOW, what's QUEUED (and that it runs one at a time), what's WAITING on you (and
// exactly why), what's BLOCKED (and how to unblock it), what's recently COMPLETED, and whether the
// autonomous sweeper is alive. All the reason/action strings come pre-computed from the endpoint; this
// screen just renders them and wires each action to an existing endpoint or a deep-link.

const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";
const MONO = "font-family:var(--mono);font-size:10.5px;color:var(--dim)";
const CARD =
  "border:1px solid var(--line);border-radius:var(--r);padding:13px 14px;background:var(--bg3)";

function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function place(x: { chapter_no?: number | null; scene_no?: number | null }): string {
  const ch = x.chapter_no != null ? `Ch ${x.chapter_no}` : "Ch ?";
  return x.scene_no != null ? `${ch} · Scene ${x.scene_no}` : ch;
}

const RUN_TONE: Record<string, ChipTone> = {
  completed: "good",
  repairing: "warn",
  running: "info",
  queued: "info",
  waiting_for_human: "warn",
};

const SEV_TONE: Record<string, ChipTone> = {
  block: "bad",
  repair: "warn",
  warn: "warn",
  info: "info",
};

const SEV_COLOR: Record<string, string> = {
  info: "var(--info)",
  success: "var(--good)",
  warn: "var(--warn)",
  error: "var(--bad)",
};

// --- sweeper heartbeat line -----------------------------------------------------------------------
function sweeperLine(sw: SweeperStatusOut): { text: string; tone: string } {
  if (!sw.autonomy_enabled)
    return { text: "Autonomy off — the sweeper isn't driving runs", tone: "var(--dim)" };
  if (sw.paused)
    return {
      text: "Autonomy paused — the queue is paused, so the sweep is idle",
      tone: "var(--warn)",
    };
  const parts = ["Autonomy on"];
  if (sw.last_tick_at) parts.push(`swept ${relTime(sw.last_tick_at)}`);
  if (sw.driving.length)
    parts.push(`driving ${sw.driving.length} run${sw.driving.length === 1 ? "" : "s"}`);
  else if (sw.ran) parts.push("idle — nothing stale to drive");
  return { text: parts.join(" · "), tone: "var(--good)" };
}

// --- action cards ---------------------------------------------------------------------------------
function CardShell({
  title,
  chips,
  reason,
  children,
}: {
  title: string;
  chips?: ReactNode;
  reason?: string | null;
  children?: ReactNode;
}) {
  return (
    <div style={css(CARD)}>
      <div style={css("display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px")}>
        <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>
          {title}
        </span>
        {chips}
      </div>
      {reason && (
        <div
          style={css(
            "font-family:var(--ui);font-size:12.5px;color:var(--dim);line-height:1.5;margin-bottom:10px",
          )}
        >
          {reason}
        </div>
      )}
      {children}
    </div>
  );
}

function ActionRow({ children }: { children: ReactNode }) {
  return (
    <div style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}>{children}</div>
  );
}

export default function PipelineScreen() {
  const data = useDeskData();
  const router = useRouter();
  const { pipeline, refreshPipeline } = useDeskPipeline(data.bookId, true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const goProduction = (chapterId: string) => router.push(`/production?chapter=${chapterId}`);
  const goPackets = (chapterId: string) => router.push(`/packets?chapter=${chapterId}`);

  const act = useCallback(
    async (id: string, fn: () => Promise<unknown>, okMsg: string) => {
      setBusyId(id);
      try {
        await fn();
        data.pushToast({ tone: "success", message: okMsg });
        await refreshPipeline();
      } catch {
        data.pushToast({ tone: "error", message: "That action didn't go through — try again" });
      } finally {
        setBusyId(null);
      }
    },
    [data, refreshPipeline],
  );

  // Always-visible live event feed (its own light poll — the drawer's feed is gated to the drawer).
  const [events, setEvents] = useState<ActivityOut[]>([]);
  useEffect(() => {
    const id = data.bookId;
    if (!id) return;
    let alive = true;
    let handle = 0;
    const tick = async () => {
      try {
        setEvents(await api.activity(id, 25));
      } catch {
        /* transient — keep the last snapshot */
      }
      if (alive) handle = window.setTimeout(tick, 4000);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [data.bookId]);

  if (!data.bookId) {
    return <div style={css(MONO)}>Select a book to see its pipeline.</div>;
  }

  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px",
        )}
      >
        <div>
          <h1 style={css(TITLE_XL)}>Pipeline</h1>
          <p style={css("margin:6px 0 0;color:var(--dim);font-size:14.5px")}>
            Everything production is doing right now — live, and honest about the order it runs in.
          </p>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void refreshPipeline()}
          title="Re-fetch the pipeline snapshot"
        >
          ⟳ Refresh
        </Button>
      </div>

      {pipeline === null ? (
        <div
          style={css(
            "display:flex;align-items:center;gap:10px;color:var(--dim);font-family:var(--mono);font-size:12px",
          )}
        >
          <Spinner size={13} /> loading the pipeline…
        </div>
      ) : (
        <PipelineBody
          p={pipeline}
          busyId={busyId}
          act={act}
          events={events}
          onProduction={goProduction}
          onPackets={goPackets}
          onPauseToggle={() => void data.setQueuePaused(!pipeline.queue.queue_paused)}
        />
      )}
    </div>
  );
}

function PipelineBody({
  p,
  busyId,
  act,
  events,
  onProduction,
  onPackets,
  onPauseToggle,
}: {
  p: PipelineStatusOut;
  busyId: string | null;
  act: (id: string, fn: () => Promise<unknown>, okMsg: string) => Promise<void>;
  events: ActivityOut[];
  onProduction: (chapterId: string) => void;
  onPackets: (chapterId: string) => void;
  onPauseToggle: () => void;
}) {
  const sw = sweeperLine(p.sweeper);
  const runningCount = p.now.jobs.length + p.now.agent_runs.length;
  const queuedCount =
    p.queue.jobs_queued + p.queue.repair_tasks_auto.length + p.queue.repair_tasks_approval.length;
  const waitingCount =
    p.waiting_on_human.runs.length +
    p.waiting_on_human.repair_tasks.length +
    p.waiting_on_human.issues.length;
  const blockedCount = p.blocked.runs.length + p.blocked.failed_jobs.length;

  return (
    <div style={css("display:flex;flex-direction:column;gap:18px")}>
      {/* Sweeper heartbeat line */}
      <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
        <span
          style={css(
            `width:9px;height:9px;border-radius:50%;flex:none;background:${sw.tone};${p.sweeper.autonomy_enabled && !p.sweeper.paused ? "animation:pulseDot 1.6s ease-in-out infinite" : ""}`,
          )}
        />
        <span style={css(`font-family:var(--mono);font-size:12px;color:${sw.tone}`)}>
          {sw.text}
        </span>
        {p.sweeper.last_error && (
          <span
            style={css("font-family:var(--mono);font-size:10.5px;color:var(--bad)")}
            title={p.sweeper.last_error}
          >
            · last sweep hit an error
          </span>
        )}
      </div>

      {/* Glance metrics */}
      <div
        style={css(
          "display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px",
        )}
      >
        <MetricCard
          label="Running now"
          value={String(runningCount)}
          tone={runningCount ? "var(--info)" : "var(--dim)"}
        />
        <MetricCard label="Queued" value={String(queuedCount)} hint="runs one at a time" />
        <MetricCard
          label="Waiting on you"
          value={String(waitingCount)}
          tone={waitingCount ? "var(--warn)" : "var(--dim)"}
        />
        <MetricCard
          label="Blocked"
          value={String(blockedCount)}
          tone={blockedCount ? "var(--bad)" : "var(--dim)"}
        />
        <MetricCard label="Completed" value={String(p.completed.runs.length)} tone="var(--good)" />
      </div>

      {/* NOW */}
      <Panel eyebrow="Now" title="Running">
        {runningCount === 0 && p.now.runs.length === 0 ? (
          <div style={css(MONO)}>idle — nothing is running right now</div>
        ) : (
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {p.now.jobs.map((j) => (
              <NowJob key={j.id} j={j} />
            ))}
            {p.now.runs.map((r) => (
              <div key={r.run_id} style={css(CARD)}>
                <div style={css("display:flex;align-items:center;gap:8px;flex-wrap:wrap")}>
                  <Spinner size={12} />
                  <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>
                    {place(r)}
                  </span>
                  <Chip
                    label={String(r.status).replace(/_/g, " ")}
                    tone={RUN_TONE[r.status] ?? "info"}
                    size="sm"
                  />
                  <span style={css(MONO)}>{r.reason}</span>
                  {r.scenes_expected != null && (
                    <span style={css("margin-left:auto")}>
                      <SceneProgress drafted={r.scenes_drafted} expected={r.scenes_expected} />
                    </span>
                  )}
                </div>
              </div>
            ))}
            {p.now.agent_runs.map((a) => (
              <div
                key={a.id}
                style={css(`${CARD};display:flex;align-items:center;gap:8px;flex-wrap:wrap`)}
              >
                <Spinner size={11} />
                <span style={css("font-family:var(--ui);font-size:13px;color:var(--ink)")}>
                  {a.agent_name}
                </span>
                <Chip label={a.stage.replace(/_/g, " ")} tone="info" size="sm" />
                {a.started_at && (
                  <span style={css(`${MONO};margin-left:auto`)}>{relTime(a.started_at)}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* QUEUE */}
      <Panel
        eyebrow="Queue"
        title="Runs one at a time"
        actions={
          <span style={css("display:flex;align-items:center;gap:8px")}>
            {p.queue.queue_paused && <Chip label="paused" size="sm" tone="warn" />}
            <Button
              size="sm"
              variant="ghost"
              onClick={onPauseToggle}
              title={
                p.queue.queue_paused
                  ? "Resume — the drain picks the queue back up"
                  : "Pause — the current item finishes, nothing new starts (survives redeploys)"
              }
            >
              {p.queue.queue_paused ? "Resume queue" : "Pause queue"}
            </Button>
          </span>
        }
      >
        <div
          style={css(
            "font-family:var(--ui);font-size:12.5px;color:var(--dim);line-height:1.5;margin-bottom:12px",
          )}
        >
          {p.queue.note}
        </div>
        {p.queue.jobs.length === 0 &&
        p.queue.repair_tasks_auto.length === 0 &&
        p.queue.repair_tasks_approval.length === 0 &&
        p.queue.runs_queued.length === 0 ? (
          <div style={css(MONO)}>{p.queue.queue_paused ? "queue paused" : "queue clear"}</div>
        ) : (
          <div style={css("display:flex;flex-direction:column;gap:14px")}>
            {p.queue.jobs.length > 0 && (
              <QueueGroup label={`Draft / revision jobs · ${p.queue.jobs_queued}`}>
                {p.queue.jobs.map((j) => (
                  <div
                    key={j.id}
                    style={css(
                      "display:flex;align-items:center;gap:10px;padding:8px 11px;border-radius:8px;background:var(--bg3)",
                    )}
                  >
                    <span style={css(`${MONO};width:18px;text-align:center;flex:none`)}>
                      {j.position}
                    </span>
                    <span
                      style={css("font-family:var(--ui);font-size:13px;color:var(--ink);flex:1")}
                    >
                      {place(j)}
                    </span>
                    {j.kind !== "draft" && (
                      <Chip label={j.kind.replace(/_/g, " ")} tone="info" size="sm" />
                    )}
                  </div>
                ))}
              </QueueGroup>
            )}
            {p.queue.repair_tasks_auto.length > 0 && (
              <QueueGroup
                label={`Repairs — run automatically · ${p.queue.repair_tasks_auto.length}`}
              >
                {p.queue.repair_tasks_auto.map((t) => (
                  <RepairQueueRow key={t.task_id} t={t} />
                ))}
              </QueueGroup>
            )}
            {p.queue.repair_tasks_approval.length > 0 && (
              <QueueGroup
                label={`Repairs — need your approval · ${p.queue.repair_tasks_approval.length}`}
              >
                {p.queue.repair_tasks_approval.map((t) => (
                  <RepairQueueRow key={t.task_id} t={t} approval />
                ))}
              </QueueGroup>
            )}
            {p.queue.runs_queued.length > 0 && (
              <QueueGroup label={`Production runs · ${p.queue.runs_queued.length}`}>
                {p.queue.runs_queued.map((r) => (
                  <div
                    key={r.run_id}
                    style={css(
                      "display:flex;align-items:center;gap:10px;padding:8px 11px;border-radius:8px;background:var(--bg3)",
                    )}
                  >
                    <span
                      style={css("font-family:var(--ui);font-size:13px;color:var(--ink);flex:1")}
                    >
                      {place(r)}
                    </span>
                    <span style={css(MONO)}>{r.reason}</span>
                  </div>
                ))}
              </QueueGroup>
            )}
          </div>
        )}
      </Panel>

      {/* WAITING ON YOU */}
      <Panel
        eyebrow="Waiting on you"
        title={waitingCount ? `${waitingCount} need a decision` : "Nothing waiting"}
      >
        {waitingCount === 0 ? (
          <div style={css(MONO)}>nothing is waiting on you — nice</div>
        ) : (
          <div
            style={css(
              "display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px",
            )}
          >
            {p.waiting_on_human.repair_tasks.map((t) => (
              <RepairActionCard
                key={t.task_id}
                t={t}
                busy={busyId === t.task_id}
                onVerify={() =>
                  void act(t.task_id, () => api.verifyRepairTask(t.task_id), "Verify started")
                }
                onApprove={() =>
                  void act(
                    t.task_id,
                    () => api.approveApplyRepairTask(t.task_id),
                    "Approved & applied",
                  )
                }
                onProduction={() => onProduction(t.chapter_id)}
              />
            ))}
            {p.waiting_on_human.runs.map((r) => (
              <RunActionCard
                key={r.run_id}
                r={r}
                onProduction={() => onProduction(r.chapter_id)}
                onPackets={() => onPackets(r.chapter_id)}
              />
            ))}
            {p.waiting_on_human.issues.map((i) => (
              <IssueActionCard
                key={i.issue_id}
                i={i}
                onProduction={() => onProduction(i.chapter_id)}
              />
            ))}
          </div>
        )}
      </Panel>

      {/* BLOCKED */}
      {blockedCount > 0 && (
        <Panel eyebrow="Blocked" title={`${blockedCount} stuck on a fault`}>
          <div
            style={css(
              "display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px",
            )}
          >
            {p.blocked.runs.map((r) => (
              <RunActionCard
                key={r.run_id}
                r={r}
                blocked
                onProduction={() => onProduction(r.chapter_id)}
                onPackets={() => onPackets(r.chapter_id)}
              />
            ))}
            {p.blocked.failed_jobs.length > 0 && (
              <CardShell
                title={`${p.blocked.failed_jobs.length} failed draft${p.blocked.failed_jobs.length === 1 ? "" : "s"}`}
                reason={p.blocked.failed_jobs[0]?.last_error ?? undefined}
              >
                <div style={css("display:flex;flex-direction:column;gap:5px;margin-bottom:10px")}>
                  {p.blocked.failed_jobs.slice(0, 4).map((j) => (
                    <div key={j.id} style={css(`${MONO};display:flex;gap:8px`)}>
                      <span style={css("color:var(--bad)")}>!</span>
                      <span>{place(j)}</span>
                    </div>
                  ))}
                </div>
                <ActionRow>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={busyId === "failed-jobs"}
                    onClick={() =>
                      void act(
                        "failed-jobs",
                        () => api.retryFailed(p.book_id),
                        "Re-queued failed drafts",
                      )
                    }
                  >
                    Retry all failed
                  </Button>
                </ActionRow>
              </CardShell>
            )}
          </div>
        </Panel>
      )}

      {/* COMPLETED */}
      <Panel
        eyebrow="Recently completed"
        title={p.completed.runs.length ? undefined : "Nothing completed yet"}
      >
        {p.completed.runs.length === 0 ? (
          <div style={css(MONO)}>no completed runs yet</div>
        ) : (
          <div style={css("display:flex;flex-direction:column;gap:8px")}>
            {p.completed.runs.map((r) => (
              <div
                key={r.run_id}
                onClick={() => onProduction(r.chapter_id)}
                style={css(
                  `${CARD};display:flex;align-items:center;gap:10px;flex-wrap:wrap;cursor:pointer`,
                )}
              >
                <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>
                  {place(r)}
                </span>
                <Chip label="completed" tone="good" size="sm" />
                {r.final_chapter_status && (
                  <Chip label={r.final_chapter_status.replace(/_/g, " ")} tone="good" size="sm" />
                )}
                {r.scenes_expected != null && (
                  <span style={css("margin-left:auto")}>
                    <SceneProgress drafted={r.scenes_drafted} expected={r.scenes_expected} />
                  </span>
                )}
                <span style={css(MONO)}>{relTime(r.updated_at)}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* LIVE EVENTS */}
      <Panel eyebrow="Live events" title="Activity">
        {events.length === 0 ? (
          <div style={css(MONO)}>nothing yet</div>
        ) : (
          <div
            style={css(
              "display:flex;flex-direction:column;gap:6px;max-height:360px;overflow-y:auto",
            )}
          >
            {events.map((e) => (
              <EventRow key={e.id} a={e} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

function NowJob({ j }: { j: PipelineJobOut }) {
  const elapsed = formatElapsed(j.elapsed_s);
  return (
    <div style={css(`${CARD};border-color:color-mix(in srgb,var(--info) 35%,var(--line))`)}>
      <div style={css("display:flex;align-items:center;gap:9px;flex-wrap:wrap")}>
        <Spinner size={13} />
        <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>
          {place(j)}
        </span>
        {j.kind !== "draft" && <Chip label={j.kind.replace(/_/g, " ")} tone="info" size="sm" />}
        {j.phase && (
          <span style={css("font-family:var(--mono);font-size:11px;color:var(--info)")}>
            {j.phase}
          </span>
        )}
        {elapsed && <span style={css(`${MONO};margin-left:auto`)}>{elapsed}</span>}
      </div>
    </div>
  );
}

function SceneProgress({ drafted, expected }: { drafted?: number | null; expected: number }) {
  const d = drafted ?? 0;
  const frac = expected > 0 ? Math.min(100, Math.round((d / expected) * 100)) : 0;
  return (
    <span style={css("display:inline-flex;align-items:center;gap:8px")}>
      <span
        style={css(
          "position:relative;width:70px;height:7px;border-radius:5px;background:var(--bg3);overflow:hidden;display:inline-block",
        )}
      >
        <span style={css(`position:absolute;inset:0;width:${frac}%;background:var(--good)`)} />
      </span>
      <span style={css(MONO)}>
        {d} of {expected} scenes
      </span>
    </span>
  );
}

function QueueGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Eyebrow style="margin-bottom:7px">{label}</Eyebrow>
      <div style={css("display:flex;flex-direction:column;gap:5px")}>{children}</div>
    </div>
  );
}

function RepairQueueRow({ t, approval = false }: { t: PipelineRepairTaskRef; approval?: boolean }) {
  return (
    <div
      style={css(
        "display:flex;align-items:center;gap:10px;padding:8px 11px;border-radius:8px;background:var(--bg3)",
      )}
    >
      <span style={css("font-family:var(--ui);font-size:13px;color:var(--ink);flex:1")}>
        {place(t)} · {t.repair_kind.replace(/_/g, " ")}
      </span>
      <Chip label={t.authority_level.replace(/_/g, " ")} tone="neutral" size="sm" />
      {approval && <Chip label="needs approval" tone="warn" size="sm" />}
    </div>
  );
}

function RepairActionCard({
  t,
  busy,
  onVerify,
  onApprove,
  onProduction,
}: {
  t: PipelineRepairTaskRef;
  busy: boolean;
  onVerify: () => void;
  onApprove: () => void;
  onProduction: () => void;
}) {
  return (
    <CardShell
      title={place(t)}
      reason={t.reason}
      chips={
        <>
          <Chip label={t.repair_kind.replace(/_/g, " ")} tone="neutral" size="sm" />
          <Chip label={t.authority_level.replace(/_/g, " ")} tone="neutral" size="sm" />
        </>
      }
    >
      <ActionRow>
        {t.action_kind === "approve_apply" ? (
          <Button size="sm" variant="primary" disabled={busy} onClick={onApprove}>
            {busy ? "Working…" : "Approve & apply"}
          </Button>
        ) : (
          <Button size="sm" variant="primary" disabled={busy} onClick={onVerify}>
            {busy ? "Working…" : "Verify"}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onProduction}>
          Open in Production
        </Button>
      </ActionRow>
    </CardShell>
  );
}

function RunActionCard({
  r,
  blocked = false,
  onProduction,
  onPackets,
}: {
  r: PipelineRunRef;
  blocked?: boolean;
  onProduction: () => void;
  onPackets: () => void;
}) {
  const showPackets = r.action_kind === "draft_missing" || r.action_kind === "decide_issue";
  return (
    <CardShell
      title={place(r)}
      reason={r.reason}
      chips={
        <>
          <Chip
            label={String(r.status).replace(/_/g, " ")}
            tone={RUN_TONE[r.status] ?? (blocked ? "bad" : "warn")}
            size="sm"
          />
          {r.current_stage && (
            <Chip
              label={r.current_stage.replace(/_/g, " ")}
              tone={blocked ? "bad" : "neutral"}
              size="sm"
            />
          )}
        </>
      }
    >
      {r.scenes_expected != null && (
        <div style={css("margin-bottom:10px")}>
          <SceneProgress drafted={r.scenes_drafted} expected={r.scenes_expected} />
        </div>
      )}
      <ActionRow>
        <Button
          size="sm"
          variant="primary"
          onClick={onProduction}
          title={`${r.suggested_action ?? "Open"} — opens the Production tab for this chapter, where the control lives`}
        >
          {r.suggested_action ?? "Open in Production"}
        </Button>
        {showPackets && (
          <Button size="sm" variant="ghost" onClick={onPackets}>
            Packets
          </Button>
        )}
      </ActionRow>
    </CardShell>
  );
}

function IssueActionCard({ i, onProduction }: { i: PipelineIssueRef; onProduction: () => void }) {
  return (
    <CardShell
      title={place(i)}
      reason={i.reason}
      chips={
        <>
          <Chip label={i.issue_kind.replace(/_/g, " ")} tone="neutral" size="sm" />
          <Chip label={i.severity} tone={SEV_TONE[i.severity] ?? "neutral"} size="sm" />
        </>
      }
    >
      <div
        style={css(
          "font-family:var(--ui);font-size:12px;color:var(--dim);line-height:1.5;margin-bottom:10px",
        )}
      >
        “{i.claim}”
      </div>
      <ActionRow>
        <Button size="sm" variant="primary" onClick={onProduction}>
          {i.suggested_action ?? "Decide"} in Production
        </Button>
      </ActionRow>
    </CardShell>
  );
}

function EventRow({ a }: { a: ActivityOut }) {
  const color = SEV_COLOR[a.severity] ?? "var(--dim)";
  return (
    <div
      style={css(
        "display:flex;align-items:flex-start;gap:10px;padding:9px 11px;border-radius:8px;background:var(--bg3)",
      )}
    >
      <span
        style={css(
          `width:8px;height:8px;border-radius:50%;background:${color};flex:none;margin-top:5px`,
        )}
      />
      <div style={css("min-width:0;flex:1")}>
        <div style={css("font-family:var(--ui);font-size:13px;color:var(--ink)")}>{a.title}</div>
        {a.detail && (
          <div
            style={css(
              `font-family:var(--mono);font-size:10px;color:${a.severity === "error" ? "var(--bad)" : "var(--dim)"};margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap`,
            )}
            title={a.detail}
          >
            {a.detail}
          </div>
        )}
        <div style={css(`${MONO};margin-top:2px`)}>
          {a.source} · {a.kind.replace(/_/g, " ")}
        </div>
      </div>
      <span style={css(`${MONO};flex:none`)}>{relTime(a.created_at)}</span>
    </div>
  );
}
