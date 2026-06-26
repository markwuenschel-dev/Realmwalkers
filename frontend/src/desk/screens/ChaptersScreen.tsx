"use client";

import { useMemo, useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { wordCount } from "../lib/format";
import { useSelection } from "../lib/useSelection";
import BulkBar, { BulkButton } from "../components/BulkBar";
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

  // current state of each (chapter, scene) — derived once in the data layer. A latest row can only be
  // `superseded` when the scene was rejected (revisions create a newer version, so a superseded row is
  // never the latest), so dropping them here is what makes a rejected scene vanish from the board.
  const latest = useMemo(
    () => data.latestScenes.filter((s) => s.status !== "superseded"),
    [data.latestScenes],
  );
  const scenesByChapter = (chapterId: string): SceneOut[] =>
    latest.filter((s) => s.chapter_id === chapterId).sort((a, b) => a.scene_no - b.scene_no);

  const colorOf = (status: string): string => t[STATUS_COLORS[status] ?? "dim"];

  // Bulk re-draft: tick scenes on the board, re-queue a fresh draft for each (supersedes the version).
  const sel = useSelection();
  const redraftSelected = async () => {
    const byChapter = new Map<string, string[]>();
    for (const id of sel.ids) {
      const sc = latest.find((s) => s.id === id);
      if (sc) byChapter.set(sc.chapter_id, [...(byChapter.get(sc.chapter_id) ?? []), id]);
    }
    sel.clear();
    await Promise.allSettled([...byChapter].map(([cid, ids]) => api.redraftScenes(cid, ids)));
    await data.draftNext();
  };

  // Write a section by hand → an approved human-authored scene (flows into summaries + prior context).
  const [writeFor, setWriteFor] = useState<string | null>(null); // chapter id whose form is open
  const [sceneNo, setSceneNo] = useState("");
  const [prose, setProse] = useState("");
  const [busy, setBusy] = useState(false);
  const saveSection = async (chapterId: string) => {
    const n = Number(sceneNo);
    if (!Number.isFinite(n) || n < 1 || !prose.trim()) return;
    setBusy(true);
    try {
      await api.createHumanScene(chapterId, { scene_no: n, prose: prose.trim() });
      await data.refreshAll();
      setWriteFor(null);
      setSceneNo("");
      setProse("");
    } finally {
      setBusy(false);
    }
  };

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
          .map((s) => ({ scene: s, chapter: c })),
      );
    const tCols = Math.max(1, ordered.length);
    const tlGridStyle = `display:grid;grid-template-columns:96px repeat(${tCols},minmax(56px,1fr));grid-template-rows:auto ${lanes.map(() => "70px").join(" ")};gap:0 8px;align-items:stretch`;
    return { lanes, ordered, tCols, tlGridStyle };
  }, [data.chapters, latest]);

  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:24px",
        )}
      >
        <div>
          <h1
            style={css(
              "margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)",
            )}
          >
            Chapters & progress
          </h1>
          <p style={css("margin:0;color:var(--dim);font-size:14.5px")}>
            Where each scene stands, and the order they compile in.
          </p>
        </div>
        <div
          style={css(
            "display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px",
          )}
        >
          {chViewItems.map((v) => {
            const active = desk.chaptersView === v.id;
            return (
              <button
                key={v.id}
                onClick={() => desk.setChaptersView(v.id)}
                style={css(
                  `padding:6px 14px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12.5px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`,
                )}
              >
                {v.label}
              </button>
            );
          })}
        </div>
      </div>

      <div
        style={css(
          "display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px;align-items:start;margin-bottom:30px",
        )}
      >
        <div
          style={css(
            "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:20px",
          )}
        >
          <div
            style={css(
              "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:12px",
            )}
          >
            Manuscript
          </div>
          <div
            style={css("font-family:var(--display);font-size:40px;line-height:1;color:var(--ink)")}
          >
            {manuscriptWords.toLocaleString()}
          </div>
          <div
            style={css("font-family:var(--mono);font-size:12px;color:var(--dim);margin-top:6px")}
          >
            words approved
          </div>
          <div style={css("height:1px;background:var(--line);margin:16px 0")} />
          <Row k="scenes approved" v={`${approvedScenes} / ${latest.length}`} />
          <Row k="chapters" v={`${data.chapters.length}`} />
          <Row
            k="awaiting you"
            v={`${data.pending.length} scenes`}
            accent={data.pending.length > 0}
            t={t}
          />
        </div>

        <div
          style={css(
            "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px",
          )}
        >
          <div
            style={css(
              "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:18px",
            )}
          >
            Pacing · per chapter
          </div>
          <div style={css("display:flex;flex-direction:column;gap:18px")}>
            {data.chapters.length === 0 && (
              <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>
                No chapters yet — plan one from the Inbox.
              </span>
            )}
            {[...data.chapters]
              .sort((a, b) => a.chapter_no - b.chapter_no)
              .map((c) => {
                const scs = scenesByChapter(c.id);
                const words = scs.reduce((acc, s) => acc + wordCount(s.prose), 0);
                const appr = scs.filter((s) => s.status === "approved").length;
                const frac = scs.length ? Math.round((appr / scs.length) * 100) : 0;
                return (
                  <div key={c.id}>
                    <div
                      style={css(
                        "display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:8px",
                      )}
                    >
                      <span
                        style={css(
                          "font-family:var(--display);font-size:15px;color:var(--ink);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap",
                        )}
                      >
                        Ch {c.chapter_no}
                        {c.title ? (
                          <span style={css("color:var(--ink)")}> · {c.title}</span>
                        ) : null}{" "}
                        <span
                          style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
                        >
                          {c.pov}
                        </span>
                      </span>
                      <span
                        style={css(
                          "font-family:var(--mono);font-size:11.5px;color:var(--dim);flex:none",
                        )}
                      >
                        {words.toLocaleString()} words · {appr}/{scs.length} approved
                      </span>
                    </div>
                    <div
                      style={css(
                        "position:relative;height:9px;border-radius:5px;background:var(--bg3);overflow:hidden",
                      )}
                    >
                      <div
                        style={css(
                          `position:absolute;inset:0;width:${frac}%;background:var(--good)`,
                        )}
                      />
                    </div>
                    <div style={css("margin-top:8px")}>
                      {writeFor === c.id ? (
                        <div
                          style={css(
                            "display:flex;flex-direction:column;gap:7px;border:1px solid var(--accentLine);border-radius:9px;padding:11px 12px;background:var(--bg2b)",
                          )}
                        >
                          <div
                            style={css("display:flex;gap:8px;align-items:center;flex-wrap:wrap")}
                          >
                            <input
                              type="number"
                              min={1}
                              value={sceneNo}
                              onChange={(e) => setSceneNo(e.target.value)}
                              placeholder="scene #"
                              style={css(
                                "width:90px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12.5px;font-family:var(--ui)",
                              )}
                            />
                            <span
                              style={css(
                                "font-family:var(--mono);font-size:10.5px;color:var(--dim)",
                              )}
                            >
                              approved on save; supersedes any existing version at this scene number
                            </span>
                          </div>
                          <textarea
                            value={prose}
                            onChange={(e) => setProse(e.target.value)}
                            placeholder="write the prose for this section…"
                            style={css(
                              "width:100%;min-height:120px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:9px 11px;font-size:13px;line-height:1.55;resize:vertical;font-family:var(--ui)",
                            )}
                          />
                          <div style={css("display:flex;gap:8px")}>
                            <button
                              disabled={busy || !sceneNo.trim() || !prose.trim()}
                              onClick={() => saveSection(c.id)}
                              style={css(
                                `padding:6px 12px;border-radius:7px;border:1px solid color-mix(in srgb,var(--good) 45%,var(--line));background:color-mix(in srgb,var(--good) 12%,var(--bg3));color:var(--good);font-family:var(--ui);font-size:12.5px;cursor:${busy ? "default" : "pointer"}`,
                              )}
                            >
                              {busy ? "Saving…" : "Save section"}
                            </button>
                            <button
                              onClick={() => {
                                setWriteFor(null);
                                setSceneNo("");
                                setProse("");
                              }}
                              style={css(
                                "padding:6px 12px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-family:var(--ui);font-size:12.5px;cursor:pointer",
                              )}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setWriteFor(c.id);
                            setSceneNo(String(scs.length + 1));
                            setProse("");
                          }}
                          style={css(
                            "font-family:var(--mono);font-size:11px;color:var(--dim);background:none;border:none;cursor:pointer;padding:2px 0",
                          )}
                        >
                          + Write section by hand
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
          </div>
        </div>
      </div>

      {desk.chaptersView === "board" && (
        <>
          <div
            style={css(
              "display:flex;align-items:center;gap:14px;margin-bottom:14px;font-family:var(--mono);font-size:10.5px;color:var(--dim);flex-wrap:wrap",
            )}
          >
            <h2
              style={css(
                "margin:0;font-family:var(--display);font-weight:500;font-size:19px;color:var(--ink)",
              )}
            >
              Scene board
            </h2>
            <span style={css("display:flex;align-items:center;gap:5px")}>
              <span style={css("width:9px;height:9px;border-radius:2px;background:var(--good)")} />
              approved
            </span>
            <span style={css("display:flex;align-items:center;gap:5px")}>
              <span style={css("width:9px;height:9px;border-radius:2px;background:var(--warn)")} />
              awaiting
            </span>
            <span style={css("display:flex;align-items:center;gap:5px")}>
              <span style={css("width:9px;height:9px;border-radius:2px;background:var(--bad)")} />
              revising
            </span>
          </div>
          <div style={css("display:flex;flex-wrap:wrap;gap:12px")}>
            {latest.length === 0 && (
              <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>
                No scenes drafted yet.
              </span>
            )}
            {ordered.map(({ scene: s, chapter: c }) => {
              const color = colorOf(s.status);
              return (
                <div
                  key={s.id}
                  onClick={() => desk.openSceneId(s.id)}
                  style={css(
                    `flex:1 1 168px;min-width:160px;background:var(--bg2);border:1px solid var(--line);border-left:3px solid ${color};border-radius:10px;padding:13px 14px;box-shadow:var(--shadow);cursor:pointer`,
                  )}
                >
                  <div
                    style={css(
                      "display:flex;align-items:center;gap:6px;margin-bottom:9px;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={sel.has(s.id)}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => sel.toggle(s.id)}
                      title="select to re-draft"
                      style={css(
                        "width:13px;height:13px;cursor:pointer;accent-color:var(--accent)",
                      )}
                    />
                    <span>
                      Ch {c.chapter_no} · Scene {s.scene_no}
                    </span>
                    <span style={css("margin-left:auto")}>v{s.version}</span>
                  </div>
                  <div
                    style={css(
                      "display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10.5px",
                    )}
                  >
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
        <div
          style={css(
            "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;overflow-x:auto",
          )}
        >
          {ordered.length === 0 ? (
            <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>
              No scenes to chart yet.
            </span>
          ) : (
            <div style={css(`${tlGridStyle};min-width:680px`)}>
              {lanes.map((ln, li) => (
                <div
                  key={`ll${li}`}
                  style={css(
                    `grid-column:1;grid-row:${2 + li};display:flex;align-items:center;font-family:var(--display);font-size:14px;color:var(--ink)`,
                  )}
                >
                  {ln}
                </div>
              ))}
              {lanes.map((_, li) => (
                <div
                  key={`lt${li}`}
                  style={css(
                    `grid-column:2 / span ${tCols};grid-row:${2 + li};align-self:center;height:2px;background:var(--line)`,
                  )}
                />
              ))}
              {ordered.map(({ scene: s, chapter: c }, i) => {
                const li = Math.max(0, lanes.indexOf(c.pov));
                const color = colorOf(s.status);
                return (
                  <div
                    key={s.id}
                    onClick={() => desk.openSceneId(s.id)}
                    style={css(
                      `grid-column:${2 + i};grid-row:${2 + li};align-self:center;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;height:50px;border-radius:9px;border:1px solid var(--line);border-top:3px solid ${color};background:var(--bg2);box-shadow:var(--shadow);cursor:pointer`,
                    )}
                  >
                    <span style={css("font-family:var(--mono);font-size:12px;color:var(--ink)")}>
                      C{c.chapter_no}·S{s.scene_no}
                    </span>
                    <span
                      style={css(`width:5px;height:5px;border-radius:50%;background:${color}`)}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <BulkBar count={sel.count} noun="scene" onClear={sel.clear}>
        <BulkButton onClick={() => void redraftSelected()}>Re-draft selected</BulkButton>
      </BulkBar>
    </div>
  );
}

function Row({
  k,
  v,
  accent,
  t,
}: {
  k: string;
  v: string;
  accent?: boolean;
  t?: { warn: string };
}) {
  return (
    <div
      style={css(
        "display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;color:var(--dim);line-height:2.1",
      )}
    >
      <span>{k}</span>
      <span style={css(accent && t ? `color:${t.warn}` : "color:var(--ink)")}>{v}</span>
    </div>
  );
}
