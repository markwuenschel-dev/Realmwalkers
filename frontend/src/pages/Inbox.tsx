import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SceneOut } from "../types";

export default function Inbox() {
  const [scenes, setScenes] = useState<SceneOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .pending()
        .then((s) => {
          if (active) {
            setScenes(s);
            setError(null);
          }
        })
        .catch((e) => {
          if (active) setError(String(e));
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    load(); // immediately on mount
    const id = setInterval(load, 4000); // then poll, so new drafts show up on their own
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  if (loading) return <p className="muted">Loading inbox…</p>;
  if (error) return <p className="error">Couldn't reach the API: {error}</p>;
  if (scenes.length === 0)
    return (
      <p className="muted">
        Inbox empty — no scenes awaiting review. Drafting takes the model a little while; this list
        refreshes on its own.
      </p>
    );

  return (
    <ul className="inbox">
      {scenes.map((s) => (
        <li key={s.id}>
          <Link to={`/scenes/${s.id}`}>
            <span className="sceneref">
              Scene {s.scene_no} · v{s.version}
            </span>
            <span className="passes">{(s.passes_run ?? []).join(" → ")}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
