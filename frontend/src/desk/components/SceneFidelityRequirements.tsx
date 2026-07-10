"use client";

import { useEffect, useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { Button, Chip } from "./ui";
import type { ScenePacketFidelityOut } from "../api/types";

type Req = Record<string, unknown>;

function str(r: Req, k: string): string {
  const v = r[k];
  return typeof v === "string" ? v : "";
}

function clauseCount(r: Req): number {
  const c = r.clauses;
  return Array.isArray(c) ? c.length : 0;
}

const LABEL =
  "font-family:var(--mono);font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--dim)";
const REQBOX =
  "border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--bg2);display:flex;flex-direction:column;gap:6px";
const EDITBOX =
  "width:100%;box-sizing:border-box;font-family:var(--mono);font-size:11px;padding:8px;border-radius:8px;border:1px solid var(--line);background:var(--bg2);color:var(--ink)";

/** Metadata line for one fidelity requirement: mode + export-required flag + id/clause count. The
 *  requirement body is an opaque typed object (FidelityRequirement) validated server-side, so only
 *  the stable header fields are surfaced here; the full shape is edited as JSON. */
function ReqMeta({ req }: { req: Req }) {
  const mode = str(req, "mode");
  const policy = str(req, "post_draft_policy");
  const n = clauseCount(req);
  return (
    <div style={css("display:flex;gap:6px;align-items:center;flex-wrap:wrap")}>
      <Chip label={mode || "requirement"} tone="neutral" size="sm" />
      {policy === "export_required" && <Chip label="export-required" tone="warn" size="sm" />}
      <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
        {str(req, "requirement_id")} · {n} clause{n === 1 ? "" : "s"}
      </span>
    </div>
  );
}

/** Author controls for a scene packet's fidelity contract (ADR 0016): accept model-suggested
 *  requirements into the active contract, or refine/replace an active requirement (edited as JSON —
 *  the requirement body is validated deterministically server-side; violations render inline).
 *  Renders nothing when the packet has no active requirements, suggestions, or violations. */
export default function SceneFidelityRequirements({ packetId }: { packetId: string }) {
  const [data, setData] = useState<ScenePacketFidelityOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ mode: "refine" | "replace"; id: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .scenePacketFidelity(packetId)
      .then((d) => {
        if (live) setData(d);
      })
      .catch(() => {
        if (live) setData(null);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [packetId]);

  // No spinner here: this mounts inside every expanded packet card, and most packets have no fidelity
  // contract — a flash-then-vanish spinner would be noise. Stay silent until there is something to show.
  if (loading || !data) return null;

  const active = (data.active_requirements ?? []) as Req[];
  const suggested = (data.suggested_requirements ?? []) as Req[];
  const violations = data.violations ?? [];
  if (!active.length && !suggested.length && !violations.length) return null;

  async function accept(ids: string[]) {
    if (!ids.length) return;
    setBusy(true);
    setErr(null);
    try {
      setData(await api.acceptFidelitySuggestions(packetId, { requirement_ids: ids }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Accept failed.");
    } finally {
      setBusy(false);
    }
  }

  function openEditor(mode: "refine" | "replace", req: Req) {
    setErr(null);
    setEditing({ mode, id: str(req, "requirement_id") });
    setDraft(JSON.stringify(req, null, 2));
  }

  async function submitEditor() {
    if (!editing) return;
    let requirement: Record<string, unknown>;
    try {
      requirement = JSON.parse(draft) as Record<string, unknown>;
    } catch {
      setErr("Requirement is not valid JSON.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const body = { requirement_id: editing.id, requirement };
      const next =
        editing.mode === "refine"
          ? await api.refineFidelityRequirement(packetId, body)
          : await api.replaceFidelityRequirement(packetId, body);
      setData(next);
      setEditing(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="fidelity-requirements"
      style={css(
        "margin-top:4px;border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:10px",
      )}
    >
      <div style={css(LABEL)}>Fidelity contract</div>

      {violations.length > 0 && (
        <div style={css("display:flex;flex-direction:column;gap:4px")}>
          {violations.map((v, i) => (
            <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
              <Chip label={v.severity} tone="warn" size="sm" />{" "}
              {v.field ? <strong>{v.field}: </strong> : null}
              {v.detail}
            </div>
          ))}
        </div>
      )}

      {active.length > 0 && (
        <div style={css("display:flex;flex-direction:column;gap:6px")}>
          <div style={css("font-size:11px;color:var(--dim)")}>Active requirements</div>
          {active.map((req) => {
            const id = str(req, "requirement_id");
            const isEditing = editing?.id === id;
            return (
              <div key={id} style={css(REQBOX)}>
                <ReqMeta req={req} />
                {isEditing ? (
                  <>
                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      rows={10}
                      style={css(EDITBOX)}
                    />
                    <div style={css("display:flex;gap:8px")}>
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={busy}
                        onClick={() => void submitEditor()}
                      >
                        {busy
                          ? "Saving…"
                          : editing?.mode === "refine"
                            ? "Save refinement"
                            : "Replace"}
                      </Button>
                      <Button size="sm" disabled={busy} onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </div>
                  </>
                ) : (
                  <div style={css("display:flex;gap:8px")}>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => openEditor("refine", req)}
                    >
                      Refine
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => openEditor("replace", req)}
                    >
                      Replace
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {suggested.length > 0 && (
        <div style={css("display:flex;flex-direction:column;gap:6px")}>
          <div style={css("display:flex;justify-content:space-between;align-items:center;gap:8px")}>
            <div style={css("font-size:11px;color:var(--dim)")}>Suggested (not active)</div>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() =>
                void accept(suggested.map((r) => str(r, "requirement_id")).filter(Boolean))
              }
            >
              Accept all
            </Button>
          </div>
          {suggested.map((req) => {
            const id = str(req, "requirement_id");
            return (
              <div key={id} style={css(REQBOX)}>
                <ReqMeta req={req} />
                <div>
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={busy}
                    onClick={() => void accept([id])}
                  >
                    Accept
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {err && <div style={css("font-size:12px;color:var(--bad)")}>{err}</div>}
    </div>
  );
}
