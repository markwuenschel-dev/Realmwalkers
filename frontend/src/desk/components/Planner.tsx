import { useEffect, useRef, useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import type { BatchChapterResult, BatchChapterSpec, BeatOut } from "../api/types";

// One staged chapter in the batch panel. Inputs are kept as strings (raw form state) and coerced to
// the BatchChapterSpec wire shape only at submit time.
interface BatchRow {
  chapter_no: string;
  pov: string;
  outline: string;
  max_beats: string;
  target_words: string;
}

// Gate 1, in the browser: create a book, outline a chapter (the planner proposes per-scene beats),
// then edit / add / delete / re-propose those beats, pick which to draft, and approve. One beat =
// one scene. Nothing drafts until you approve.
export default function Planner() {
  const { t } = useDesk();
  const data = useDeskData();
  const [title, setTitle] = useState("");
  const [chapterNo, setChapterNo] = useState(1);
  const [pov, setPov] = useState("");
  const [outline, setOutline] = useState("");
  const [maxBeats, setMaxBeats] = useState("");
  const [targetWords, setTargetWords] = useState("");
  const [chapterId, setChapterId] = useState<string | null>(null);
  const [beats, setBeats] = useState<BeatOut[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Batch panel: stage several chapters and plan them in one /runs/batch call. Additive and opt-in —
  // the single-chapter propose flow above is untouched and remains the default review path.
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchRows, setBatchRows] = useState<BatchRow[]>([
    { chapter_no: "1", pov: "", outline: "", max_beats: "", target_words: "" },
  ]);
  const [batchAuto, setBatchAuto] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResults, setBatchResults] = useState<BatchChapterResult[] | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

  // Is a gate-1 plan call in flight for the chapter in the form? Tracked in the data provider (not
  // here) so it survives an in-app tab switch that unmounts this panel — the propose keeps running
  // server-side, and this stays true until it lands.
  const planning = data.planningChapters.has(chapterNo);

  // Proposed beats are persisted server-side, but the propose *response* is the only thing that
  // populates this panel — so if that request was lost (timeout, reload, navigating away) the beats
  // become invisible even though they're safe in the DB. Re-hydrate them: once per (book, chapter),
  // pull the chapter's still-proposed beats and surface them for editing/approval. We never clobber
  // an in-progress edit (only hydrate when the panel is empty), and we WAIT for any in-flight plan
  // call to finish first — pulling mid-flight would find no beats yet and then never retry.
  const beatsRef = useRef(beats);
  beatsRef.current = beats;
  const hydratedKey = useRef<string | null>(null);
  // Re-arm the guard whenever the targeted chapter (or book) changes, so navigating away and back
  // re-checks for proposed beats. Same-chapter re-renders (poll ticks) keep the guard set and are
  // deduped. Declared before the hydrate effect so the reset wins on a chapter change.
  useEffect(() => {
    hydratedKey.current = null;
  }, [data.bookId, chapterNo]);
  useEffect(() => {
    if (!data.bookId) return;
    if (planning) return; // wait for the in-flight plan call to land its beats
    if (beatsRef.current.length > 0) return; // don't clobber shown/edited beats
    const key = `${data.bookId}:${chapterNo}`;
    if (hydratedKey.current === key) return;
    const ch = data.chapters.find((c) => c.chapter_no === chapterNo);
    if (!ch) return; // chapter not created yet — retry when chapters load
    hydratedKey.current = key;
    (async () => {
      const all = await api.chapterBeats(ch.id).catch(() => []);
      const proposed = all.filter((b) => b.status === "proposed");
      if (proposed.length === 0 || beatsRef.current.length > 0) return;
      setChapterId(ch.id);
      setBeats(proposed);
      setSelected(new Set(proposed.map((b) => b.id)));
      if (!pov.trim()) setPov(ch.pov);
      if (!outline.trim() && ch.outline) setOutline(ch.outline);
    })();
  }, [data.bookId, data.chapters, chapterNo, pov, outline, planning]);

  const card = css(
    "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:26px",
  );
  const label = css(
    "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:10px",
  );
  const fieldLabel = css(
    "display:block;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:4px",
  );
  const input = css(
    "width:100%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13.5px;font-family:var(--ui)",
  );
  const numInput = (w: number) =>
    css(
      `width:${w}px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13.5px;font-family:var(--ui)`,
    );
  const btn = css(
    "padding:8px 14px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:13px;cursor:pointer;font-family:var(--ui);white-space:nowrap",
  );
  const ghost = css(
    "padding:7px 12px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-size:12.5px;cursor:pointer;font-family:var(--ui);white-space:nowrap",
  );
  const btnGo = css(
    "padding:9px 16px;border-radius:8px;border:1px solid color-mix(in srgb,var(--good) 50%,var(--line));background:color-mix(in srgb,var(--good) 13%,var(--bg3));color:var(--good);font-size:13.5px;font-weight:500;cursor:pointer;font-family:var(--ui)",
  );

  // The chapter row for the number currently in the form (exists once the chapter's been created by a
  // propose). Its title is generated by the plan-call and editable here without re-running the planner.
  const currentChapter = data.chapters.find((c) => c.chapter_no === chapterNo);

  const numOrUndef = (s: string): number | undefined => {
    const n = Number(s);
    return s.trim() && Number.isFinite(n) && n > 0 ? n : undefined;
  };
  const split = (raw: string): string[] =>
    raw
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);

  const propose = async () => {
    if (!pov.trim() || !outline.trim()) return;
    setNotice(null);
    // Busy state lives in the provider (data.planningChapters) so it survives a tab switch — don't
    // toggle a local flag here. The await still resolves here when the panel stays mounted.
    const out = await data.startRun(
      chapterNo,
      pov.trim(),
      outline.trim(),
      numOrUndef(maxBeats),
      numOrUndef(targetWords),
    );
    if (out) {
      setChapterId(out.chapter_id);
      setBeats(out.beats);
      setSelected(new Set(out.beats.map((b) => b.id)));
      // Empty result = the planner couldn't produce beats from this outline. Say so explicitly —
      // existing beats are preserved server-side, so this is a safe retry, not a silent wipe.
      setNotice(
        out.beats.length === 0
          ? "The planner returned no beats for this outline. Your existing beats are kept — try again, or make the outline more concrete and scene-by-scene."
          : null,
      );
    }
  };

  const patchBeat = async (id: string, patch: Record<string, unknown>) => {
    const updated = await api.updateBeat(id, patch).catch(() => null);
    if (updated) setBeats((bs) => bs.map((b) => (b.id === id ? updated : b)));
  };
  const removeBeat = async (id: string) => {
    await api.deleteBeat(id).catch(() => {});
    setBeats((bs) => bs.filter((b) => b.id !== id));
    setSelected((s) => {
      const n = new Set(s);
      n.delete(id);
      return n;
    });
  };
  const addBeat = async () => {
    if (!chapterId) return;
    const nextNo = beats.reduce((m, b) => Math.max(m, b.scene_no), 0) + 1;
    const created = await api
      .createBeat(chapterId, { scene_no: nextNo, beat_text: "" })
      .catch(() => null);
    if (created) {
      setBeats((bs) => [...bs, created]);
      setSelected((s) => new Set(s).add(created.id));
    }
  };
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const approve = async () => {
    if (!chapterId || selected.size === 0) return;
    setBusy(true);
    await data.approveAndDraft(chapterId, [...selected]);
    setBusy(false);
    setBeats([]);
    setChapterId(null);
    setSelected(new Set());
    setOutline("");
  };

  // --- batch planning -----------------------------------------------------------------------------
  const setBatchRow = (i: number, patch: Partial<BatchRow>) =>
    setBatchRows((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addBatchRow = () =>
    setBatchRows((rows) => {
      const next = rows.reduce((m, r) => Math.max(m, Number(r.chapter_no) || 0), 0) + 1;
      return [
        ...rows,
        { chapter_no: String(next), pov: "", outline: "", max_beats: "", target_words: "" },
      ];
    });
  const removeBatchRow = (i: number) =>
    setBatchRows((rows) => (rows.length > 1 ? rows.filter((_, j) => j !== i) : rows));

  const proposeAll = async () => {
    if (!data.bookId) return;
    const chapters: BatchChapterSpec[] = batchRows
      .filter((r) => r.pov.trim() && r.outline.trim())
      .map((r) => ({
        chapter_no: Number(r.chapter_no) || 1,
        pov: r.pov.trim(),
        outline: r.outline.trim(),
        max_beats: numOrUndef(r.max_beats) ?? null,
        target_words: numOrUndef(r.target_words) ?? null,
      }));
    if (chapters.length === 0) {
      setBatchError("Add at least one row with a POV and an outline.");
      setBatchResults(null);
      return;
    }
    setBatchBusy(true);
    setBatchError(null);
    setBatchResults(null);
    try {
      const out = await api.batchRun({
        book_id: data.bookId,
        chapters,
        gate_mode: batchAuto ? "draft_ahead" : "pause_each",
        auto_draft: batchAuto,
      });
      setBatchResults(out.results);
      // Surface the freshly created chapters/beats elsewhere (dropdown, hydrate path) without a reload.
      await data.refreshAll();
    } catch (e) {
      setBatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
    }
  };

  return (
    <div style={card}>
      <div style={label}>Plan · gate 1</div>

      {data.books.length === 0 ? (
        <div style={css("display:flex;gap:8px;align-items:center")}>
          <input
            style={input}
            placeholder="Title your book to begin…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button
            style={btn}
            disabled={!title.trim()}
            onClick={() => data.createBook(title.trim())}
          >
            Create book
          </button>
        </div>
      ) : (
        <>
          <div
            style={css(
              "display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px",
            )}
          >
            <select
              value={data.bookId ?? ""}
              onChange={(e) => data.setBook(e.target.value)}
              style={css(
                "background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:13px;font-family:var(--ui)",
              )}
            >
              {data.books.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.title}
                </option>
              ))}
            </select>
            <input
              style={numInput(120)}
              placeholder="new book…"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <button
              style={btn}
              disabled={!title.trim()}
              onClick={() => {
                data.createBook(title.trim());
                setTitle("");
              }}
            >
              + book
            </button>
          </div>

          <div style={css("display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap")}>
            <label>
              <span style={fieldLabel}>Chapter</span>
              <input
                type="number"
                min={1}
                value={chapterNo}
                onChange={(e) => setChapterNo(Number(e.target.value) || 1)}
                style={numInput(70)}
              />
            </label>
            <label style={css("flex:1 1 200px")}>
              <span style={fieldLabel}>POV character</span>
              <input
                style={input}
                placeholder="e.g. Soren"
                value={pov}
                onChange={(e) => setPov(e.target.value)}
              />
            </label>
            <label>
              <span style={fieldLabel}>Max scenes</span>
              <input
                type="number"
                min={1}
                value={maxBeats}
                placeholder="max 24"
                title="Upper limit — the planner proposes only what the outline needs, never more than this."
                onChange={(e) => setMaxBeats(e.target.value)}
                style={numInput(90)}
              />
            </label>
            <label>
              <span style={fieldLabel}>Words / scene</span>
              <input
                type="number"
                min={50}
                step={50}
                value={targetWords}
                placeholder="default"
                onChange={(e) => setTargetWords(e.target.value)}
                style={numInput(90)}
              />
            </label>
          </div>

          <div
            style={css(
              "font-family:var(--mono);font-size:10px;color:var(--dim);margin-top:6px;line-height:1.5",
            )}
          >
            Max scenes is an upper limit — the planner proposes only what the outline needs.
          </div>

          {currentChapter && (
            <label style={css("display:block;margin-top:10px")}>
              <span style={fieldLabel}>
                Chapter title{" "}
                <span style={css("text-transform:none;letter-spacing:0;color:var(--dim)")}>
                  — generated on propose; edit freely
                </span>
              </span>
              <input
                key={currentChapter.id}
                defaultValue={currentChapter.title ?? ""}
                placeholder="(propose beats to generate a title, or type your own)"
                onBlur={(e) => {
                  const v = e.target.value.trim();
                  if (v !== (currentChapter.title ?? ""))
                    data.updateChapter(currentChapter.id, { title: v || null });
                }}
                style={input}
              />
            </label>
          )}

          <label style={css("display:block;margin-top:10px")}>
            <span style={fieldLabel}>Outline</span>
            <textarea
              style={css(
                "width:100%;min-height:96px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:10px 12px;font-size:13.5px;line-height:1.55;resize:vertical;font-family:var(--ui)",
              )}
              placeholder="Outline this chapter — the planner proposes one beat (= one scene) per beat…"
              value={outline}
              onChange={(e) => setOutline(e.target.value)}
            />
          </label>

          <div
            style={css("display:flex;gap:9px;align-items:center;margin-top:10px;flex-wrap:wrap")}
          >
            <button
              style={btn}
              disabled={planning || !pov.trim() || !outline.trim()}
              onClick={propose}
            >
              {planning
                ? "Proposing…"
                : beats.length
                  ? "Re-propose (replaces below)"
                  : "Propose beats"}
            </button>
            {beats.length > 0 && (
              <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
                {beats.length} beat{beats.length === 1 ? "" : "s"} = {beats.length} scene
                {beats.length === 1 ? "" : "s"}
              </span>
            )}
          </div>

          {notice && (
            <div
              style={css(
                `margin-top:10px;padding:9px 12px;border-radius:7px;border:1px solid ${t.warn};background:color-mix(in srgb,${t.warn} 12%,transparent);color:var(--ink);font-size:12.5px;line-height:1.5`,
              )}
            >
              {notice}
            </div>
          )}

          {beats.length > 0 && (
            <div
              style={css(
                "margin-top:14px;border-top:1px solid var(--line);padding-top:14px;display:flex;flex-direction:column;gap:10px",
              )}
            >
              {[...beats]
                .sort((a, b) => a.scene_no - b.scene_no)
                .map((b) => (
                  <div
                    key={b.id}
                    style={css(
                      `display:flex;gap:10px;align-items:flex-start;padding:13px 14px;border:1px solid var(--line);border-radius:9px;background:var(--bg2b);opacity:${selected.has(b.id) ? "1" : ".5"}`,
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(b.id)}
                      onChange={() => toggle(b.id)}
                      title="draft this scene"
                      style={css("margin-top:5px;cursor:pointer")}
                    />
                    <span
                      style={css(
                        "font-family:var(--mono);font-size:11px;color:var(--accent);flex:none;margin-top:5px;width:28px",
                      )}
                    >
                      S{b.scene_no}
                    </span>
                    <div
                      style={css(
                        "flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:7px",
                      )}
                    >
                      <textarea
                        defaultValue={b.beat_text ?? ""}
                        onBlur={(e) => {
                          if (e.target.value !== (b.beat_text ?? ""))
                            patchBeat(b.id, { beat_text: e.target.value });
                        }}
                        placeholder="what happens in this scene…"
                        style={css(
                          "width:100%;min-height:104px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:9px 11px;font-size:13.5px;line-height:1.5;resize:vertical;font-family:var(--ui)",
                        )}
                      />
                      <div style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}>
                        <input
                          defaultValue={b.pov ?? ""}
                          placeholder={pov.trim() || currentChapter?.pov || "POV"}
                          onBlur={(e) => {
                            const v = e.target.value.trim();
                            if (v !== (b.pov ?? "").trim()) patchBeat(b.id, { pov: v });
                          }}
                          title="POV override for this scene — leave blank to inherit the chapter POV"
                          style={css(
                            "width:110px;background:var(--bg3);color:var(--dim);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:11.5px;font-family:var(--mono)",
                          )}
                        />
                        <input
                          defaultValue={(b.tags ?? []).join(", ")}
                          placeholder="tags: combat, dialogue…"
                          onBlur={(e) => patchBeat(b.id, { tags: split(e.target.value) })}
                          style={css(
                            "flex:1 1 160px;background:var(--bg3);color:var(--dim);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:11.5px;font-family:var(--mono)",
                          )}
                        />
                        <input
                          type="number"
                          min={50}
                          step={50}
                          defaultValue={b.target_words ?? ""}
                          placeholder="words"
                          onBlur={(e) =>
                            patchBeat(b.id, {
                              target_words: e.target.value ? Number(e.target.value) : null,
                            })
                          }
                          style={css(
                            "width:84px;background:var(--bg3);color:var(--dim);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:11.5px;font-family:var(--mono)",
                          )}
                          title="target words for this scene"
                        />
                      </div>
                    </div>
                    <button
                      onClick={() => removeBeat(b.id)}
                      title="delete beat"
                      style={css(
                        "flex:none;background:none;border:none;color:var(--dim);font-size:16px;cursor:pointer;line-height:1;margin-top:3px",
                      )}
                    >
                      ×
                    </button>
                  </div>
                ))}

              <div
                style={css("display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:4px")}
              >
                <button style={ghost} onClick={addBeat}>
                  + Add scene
                </button>
                <button style={btnGo} disabled={busy || selected.size === 0} onClick={approve}>
                  {busy ? "Drafting…" : `Approve ${selected.size} selected & draft`}
                </button>
              </div>
            </div>
          )}

          <div style={css("margin-top:22px;border-top:1px solid var(--line);padding-top:14px")}>
            <button style={ghost} onClick={() => setBatchOpen((o) => !o)}>
              {batchOpen ? "Hide batch planning" : "Batch · propose multiple chapters"}
            </button>

            {batchOpen && (
              <div style={css("margin-top:14px;display:flex;flex-direction:column;gap:12px")}>
                <p
                  style={css(
                    "margin:0;color:var(--dim);font-size:12.5px;line-height:1.55;max-width:620px",
                  )}
                >
                  Stage several chapters and plan them all at once — each row outlines one chapter.
                  With Auto-approve and draft on, the planner approves and queues its beats for
                  drafting, skipping the manual gate-1 review.
                </p>

                {batchRows.map((r, i) => (
                  <div
                    key={i}
                    style={css(
                      "border:1px solid var(--line);border-radius:9px;background:var(--bg2b);padding:12px 13px;display:flex;flex-direction:column;gap:9px",
                    )}
                  >
                    <div style={css("display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap")}>
                      <label>
                        <span style={fieldLabel}>Chapter</span>
                        <input
                          type="number"
                          min={1}
                          value={r.chapter_no}
                          onChange={(e) => setBatchRow(i, { chapter_no: e.target.value })}
                          style={numInput(70)}
                        />
                      </label>
                      <label style={css("flex:1 1 160px")}>
                        <span style={fieldLabel}>POV character</span>
                        <input
                          style={input}
                          placeholder="e.g. Soren"
                          value={r.pov}
                          onChange={(e) => setBatchRow(i, { pov: e.target.value })}
                        />
                      </label>
                      <label>
                        <span style={fieldLabel}>Max scenes</span>
                        <input
                          type="number"
                          min={1}
                          value={r.max_beats}
                          placeholder="max 24"
                          title="Upper limit — the planner proposes only what the outline needs."
                          onChange={(e) => setBatchRow(i, { max_beats: e.target.value })}
                          style={numInput(90)}
                        />
                      </label>
                      <label>
                        <span style={fieldLabel}>Words / scene</span>
                        <input
                          type="number"
                          min={50}
                          step={50}
                          value={r.target_words}
                          placeholder="default"
                          onChange={(e) => setBatchRow(i, { target_words: e.target.value })}
                          style={numInput(90)}
                        />
                      </label>
                      {batchRows.length > 1 && (
                        <button
                          onClick={() => removeBatchRow(i)}
                          title="remove chapter row"
                          style={css(
                            "flex:none;background:none;border:none;color:var(--dim);font-size:16px;cursor:pointer;line-height:1;padding:6px",
                          )}
                        >
                          ×
                        </button>
                      )}
                    </div>
                    <textarea
                      style={css(
                        "width:100%;min-height:72px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:9px 11px;font-size:13px;line-height:1.5;resize:vertical;font-family:var(--ui)",
                      )}
                      placeholder="Outline this chapter — one beat (= one scene) per beat…"
                      value={r.outline}
                      onChange={(e) => setBatchRow(i, { outline: e.target.value })}
                    />
                  </div>
                ))}

                <div style={css("display:flex;gap:14px;align-items:center;flex-wrap:wrap")}>
                  <button style={ghost} onClick={addBatchRow}>
                    + Add chapter
                  </button>
                  <label
                    style={css(
                      "display:flex;gap:7px;align-items:center;cursor:pointer;font-size:13px;color:var(--ink);font-family:var(--ui)",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={batchAuto}
                      onChange={(e) => setBatchAuto(e.target.checked)}
                      style={css("cursor:pointer")}
                    />
                    Auto-approve and draft
                  </label>
                  <button style={btnGo} disabled={batchBusy || !data.bookId} onClick={proposeAll}>
                    {batchBusy ? "Proposing…" : "Propose all"}
                  </button>
                </div>

                {batchError && (
                  <div
                    style={css(
                      `padding:9px 12px;border-radius:7px;border:1px solid ${t.bad};background:color-mix(in srgb,${t.bad} 10%,transparent);color:var(--ink);font-size:12.5px;line-height:1.5`,
                    )}
                  >
                    {batchError}
                  </div>
                )}

                {batchResults && (
                  <div style={css("display:flex;flex-direction:column;gap:6px")}>
                    {batchResults.length === 0 ? (
                      <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
                        No chapters were planned.
                      </div>
                    ) : (
                      batchResults.map((res) => (
                        <div
                          key={res.chapter_id}
                          style={css(
                            "display:flex;gap:10px;align-items:center;flex-wrap:wrap;border:1px solid var(--line);border-radius:8px;background:var(--bg2b);padding:8px 11px;font-family:var(--mono);font-size:11.5px;color:var(--ink)",
                          )}
                        >
                          <span style={css("color:var(--accent)")}>Ch {res.chapter_no}</span>
                          <span style={css("color:var(--dim)")}>{res.pov}</span>
                          <span>
                            {res.beat_count} beat{res.beat_count === 1 ? "" : "s"}
                          </span>
                          <span style={css("color:var(--dim)")}>{res.queued_jobs} queued</span>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
