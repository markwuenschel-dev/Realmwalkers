import { useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import type { BeatOut } from "../api/types";

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

  const card = css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:26px");
  const label = css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:10px");
  const fieldLabel = css("display:block;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:4px");
  const input = css("width:100%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13.5px;font-family:var(--ui)");
  const numInput = (w: number) => css(`width:${w}px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13.5px;font-family:var(--ui)`);
  const btn = css("padding:8px 14px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:13px;cursor:pointer;font-family:var(--ui);white-space:nowrap");
  const ghost = css("padding:7px 12px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-size:12.5px;cursor:pointer;font-family:var(--ui);white-space:nowrap");
  const btnGo = css("padding:9px 16px;border-radius:8px;border:1px solid color-mix(in srgb,var(--good) 50%,var(--line));background:color-mix(in srgb,var(--good) 13%,var(--bg3));color:var(--good);font-size:13.5px;font-weight:500;cursor:pointer;font-family:var(--ui)");

  const numOrUndef = (s: string): number | undefined => {
    const n = Number(s);
    return s.trim() && Number.isFinite(n) && n > 0 ? n : undefined;
  };
  const split = (raw: string): string[] => raw.split(",").map((x) => x.trim()).filter(Boolean);

  const propose = async () => {
    if (!pov.trim() || !outline.trim()) return;
    setBusy(true);
    const out = await data.startRun(chapterNo, pov.trim(), outline.trim(), numOrUndef(maxBeats), numOrUndef(targetWords));
    setBusy(false);
    if (out) {
      setChapterId(out.chapter_id);
      setBeats(out.beats);
      setSelected(new Set(out.beats.map((b) => b.id)));
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
    const created = await api.createBeat(chapterId, { scene_no: nextNo, beat_text: "" }).catch(() => null);
    if (created) {
      setBeats((bs) => [...bs, created]);
      setSelected((s) => new Set(s).add(created.id));
    }
  };
  const toggle = (id: string) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
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

  return (
    <div style={card}>
      <div style={label}>Plan · gate 1</div>

      {data.books.length === 0 ? (
        <div style={css("display:flex;gap:8px;align-items:center")}>
          <input style={input} placeholder="Title your book to begin…" value={title} onChange={(e) => setTitle(e.target.value)} />
          <button style={btn} disabled={!title.trim()} onClick={() => data.createBook(title.trim())}>Create book</button>
        </div>
      ) : (
        <>
          <div style={css("display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px")}>
            <select value={data.bookId ?? ""} onChange={(e) => data.setBook(e.target.value)}
              style={css("background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:13px;font-family:var(--ui)")}>
              {data.books.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
            </select>
            <input style={numInput(120)} placeholder="new book…" value={title} onChange={(e) => setTitle(e.target.value)} />
            <button style={btn} disabled={!title.trim()} onClick={() => { data.createBook(title.trim()); setTitle(""); }}>+ book</button>
          </div>

          <div style={css("display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap")}>
            <label><span style={fieldLabel}>Chapter</span>
              <input type="number" min={1} value={chapterNo} onChange={(e) => setChapterNo(Number(e.target.value) || 1)} style={numInput(70)} /></label>
            <label style={css("flex:1 1 200px")}><span style={fieldLabel}>POV character</span>
              <input style={input} placeholder="e.g. Soren" value={pov} onChange={(e) => setPov(e.target.value)} /></label>
            <label><span style={fieldLabel}>Max scenes</span>
              <input type="number" min={1} value={maxBeats} placeholder="auto" onChange={(e) => setMaxBeats(e.target.value)} style={numInput(80)} /></label>
            <label><span style={fieldLabel}>Words / scene</span>
              <input type="number" min={50} step={50} value={targetWords} placeholder="default" onChange={(e) => setTargetWords(e.target.value)} style={numInput(90)} /></label>
          </div>

          <label style={css("display:block;margin-top:10px")}><span style={fieldLabel}>Outline</span>
            <textarea
              style={css("width:100%;min-height:64px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:10px 12px;font-size:13.5px;line-height:1.55;resize:vertical;font-family:var(--ui)")}
              placeholder="Outline this chapter — the planner proposes one beat (= one scene) per beat…"
              value={outline} onChange={(e) => setOutline(e.target.value)} />
          </label>

          <div style={css("display:flex;gap:9px;align-items:center;margin-top:10px;flex-wrap:wrap")}>
            <button style={btn} disabled={busy || !pov.trim() || !outline.trim()} onClick={propose}>
              {busy && beats.length === 0 ? "Proposing…" : beats.length ? "Re-propose (replaces below)" : "Propose beats"}
            </button>
            {beats.length > 0 && (
              <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
                {beats.length} beat{beats.length === 1 ? "" : "s"} = {beats.length} scene{beats.length === 1 ? "" : "s"}
              </span>
            )}
          </div>

          {beats.length > 0 && (
            <div style={css("margin-top:14px;border-top:1px solid var(--line);padding-top:14px;display:flex;flex-direction:column;gap:10px")}>
              {[...beats].sort((a, b) => a.scene_no - b.scene_no).map((b) => (
                <div key={b.id} style={css(`display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg2b);opacity:${selected.has(b.id) ? "1" : ".5"}`)}>
                  <input type="checkbox" checked={selected.has(b.id)} onChange={() => toggle(b.id)} title="draft this scene" style={css("margin-top:5px;cursor:pointer")} />
                  <span style={css("font-family:var(--mono);font-size:11px;color:var(--accent);flex:none;margin-top:5px;width:28px")}>S{b.scene_no}</span>
                  <div style={css("flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:7px")}>
                    <textarea
                      defaultValue={b.beat_text ?? ""}
                      onBlur={(e) => { if (e.target.value !== (b.beat_text ?? "")) patchBeat(b.id, { beat_text: e.target.value }); }}
                      placeholder="what happens in this scene…"
                      style={css("width:100%;min-height:46px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:7px 9px;font-size:13px;line-height:1.45;resize:vertical;font-family:var(--ui)")}
                    />
                    <div style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}>
                      <input defaultValue={(b.tags ?? []).join(", ")} placeholder="tags: combat, dialogue…"
                        onBlur={(e) => patchBeat(b.id, { tags: split(e.target.value) })}
                        style={css("flex:1 1 160px;background:var(--bg3);color:var(--dim);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:11.5px;font-family:var(--mono)")} />
                      <input type="number" min={50} step={50} defaultValue={b.target_words ?? ""} placeholder="words"
                        onBlur={(e) => patchBeat(b.id, { target_words: e.target.value ? Number(e.target.value) : null })}
                        style={css("width:84px;background:var(--bg3);color:var(--dim);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font-size:11.5px;font-family:var(--mono)")} title="target words for this scene" />
                    </div>
                  </div>
                  <button onClick={() => removeBeat(b.id)} title="delete beat"
                    style={css("flex:none;background:none;border:none;color:var(--dim);font-size:16px;cursor:pointer;line-height:1;margin-top:3px")}>×</button>
                </div>
              ))}

              <div style={css("display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:4px")}>
                <button style={ghost} onClick={addBeat}>+ Add scene</button>
                <button style={btnGo} disabled={busy || selected.size === 0} onClick={approve}>
                  {busy ? "Drafting…" : `Approve ${selected.size} selected & draft`}
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {data.error && <div style={css(`margin-top:12px;font-family:var(--mono);font-size:11.5px;color:${t.bad}`)}>{data.error}</div>}
    </div>
  );
}
