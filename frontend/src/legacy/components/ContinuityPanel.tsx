import { useState } from "react";
import { api } from "../api/client";
import type { Critique } from "../types";

type Choice = "use_prose" | "use_ledger";

// Resolving a conflict either corrects the Oracle's ledger (prose was right) or queues a targeted
// prose fix (ledger was right). The API deletes the handled critique; the parent then refreshes
// (use_prose) or returns to the inbox (use_ledger, since the scene is now revision_requested).
export default function ContinuityPanel({
  sceneId,
  flags,
  onResolved,
}: {
  sceneId: string;
  flags: Critique[];
  onResolved: (choice: Choice) => void | Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resolve = async (critiqueId: string, choice: Choice) => {
    setBusyId(critiqueId);
    setError(null);
    try {
      await api.resolveContinuity(sceneId, { critique_id: critiqueId, choice });
      await onResolved(choice);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusyId(null);
    }
  };

  return (
    <section className="continuity">
      <h3>Continuity conflicts</h3>
      <p className="muted">Advisory — nothing is blocked. You choose which source is canon.</p>
      {error && <p className="resolve-error">Couldn’t resolve: {error}</p>}
      <ul>
        {flags.map((f) => {
          const p = (f.payload ?? {}) as Record<string, unknown>;
          const attr = p.attribute ? `${String(p.attribute)} · ` : "";
          const busy = busyId === f.id;
          return (
            <li key={f.id} className="conflict">
              <p className="ctx">{String(p.context_sentence ?? f.note ?? "")}</p>
              <div className="vs">
                <span>
                  {attr}prose: <b>{String(p.prose_value ?? "?")}</b>
                </span>
                <span>
                  ledger: <b>{String(p.ledger_value ?? "?")}</b>
                </span>
              </div>
              <div className="resolve">
                <button disabled={busy} onClick={() => resolve(f.id, "use_prose")}>
                  {busy ? "…" : "Keep prose · fix ledger"}
                </button>
                <button disabled={busy} onClick={() => resolve(f.id, "use_ledger")}>
                  {busy ? "…" : "Keep ledger · fix prose"}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
