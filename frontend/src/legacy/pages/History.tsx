import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { lineDiff } from "../lib/diff";
import type { BookOut, ChapterOut, SceneOut, SceneVersionOut } from "../types";

type Compare = "previous" | "original";

export default function History() {
  const [books, setBooks] = useState<BookOut[]>([]);
  const [bookId, setBookId] = useState("");
  const [chapters, setChapters] = useState<ChapterOut[]>([]);
  const [chapterId, setChapterId] = useState("");
  const [scenes, setScenes] = useState<SceneOut[]>([]);
  const [sceneId, setSceneId] = useState("");
  const [versions, setVersions] = useState<SceneVersionOut[]>([]);
  const [versionId, setVersionId] = useState("");
  const [compare, setCompare] = useState<Compare>("previous");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .books()
      .then((b) => {
        setBooks(b);
        setBookId((prev) => prev || (b[0]?.id ?? ""));
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!bookId) return;
    setChapters([]);
    setChapterId("");
    api
      .chapters(bookId)
      .then(setChapters)
      .catch((e) => setError(String(e)));
  }, [bookId]);

  useEffect(() => {
    if (!chapterId) {
      setScenes([]);
      setSceneId("");
      return;
    }
    setScenes([]);
    setSceneId("");
    api
      .chapterScenes(chapterId)
      .then(setScenes)
      .catch((e) => setError(String(e)));
  }, [chapterId]);

  useEffect(() => {
    if (!sceneId) {
      setVersions([]);
      setVersionId("");
      return;
    }
    setVersions([]);
    setVersionId("");
    api
      .sceneVersions(sceneId)
      .then((v) => {
        setVersions(v);
        const sorted = [...v].sort((a, b) => a.version - b.version);
        const latest = sorted[sorted.length - 1];
        if (latest) setVersionId(latest.id);
      })
      .catch((e) => setError(String(e)));
  }, [sceneId]);

  // One entry per scene_no (the highest-version row) for the scene picker.
  const sceneList = useMemo(() => {
    const byNo = new Map<number, SceneOut>();
    for (const s of scenes) {
      const cur = byNo.get(s.scene_no);
      if (!cur || s.version > cur.version) byNo.set(s.scene_no, s);
    }
    return [...byNo.values()].sort((a, b) => a.scene_no - b.scene_no);
  }, [scenes]);

  const sortedVersions = useMemo(
    () => [...versions].sort((a, b) => a.version - b.version),
    [versions],
  );
  const selected = sortedVersions.find((v) => v.id === versionId) ?? null;
  const selIdx = selected ? sortedVersions.findIndex((v) => v.id === selected.id) : -1;

  const base =
    compare === "original"
      ? (selected?.agent_original ?? "")
      : selIdx > 0
        ? (sortedVersions[selIdx - 1].prose ?? "")
        : "";
  const target = selected?.prose ?? "";
  const ops = useMemo(() => (selected ? lineDiff(base, target) : []), [selected, base, target]);

  const noBase = !!selected && (compare === "original" ? !selected.agent_original : selIdx <= 0);

  return (
    <div className="history">
      <h2>History</h2>
      <p className="muted">
        Every revision is a new row that supersedes its parent. Browse a scene's lineage and diff a
        version against the previous one or against the agent's original.
      </p>
      {error && <p className="error">{error}</p>}

      <div className="row pickers">
        <label className="narrow">
          Book
          <select value={bookId} onChange={(e) => setBookId(e.target.value)}>
            {books.length === 0 && <option value="">— none —</option>}
            {books.map((b) => (
              <option key={b.id} value={b.id}>
                {b.title}
              </option>
            ))}
          </select>
        </label>
        <label className="narrow">
          Chapter
          <select value={chapterId} onChange={(e) => setChapterId(e.target.value)}>
            <option value="">— pick —</option>
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>
                Ch {c.chapter_no} · {c.pov}
              </option>
            ))}
          </select>
        </label>
        <label className="narrow">
          Scene
          <select value={sceneId} onChange={(e) => setSceneId(e.target.value)}>
            <option value="">— pick —</option>
            {sceneList.map((s) => (
              <option key={s.id} value={s.id}>
                Scene {s.scene_no} ({s.status})
              </option>
            ))}
          </select>
        </label>
      </div>

      {sortedVersions.length > 0 && (
        <div className="row pickers">
          <label className="narrow">
            Version
            <select value={versionId} onChange={(e) => setVersionId(e.target.value)}>
              {sortedVersions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} · {v.status}
                </option>
              ))}
            </select>
          </label>
          <label className="narrow">
            Compare against
            <select value={compare} onChange={(e) => setCompare(e.target.value as Compare)}>
              <option value="previous">previous version</option>
              <option value="original">agent original</option>
            </select>
          </label>
        </div>
      )}

      {selected && (
        <section className="diff">
          {noBase && (
            <p className="muted">
              {compare === "original"
                ? "No agent original recorded for this version — showing it as all-new."
                : "No previous version — showing this version as all-new."}
            </p>
          )}
          <pre>
            {ops.map((op, i) => (
              <div key={i} className={`diff-line diff-${op.type}`}>
                <span className="gutter">
                  {op.type === "add" ? "+" : op.type === "del" ? "−" : " "}
                </span>
                {op.text || " "}
              </div>
            ))}
          </pre>
        </section>
      )}
    </div>
  );
}
