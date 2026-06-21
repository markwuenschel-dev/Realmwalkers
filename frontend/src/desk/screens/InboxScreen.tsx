import { css } from "../css";
import { useDesk } from "../state";
import { api } from "../api/client";
import { useFetch, useSelectedBook } from "../api/hooks";
import type { SceneOut } from "../api/client";
import { latestPerScene } from "../api/adapters.chapters";
import { awaitingQueue, inboxColumns, inboxStats } from "../api/adapters.inbox";

const COLUMN_COLOR: Record<string, "info" | "warn" | "bad" | "good"> = {
  drafting: "info",
  awaiting: "warn",
  revising: "bad",
  approved: "good",
};

const CARD_STYLE: Record<string, string> = {
  drafting: "background:var(--bg2);border:1px dashed var(--line);border-radius:10px;padding:13px 14px",
  awaiting: "background:var(--bg2);border:1px solid var(--accentLine);border-radius:10px;padding:13px 14px;cursor:pointer;box-shadow:var(--shadow)",
  revising: "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px;cursor:pointer",
  approved: "background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 14px;cursor:pointer;opacity:.78",
};

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div style={css("padding:40px;text-align:center;font-family:var(--mono);font-size:13px;color:var(--dim)")}>{children}</div>
    </div>
  );
}

export default function InboxScreen() {
  const { t, openScene } = useDesk();
  const book = useSelectedBook();
  const bookId = book.bookId;

  // Gather the whole book's scenes: chapters → each chapter's scenes, unioned, latest per scene_no.
  const scenesState = useFetch<SceneOut[]>(async () => {
    if (!bookId) return [];
    const chapters = await api.chapters(bookId);
    const lists = await Promise.all(chapters.map((c) => api.chapterScenes(c.id)));
    return latestPerScene(lists.flat());
  }, [bookId]);

  const scenes = scenesState.data ?? [];
  const columns = inboxColumns(scenes);
  const stats = inboxStats(scenes);

  // A card click opens the scene at its index within the awaiting/pending queue, then navigates to
  // the scene screen (openScene sets the screen for us).
  const queue = awaitingQueue(scenes);
  const queueIndex = (id: string) => queue.findIndex((s) => s.id === id);

  const tcolor = (key: string) => {
    const c = COLUMN_COLOR[key];
    return c === "info" ? t.info : c === "warn" ? t.warn : c === "bad" ? t.bad : t.good;
  };

  if (book.loading || scenesState.loading) return <Frame>Loading the desk…</Frame>;
  if (book.error) return <Frame>Couldn't load books — {book.error}</Frame>;
  if (scenesState.error) return <Frame>Couldn't load scenes — {scenesState.error}</Frame>;
  if (!bookId) return <Frame>No book yet — create one to start drafting.</Frame>;
  if (scenes.length === 0) return <Frame>No scenes drafted yet.</Frame>;

  return (
    <div>
      <div style={css("margin-bottom:24px")}>
        <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)")}>Drafting desk</h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>Scenes the Oracle has drafted and is waiting on you to judge.</p>
      </div>

      <div style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px")}>
        {stats.map((s) => (
          <div key={s.label} style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px")}>
            <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:10px")}>{s.label}</div>
            <div style={css("font-family:var(--display);font-size:27px;color:var(--ink);line-height:1")}>
              {s.value}<span style={css("font-size:14px;color:var(--dim)")}>{" "}{s.suffix}</span>
            </div>
            {s.hasBar && (
              <div style={css("height:5px;border-radius:3px;background:var(--bg3);margin-top:12px;overflow:hidden")}>
                <div style={css(`height:100%;width:${s.pct};background:var(--accent)`)} />
              </div>
            )}
            {s.note && <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:9px")}>{s.note}</div>}
          </div>
        ))}
      </div>

      <div style={css("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:start")}>
        {columns.map((col) => (
          <div key={col.key}>
            <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:11px;padding:0 2px")}>
              <span style={css(`width:8px;height:8px;border-radius:50%;background:${tcolor(col.key)}`)} />
              <span style={css("font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink)")}>{col.title}</span>
              <span style={css("margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)")}>{col.cards.length}</span>
            </div>
            <div style={css("display:flex;flex-direction:column;gap:10px")}>
              {col.cards.map((c) => {
                const idx = queueIndex(c.id);
                const clickable = col.key !== "drafting";
                return (
                  <div
                    key={c.id}
                    onClick={clickable ? () => openScene(idx < 0 ? 0 : idx) : undefined}
                    style={css(CARD_STYLE[col.key])}
                  >
                    <div style={css("display:flex;align-items:baseline;justify-content:space-between;margin-bottom:7px")}>
                      <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>Scene {c.no}</span>
                      <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>v{c.version}</span>
                    </div>
                    <div style={css("font-size:13px;color:var(--dim);line-height:1.4;margin-bottom:10px")}>{c.title}</div>
                    <div style={css("display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                      <span>{c.words}</span>
                      <span style={css(`color:${tcolor(col.key)}`)}>{col.key === "drafting" ? "writing…" : col.key}</span>
                    </div>
                  </div>
                );
              })}
              {col.cards.length === 0 && (
                <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);padding:6px 2px")}>—</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
