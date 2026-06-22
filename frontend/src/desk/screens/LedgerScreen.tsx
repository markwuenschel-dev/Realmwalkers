import type { ReactNode } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { statValue } from "../lib/format";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function LedgerScreen() {
  const { t, ledgerCat, selectedThread, setLedgerCat, selectThread } = useDesk();
  const data = useDeskData();

  const canonKinds = [...new Set(data.canon.map((c) => c.kind ?? "other"))].filter((k) => k !== "character");
  const cats = [
    { id: "characters", label: "Characters", count: data.characters.length },
    { id: "threads", label: "Threads", count: data.threads.length },
    ...canonKinds.map((k) => ({ id: `canon:${k}`, label: cap(k), count: data.canon.filter((c) => (c.kind ?? "other") === k).length })),
  ];

  const threadKinds: Record<string, string> = {
    relationship: t.bad, mentorship: t.info, system: t.accent, power: t.warn,
  };

  const newThread = async () => {
    const name = window.prompt("Thread name (e.g. Soren ⇄ Lyra):");
    if (!name?.trim()) return;
    const kind = window.prompt("Kind (relationship / mentorship / system / power):", "relationship") ?? undefined;
    await data.createThread({ name: name.trim(), kind: kind?.trim() || null, state: "active" });
  };
  const addBeat = async (threadId: string) => {
    const raw = window.prompt("Add a beat as `scene_no, label` (e.g. 5, threadbound):");
    if (!raw) return;
    const [no, ...rest] = raw.split(",");
    const sceneNo = Number(no.trim());
    if (!Number.isFinite(sceneNo)) return;
    await data.addThreadBeat(threadId, { scene_no: sceneNo, label: rest.join(",").trim() || null });
  };

  const isChars = ledgerCat === "characters";
  const isThreads = ledgerCat === "threads";
  const canonKind = ledgerCat.startsWith("canon:") ? ledgerCat.slice("canon:".length) : null;

  return (
    <div>
      <div style={css("margin-bottom:22px")}>
        <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:28px;color:var(--ink)")}>World ledger</h1>
        <p style={css("margin:0;color:var(--dim);font-size:14px")}>The Oracle's canon — the hard numbers and lore the continuity passes check prose against.</p>
      </div>
      <div style={css("display:grid;grid-template-columns:184px 1fr;gap:22px;align-items:start")}>
        <div style={css("display:flex;flex-direction:column;gap:3px;position:sticky;top:84px")}>
          {cats.map((cat) => {
            const active = ledgerCat === cat.id;
            return (
              <button key={cat.id} onClick={() => setLedgerCat(cat.id)}
                style={css(`display:flex;align-items:center;width:100%;padding:9px 12px;border:1px solid ${active ? "var(--accentLine)" : "transparent"};border-radius:8px;background:${active ? "var(--accentSoft)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:13.5px;cursor:pointer`)}>
                {cat.label}
                <span style={css("margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)")}>{cat.count}</span>
              </button>
            );
          })}
        </div>

        <div style={css("min-width:0")}>
          {isChars && (
            data.characters.length === 0 ? (
              <Empty>No character state yet — it accrues as you approve scenes whose beats declare stat changes.</Empty>
            ) : (
              <div style={css("display:grid;grid-template-columns:1fr 1fr;gap:14px")}>
                {data.characters.map((ch) => (
                  <div key={ch.character} style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);overflow:hidden")}>
                    <div style={css("display:flex;align-items:center;gap:12px;padding:15px 16px;border-bottom:1px solid var(--line);background:var(--bg2b)")}>
                      <div style={css("width:38px;height:38px;border-radius:9px;background:var(--accentSoft);border:1px solid var(--accentLine);display:flex;align-items:center;justify-content:center;font-family:var(--display);font-size:17px;color:var(--accent);flex:none")}>{ch.character.charAt(0)}</div>
                      <div style={css("min-width:0")}>
                        <div style={css("font-family:var(--display);font-size:16px;color:var(--ink)")}>{ch.character}</div>
                        <div style={css("font-family:var(--mono);font-size:10.5px;text-transform:uppercase;color:var(--dim);margin-top:2px")}>{ch.is_pov ? "POV" : "character"}{ch.provisional ? " · provisional" : ""}</div>
                      </div>
                    </div>
                    <div style={css("padding:13px 16px")}>
                      {Object.keys(ch.stats).length === 0 && <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>no tracked stats</div>}
                      {Object.entries(ch.stats).map(([k, v]) => (
                        <div key={k} style={css("display:flex;justify-content:space-between;gap:12px;padding:5px 0;font-size:13px;border-bottom:1px solid var(--hairline)")}>
                          <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>{k}</span>
                          <span style={css("color:var(--ink);text-align:right")}>{statValue(v)}</span>
                        </div>
                      ))}
                      {ch.body && <p style={css("margin:10px 0 0;font-size:12.5px;color:var(--dim);line-height:1.5")}>{ch.body}</p>}
                    </div>
                  </div>
                ))}
              </div>
            )
          )}

          {isThreads && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <div style={css("display:flex;align-items:center;justify-content:space-between")}>
                <p style={css("margin:0;font-size:13px;color:var(--dim);line-height:1.5")}>Follow a relationship or plot thread across the scenes it touches.</p>
                <button onClick={newThread} style={css("padding:7px 12px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:12.5px;cursor:pointer;font-family:var(--ui);white-space:nowrap")}>+ New thread</button>
              </div>
              {data.threads.length === 0 && <Empty>No threads yet — add one to track an arc across scenes.</Empty>}
              {data.threads.map((th) => {
                const sel = selectedThread === th.id;
                const kindColor = threadKinds[th.kind ?? ""] ?? t.dim;
                return (
                  <div key={th.id} onClick={() => selectThread(th.id)} style={css(`background:var(--bg2);border:1px solid ${sel ? "var(--accentLine)" : "var(--line)"};border-radius:var(--r);padding:16px 18px;cursor:pointer;box-shadow:${sel ? "var(--shadow)" : "none"}`)}>
                    <div style={css("display:flex;align-items:center;gap:11px;margin-bottom:8px;flex-wrap:wrap")}>
                      <span style={css("font-family:var(--display);font-size:18px;color:var(--ink)")}>{th.name}</span>
                      {th.kind && <span style={css(`font-family:var(--mono);font-size:9.5px;text-transform:uppercase;color:${kindColor};background:color-mix(in srgb,${kindColor} 13%,transparent);border-radius:999px;padding:3px 9px`)}>{th.kind}</span>}
                      <span style={css("margin-left:auto;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>{th.state ? `state · ${th.state}` : ""}</span>
                    </div>
                    {th.note && <p style={css("margin:0 0 14px;font-size:13.5px;color:var(--dim);line-height:1.55")}>{th.note}</p>}
                    <div style={css("display:flex;align-items:center;flex-wrap:wrap;row-gap:10px")}>
                      {th.beats.map((b, i) => (
                        <div key={b.id} style={css("display:flex;align-items:center")}>
                          <div style={css(`display:flex;flex-direction:column;gap:2px;padding:7px 11px;border-radius:8px;border:1px solid ${b.flag ? "color-mix(in srgb,var(--bad) 40%,var(--line))" : "var(--line)"};background:${b.flag ? "color-mix(in srgb,var(--bad) 9%,var(--bg3))" : "var(--bg3)"};white-space:nowrap`)}>
                            <span style={css("font-family:var(--mono);font-size:9px;color:var(--dim)")}>SCENE {b.scene_no}</span>
                            <span style={css("font-size:12.5px;color:var(--ink)")}>{b.label ?? "—"}</span>
                          </div>
                          {i !== th.beats.length - 1 && <span style={css("margin:0 9px;color:var(--dim);font-size:13px")}>→</span>}
                        </div>
                      ))}
                      <button onClick={(e) => { e.stopPropagation(); addBeat(th.id); }} style={css("margin-left:10px;padding:6px 10px;border-radius:7px;border:1px dashed var(--line);background:transparent;color:var(--dim);font-size:11.5px;cursor:pointer;font-family:var(--ui)")}>+ beat</button>
                      <button onClick={(e) => { e.stopPropagation(); if (confirm(`Delete thread "${th.name}"?`)) data.deleteThread(th.id); }} style={css("margin-left:auto;padding:6px 10px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-size:11.5px;cursor:pointer;font-family:var(--ui)")}>delete</button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {canonKind && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              {data.canon.filter((c) => (c.kind ?? "other") === canonKind).length === 0 && <Empty>Nothing in this section yet.</Empty>}
              {data.canon.filter((c) => (c.kind ?? "other") === canonKind).map((e) => (
                <div key={e.id} style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:15px 18px")}>
                  <div style={css("font-family:var(--display);font-size:16px;color:var(--ink);margin-bottom:5px")}>{e.name ?? "—"}</div>
                  {e.body && <p style={css("margin:0;font-size:13px;color:var(--dim);line-height:1.55")}>{e.body}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div style={css("background:var(--bg2);border:1px dashed var(--line);border-radius:var(--r);padding:40px;text-align:center;font-family:var(--mono);font-size:12.5px;color:var(--dim)")}>{children}</div>
  );
}
