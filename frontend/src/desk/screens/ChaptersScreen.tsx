import { useEffect, useMemo, useRef, useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { api } from "../api/client";
import { useFetch, useSelectedBook } from "../api/hooks";
import type { ChapterOut, SceneOut } from "../api/client";
import {
  boardScenes,
  chapterRow,
  latestPerScene,
  povLanes,
  timelineScenes,
} from "../api/adapters.chapters";
import type { BoardScene, ChaptersView } from "../types";

// No word-target model exists (roadmap: target stays local/derived). One sensible per-chapter target.
const CHAPTER_TARGET = 2000;

interface ChapterBundle {
  chapter: ChapterOut;
  latest: SceneOut[];
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div style={css("padding:40px;text-align:center;font-family:var(--mono);font-size:13px;color:var(--dim)")}>{children}</div>
    </div>
  );
}

export default function ChaptersScreen() {
  const desk = useDesk();
  const { t } = desk;
  const book = useSelectedBook();
  const bookId = book.bookId;

  // Chapters → each chapter's scenes (latest version per scene_no).
  const bundles = useFetch<ChapterBundle[]>(async () => {
    if (!bookId) return [];
    const chapters = await api.chapters(bookId);
    return Promise.all(
      chapters.map(async (chapter) => ({ chapter, latest: latestPerScene(await api.chapterScenes(chapter.id)) })),
    );
  }, [bookId]);

  const data = bundles.data ?? [];

  // Board drag-reorder is local-only and non-persisting. Seed a local board (and a scene lookup) from
  // the live data; never touch the global state.ts board.
  const liveScenes = useMemo(() => data.flatMap((b) => boardScenes(b.latest)), [data]);
  const [order, setOrder] = useState<string[]>([]);
  const [localDrag, setLocalDrag] = useState<string | null>(null);
  const dragRef = useRef<string | null>(null);
  useEffect(() => {
    setOrder(liveScenes.map((s) => s.id));
  }, [liveScenes]);

  const sceneById = useMemo(() => {
    const m = new Map<string, BoardScene>();
    for (const s of liveScenes) m.set(s.id, s.scene);
    return m;
  }, [liveScenes]);

  const onDragStart = (id: string) => {
    dragRef.current = id;
    setLocalDrag(id);
  };
  const onDragEnter = (id: string) => {
    const d = dragRef.current;
    if (!d || d === id) return;
    setOrder((prev) => {
      const arr = prev.slice();
      arr.splice(arr.indexOf(id), 0, arr.splice(arr.indexOf(d), 1)[0]);
      return arr;
    });
  };
  const onDragEnd = () => {
    dragRef.current = null;
    setLocalDrag(null);
  };

  // Derived view-models.
  const chapterRows = useMemo(
    () => data.map((b) => chapterRow(b.chapter, b.latest, CHAPTER_TARGET)),
    [data],
  );
  const lanes = useMemo(() => povLanes(data.map((b) => b.chapter)), [data]);
  const tScenes = useMemo(() => timelineScenes(data), [data]);

  // book summary
  const bookTarget = chapterRows.reduce((a, c) => a + c.target, 0);
  const bookWords = chapterRows.reduce((a, c) => a + c.words, 0);
  const bookPct = bookTarget ? Math.round((bookWords / bookTarget) * 100) : 0;
  const totalScenes = liveScenes.length;
  const approvedScenes = liveScenes.filter((s) => s.scene.status === "approved").length;
  const awaitingScenes = liveScenes.filter((s) => s.scene.status === "awaiting").length;

  // pacing graph
  const progressChapters = chapterRows.map((c) => {
    const pct = c.target ? Math.round((c.words / c.target) * 100) : 0;
    return {
      no: c.no, title: c.title, pov: c.pov,
      words: c.words.toLocaleString(), target: c.target.toLocaleString(),
      barWidth: Math.min(100, pct) + "%",
      approvedWidth: Math.round(c.approved * 100) + "%",
      approvedLabel: Math.round(c.approved * 100) + "% approved",
      barColor: c.words === 0 ? "var(--dim)" : c.approved >= 1 ? "var(--good)" : "var(--accent)",
    };
  });

  // view toggle
  const cv = desk.chaptersView;
  const chViewItems: { id: ChaptersView; label: string }[] = [
    { id: "board", label: "Board" },
    { id: "timeline", label: "Timeline" },
  ];

  // chapter board (drag to reorder, local-only)
  const statusColors: Record<string, string> = { approved: t.good, awaiting: t.warn, drafting: t.info, revising: t.bad };
  const statusText: Record<string, string> = { approved: "approved", awaiting: "awaiting", drafting: "drafting…", revising: "revising" };
  const boardCards = order
    .map((id) => ({ id, s: sceneById.get(id) }))
    .filter((x): x is { id: string; s: BoardScene } => !!x.s)
    .map(({ id, s }) => {
      const c = statusColors[s.status] || t.dim;
      const dragging = localDrag === id;
      return {
        id, no: s.no, title: s.title, words: s.words ? s.words + "w" : "—",
        statusLabel: statusText[s.status] || s.status, statusColor: c,
        cardStyle: `flex:1 1 168px;min-width:160px;background:var(--bg2);border:1px solid ${dragging ? "var(--accent)" : "var(--line)"};border-left:3px solid ${c};border-radius:10px;padding:13px 14px;cursor:grab;box-shadow:var(--shadow);opacity:${dragging ? ".45" : "1"};transition:opacity .15s`,
      };
    });

  // timeline: POV swimlanes, scenes by global order, continuity flags marked
  const tCols = Math.max(tScenes.length, 1);
  const tlGridStyle = `display:grid;grid-template-columns:84px repeat(${tCols},minmax(56px,1fr));grid-template-rows:auto ${lanes.map(() => "78px").join(" ")};gap:0 8px;align-items:stretch`;
  const chBands: { title: string; style: string }[] = [];
  const seen: Record<number, boolean> = {};
  tScenes.forEach((s, i) => {
    if (!seen[s.ch]) {
      const span = tScenes.filter((x) => x.ch === s.ch).length;
      const chap = chapterRows.find((c) => c.no === s.ch);
      chBands.push({
        title: "Ch " + s.ch + " · " + (chap ? chap.title : ""),
        style: `grid-column:${2 + i} / span ${span};grid-row:1;display:flex;align-items:center;justify-content:center;padding:6px;margin-bottom:8px;border-radius:7px;background:var(--bg3);border:1px solid var(--line);font-family:var(--mono);font-size:10.5px;letter-spacing:.04em;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis`,
      });
      seen[s.ch] = true;
    }
  });
  const laneLabels = lanes.map((ln, li) => ({
    label: ln,
    style: `grid-column:1;grid-row:${2 + li};display:flex;align-items:center;font-family:var(--display);font-size:14px;color:var(--ink)`,
  }));
  const laneTracks = lanes.map((_, li) => ({
    style: `grid-column:2 / span ${tCols};grid-row:${2 + li};align-self:center;height:2px;background:var(--line);border-radius:2px`,
  }));
  const tStatusColor: Record<string, string> = { approved: t.good, awaiting: t.warn, drafting: t.info, planned: t.dim, revising: t.bad };
  const timelineNodes = tScenes.map((s, i) => {
    const li = lanes.indexOf(s.pov);
    const c = tStatusColor[s.status] || t.dim;
    const dim = s.status === "planned";
    return {
      n: s.n, flags: s.flags, hasFlags: s.flags > 0,
      style: `grid-column:${2 + i};grid-row:${2 + (li < 0 ? 0 : li)};align-self:center;justify-self:stretch;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;height:54px;border-radius:9px;border:1px solid var(--line);border-top:3px solid ${c};background:var(--bg2);box-shadow:var(--shadow);cursor:${dim ? "default" : "default"};opacity:${dim ? ".5" : "1"};position:relative`,
      dot: c,
    };
  });

  if (book.loading || bundles.loading) return <Frame>Loading chapters…</Frame>;
  if (book.error) return <Frame>Couldn't load books — {book.error}</Frame>;
  if (bundles.error) return <Frame>Couldn't load chapters — {bundles.error}</Frame>;
  if (!bookId) return <Frame>No book yet — create one to plan chapters.</Frame>;
  if (data.length === 0) return <Frame>This book has no chapters yet.</Frame>;

  return (
    <div>
      <div style={css("display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:24px")}>
        <div>
          <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)")}>Chapters & progress</h1>
          <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>Pacing against targets, and the order scenes will compile in.</p>
        </div>
        <div style={css("display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px")}>
          {chViewItems.map((v) => {
            const active = cv === v.id;
            return (
              <button key={v.id} onClick={() => desk.setChaptersView(v.id)} style={css(`padding:6px 14px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12.5px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`)}>{v.label}</button>
            );
          })}
        </div>
      </div>

      <div style={css("display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px;align-items:start;margin-bottom:30px")}>
        {/* book summary */}
        <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:20px")}>
          <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:12px")}>Manuscript</div>
          <div style={css("font-family:var(--display);font-size:44px;line-height:1;color:var(--ink)")}>{bookPct}%</div>
          <div style={css("font-family:var(--mono);font-size:12px;color:var(--dim);margin-top:6px")}>{bookWords.toLocaleString()} / {bookTarget.toLocaleString()} words</div>
          <div style={css("height:6px;border-radius:4px;background:var(--bg3);margin:14px 0 18px;overflow:hidden")}>
            <div style={css(`height:100%;width:${bookPct}%;background:var(--accent)`)} />
          </div>
          <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;color:var(--dim);line-height:2.1")}><span>scenes approved</span><span style={css("color:var(--ink)")}>{approvedScenes} / {totalScenes}</span></div>
          <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;color:var(--dim);line-height:2.1")}><span>chapters</span><span style={css("color:var(--ink)")}>{chapterRows.length} planned</span></div>
          <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;color:var(--dim);line-height:2.1")}><span>awaiting you</span><span style={css("color:var(--warn)")}>{awaitingScenes} scenes</span></div>
        </div>

        {/* pacing graph */}
        <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px")}>
          <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:18px")}>Pacing · words vs target</div>
          <div style={css("display:flex;flex-direction:column;gap:20px")}>
            {progressChapters.map((c) => (
              <div key={c.no}>
                <div style={css("display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px")}>
                  <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>Ch {c.no} · {c.title} <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>{c.pov}</span></span>
                  <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>{c.words} / {c.target} · {c.approvedLabel}</span>
                </div>
                <div style={css("position:relative;height:9px;border-radius:5px;background:var(--bg3);overflow:hidden")}>
                  <div style={css(`position:absolute;inset:0;width:${c.barWidth};background:color-mix(in srgb,var(--accent) 30%,transparent)`)} />
                  <div style={css(`position:absolute;inset:0;width:${c.approvedWidth};background:${c.barColor}`)} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* chapter board */}
      {cv === "board" && (
        <>
          <div style={css("display:flex;align-items:center;justify-content:space-between;margin-bottom:14px")}>
            <div style={css("display:flex;align-items:center;gap:10px")}>
              <h2 style={css("margin:0;font-family:var(--display);font-weight:500;font-size:19px;color:var(--ink)")}>Scene order</h2>
              <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>drag to reorder scenes</span>
            </div>
            <div style={css("display:flex;gap:14px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
              <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:2px;background:var(--good)")} />approved</span>
              <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:2px;background:var(--warn)")} />awaiting</span>
              <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:2px;background:var(--info)")} />drafting</span>
            </div>
          </div>
          <div style={css("display:flex;flex-wrap:wrap;gap:12px")}>
            {boardCards.map((c) => (
              <div
                key={c.id}
                draggable
                onDragStart={() => onDragStart(c.id)}
                onDragEnter={() => onDragEnter(c.id)}
                onDragOver={(e) => e.preventDefault()}
                onDragEnd={onDragEnd}
                style={css(c.cardStyle)}
              >
                <div style={css("display:flex;align-items:center;justify-content:space-between;margin-bottom:9px")}>
                  <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>Scene {c.no}</span>
                  <span style={css("color:var(--dim);font-size:13px;letter-spacing:.1em")}>⠿</span>
                </div>
                <div style={css("font-family:var(--display);font-size:15.5px;color:var(--ink);line-height:1.25;margin-bottom:12px")}>{c.title}</div>
                <div style={css("display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10.5px")}>
                  <span style={css(`color:${c.statusColor}`)}>● {c.statusLabel}</span>
                  <span style={css("color:var(--dim)")}>{c.words}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* timeline view */}
      {cv === "timeline" && (
        <>
          <div style={css("display:flex;align-items:center;justify-content:space-between;margin-bottom:16px")}>
            <h2 style={css("margin:0;font-family:var(--display);font-weight:500;font-size:19px;color:var(--ink)")}>Story timeline <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>· POV swimlanes</span></h2>
            <div style={css("display:flex;gap:14px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
              <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:50%;background:var(--bad)")} />continuity flag</span>
              <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:2px;background:var(--accent)")} />current</span>
            </div>
          </div>
          <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;overflow-x:auto")}>
            <div style={css(`${tlGridStyle};min-width:760px`)}>
              {chBands.map((cb, i) => <div key={`cb${i}`} style={css(cb.style)}>{cb.title}</div>)}
              {laneLabels.map((ll, i) => <div key={`ll${i}`} style={css(ll.style)}>{ll.label}</div>)}
              {laneTracks.map((lt, i) => <div key={`lt${i}`} style={css(lt.style)} />)}
              {timelineNodes.map((nd) => (
                <div key={nd.n} style={css(nd.style)}>
                  <span style={css("font-family:var(--mono);font-size:12px;color:var(--ink)")}>S{nd.n}</span>
                  {nd.hasFlags && <span style={css("position:absolute;top:-7px;right:-6px;min-width:16px;height:16px;border-radius:8px;background:var(--bad);color:#fff;font-family:var(--mono);font-size:9px;display:flex;align-items:center;justify-content:center;padding:0 3px")}>{nd.flags}</span>}
                  <span style={css(`width:5px;height:5px;border-radius:50%;background:${nd.dot}`)} />
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
