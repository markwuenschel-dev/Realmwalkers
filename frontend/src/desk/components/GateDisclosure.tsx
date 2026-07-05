import { useState } from "react";
import { css } from "../css";
import { Chip } from "./ui";

export interface GateRow {
  label: string;
  pass: boolean;
  detail: string;
}

/** The "▸ Why is this disabled?" disclosure — the Desk's one idiom for explaining a gated action:
 *  a collapsed toggle that expands into pass/fail chip rows, one per gate, with a detail sentence.
 *  Extracted from ProductionScreen's AssemblyGateDiagnostics so Chapters (and future gates) read as
 *  the same system. `lead` renders above the rows — use it for the authoritative disabled_reason. */
export default function GateDisclosure({
  lead,
  rows,
  testId,
}: {
  lead?: string | null;
  rows: GateRow[];
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div style={css("display:flex;flex-direction:column;gap:6px")} data-testid={testId}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={css(
          "background:none;border:none;padding:0;cursor:pointer;text-align:left;font-family:var(--mono);font-size:11px;color:var(--warn)",
        )}
      >
        {open ? "▾" : "▸"} Why is this disabled?
      </button>
      {open && lead && (
        <span style={css("font-family:var(--mono);font-size:11px;color:var(--ink)")}>{lead}</span>
      )}
      {open &&
        rows.map((g) => (
          <div
            key={g.label}
            style={css("display:flex;align-items:baseline;gap:8px;flex-wrap:wrap")}
          >
            <Chip label={g.pass ? "pass" : "fail"} tone={g.pass ? "good" : "bad"} size="sm" />
            <span style={css("font-family:var(--mono);font-size:11px;color:var(--ink)")}>
              {g.label}
            </span>
            <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
              {g.detail}
            </span>
          </div>
        ))}
    </div>
  );
}
