"use client";

import { useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { Button, Chip } from "./ui";
import type { RepairPreviewOut } from "../api/types";

function str(body: Record<string, unknown>, key: string): string {
  const v = body[key];
  return typeof v === "string" ? v : "";
}

/** An author-controlled repair preview (ADR 0017): a bounded diff + rationale + preservation boundary.
 *  It never changed the current scene; accepting or editing here materializes a new revision, rejecting
 *  leaves the Critique/Issue intact. `onResolved` lets the parent refetch after the author acts. */
export default function SceneFidelityPreview({
  preview,
  onResolved,
}: {
  preview: RepairPreviewOut;
  onResolved?: () => void;
}) {
  const body = (preview.body ?? {}) as Record<string, unknown>;
  const diff = str(body, "diff");
  const rationale = str(body, "rationale");
  const boundary = str(body, "preservation_boundary");
  const candidate = str(body, "candidate_prose");

  const [editing, setEditing] = useState(false);
  const [edited, setEdited] = useState(candidate);
  const [busy, setBusy] = useState<"accept" | "reject" | null>(null);
  const resolved = preview.status !== "active";

  async function accept() {
    setBusy("accept");
    try {
      await api.acceptFidelityPreview(preview.id, {
        edited_prose: editing ? edited : null,
        reason: null,
      });
      onResolved?.();
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy("reject");
    try {
      await api.rejectFidelityPreview(preview.id, {
        edited_prose: null,
        reason: "rejected by author",
      });
      onResolved?.();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      style={css(
        "border:1px solid var(--line);border-radius:10px;padding:12px;background:var(--bg2)",
      )}
    >
      <div style={css("display:flex;gap:8px;align-items:center;margin-bottom:8px")}>
        <Chip
          label={resolved ? preview.status : "proposed repair"}
          tone={resolved ? "neutral" : "info"}
          size="sm"
        />
        {rationale && <div style={css("font-size:12px;color:var(--dim)")}>{rationale}</div>}
      </div>
      {boundary && (
        <div style={css("font-size:11px;color:var(--dim);margin-bottom:8px")}>{boundary}</div>
      )}
      <pre
        style={css(
          "font-family:var(--mono);font-size:11px;white-space:pre-wrap;background:var(--boxbg);padding:8px;border-radius:8px;max-height:240px;overflow:auto;margin:0",
        )}
      >
        {diff || "(no diff)"}
      </pre>
      {editing && (
        <textarea
          value={edited}
          onChange={(ev) => setEdited(ev.target.value)}
          rows={8}
          style={css(
            "width:100%;box-sizing:border-box;font-family:var(--mono);font-size:12px;margin-top:8px;padding:8px;border-radius:8px;border:1px solid var(--line);background:var(--boxbg);color:var(--fg)",
          )}
        />
      )}
      {!resolved && (
        <div style={css("display:flex;gap:8px;margin-top:10px")}>
          <Button variant="primary" onClick={accept} disabled={busy != null}>
            {editing ? "Accept edit" : "Accept"}
          </Button>
          <Button onClick={() => setEditing((v) => !v)} disabled={busy != null}>
            {editing ? "Cancel edit" : "Edit"}
          </Button>
          <Button variant="ghost" onClick={reject} disabled={busy != null}>
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}
