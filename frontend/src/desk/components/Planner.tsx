import { useState } from "react";
import { useRouter } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";

// One staged chapter in the batch panel. Inputs are kept as strings (raw form state) and coerced to
// numbers only at submit time.
interface BatchRow {
  chapter_no: string;
  pov: string;
  outline: string;
  max_beats: string;
  target_words: string;
}

interface BatchResultRow {
  chapter_id: string;
  chapter_no: number;
  pov: string;
}

// New-chapter entry point (contract-first): create the chapter, kick off chapter-packet authoring,
// and hand off to the Packets screen — which already knows how to review/approve the chapter packet,
// derive + approve scene packets, and Draft Chapter. This screen no longer authors or approves beats
// directly; that path (gate 1) had no way to reach drafting once the app moved to contract-first.
export default function Planner() {
  const { t } = useDesk();
  const data = useDeskData();
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [chapterNo, setChapterNo] = useState(1);
  const [pov, setPov] = useState("");
  const [outline, setOutline] = useState("");
  const [maxBeats, setMaxBeats] = useState("");
  const [targetWords, setTargetWords] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  // Batch panel: stage several chapters and generate a packet for each. Additive and opt-in — the
  // single-chapter flow above is untouched and remains the default path.
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchRows, setBatchRows] = useState<BatchRow[]>([
    { chapter_no: "1", pov: "", outline: "", max_beats: "", target_words: "" },
  ]);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResults, setBatchResults] = useState<BatchResultRow[] | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

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

  // The chapter row for the number currently in the form (exists once created). Its title is
  // generated server-side and editable here without re-running anything.
  const currentChapter = data.chapters.find((c) => c.chapter_no === chapterNo);

  const numOrUndef = (s: string): number | undefined => {
    const n = Number(s);
    return s.trim() && Number.isFinite(n) && n > 0 ? n : undefined;
  };

  // author_packet() has no structured length/scene-count parameter — it reads the outline verbatim —
  // so folding max-scenes/words-per-scene into the outline text itself is the only way to keep what
  // those two fields meant.
  const withGuidance = (
    outlineText: string,
    maxBeatsN: number | undefined,
    targetWordsN: number | undefined,
  ): string => {
    if (!maxBeatsN && !targetWordsN) return outlineText;
    const parts: string[] = [];
    if (targetWordsN) parts.push(`~${targetWordsN} words`);
    if (maxBeatsN) parts.push(`up to ${maxBeatsN} scenes`);
    return `${outlineText}\n\n(Target: ${parts.join(" across ")})`;
  };

  const generate = async () => {
    if (!pov.trim() || !outline.trim()) return;
    setNotice(null);
    const guided = withGuidance(outline.trim(), numOrUndef(maxBeats), numOrUndef(targetWords));
    const newChapterId = await data.createAndPropose(chapterNo, pov.trim(), guided);
    if (newChapterId) {
      router.push(`/packets?chapter=${newChapterId}`);
    } else {
      setNotice(data.error ?? "Could not create the chapter. Try again.");
    }
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
    const rows = batchRows.filter((r) => r.pov.trim() && r.outline.trim());
    if (rows.length === 0) {
      setBatchError("Add at least one row with a POV and an outline.");
      setBatchResults(null);
      return;
    }
    setBatchBusy(true);
    setBatchError(null);
    setBatchResults(null);
    const results: BatchResultRow[] = [];
    try {
      for (const r of rows) {
        const chNo = Number(r.chapter_no) || 1;
        const guided = withGuidance(
          r.outline.trim(),
          numOrUndef(r.max_beats),
          numOrUndef(r.target_words),
        );
        const newChapterId = await data.createAndPropose(chNo, r.pov.trim(), guided);
        if (newChapterId)
          results.push({ chapter_id: newChapterId, chapter_no: chNo, pov: r.pov.trim() });
      }
      setBatchResults(results);
      await data.refreshAll();
    } catch (e) {
      setBatchError(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
    }
  };

  return (
    <div style={card}>
      <div style={label}>New chapter</div>

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
                placeholder="e.g. 6"
                title="Folded into the outline as guidance for the chapter-packet author."
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
            Max scenes / words-per-scene are folded into the outline as guidance — the
            chapter-packet author still decides the actual scene structure.
          </div>

          {currentChapter && (
            <label style={css("display:block;margin-top:10px")}>
              <span style={fieldLabel}>
                Chapter title{" "}
                <span style={css("text-transform:none;letter-spacing:0;color:var(--dim)")}>
                  — generated on create; edit freely
                </span>
              </span>
              <input
                key={currentChapter.id}
                defaultValue={currentChapter.title ?? ""}
                placeholder="(generate a chapter packet to get a title, or type your own)"
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
              placeholder="Outline this chapter — the chapter-packet author will structure it into scenes…"
              value={outline}
              onChange={(e) => setOutline(e.target.value)}
            />
          </label>

          <div
            style={css("display:flex;gap:9px;align-items:center;margin-top:10px;flex-wrap:wrap")}
          >
            <button
              style={btn}
              disabled={data.creatingChapter || !pov.trim() || !outline.trim()}
              onClick={generate}
            >
              {data.creatingChapter ? "Generating…" : "Generate Chapter Packet"}
            </button>
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

          <div style={css("margin-top:22px;border-top:1px solid var(--line);padding-top:14px")}>
            <button style={ghost} onClick={() => setBatchOpen((o) => !o)}>
              {batchOpen ? "Hide batch planning" : "Batch · generate multiple chapter packets"}
            </button>

            {batchOpen && (
              <div style={css("margin-top:14px;display:flex;flex-direction:column;gap:12px")}>
                <p
                  style={css(
                    "margin:0;color:var(--dim);font-size:12.5px;line-height:1.55;max-width:620px",
                  )}
                >
                  Stage several chapters and generate a chapter packet for each — every chapter
                  authors concurrently in the background. Review/approve each on the Packets screen.
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
                          placeholder="e.g. 6"
                          title="Folded into the outline as guidance for the chapter-packet author."
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
                      placeholder="Outline this chapter…"
                      value={r.outline}
                      onChange={(e) => setBatchRow(i, { outline: e.target.value })}
                    />
                  </div>
                ))}

                <div style={css("display:flex;gap:14px;align-items:center;flex-wrap:wrap")}>
                  <button style={ghost} onClick={addBatchRow}>
                    + Add chapter
                  </button>
                  <button style={btnGo} disabled={batchBusy || !data.bookId} onClick={proposeAll}>
                    {batchBusy ? "Generating…" : "Generate all"}
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
                        No chapters were created.
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
                          <button
                            style={ghost}
                            onClick={() => router.push(`/packets?chapter=${res.chapter_id}`)}
                          >
                            Open in Packets →
                          </button>
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
