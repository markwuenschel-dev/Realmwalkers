"use client";

import { useState } from "react";
import type { PacketViolation } from "../lib/packetBlockers";
import { isSurfaceAuditEvent, surfaceAuditSummary } from "../lib/packetBlockers";
import { css } from "../css";

/** Grouped rendering for deterministic validation output, in reading order of urgency:
 *  1. Blocking issues (drafting stops) — red, expanded
 *  2. Repair tasks (fixable; final export waits, drafting proceeds) — amber, expanded
 *  3. Advisory warnings — dim, expanded
 *  4. Surface projection audit (safe replacements/omissions the projector applied) — collapsed to a
 *     one-line receipt; these are records of successful automatic work, never validation failures. */
export function ViolationGroups({ violations }: { violations: PacketViolation[] }) {
  const blocking = violations.filter((v) => v.blocks_drafting);
  const repairs = violations.filter((v) => v.blocks_final_export && !v.blocks_drafting);
  const audit = violations.filter(isSurfaceAuditEvent);
  const advisories = violations.filter(
    (v) => !v.blocks_drafting && !v.blocks_final_export && !isSurfaceAuditEvent(v),
  );
  const [auditOpen, setAuditOpen] = useState(false);

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      {blocking.length > 0 && (
        <ViolationGroup title={`Blocking · ${blocking.length}`} colorVar="--bad" items={blocking} />
      )}
      {repairs.length > 0 && (
        <ViolationGroup
          title={`Repair tasks · ${repairs.length} · fixable, not blocking`}
          colorVar="--warn"
          items={repairs}
        />
      )}
      {advisories.length > 0 && (
        <ViolationGroup
          title={`Advisory · ${advisories.length}`}
          colorVar="--dim"
          items={advisories}
        />
      )}
      {audit.length > 0 && (
        <div>
          <button
            onClick={() => setAuditOpen((v) => !v)}
            style={css(
              "display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:none;border:none;padding:0;cursor:pointer",
            )}
          >
            <span
              style={css(
                "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)",
              )}
            >
              {auditOpen ? "▾" : "▸"} Surface projection audit · {audit.length}
            </span>
          </button>
          <div style={css("font-size:12.5px;color:var(--dim);margin-top:4px")}>
            {surfaceAuditSummary(audit)}
          </div>
          {auditOpen && (
            <div style={css("margin-top:8px")}>
              <ViolationList items={audit} colorVar="--dim" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ViolationGroup({
  title,
  colorVar,
  items,
}: {
  title: string;
  colorVar: string;
  items: PacketViolation[];
}) {
  return (
    <div>
      <div
        style={css(
          `font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(${colorVar});margin-bottom:6px`,
        )}
      >
        {title}
      </div>
      <ViolationList items={items} colorVar={colorVar} />
    </div>
  );
}

function ViolationList({ items, colorVar }: { items: PacketViolation[]; colorVar: string }) {
  return (
    <div style={css("display:flex;flex-direction:column;gap:6px")}>
      {items.map((v, i) => (
        <div key={i} style={css("font-size:12.5px;color:var(--ink)")}>
          <span style={css(`font-family:var(--mono);font-size:11px;color:var(${colorVar})`)}>
            {v.severity} · {v.kind}
            {v.field ? ` · ${v.field}` : ""}
            {v.blocks_final_export && !v.blocks_drafting ? " · blocks final export only" : ""}:{" "}
          </span>
          {v.detail}
        </div>
      ))}
    </div>
  );
}
