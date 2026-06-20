import type { ReactNode } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import CanonCard from "../components/CanonCard";
import type { CardModel } from "../components/CanonCard";
import { box, seg, tokenize } from "../prose";
import type { Token } from "../prose";
import {
  ANNOTATION,
  CONFLICT_IDS,
  CONFLICTS,
  ENTITIES,
  MARKERS,
  QUEUE,
  SUGGESTIONS,
} from "../data";

const KEEP_BTN =
  "flex:1;padding:8px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:11.5px;cursor:pointer";

export default function SceneScreen() {
  const desk = useDesk();
  const { t } = desk;

  // active scene in the review queue (j / k)
  const cur = QUEUE[desk.activeScene] || QUEUE[0];
  const statusMap: Record<string, { label: string; color: string }> = {
    awaiting: { label: "Awaiting review", color: t.warn },
    note: { label: "1 reviewer note", color: t.info },
    approved: { label: "Approved", color: t.good },
    revising: { label: "Revising", color: t.bad },
  };
  const cst = statusMap[cur.status] || statusMap.awaiting;
  const sceneStatusStyle = `display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:${cst.color};background:color-mix(in srgb,${cst.color} 14%,transparent);border:1px solid color-mix(in srgb,${cst.color} 40%,transparent);border-radius:999px;padding:4px 11px`;
  const queuePos = `${desk.activeScene + 1} / ${QUEUE.length}`;

  const editing = desk.mode === "editing";
  const suggesting = desk.mode === "suggesting";
  const showMarks = !editing;

  // canon hover-card model for an entity name or a flagged conflict span
  const makeCard = (kind: "entity" | "conflict", id: string): CardModel => {
    if (kind === "entity") {
      const e = ENTITIES[id];
      const cf = e.conflict ? CONFLICTS[e.conflict] : null;
      const resolved = cf ? !!desk.resolved[e.conflict as string] : false;
      return {
        title: e.name,
        subtitle: e.role,
        rows: e.rows.map(([k, v]) => ({ k, v })),
        hasFlag: !!cf,
        open: cf ? !resolved : false,
        resolved,
        flagProse: cf ? cf.proseValue : "",
        flagLedger: cf ? cf.ledgerValue : "",
        resolvedLabel: resolved
          ? desk.resolved[e.conflict as string] === "prose" ? "prose kept" : "ledger kept"
          : "",
        keepProse: cf ? () => desk.resolve(e.conflict as string, "prose") : () => {},
        keepLedger: cf ? () => desk.resolve(e.conflict as string, "ledger") : () => {},
      };
    }
    const cf = CONFLICTS[id];
    const resolved = !!desk.resolved[id];
    return {
      title: cf.attribute,
      subtitle: "continuity conflict",
      rows: [],
      hasFlag: true,
      open: !resolved,
      resolved,
      flagProse: cf.proseValue,
      flagLedger: cf.ledgerValue,
      resolvedLabel: resolved ? (desk.resolved[id] === "prose" ? "prose kept" : "ledger kept") : "",
      keepProse: () => desk.resolve(id, "prose"),
      keepLedger: () => desk.resolve(id, "ledger"),
    };
  };

  const renderToken = (tok: Token, key: string): ReactNode => {
    if (tok.kind === "text") return <span key={key} style={css("color:inherit")}>{tok.text}</span>;

    if (tok.kind === "anno") {
      const style = showMarks
        ? "background:var(--accentSoft);border-bottom:1px dashed var(--accent);border-radius:2px;cursor:pointer;color:inherit"
        : "color:inherit;cursor:pointer";
      return (
        <span key={key} onClick={() => desk.selectAnn(tok.id)} style={css(style)}>{tok.text}</span>
      );
    }

    if (tok.kind === "entity" || tok.kind === "conflict") {
      const hovered = desk.hoveredKey === key;
      let span: string;
      if (tok.kind === "entity") {
        span = showMarks ? "border-bottom:1px dotted var(--accent);cursor:help;color:inherit" : "color:inherit";
      } else {
        const resolved = !!desk.resolved[tok.id];
        span = !showMarks
          ? "color:inherit"
          : resolved
            ? "border-bottom:1px solid var(--good);cursor:help;color:inherit"
            : "border-bottom:1px dotted var(--bad);background:color-mix(in srgb,var(--bad) 9%,transparent);border-radius:2px;cursor:help;color:inherit";
      }
      return (
        <span
          key={key}
          style={css("position:relative;display:inline")}
          onMouseEnter={() => desk.setHover(key)}
          onMouseLeave={desk.clearHover}
        >
          <span onClick={() => desk.setHover(hovered ? null : key)} style={css(span)}>{tok.text}</span>
          {hovered && <CanonCard card={makeCard(tok.kind, tok.id)} />}
        </span>
      );
    }

    // suggestion (track-changes)
    const s = SUGGESTIONS[tok.id];
    const st = desk.suggStatus[tok.id];
    let showDel = false, showIns = false, showPlain = false, plainText = "";
    if (st === "accepted") {
      if (s.neu) { showPlain = true; plainText = s.neu; }
    } else if (st === "rejected") {
      showPlain = true; plainText = s.old;
    } else if (suggesting) {
      showDel = true; if (s.neu) showIns = true;
    } else {
      showPlain = true; plainText = s.old;
    }
    return (
      <span key={key}>
        {showDel && <span style={css("text-decoration:line-through;color:var(--bad);background:color-mix(in srgb,var(--bad) 9%,transparent)")}>{s.old}</span>}
        {showIns && <span style={css("text-decoration:underline;color:var(--good);background:color-mix(in srgb,var(--good) 13%,transparent)")}>{s.neu}</span>}
        {showPlain && <span style={css("color:inherit")}>{plainText}</span>}
      </span>
    );
  };

  // prose blocks (paragraphs + the [BOX] stat window)
  let pkey = 0;
  const blocks = seg(desk.rawProse).map((b, bi) => {
    if (b.kind === "box") {
      return (
        <pre key={`b${bi}`} style={css("font-family:var(--mono);font-size:13px;line-height:1.5;white-space:pre;overflow-x:auto;margin:22px 0;padding:16px 18px;color:var(--ink);background:var(--boxbg);border:1px solid var(--accentLine);border-radius:8px")}>{box()}</pre>
      );
    }
    const isLead = b.n === 0;
    let text = b.text;
    let lead = "";
    let leadStyle = "";
    if (isLead) {
      lead = text.charAt(0);
      text = text.slice(1);
      leadStyle = desk.isManu
        ? "float:left;font-family:var(--display);font-size:60px;line-height:.74;padding:9px 12px 0 0;color:var(--accent)"
        : "font:inherit;color:inherit";
    }
    const parts = tokenize(text, MARKERS[b.n]).map((tok) => renderToken(tok, "tk" + pkey++));
    return (
      <p key={`b${bi}`} style={css("font-family:var(--prose);font-size:18px;line-height:1.86;color:var(--ink);margin:0 0 1.05em")}>
        {isLead && <span style={css(leadStyle)}>{lead}</span>}
        {parts}
      </p>
    );
  });

  // mode switch
  const modeList: { id: "reading" | "suggesting" | "editing"; label: string }[] = [
    { id: "reading", label: "Reading" },
    { id: "suggesting", label: "Suggesting" },
    { id: "editing", label: "Editing" },
  ];

  // gutter suggestion cards
  const suggList = Object.keys(SUGGESTIONS).map((id) => {
    const s = SUGGESTIONS[id];
    const st = desk.suggStatus[id];
    return {
      id,
      author: s.author,
      why: s.why,
      oldText: s.old.trim() || "—",
      newText: s.neu.trim() || "(delete)",
      pending: !st,
      accepted: st === "accepted",
      rejected: st === "rejected",
      cardStyle: `background:var(--bg2);border:1px solid ${st === "accepted" ? "color-mix(in srgb,var(--good) 42%,var(--line))" : st === "rejected" ? "var(--line)" : "var(--accentLine)"};border-radius:9px;padding:11px 12px`,
    };
  });
  const pendingSugg = suggList.filter((s) => s.pending).length;

  // review pipeline
  const passes = [
    { label: "draft", status: "done", dot: t.good },
    { label: "continuity", status: "2 flags", dot: t.bad },
    { label: "style", status: "1 note", dot: t.warn },
    { label: "sensory", status: "clean", dot: t.good },
  ];

  // tabs
  const unresolved = 2 - Object.keys(desk.resolved).length;
  const tabDefs: { id: "continuity" | "notes" | "changes"; label: string; badge: string | null; badgeBg: string; badgeFg: string }[] = [
    { id: "continuity", label: "Continuity", badge: unresolved > 0 ? String(unresolved) : null, badgeBg: t.bad, badgeFg: "#fff" },
    { id: "notes", label: "Notes", badge: "3", badgeBg: t.accentSoft, badgeFg: t.accent },
    { id: "changes", label: "Changes", badge: null, badgeBg: "", badgeFg: "" },
  ];

  // continuity conflicts (rail)
  const conflicts = CONFLICT_IDS.map((id) => {
    const c = CONFLICTS[id];
    const r = desk.resolved[id];
    const resolved = !!r;
    return {
      id,
      attribute: c.attribute,
      context: c.context,
      proseValue: c.proseValue,
      ledgerValue: c.ledgerValue,
      isResolved: resolved,
      isOpen: !resolved,
      resolvedLabel: r === "prose" ? "prose kept" : r === "ledger" ? "ledger kept" : "",
      cardStyle: `background:var(--bg2);border:1px solid ${resolved ? "var(--line)" : "color-mix(in srgb,var(--bad) 32%,var(--line))"};border-radius:10px;padding:14px;opacity:${resolved ? ".62" : "1"};transition:opacity .2s`,
      proseBox: `flex:1;padding:8px 10px;border-radius:7px;background:var(--bg3);border:1px solid ${r === "prose" ? "var(--accentLine)" : "var(--line)"}`,
      ledgerBox: `flex:1;padding:8px 10px;border-radius:7px;background:var(--bg3);border:1px solid ${r === "ledger" ? "var(--accentLine)" : "var(--line)"}`,
    };
  });

  const notes = [
    { reviewer: "Pacing", severity: "advisory", color: t.warn, note: "Two cold/draft sensory beats inside 80 words — consider trimming one for momentum." },
    { reviewer: "Continuity-soft", severity: "info", color: t.info, note: "Lyra last appeared two scenes ago; a one-line reminder of the seal may help the reader." },
    { reviewer: "Style", severity: "info", color: t.good, note: "Strong close. The 'as if read from a page' metaphor lands the ward's nature cleanly." },
  ];

  const changes = [
    { glyph: "▲", color: t.good, label: "Soren · Level", detail: "14 → 15  (ascension threshold)" },
    { glyph: "△", color: t.good, label: "Soren · Mana cap", detail: "440 → 480" },
    { glyph: "✦", color: t.accent, label: "New mark acquired", detail: "Oathkeeper" },
    { glyph: "⚑", color: t.bad, label: "Thread · Lyra", detail: "active → sealed  (pending conflict c2)" },
  ];

  return (
    <div>
      {/* breadcrumb / status row */}
      <div style={css("display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:20px")}>
        <div>
          <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.06em;color:var(--dim);margin-bottom:8px")}>INBOX / CHAPTER 2 · SOREN / SCENE {cur.no}</div>
          <div style={css("display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap")}>
            <h1 style={css("margin:0;font-family:var(--display);font-weight:600;font-size:30px;letter-spacing:.01em;color:var(--ink);white-space:nowrap")}>{cur.title}</h1>
            <span style={css(sceneStatusStyle)}>● {cst.label}</span>
          </div>
        </div>
        <div style={css("display:flex;align-items:center;gap:18px;font-family:var(--mono);font-size:12px;color:var(--dim)")}>
          <div style={css("display:flex;align-items:center;gap:8px")}>
            <button onClick={desk.prevScene} title="Previous scene (k)" style={css("width:26px;height:26px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer;font-size:13px")}>‹</button>
            <span style={css("color:var(--dim)")}>{queuePos}</span>
            <button onClick={desk.nextScene} title="Next scene (j)" style={css("width:26px;height:26px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer;font-size:13px")}>›</button>
          </div>
          <span style={css("opacity:.4")}>·</span>
          <span><b style={css("color:var(--ink)")}>{cur.words}</b> words</span>
          <span style={css("opacity:.4")}>·</span>
          <span onClick={() => desk.go("diff")} style={css("cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft)")}>v{cur.version} · compare ▾</span>
        </div>
      </div>

      <div style={css("display:grid;grid-template-columns:minmax(0,1fr) 388px;gap:22px;align-items:start")}>
        {/* ── PROSE COLUMN ── */}
        <section style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow)")}>
          <div style={css("display:flex;align-items:center;justify-content:space-between;padding:11px 14px;border-bottom:1px solid var(--line);background:var(--bg2b);border-radius:var(--r) var(--r) 0 0")}>
            <div style={css("display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px")}>
              {modeList.map((m) => {
                const active = desk.mode === m.id;
                return (
                  <button key={m.id} onClick={() => desk.setMode(m.id)} style={css(`padding:5px 12px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12.5px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`)}>{m.label}</button>
                );
              })}
            </div>
            <div style={css("display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:10.5px;letter-spacing:.04em;color:var(--dim)")}>
              {suggesting && <span style={css("color:var(--accent)")}>{pendingSugg} open suggestions</span>}
              {!editing && <span>hover a name for canon</span>}
            </div>
          </div>

          {editing && (
            <div style={css("padding:30px 36px")}>
              <textarea
                onChange={(e) => desk.setProse(e.target.value)}
                defaultValue={desk.rawProse}
                spellCheck
                style={css("width:100%;min-height:56vh;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:18px;font-family:var(--mono);font-size:13.5px;line-height:1.75;resize:vertical")}
              />
            </div>
          )}

          {!editing && (
            <div style={css("display:flex;flex-wrap:wrap;gap:30px;padding:34px 32px 14px 42px")}>
              <div style={css("flex:1 1 380px;min-width:330px")}>{blocks}</div>

              <div style={css("flex:0 1 244px;display:flex;flex-direction:column;gap:11px;padding-top:2px")}>
                {suggesting && (
                  <div style={css("display:flex;flex-direction:column;gap:9px;margin-bottom:6px")}>
                    <span style={css("font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)")}>Suggestions</span>
                    {suggList.map((g) => (
                      <div key={g.id} style={css(g.cardStyle)}>
                        <div style={css("font-family:var(--mono);font-size:9.5px;color:var(--dim);margin-bottom:6px")}>{g.author}</div>
                        <div style={css("font-size:12.5px;line-height:1.4;margin-bottom:7px")}>
                          <span style={css("text-decoration:line-through;color:var(--bad)")}>{g.oldText}</span>{" "}
                          <span style={css("color:var(--good)")}>{g.newText}</span>
                        </div>
                        <div style={css("font-size:11px;color:var(--dim);font-style:italic;margin-bottom:9px")}>{g.why}</div>
                        {g.pending && (
                          <div style={css("display:flex;gap:6px")}>
                            <button onClick={() => desk.acceptSugg(g.id)} style={css("flex:1;padding:6px;border-radius:6px;border:1px solid color-mix(in srgb,var(--good) 45%,var(--line));background:color-mix(in srgb,var(--good) 12%,var(--bg3));color:var(--good);font-size:11px;cursor:pointer;font-family:var(--ui)")}>Accept</button>
                            <button onClick={() => desk.rejectSugg(g.id)} style={css("flex:1;padding:6px;border-radius:6px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:11px;cursor:pointer;font-family:var(--ui)")}>Reject</button>
                          </div>
                        )}
                        {g.accepted && (
                          <div style={css("display:flex;align-items:center;justify-content:space-between")}>
                            <span style={css("font-family:var(--mono);font-size:10px;color:var(--good)")}>✓ accepted</span>
                            <button onClick={() => desk.undoSugg(g.id)} style={css("background:none;border:none;color:var(--dim);font-size:10.5px;cursor:pointer")}>undo</button>
                          </div>
                        )}
                        {g.rejected && (
                          <div style={css("display:flex;align-items:center;justify-content:space-between")}>
                            <span style={css("font-family:var(--mono);font-size:10px;color:var(--dim)")}>rejected</span>
                            <button onClick={() => desk.undoSugg(g.id)} style={css("background:none;border:none;color:var(--dim);font-size:10.5px;cursor:pointer")}>undo</button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                <span style={css("font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)")}>Margin notes</span>
                <div
                  onClick={() => desk.highlightAnn(ANNOTATION.id)}
                  style={css(`background:${desk.selectedAnn === ANNOTATION.id ? "var(--accentSoft)" : "var(--bg2)"};border:1px solid ${desk.selectedAnn === ANNOTATION.id ? "var(--accentLine)" : "var(--line)"};border-radius:9px;padding:11px 13px;cursor:pointer`)}
                >
                  <div style={css("font-family:var(--prose);font-size:12.5px;color:var(--accent);font-style:italic;margin-bottom:6px")}>"{ANNOTATION.quote}"</div>
                  <p style={css("margin:0 0 6px;font-size:12px;line-height:1.5;color:var(--ink)")}>{ANNOTATION.note}</p>
                  <div style={css("font-family:var(--mono);font-size:9.5px;color:var(--dim)")}>— {ANNOTATION.author}</div>
                </div>
              </div>
            </div>
          )}

          {/* decision footer */}
          <div style={css("border-top:1px solid var(--line);padding:18px;background:var(--bg2b);border-radius:0 0 var(--r) var(--r)")}>
            <textarea
              onChange={(e) => desk.setFeedback(e.target.value)}
              value={desk.feedback}
              placeholder="Revision notes for the drafter (optional)…"
              style={css("width:100%;min-height:62px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:12px 14px;font-size:13.5px;line-height:1.6;resize:vertical;margin-bottom:12px")}
            />
            <div style={css("display:flex;gap:10px;align-items:center")}>
              <button onClick={() => desk.decide("approve")} style={css("flex:1;display:flex;align-items:center;justify-content:center;gap:9px;padding:12px;border-radius:8px;border:1px solid color-mix(in srgb,var(--good) 50%,var(--line));background:color-mix(in srgb,var(--good) 13%,var(--bg3));color:var(--good);font-size:14px;font-weight:500;cursor:pointer")}>
                Approve <span style={css("font-family:var(--mono);font-size:10px;opacity:.7;border:1px solid currentColor;border-radius:4px;padding:0 5px")}>A</span>
              </button>
              <button onClick={() => desk.decide("revise")} style={css("flex:1;display:flex;align-items:center;justify-content:center;gap:9px;padding:12px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:14px;cursor:pointer")}>
                Request revision <span style={css("font-family:var(--mono);font-size:10px;opacity:.6;border:1px solid currentColor;border-radius:4px;padding:0 5px")}>R</span>
              </button>
              <button onClick={() => desk.decide("deny")} style={css("flex:none;display:flex;align-items:center;justify-content:center;gap:9px;padding:12px 16px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:14px;cursor:pointer")}>
                Reject <span style={css("font-family:var(--mono);font-size:10px;opacity:.6;border:1px solid currentColor;border-radius:4px;padding:0 5px")}>X</span>
              </button>
            </div>
          </div>
        </section>

        {/* ── REVIEW RAIL ── */}
        <aside style={css("position:sticky;top:84px;display:flex;flex-direction:column;gap:16px")}>
          {/* meta */}
          <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px")}>
            <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:11.5px;color:var(--dim);line-height:2.1")}>
              <span>model</span><span style={css("color:var(--ink)")}>oracle-draft-4</span>
            </div>
            <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:11.5px;color:var(--dim);line-height:2.1")}>
              <span>drafted</span><span style={css("color:var(--ink)")}>14m ago</span>
            </div>
            <div style={css("height:1px;background:var(--line);margin:11px 0")} />
            <div style={css("font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin-bottom:9px")}>Review pipeline</div>
            <div style={css("display:flex;flex-direction:column;gap:7px")}>
              {passes.map((p) => (
                <div key={p.label} style={css("display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:12px;color:var(--ink)")}>
                  <span style={css(`width:7px;height:7px;border-radius:50%;background:${p.dot}`)} />
                  {p.label}
                  <span style={css("margin-left:auto;color:var(--dim);font-size:10.5px")}>{p.status}</span>
                </div>
              ))}
            </div>
          </div>

          {/* tabs */}
          <div style={css("display:flex;gap:2px;padding:3px;background:var(--bg3);border:1px solid var(--line);border-radius:999px")}>
            {tabDefs.map((tb) => {
              const active = desk.tab === tb.id;
              return (
                <button key={tb.id} onClick={() => desk.setTab(tb.id)} style={css(`flex:1;padding:7px;border:none;border-radius:999px;cursor:pointer;font-family:var(--ui);font-size:12.5px;background:${active ? "var(--bg2)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-weight:${active ? "500" : "400"};box-shadow:${active ? "0 1px 3px rgba(0,0,0,.18)" : "none"}`)}>
                  {tb.label}
                  {tb.badge && <span style={css(`margin-left:6px;font-family:var(--mono);font-size:10px;padding:0 5px;border-radius:999px;background:${tb.badgeBg};color:${tb.badgeFg}`)}>{tb.badge}</span>}
                </button>
              );
            })}
          </div>

          {/* CONTINUITY tab */}
          {desk.tab === "continuity" && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <p style={css("margin:0;font-size:12.5px;color:var(--dim);line-height:1.55")}>Advisory — nothing is blocked. You decide which source is canon; resolving updates the world ledger.</p>
              {conflicts.map((c) => (
                <div key={c.id} style={css(c.cardStyle)}>
                  <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:9px")}>
                    <span style={css("width:6px;height:6px;border-radius:50%;background:var(--bad)")} />
                    <span style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--bad)")}>{c.attribute}</span>
                    {c.isResolved && <span style={css("margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--good)")}>✓ {c.resolvedLabel}</span>}
                  </div>
                  <p style={css("margin:0 0 11px;font-size:13.5px;font-style:italic;line-height:1.5;color:var(--ink)")}>"{c.context}"</p>
                  <div style={css("display:flex;gap:8px;margin-bottom:11px")}>
                    <div style={css(c.proseBox)}>
                      <div style={css("font-family:var(--mono);font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:3px")}>Prose</div>
                      <div style={css("font-family:var(--mono);font-size:13px;color:var(--ink)")}>{c.proseValue}</div>
                    </div>
                    <div style={css(c.ledgerBox)}>
                      <div style={css("font-family:var(--mono);font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:3px")}>Ledger</div>
                      <div style={css("font-family:var(--mono);font-size:13px;color:var(--ink)")}>{c.ledgerValue}</div>
                    </div>
                  </div>
                  {c.isOpen && (
                    <div style={css("display:flex;gap:7px")}>
                      <button onClick={() => desk.resolve(c.id, "prose")} style={css(KEEP_BTN)}>Keep prose · fix ledger</button>
                      <button onClick={() => desk.resolve(c.id, "ledger")} style={css(KEEP_BTN)}>Keep ledger · fix prose</button>
                    </div>
                  )}
                  {c.isResolved && (
                    <button onClick={() => desk.unresolve(c.id)} style={css("width:100%;padding:7px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-size:11px;cursor:pointer")}>Undo</button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* NOTES tab */}
          {desk.tab === "notes" && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <p style={css("margin:0;font-size:12.5px;color:var(--dim);line-height:1.55")}>Advisory flags from the review passes. Inline comments live in the margin beside the prose.</p>
              <div style={css("font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin-top:2px")}>Reviewer flags</div>
              {notes.map((n) => (
                <div key={n.reviewer} style={css(`border-left:2px solid ${n.color};background:var(--bg2);border-radius:0 7px 7px 0;padding:10px 13px`)}>
                  <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:5px")}>
                    <span style={css("font-family:var(--mono);font-size:11px;color:var(--ink)")}>{n.reviewer}</span>
                    <span style={css(`font-family:var(--mono);font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;color:${n.color}`)}>{n.severity}</span>
                  </div>
                  <p style={css("margin:0;font-size:13px;line-height:1.5;color:var(--dim)")}>{n.note}</p>
                </div>
              ))}
            </div>
          )}

          {/* CHANGES tab */}
          {desk.tab === "changes" && (
            <div style={css("display:flex;flex-direction:column;gap:9px")}>
              <p style={css("margin:0 0 3px;font-size:12.5px;color:var(--dim);line-height:1.55")}>Ledger deltas this scene proposes once approved.</p>
              {changes.map((ch) => (
                <div key={ch.label} style={css("display:flex;align-items:center;gap:11px;padding:11px 13px;background:var(--bg2);border:1px solid var(--line);border-radius:8px")}>
                  <span style={css(`font-family:var(--mono);font-size:15px;color:${ch.color}`)}>{ch.glyph}</span>
                  <div style={css("min-width:0")}>
                    <div style={css("font-size:13px;color:var(--ink)")}>{ch.label}</div>
                    <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim);margin-top:2px")}>{ch.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
