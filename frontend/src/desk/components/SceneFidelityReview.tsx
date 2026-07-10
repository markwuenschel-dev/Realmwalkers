"use client";

import { useEffect, useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { Chip, Panel, Spinner } from "./ui";
import type { ChipTone } from "./ui";
import type { ClauseEvaluationOut, SceneFidelityOut } from "../api/types";

// A clause result -> chip tone. Only `lost` is a prose failure; the operational-uncertain results read
// as warnings, and `satisfied` reads as good (ADR 0019/0022).
const RESULT_TONE: Record<string, ChipTone> = {
  satisfied: "good",
  lost: "bad",
  indeterminate: "warn",
  blocked_by_dependency: "warn",
  adapter_failed: "warn",
  not_evaluated: "neutral",
};

const RESULT_LABEL: Record<string, string> = {
  satisfied: "satisfied",
  lost: "lost",
  indeterminate: "indeterminate",
  blocked_by_dependency: "blocked",
  adapter_failed: "adapter failed",
  not_evaluated: "not evaluated",
};

function reasonLabel(reason: string): string {
  const map: Record<string, string> = {
    no_active_contract: "no active fidelity contract",
    no_draft_attempt: "no draft to evaluate yet",
    no_report: "evaluation not yet run",
    scene_packet_changed: "scene packet changed",
    packet_fingerprint_changed: "contract edited since evaluation",
    draft_attempt_changed: "scene was re-drafted",
    prose_changed: "prose changed since evaluation",
    current: "current",
  };
  return map[reason] ?? reason;
}

function ClauseRow({ e }: { e: ClauseEvaluationOut }) {
  return (
    <div
      style={css(
        "display:flex;gap:8px;align-items:flex-start;padding:6px 0;border-top:1px solid var(--line)",
      )}
    >
      <Chip
        label={RESULT_LABEL[e.result] ?? e.result}
        tone={RESULT_TONE[e.result] ?? "neutral"}
        size="sm"
      />
      <div style={css("flex:1;min-width:0")}>
        <div style={css("font-size:12px;color:var(--fg)")}>
          {e.explanation || `${e.mode} · ${e.clause_id}`}
        </div>
        <div style={css("font-family:var(--mono);font-size:10px;color:var(--dim)")}>
          {e.mode} · {e.enforcement}
          {e.post_draft_policy === "export_required" ? " · export-required" : ""}
        </div>
      </div>
    </div>
  );
}

/** Decision-ready SceneFidelity status for one scene (ADR 0016): losses first (problem → why), then the
 *  rest, with operational (incomplete-evaluation) holds surfaced distinctly from prose failures. Read-only
 *  — it never triggers evaluation. Renders nothing when the scene has no active fidelity contract. */
export default function SceneFidelityReview({ sceneId }: { sceneId: string }) {
  const [data, setData] = useState<SceneFidelityOut | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .sceneFidelity(sceneId)
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
  }, [sceneId]);

  if (loading) return <Spinner />;
  if (!data) return null;
  if (!data.has_report && data.currentness_reason === "no_active_contract") return null;

  if (!data.has_report) {
    return (
      <Panel eyebrow="Fidelity" title="Evaluation pending">
        <div style={css("font-size:12px;color:var(--dim)")}>
          {reasonLabel(data.currentness_reason)} — an operational hold, not a prose failure.
        </div>
      </Panel>
    );
  }

  const evals = data.clause_evaluations ?? [];
  const ordered = [
    ...evals.filter((e) => e.result === "lost"),
    ...evals.filter((e) => e.result !== "lost"),
  ];
  const holds = data.operational_holds ?? [];

  return (
    <Panel
      eyebrow="Fidelity"
      title={data.is_current ? "Fidelity report" : "Fidelity report (stale)"}
      actions={
        <Chip
          label={data.is_current ? "current" : reasonLabel(data.currentness_reason)}
          tone={data.is_current ? "good" : "warn"}
          size="sm"
        />
      }
    >
      {!data.is_current && (
        <div style={css("font-size:12px;color:var(--warn);margin-bottom:8px")}>
          This report is stale ({reasonLabel(data.currentness_reason)}) — re-evaluate before it can
          gate export.
        </div>
      )}
      {holds.length > 0 && (
        <div style={css("margin-bottom:10px")}>
          <div
            style={css(
              "font-family:var(--mono);font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--dim);margin-bottom:4px",
            )}
          >
            Operational holds (incomplete evaluation)
          </div>
          {holds.map((h, i) => (
            <div key={i} style={css("font-size:12px;color:var(--dim)")}>
              • {h}
            </div>
          ))}
        </div>
      )}
      {ordered.map((e) => (
        <ClauseRow key={`${e.requirement_id}:${e.clause_id}`} e={e} />
      ))}
    </Panel>
  );
}
