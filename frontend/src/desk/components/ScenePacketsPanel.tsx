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

const BLOCKER_SOURCE_LABEL: Record<string, string> = {
  author: "author",
  derive: "derive",
  qa: "QA",
  unknown: "gate",
};

function validScenePacketBody(body: ScenePacketBody | undefined | null): boolean {
  if (!body) return false;
  return !!(body.known_before_scene && body.learned_during_scene && body.word_budget);
}

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
              busy={busy}
              onApprove={() => run(`approve:${p.id}`, () => api.approveScenePacket(p.id))}
              onReQa={() => run(`qa:${p.id}`, () => api.qaScenePacket(p.id))}
              onSave={(body) => run(`save:${p.id}`, () => api.updateScenePacket(p.id, { body }))}
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
  onReQa,
  onSave,
}: {
  packet: ScenePacketOut;
  busy: string | null;
  onApprove: () => void;
  onReQa: () => void;
  onSave: (body: ScenePacketBody) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const b: ScenePacketBody = packet.body ?? {};
  const wb = b.word_budget ?? {};
  const known = b.known_before_scene ?? {};
  const learned = b.learned_during_scene ?? {};
  const hidden = b.must_remain_hidden ?? {};
  const statusVar = STATUS_VAR[packet.status] ?? "--dim";
  const isBlocked = packet.status === "blocked";
  const blockedReason =
    packet.blocked_reason ??
    packet.qa_warnings?.blocked_reason ??
    b.blocked_reason ??
    packet.approval_blockers?.[0] ??
    "Blocked, but no reason was recorded. Re-run derive or inspect telemetry.";
  const blockerLabel = packet.blocker_source
    ? (BLOCKER_SOURCE_LABEL[packet.blocker_source] ?? packet.blocker_source)
    : null;
  const qaApprovedWhileBlocked = isBlocked && packet.qa_verdict === "approve";
  const bodyValid = validScenePacketBody(b);
  const residual = packet.qa_warnings?.residual_risks ?? [];
  const issues = packet.qa_warnings?.issues ?? [];
  const reasons = packet.approval_blockers;
  const canApprove = packet.can_approve;
  const showBlockers = reasons.length > 0 && (packet.status === "proposed" || isBlocked);

  // Per-action busy flags (the panel keys busy as "<action>:<id>"). cardBusy disables every action on
  // this card while any one of them is in flight.
  const mine = (action: string) => busy === `${action}:${packet.id}`;
  const cardBusy = mine("approve") || mine("qa") || mine("save");

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
          <Chip
            label={`QA: ${packet.qa_verdict.replace(/_/g, " ")}`}
            colorVar={isBlocked || packet.approval_blockers.length > 0 ? "--bad" : "--info"}
          />
        )}
        {wb.target ? (
          <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
            ~{wb.target}w{wb.min || wb.max ? ` (${wb.min ?? "?"}–${wb.max ?? "?"})` : ""}
            {wb.hard_max ? ` · ≤${wb.hard_max}` : ""}
          </span>
        ) : null}
        <span style={css("margin-left:auto;display:flex;align-items:center;gap:8px")}>
          {!editing && canApprove && (
            <button
              disabled={cardBusy}
              onClick={(e) => {
                e.stopPropagation();
                onApprove();
              }}
              style={btn(!cardBusy, "var(--good)", "var(--bg)")}
            >
              {mine("approve") ? "Approving…" : "Approve"}
            </button>
          )}
          {!editing && packet.status !== "approved" && (
            <button
              disabled={cardBusy || !bodyValid}
              onClick={(e) => {
                e.stopPropagation();
                onReQa();
              }}
              style={btn(!cardBusy && bodyValid, "var(--bg3)", "var(--ink)")}
              title={
                bodyValid
                  ? undefined
                  : "Cannot rerun QA: this packet failed during author/derive and has no valid scene contract. Re-run derive instead."
              }
            >
              {mine("qa") ? "Re-running QA…" : "Re-run QA"}
            </button>
          )}
          {!editing && packet.status !== "approved" && (
            <button
              disabled={cardBusy}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(true);
                setEditing(true);
              }}
              style={btn(!cardBusy, "var(--bg3)", "var(--ink)")}
            >
              Edit
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
      {!editing && !bodyValid && packet.status !== "approved" && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:7px")}>
          Cannot rerun QA: this packet failed during author/derive and has no valid scene contract.
          Re-run derive instead.
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
            QA blocks approval — fix the contract below (Edit) and Re-run QA, or Re-derive:
          </div>
          {reasons.map((r, i) => (
            <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
              · {r}
            </div>
          ))}
        </div>
      )}

      {editing ? (
        <ScenePacketEditor
          body={b}
          busy={mine("save")}
          onSave={(body) => {
            onSave(body);
            setEditing(false);
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        open && (
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
                  const sevVar = sev === "block" ? "--bad" : sev === "warn" ? "--warn" : "--dim";
                  return (
                    <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
                      <Chip label={sev} colorVar={sevVar} />{" "}
                      {it.kind ? <strong>{it.kind}: </strong> : null}
                      {it.detail}
                    </div>
                  );
                })}
                <Pills label="Residual risks (non-blocking)" items={residual} tone="warn" />
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}

// Edit the high-leverage scene-contract fields and PUT the whole body. Works on a local draft (Save
// persists, Cancel discards). Editing the body returns an approved packet to `proposed` server-side;
// after a save the human re-runs QA, then approves. Mirrors PacketEditor on the chapter-packet screen.
function ScenePacketEditor({
  body,
  busy,
  onSave,
  onCancel,
}: {
  body: ScenePacketBody;
  busy: boolean;
  onSave: (body: ScenePacketBody) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<ScenePacketBody>(() => structuredClone(body));

  const setField = (k: keyof ScenePacketBody, v: unknown) => setDraft((d) => ({ ...d, [k]: v }));
  // Immutably set a nested list field, e.g. known_before_scene.reader.
  const setNested = (group: keyof ScenePacketBody, key: string, v: string[]) =>
    setDraft((d) => ({ ...d, [group]: { ...(d[group] as Record<string, unknown>), [key]: v } }));

  return (
    <div style={css("margin-top:12px;display:flex;flex-direction:column;gap:12px")}>
      <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
        <Chip label="editing scene packet" colorVar="--info" />
        <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          Save replaces the contract and returns it to proposed — Re-run QA, then Approve.
        </span>
      </div>

      <EditText
        label="Scene job"
        value={draft.scene_job}
        onChange={(v) => setField("scene_job", v)}
        multiline
      />
      <EditList
        label="Reader knows before"
        value={known(draft).reader}
        onChange={(v) => setNested("known_before_scene", "reader", v)}
      />
      <EditList
        label="POV knows before"
        value={known(draft).pov}
        onChange={(v) => setNested("known_before_scene", "pov", v)}
      />
      <EditList
        label="Reader must learn"
        value={draft.learned_during_scene?.reader_must_learn}
        onChange={(v) => setNested("learned_during_scene", "reader_must_learn", v)}
      />
      <EditList
        label="Reader may infer only"
        value={draft.learned_during_scene?.reader_may_infer_only}
        onChange={(v) => setNested("learned_during_scene", "reader_may_infer_only", v)}
      />
      <EditList
        label="Must stay hidden (reader)"
        value={draft.must_remain_hidden?.reader}
        onChange={(v) => setNested("must_remain_hidden", "reader", v)}
      />
      <EditList
        label="Required beats"
        value={draft.required_beats}
        onChange={(v) => setField("required_beats", v)}
      />
      <EditList
        label="Forbidden beats"
        value={draft.forbidden_beats}
        onChange={(v) => setField("forbidden_beats", v)}
      />
      <EditList
        label="Reviewer false-positive traps"
        value={draft.reviewer_false_positive_traps}
        onChange={(v) => setField("reviewer_false_positive_traps", v)}
      />
      <EditText
        label="Exit state"
        value={draft.exit_state}
        onChange={(v) => setField("exit_state", v)}
        multiline
      />

      <div style={css("display:flex;gap:9px")}>
        <button
          disabled={busy}
          onClick={() => onSave(draft)}
          style={btn(!busy, "var(--good)", "var(--bg)")}
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button disabled={busy} onClick={onCancel} style={btn(!busy, "var(--bg3)", "var(--ink)")}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// Narrow accessor so the nested-list editors don't repeat the `?? {}` dance.
function known(b: ScenePacketBody): { reader?: string[]; pov?: string[] } {
  return b.known_before_scene ?? {};
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
}: {
  label: string;
  value?: string;
  onChange: (v: string) => void;
  multiline?: boolean;
  rows?: number;
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
}: {
  label: string;
  value?: string[];
  onChange: (v: string[]) => void;
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
    </div>
  );
}
