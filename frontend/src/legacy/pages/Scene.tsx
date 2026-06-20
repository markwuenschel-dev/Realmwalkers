import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Decision, SceneDetail } from "../types";
import ContinuityPanel from "../components/ContinuityPanel";
import RenderedProse from "../components/RenderedProse";

export default function Scene() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [scene, setScene] = useState<SceneDetail | null>(null);
  const [edited, setEdited] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!id) return;
    api.scene(id).then((s) => {
      setScene(s);
      setEdited(s.prose ?? "");
    });
  }, [id]);

  if (!scene) return <p className="muted">Loading…</p>;

  const submit = async (decision: Decision) => {
    if (!id) return;
    setBusy(true);
    try {
      await api.decide(id, {
        decision,
        feedback: feedback || null,
        edited_prose: edited !== scene.prose ? edited : null,
      });
      nav("/");
    } finally {
      setBusy(false);
    }
  };

  const onResolved = async (choice: "use_prose" | "use_ledger") => {
    if (choice === "use_ledger") {
      nav("/"); // scene is now revision_requested; a new version will be drafted
      return;
    }
    if (id) setScene(await api.scene(id)); // use_prose: ledger fixed + flag cleared — refresh in place
  };

  const hardFlags = scene.critiques.filter((c) => c.severity === "hard");
  const otherFlags = scene.critiques.filter((c) => c.severity !== "hard");

  return (
    <div className="scene">
      <div className="prose-col">
        <h2>Scene {scene.scene_no} · v{scene.version}</h2>
        <p className="meta">
          {scene.model ?? "—"} · {scene.token_count ?? "—"} tokens ·{" "}
          {(scene.passes_run ?? []).join(" → ")}
        </p>
        <div className="prose-toolbar">
          <button className="toggle-edit" onClick={() => setEditing((e) => !e)}>
            {editing ? "Done editing" : "Edit prose"}
          </button>
        </div>
        {editing ? (
          <textarea
            className="prose"
            value={edited}
            onChange={(e) => setEdited(e.target.value)}
            spellCheck
          />
        ) : (
          <RenderedProse text={edited} />
        )}
        <textarea
          className="feedback"
          placeholder="Revision notes (optional)…"
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
        />
        <div className="actions">
          <button disabled={busy} className="approve" onClick={() => submit("approve")}>
            Approve
          </button>
          <button disabled={busy} className="revise" onClick={() => submit("revise")}>
            Request revision
          </button>
          <button disabled={busy} className="deny" onClick={() => submit("deny")}>
            Reject
          </button>
        </div>
      </div>

      <aside className="flags-col">
        {hardFlags.length > 0 && (
          <ContinuityPanel sceneId={scene.id} flags={hardFlags} onResolved={onResolved} />
        )}
        <h3>Reviewer notes</h3>
        {otherFlags.length === 0 ? (
          <p className="muted">No advisory flags.</p>
        ) : (
          <ul className="flags">
            {otherFlags.map((f) => (
              <li key={f.id} className={`flag ${f.severity}`}>
                <strong>{f.reviewer}</strong> <span className="sev">{f.severity}</span>
                <p>{f.note}</p>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}
