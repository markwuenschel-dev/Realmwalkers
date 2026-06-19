import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BookOut, ManuscriptOut } from "../types";

const paragraphs = (prose: string): string[] =>
  prose.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);

export default function Manuscript() {
  const [books, setBooks] = useState<BookOut[]>([]);
  const [bookId, setBookId] = useState("");
  const [ms, setMs] = useState<ManuscriptOut | null>(null);
  const [loading, setLoading] = useState(false);
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
    setLoading(true);
    setError(null);
    setMs(null);
    api
      .manuscript(bookId)
      .then(setMs)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [bookId]);

  const empty = ms && ms.chapters.every((c) => c.scenes.length === 0);

  return (
    <div className="manuscript">
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
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading manuscript…</p>}
      {empty && <p className="muted">No approved scenes yet — approve scenes to build the manuscript.</p>}

      {ms && !empty && (
        <article className="reader">
          <h1>{ms.title}</h1>
          {ms.chapters.map((c) => (
            <section key={c.chapter_no} className="ms-chapter">
              <h2>
                Chapter {c.chapter_no}
                <span className="ms-pov"> · {c.pov}</span>
              </h2>
              {c.scenes.map((s) => (
                <div key={s.scene_no} className="ms-scene">
                  {paragraphs(s.prose).map((p, i) => (
                    <p key={i}>{p}</p>
                  ))}
                </div>
              ))}
            </section>
          ))}
        </article>
      )}
    </div>
  );
}
