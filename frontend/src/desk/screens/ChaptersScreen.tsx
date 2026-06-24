import { useMemo } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { wordCount } from "../lib/format";
import type { ChaptersView } from "../types";
import type { SceneOut } from "../api/types";

const STATUS_COLORS: Record<string, "good" | "warn" | "bad" | "info" | "dim"> = {
  approved: "good",
  pending_review: "warn",
  revision_requested: "bad",
  draft: "info",
  superseded: "dim",
};

export default function ChaptersScreen() {
  const desk = useDesk();
  const { t } = desk;
  const data = useDeskData();

  // current state of each (chapter, scene) — derived once in the data layer
  const latest = data.latestScenes;
  const scenesByChapter = (chapterId: string): SceneOut[] =>
    latest.filter((s) => s.chapter_id === chapterId).sort((a, b) => a.scene_no - b.scene_no);

  const colorOf = (status: string): string => t[STATUS_COLORS[status] ?? "dim"];

  const manuscriptWords = (data.manuscript?.chapters ?? [])
    .flatMap((c) => c.scenes)
    .reduce((acc, s) => acc + wordCount(s.prose), 0);
  const approvedScenes = latest.filter((s) => s.status === "approved").length;

  const chViewItems: { id: ChaptersView; label: string }[] = [
    { id: "board", label: "Board" },
    { id: "timeline", label: "Timeline" },
  ];

  // Timeline geometry: lanes (distinct POVs), the flattened scene order, and the grid template. Runs
  // on every chapter/scene change only — not on each poll tick or unrelated context update.
  const { lanes, ordered, tCols, tlGridStyle } = useMemo(() => {
    const lanes: string[] = [];
    for (const c of data.chapters) if (!lanes.includes(c.pov)) lanes.push(c.pov);
    const ordered = [...data.chapters]
      .sort((a, b) => a.chapter_no - b.chapter_no)
      .flatMap((c) =>
        latest
          .filter((s) => s.chapter_id === c.id)
          .sort((a, b) => a.scene_no - b.scene_no)
          .map((s) => ({ scene: s, chapter: c })));
    const tCols = Math.max(1, ordered.length);
    const tlGridStyle = `display:grid;grid-template-columns:96px repeat(${tCols},minmax(56px,1fr));grid-template-rows:auto ${lanes.map(() => "70px").join(" ")};gap:0 8px;align-items:stretch`;
    return { lanes, ordered, tCols, tlGridStyle };
  }, [data.chapters, latest]);

  return (
    <div>
      <div style={css("display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:24px")}>
        <div>
          <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)")}>Chapters & progress</h1>
          <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>Where each scene stands, and the order they compile in.</p>
        </div>
        <div style={css("display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px")}>
          {chViewItems.map((v) => {
            const active = desk.chaptersView === v.id;
            return (
              <button key={v.id} onClick={() => desk.setChaptersView(v.id)} style={css(`padding:6px 14px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12.5px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`)}>{v.label}</button>
            );
          })}
        </div>
      </div>

      <div style={css("display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px;align-items:start;margin-bottom:30px")}>
        <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:20px")}>
          <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:12px")}>Manuscript</div>
          <div style={css("font-family:var(--display);font-size:40px;line-height:1;color:var(--ink)")}>{manuscriptWords.toLocaleString()}</div>
          <div style={css("font-family:var(--mono);font-size:12px;color:var(--dim);margin-top:6px")}>words approved</div>
          <div style={css("height:1px;background:var(--line);margin:16px 0")} />
          <Row k="scenes approved" v={`${approvedScenes} / ${latest.length}`} />
          <Row k="chapters" v={`${data.chapters.length}`} />
          <Row k="awaiting you" v={`${data.pending.length} scenes`} accent={data.pending.length > 0} t={t} />
        </div>

        <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px")}>
          <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:18px")}>Pacing · per chapter</div>
          <div style={css("display:flex;flex-direction:column;gap:18px")}>
            {data.chapters.length === 0 && <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>No chapters yet — plan one from the Inbox.</span>}
            {[...data.chapters].sort((a, b) => a.chapter_no - b.chapter_no).map((c) => {
              const scs = scenesByChapter(c.id);
              const words = scs.reduce((acc, s) => acc + wordCount(s.prose), 0);
              const appr = scs.filter((s) => s.status === "approved").length;
              const frac = scs.length ? Math.round((appr / scs.length) * 100) : 0;
              return (
                <div key={c.id}>
                  <div style={css("display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:8px")}>
                    <span style={css("font-family:var(--display);font-size:15px;color:var(--ink);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")}>
                      Ch {c.chapter_no}{c.title ? <span style={css("color:var(--ink)")}> · {c.title}</span> : null} <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>{c.pov}</span>
                    </span>
                    <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim);flex:none")}>{words.toLocaleString()} words · {appr}/{scs.length} approved</span>
                  </div>
                  <div style={css("position:relative;height:9px;border-radius:5px;background:var(--bg3);overflow:hidden")}>
                    <div style={css(`position:absolute;inset:0;width:${frac}%;background:var(--good)`)} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {desk.chaptersView === "board" && (
        <>
          <div style={css("display:flex;align-items:center;gap:14px;margin-bottom:14px;font-family:var(--mono);font-size:10.5px;color:var(--dim);flex-wrap:wrap")}>
            <h2 style={css("margin:0;font-family:var(--display);font-weight:500;font-size:19px;color:var(--ink)")}>Scene board</h2>
            <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:2px;background:var(--good)")} />approved</span>
            <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:2px;background:var(--warn)")} />awaiting</span>
            <span style={css("display:flex;align-items:center;gap:5px")}><span style={css("width:9px;height:9px;border-radius:2px;background:var(--bad)")} />revising</span>
          </div>
          <div style={css("display:flex;flex-wrap:wrap;gap:12px")}>
            {latest.length === 0 && <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>No scenes drafted yet.</span>}
            {ordered.map(({ scene: s, chapter: c }) => {
              const color = colorOf(s.status);
              return (
                <div key={s.id} onClick={() => desk.openSceneId(s.id)}
                  style={css(`flex:1 1 168px;min-width:160px;background:var(--bg2);border:1px solid var(--line);border-left:3px solid ${color};border-radius:10px;padding:13px 14px;box-shadow:var(--shadow);cursor:pointer`)}>
                  <div style={css("display:flex;align-items:center;justify-content:space-between;margin-bottom:9px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                    <span>Ch {c.chapter_no} · Scene {s.scene_no}</span><span>v{s.version}</span>
                  </div>
                  <div style={css("display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10.5px")}>
                    <span style={css(`color:${color}`)}>● {s.status.replace(/_/g, " ")}</span>
                    <span style={css("color:var(--dim)")}>{wordCount(s.prose)} words</span>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {desk.chaptersView === "timeline" && (
        <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;overflow-x:auto")}>
          {ordered.length === 0 ? (
            <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>No scenes to chart yet.</span>
          ) : (
            <div style={css(`${tlGridStyle};min-width:680px`)}>
              {lanes.map((ln, li) => (
                <div key={`ll${li}`} style={css(`grid-column:1;grid-row:${2 + li};display:flex;align-items:center;font-family:var(--display);font-size:14px;color:var(--ink)`)}>{ln}</div>
              ))}
              {lanes.map((_, li) => (
                <div key={`lt${li}`} style={css(`grid-column:2 / span ${tCols};grid-row:${2 + li};align-self:center;height:2px;background:var(--line)`)} />
              ))}
              {ordered.map(({ scene: s, chapter: c }, i) => {
                const li = Math.max(0, lanes.indexOf(c.pov));
                const color = colorOf(s.status);
                return (
                  <div key={s.id} onClick={() => desk.openSceneId(s.id)}
                    style={css(`grid-column:${2 + i};grid-row:${2 + li};align-self:center;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;height:50px;border-radius:9px;border:1px solid var(--line);border-top:3px solid ${color};background:var(--bg2);box-shadow:var(--shadow);cursor:pointer`)}>
                    <span style={css("font-family:var(--mono);font-size:12px;color:var(--ink)")}>C{c.chapter_no}·S{s.scene_no}</span>
                    <span style={css(`width:5px;height:5px;border-radius:50%;background:${color}`)} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ k, v, accent, t }: { k: string; v: string; accent?: boolean; t?: { warn: string } }) {
  return (
    <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;color:var(--dim);line-height:2.1")}>
      <span>{k}</span>
      <span style={css(accent && t ? `color:${t.warn}` : "color:var(--ink)")}>{v}</span>
    </div>
  );
}
