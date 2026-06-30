"use client";

import { useState } from "react";
import { css } from "../../css";
import { copyToClipboard } from "../../lib/download";
import { buildRunDiagnosisSummary, downloadCallsCsv, downloadRunJson } from "./telemetryExport";
import type { RunTelemetryOut } from "../../api/types";

export function RunExportToolbar({ data }: { data: RunTelemetryOut }) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    const ok = await copyToClipboard(buildRunDiagnosisSummary(data));
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div style={css("display:flex;flex-wrap:wrap;gap:6px")}>
      <ToolbarBtn label="JSON" onClick={() => downloadRunJson(data)} />
      <ToolbarBtn
        label="CSV"
        onClick={() => downloadCallsCsv(data.calls, `telemetry_run_${data.run_id.slice(0, 8)}`)}
      />
      <ToolbarBtn label={copied ? "Copied" : "Copy summary"} onClick={() => void onCopy()} />
    </div>
  );
}

function ToolbarBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={css(
        "height:26px;padding:0 10px;border-radius:6px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-family:var(--mono);font-size:10px;cursor:pointer",
      )}
    >
      {label}
    </button>
  );
}
