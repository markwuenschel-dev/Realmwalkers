import { useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import type { RunStartOut } from "../api/types";

// Gate 1, in the browser: create a book, outline a chapter (the planner proposes per-scene beats),
// then approve + draft — no terminal. Beats are advisory until you approve; approving enqueues a
// draft job per scene and the worker is kicked immediately.
export default function Planner() {
  const { t } = useDesk();
  const data = useDeskData();
  const [title, setTitle] = useState("");
  const [chapterNo, setChapterNo] = useState(1);
  const [pov, setPov] = useState("");
  const [outline, setOutline] = useState("");
  const [proposed, setProposed] = useState<RunStartOut | null>(null);
  const [busy, setBusy] = useState(false);

  const card = css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:26px");
  const label = css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:10px");
  const input = css("width:100%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13.5px;font-family:var(--ui)");
  const btn = css("padding:8px 14px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:13px;cursor:pointer;font-family:var(--ui);white-space:nowrap");
  const btnGo = css("padding:9px 16px;border-radius:8px;border:1px solid color-mix(in srgb,var(--good) 50%,var(--line));background:color-mix(in srgb,var(--good) 13%,var(--bg3));color:var(--good);font-size:13.5px;font-weight:500;cursor:pointer;font-family:var(--ui)");

  const propose = async () => {
    if (!pov.trim() || !outline.trim()) return;
    setBusy(true);
    const out = await data.startRun(chapterNo, pov.trim(), outline.trim());
    setBusy(false);
    if (out) setProposed(out);
  };

  const approve = async () => {
    if (!proposed) return;
    setBusy(true);
    await data.approveAndDraft(proposed.chapter_id);
    setBusy(false);
    setProposed(null);
    setOutline("");
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
          <button style={btn} disabled={!title.trim()} onClick={() => data.createBook(title.trim())}>
            Create book
          </button>
        </div>
      ) : (
        <>
          <div style={css("display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px")}>
            <select
              value={data.bookId ?? ""}
              onChange={(e) => data.setBook(e.target.value)}
              style={css("background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:13px;font-family:var(--ui)")}
            >
              {data.books.map((b) => (
                <option key={b.id} value={b.id}>{b.title}</option>
              ))}
            </select>
            <input
              style={css("width:120px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:13px")}
              placeholder="new book…"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <button style={btn} disabled={!title.trim()} onClick={() => { data.createBook(title.trim()); setTitle(""); }}>
              + book
            </button>
          </div>

          <div style={css("display:grid;grid-template-columns:90px 1fr;gap:9px;align-items:center")}>
            <input
              type="number"
              min={1}
              value={chapterNo}
              onChange={(e) => setChapterNo(Number(e.target.value) || 1)}
              style={input}
              title="chapter number"
            />
            <input
              style={input}
              placeholder="POV character (e.g. Soren)"
              value={pov}
              onChange={(e) => setPov(e.target.value)}
            />
          </div>
          <textarea
            style={css("width:100%;min-height:64px;margin-top:9px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:10px 12px;font-size:13.5px;line-height:1.55;resize:vertical;font-family:var(--ui)")}
            placeholder="Outline this chapter — the planner proposes per-scene beats from it…"
            value={outline}
            onChange={(e) => setOutline(e.target.value)}
          />
          <div style={css("display:flex;gap:9px;align-items:center;margin-top:10px")}>
            <button style={btn} disabled={busy || !pov.trim() || !outline.trim()} onClick={propose}>
              {busy && !proposed ? "Proposing…" : "Propose beats"}
            </button>
            {proposed && (
              <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
                {proposed.beats.length} beats proposed for ch {proposed.chapter_no}
              </span>
            )}
          </div>

          {proposed && (
            <div style={css("margin-top:14px;border-top:1px solid var(--line);padding-top:14px")}>
              <div style={css("display:flex;flex-direction:column;gap:8px;margin-bottom:12px")}>
                {proposed.beats.map((b) => (
                  <div key={b.id} style={css("display:flex;gap:10px;font-size:13px;line-height:1.45")}>
                    <span style={css("font-family:var(--mono);font-size:11px;color:var(--accent);flex:none")}>S{b.scene_no}</span>
                    <span style={css("color:var(--dim)")}>{b.beat_text}</span>
                    {b.tags && b.tags.length > 0 && (
                      <span style={css("margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--info);flex:none")}>{b.tags.join(" · ")}</span>
                    )}
                  </div>
                ))}
              </div>
              <button style={btnGo} disabled={busy} onClick={approve}>
                {busy ? "Drafting…" : `Approve ${proposed.beats.length} beats & draft`}
              </button>
            </div>
          )}
        </>
      )}
      {data.error && (
        <div style={css(`margin-top:12px;font-family:var(--mono);font-size:11.5px;color:${t.bad}`)}>{data.error}</div>
      )}
    </div>
  );
}
