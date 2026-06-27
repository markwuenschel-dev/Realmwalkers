"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { api } from "../api/client";
import { Spinner, formatElapsed } from "./DraftActivity";
import { ChapterTelemetryPanel } from "./Telemetry";
import type { ScenePacketBody, ScenePacketOut } from "../api/types";

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
};

export function ScenePacketsPanel({ chapterId }: { chapterId: string }) {
  const { t } = useDesk();
  const [packets, setPackets] = useState<ScenePacketOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [deriving, setDeriving] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped whenever packets reload (chapter change, derive finish) so the telemetry panel re-pulls.
  const [telemetryKey, setTelemetryKey] = useState(0);
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPackets(await api.scenePackets(chapterId));
      setTelemetryKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [chapterId]);

  // On chapter change: load the list + rejoin any in-flight derive (it runs server-side).
  useEffect(() => {
    setError(null);
    setPackets([]);
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

  return (
    <div style={css("margin-top:26px;border-top:1px solid var(--line);padding-top:22px")}>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:16px",
        )}
      >
        <div>
          <h2
            style={css(
              "margin:0 0 4px;font-family:var(--display);font-weight:600;font-size:22px;color:var(--ink)",
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
          {approvable.length > 0 && (
            <button
              disabled={busy != null || deriving}
              onClick={() => run("approve-all", () => api.approveScenePackets(chapterId))}
              style={btn(busy == null && !deriving, "var(--good)", "var(--bg)")}
            >
              {busy === "approve-all" ? "Approving…" : `Approve all (${approvable.length})`}
            </button>
          )}
          <button disabled={deriving} onClick={derive} style={btn(!deriving, t.accent, t.onAccent)}>
            {deriving
              ? `Deriving…${formatElapsed(elapsed) ? ` ${formatElapsed(elapsed)}` : ""}`
              : packets.length
                ? "Re-derive"
                : "Derive scene packets"}
          </button>
        </div>
      </div>

      {error && (
        <div
          style={css(
            `margin-bottom:14px;border:1px solid color-mix(in srgb,${t.bad} 40%,var(--line));background:color-mix(in srgb,${t.bad} 8%,var(--bg2));border-radius:9px;padding:10px 12px;color:${t.bad};font-size:12.5px`,
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

      {approvedCount > 0 && (
        <div
          style={css(
            "font-family:var(--mono);font-size:11.5px;color:var(--good);margin-bottom:12px",
          )}
        >
          {approvedCount} approved · beats derived · ready to draft.
        </div>
      )}

      {loading && packets.length === 0 ? (
        <Muted text="Loading scene packets…" />
      ) : packets.length === 0 && !deriving ? (
        <div
          style={css(
            "border:1px dashed var(--line);border-radius:11px;padding:22px;text-align:center;color:var(--dim);font-size:13px",
          )}
        >
          No scene packets yet. Derive them from the approved chapter packet above.
        </div>
      ) : (
        <div style={css("display:flex;flex-direction:column;gap:12px")}>
          {packets.map((p) => (
            <ScenePacketCard
              key={p.id}
              packet={p}
              busy={busy === p.id}
              onApprove={() => run(p.id, () => api.approveScenePacket(p.id))}
            />
          ))}
        </div>
      )}

      <ChapterTelemetryPanel chapterId={chapterId} refreshKey={telemetryKey} />
    </div>
  );
}

function ScenePacketCard({
  packet,
  busy,
  onApprove,
}: {
  packet: ScenePacketOut;
  busy: boolean;
  onApprove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const b: ScenePacketBody = packet.body ?? {};
  const wb = b.word_budget ?? {};
  const known = b.known_before_scene ?? {};
  const learned = b.learned_during_scene ?? {};
  const hidden = b.must_remain_hidden ?? {};
  const statusVar = STATUS_VAR[packet.status] ?? "--dim";
  const blocked = packet.qa_warnings?.blocked_reason ?? b.blocked_reason;
  const canApprove = packet.status === "proposed" && packet.qa_verdict !== "block_drafting";

  return (
    <div
      style={css(
        `background:var(--bg2);border:1px solid var(--line);border-left:3px solid var(${statusVar});border-radius:11px;padding:13px 15px`,
      )}
    >
      <div
        style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap;cursor:pointer")}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>
          Scene {packet.scene_no}
          {b.scene_type ? (
            <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
              {" "}
              · {b.scene_type}
            </span>
          ) : null}
        </span>
        <Chip label={packet.status} colorVar={statusVar} />
        {packet.qa_verdict && (
          <Chip label={`QA: ${packet.qa_verdict.replace(/_/g, " ")}`} colorVar="--info" />
        )}
        {wb.target ? (
          <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
            ~{wb.target}w{wb.min || wb.max ? ` (${wb.min ?? "?"}–${wb.max ?? "?"})` : ""}
            {wb.hard_max ? ` · ≤${wb.hard_max}` : ""}
          </span>
        ) : null}
        <span style={css("margin-left:auto;display:flex;align-items:center;gap:10px")}>
          {canApprove && (
            <button
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                onApprove();
              }}
              style={btn(!busy, "var(--good)", "var(--bg)")}
            >
              {busy ? "Approving…" : "Approve"}
            </button>
          )}
          <span style={css("font-family:var(--mono);font-size:14px;color:var(--dim)")}>
            {open ? "▾" : "▸"}
          </span>
        </span>
      </div>

      {packet.status === "stale" && packet.stale_reason && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--warn);margin-top:7px")}>
          stale: {packet.stale_reason} — re-derive or re-approve before drafting.
        </div>
      )}
      {blocked && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--bad);margin-top:7px")}>
          blocked: {blocked}
        </div>
      )}

      {open && (
        <div style={css("margin-top:12px;display:flex;flex-direction:column;gap:10px")}>
          {b.scene_job && (
            <div style={css("font-size:13px;color:var(--ink);line-height:1.45")}>{b.scene_job}</div>
          )}
          <Pills label="Reader knows before" items={known.reader} tone="dim" />
          <Pills label="POV knows before" items={known.pov} tone="dim" />
          <Pills label="Reader must learn" items={learned.reader_must_learn} tone="info" />
          <Pills label="Reader may infer only" items={learned.reader_may_infer_only} tone="warn" />
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
        </div>
      )}
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

function Chip({ label, colorVar }: { label: string; colorVar: string }) {
  return (
    <span
      style={css(
        `font-family:var(--mono);font-size:11px;color:var(${colorVar});background:color-mix(in srgb,var(${colorVar}) 12%,var(--bg2));border:1px solid color-mix(in srgb,var(${colorVar}) 35%,var(--line));border-radius:999px;padding:3px 10px`,
      )}
    >
      {label}
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

function btn(enabled: boolean, bg: string, fg: string): CSSProperties {
  return css(
    `height:30px;padding:0 13px;border-radius:8px;border:none;font-family:var(--ui);font-size:12.5px;font-weight:500;cursor:${enabled ? "pointer" : "default"};background:${enabled ? bg : "var(--bg3)"};color:${enabled ? fg : "var(--dim)"};opacity:${enabled ? 1 : 0.7}`,
  );
}
