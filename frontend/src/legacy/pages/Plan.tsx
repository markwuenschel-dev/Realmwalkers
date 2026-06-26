import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { BeatOut, BookOut, GateMode } from "../types";

// Local, editable representation of a proposed beat. The list-y fields are edited as plain text
// (comma- or newline-separated) and converted back on save; expected_state_changes is raw JSON.
type BeatForm = {
  id: string;
  scene_no: number;
  status: string;
  beat_text: string;
  characters: string; // comma-separated
  tags: string; // comma-separated
  knowledge: string; // one per line
  esc: string; // JSON object
  saving: boolean;
  saved: boolean;
  err: string | null;
};

const toForm = (b: BeatOut): BeatForm => ({
  id: b.id,
  scene_no: b.scene_no,
  status: b.status,
  beat_text: b.beat_text ?? "",
  characters: (b.characters_present ?? []).join(", "),
  tags: (b.tags ?? []).join(", "),
  knowledge: (b.knowledge_injections ?? []).join("\n"),
  esc: b.expected_state_changes ? JSON.stringify(b.expected_state_changes, null, 2) : "",
  saving: false,
  saved: false,
  err: null,
});

const splitCommas = (s: string): string[] =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
const splitLines = (s: string): string[] =>
  s
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

export default function Plan() {
  const nav = useNavigate();
  const [books, setBooks] = useState<BookOut[]>([]);
  const [bookId, setBookId] = useState("");
  const [newTitle, setNewTitle] = useState("");

  const [chapterNo, setChapterNo] = useState(1);
  const [pov, setPov] = useState("Soren");
  const [outline, setOutline] = useState("");
  const [gateMode, setGateMode] = useState<GateMode>("pause_each");

  const [planning, setPlanning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chapterId, setChapterId] = useState<string | null>(null);
  const [beats, setBeats] = useState<BeatForm[]>([]);

  useEffect(() => {
    api
      .books()
      .then((b) => {
        setBooks(b);
        setBookId((prev) => prev || (b[0]?.id ?? ""));
      })
      .catch((e) => setError(String(e)));
  }, []);

  const createBook = async () => {
    const title = newTitle.trim();
    if (!title) return;
    try {
      const b = await api.createBook({ title });
      setBooks((prev) => [...prev, b]);
      setBookId(b.id);
      setNewTitle("");
    } catch (e) {
      setError(String(e));
    }
  };

  const propose = async () => {
    if (!bookId) return setError("Pick or create a book first.");
    if (!outline.trim()) return setError("Write a chapter outline first.");
    setPlanning(true);
    setError(null);
    try {
      const out = await api.startRun({
        book_id: bookId,
        chapter_no: chapterNo,
        pov,
        outline,
        gate_mode: gateMode,
      });
      setChapterId(out.chapter_id);
      setBeats(out.beats.map(toForm));
    } catch (e) {
      setError(String(e));
    } finally {
      setPlanning(false);
    }
  };

  const patch = (id: string, p: Partial<BeatForm>) =>
    setBeats((prev) => prev.map((b) => (b.id === id ? { ...b, ...p } : b)));

  const saveBeat = async (bf: BeatForm) => {
    let esc: Record<string, unknown> | null = null;
    if (bf.esc.trim()) {
      try {
        esc = JSON.parse(bf.esc) as Record<string, unknown>;
      } catch {
        patch(bf.id, { err: "expected_state_changes is not valid JSON", saved: false });
        return;
      }
    }
    patch(bf.id, { saving: true, saved: false, err: null });
    try {
      const updated = await api.updateBeat(bf.id, {
        beat_text: bf.beat_text,
        characters_present: splitCommas(bf.characters),
        tags: splitCommas(bf.tags),
        knowledge_injections: splitLines(bf.knowledge),
        expected_state_changes: esc,
      });
      patch(bf.id, { saving: false, saved: true, status: updated.status });
    } catch (e) {
      patch(bf.id, { saving: false, err: String(e) });
    }
  };

  const approveAll = async () => {
    if (!chapterId) return;
    setApproving(true);
    setError(null);
    try {
      await api.approveBeats(chapterId);
      nav("/"); // queued drafts now appear in the inbox
    } catch (e) {
      setError(String(e));
      setApproving(false);
    }
  };

  return (
    <div className="plan">
      <h2>Plan a chapter</h2>
      <p className="muted">
        Write the chapter outline; a single bounded plan-call proposes per-scene beats. Edit them,
        then approve to enqueue the scene drafts (gate 1).
      </p>
      {error && <p className="error">{error}</p>}

      <section className="form">
        <label>
          Book
          <div className="row">
            <select value={bookId} onChange={(e) => setBookId(e.target.value)}>
              {books.length === 0 && <option value="">— no books yet —</option>}
              {books.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.title}
                </option>
              ))}
            </select>
            <input
              placeholder="…or new book title"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
            />
            <button onClick={createBook} disabled={!newTitle.trim()}>
              Create
            </button>
          </div>
        </label>

        <div className="row">
          <label className="narrow">
            Chapter no.
            <input
              type="number"
              min={1}
              value={chapterNo}
              onChange={(e) => setChapterNo(Number(e.target.value) || 1)}
            />
          </label>
          <label className="narrow">
            POV
            <input value={pov} onChange={(e) => setPov(e.target.value)} />
          </label>
          <label className="narrow">
            Gate mode
            <select value={gateMode} onChange={(e) => setGateMode(e.target.value as GateMode)}>
              <option value="pause_each">pause_each</option>
              <option value="draft_ahead">draft_ahead</option>
            </select>
          </label>
        </div>

        <label>
          Chapter outline
          <textarea
            className="outline"
            placeholder="What happens in this chapter, in order…"
            value={outline}
            onChange={(e) => setOutline(e.target.value)}
          />
        </label>

        <button className="primary" onClick={propose} disabled={planning}>
          {planning ? "Proposing beats…" : "Propose beats"}
        </button>
      </section>

      {beats.length > 0 && (
        <section className="beats">
          <div className="beats-head">
            <h3>Proposed beats ({beats.length})</h3>
            <button className="primary" onClick={approveAll} disabled={approving}>
              {approving ? "Enqueuing…" : "Approve all & enqueue"}
            </button>
          </div>
          <p className="muted">Save edits per beat before approving.</p>

          {beats.map((b) => (
            <article key={b.id} className="beat-card">
              <header>
                <span className="sceneref">Scene {b.scene_no}</span>
                <span className="passes">{b.status}</span>
              </header>
              <label>
                Beat
                <textarea
                  value={b.beat_text}
                  onChange={(e) => patch(b.id, { beat_text: e.target.value, saved: false })}
                />
              </label>
              <div className="row">
                <label>
                  Characters present (comma-separated)
                  <input
                    value={b.characters}
                    onChange={(e) => patch(b.id, { characters: e.target.value, saved: false })}
                  />
                </label>
                <label>
                  Tags (comma-separated)
                  <input
                    value={b.tags}
                    onChange={(e) => patch(b.id, { tags: e.target.value, saved: false })}
                  />
                </label>
              </div>
              <div className="row">
                <label>
                  Knowledge injections (one per line)
                  <textarea
                    className="short"
                    value={b.knowledge}
                    onChange={(e) => patch(b.id, { knowledge: e.target.value, saved: false })}
                  />
                </label>
                <label>
                  Expected state changes (JSON)
                  <textarea
                    className="short mono"
                    placeholder='{"Soren": {"level": "+1"}}'
                    value={b.esc}
                    onChange={(e) => patch(b.id, { esc: e.target.value, saved: false })}
                  />
                </label>
              </div>
              <div className="beat-actions">
                <button onClick={() => saveBeat(b)} disabled={b.saving}>
                  {b.saving ? "Saving…" : b.saved ? "Saved ✓" : "Save"}
                </button>
                {b.err && <span className="error">{b.err}</span>}
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
