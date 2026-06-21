import { useState } from "react";
import type { ReactNode } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import CanonCard from "../components/CanonCard";
import type { CardModel } from "../components/CanonCard";
import { box, seg, tokenize } from "../prose";
import type { Token } from "../prose";
import type { Marker } from "../types";
import { api } from "../api/client";
import { useFetch, useSelectedBook } from "../api/hooks";
import {
  annoMarkers,
  annotationCards,
  beatForScene,
  conflictSpan,
  continuityConflicts,
  entityCards,
  entityMarkers,
  pipelinePasses,
  reviewerNotes,
  stateChanges,
  suggMarkers,
  suggestionCards,
  wordCount,
} from "../api/adapters.scene";
import type { ConflictCard } from "../api/adapters.scene";

const KEEP_BTN =
  "flex:1;padding:8px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:11.5px;cursor:pointer";

export default function SceneScreen() {
  const desk = useDesk();
  const { t } = desk;

  // The book context exists so the desk knows which book is active; the scene queue itself is
  // book-agnostic (GET /scenes/pending), but we depend on bookId so a book switch refetches.
  const { bookId } = useSelectedBook();

  // 1. the review queue (pending scenes) → pick the active one by index (j/k clamps the index).
  const pending = useFetch(() => api.pending(), [bookId]);
  const queue = pending.data ?? [];
  const idx = queue.length ? Math.min(Math.max(desk.activeScene, 0), queue.length - 1) : 0;
  const queued = queue[idx] ?? null;
  const sceneId = queued?.id ?? null;

  // 2. the active scene's full detail (prose + critiques).
  const sceneFetch = useFetch(
    () => (sceneId ? api.scene(sceneId) : Promise.resolve(null)),
    [sceneId],
  );
  const scene = sceneFetch.data;

  // 3. the chapter's beats → the beat for THIS scene drives the Changes tab.
  const chapterId = scene?.chapter_id ?? null;
  const beatsFetch = useFetch(
    () => (chapterId ? api.chapterBeats(chapterId) : Promise.resolve(null)),
    [chapterId],
  );
  const beat = beatForScene(beatsFetch.data, scene?.scene_no ?? -1);

  // 4. live entity cards (book-scoped) + annotations & suggestions (scene-scoped).
  const charsFetch = useFetch(() => (bookId ? api.characters(bookId) : Promise.resolve([])), [bookId]);
  const annFetch = useFetch(() => (sceneId ? api.annotations(sceneId) : Promise.resolve([])), [sceneId]);
  const suggFetch = useFetch(() => (sceneId ? api.suggestions(sceneId) : Promise.resolve([])), [sceneId]);

  const entityMap = entityCards(charsFetch.data ?? []);
  const entMarks = entityMarkers(charsFetch.data ?? []);
  const noteCards = annotationCards(annFetch.data ?? []);
  const annoMarks = annoMarkers(annFetch.data ?? []);
  const suggCards = suggestionCards(suggFetch.data ?? []);
  const suggMarks = suggMarkers(suggFetch.data ?? []);
  const suggById = new Map(suggCards.map((s) => [s.id, s]));

  const loading = pending.loading || sceneFetch.loading;
  const error = pending.error || sceneFetch.error;

  // --- view-models from live data ---------------------------------------------------------------
  const conflicts: ConflictCard[] = continuityConflicts(scene);
  const notes = reviewerNotes(scene, t);
  const passes = pipelinePasses(scene, t);
  const changes = stateChanges(beat, t);

  // --- live continuity resolve (rail + inline card) ---------------------------------------------
  // Keep the existing desk.resolve(...) UI-state call (so the card collapses/✓), AND POST to the API.
  const resolveConflict = (critiqueId: string, choice: "prose" | "ledger") => {
    if (sceneId) {
      void api
        .resolveContinuity(sceneId, {
          critique_id: critiqueId,
          choice: choice === "prose" ? "use_prose" : "use_ledger",
        })
        .catch(() => {
          /* advisory — the UI state already reflects the user's choice; surface nothing on failure */
        });
    }
    desk.resolve(critiqueId, choice);
  };

  // Effective suggestion status: an optimistic local override (this session) wins; otherwise the
  // server's persisted decision. Undo drops the local override and reveals the server's truth.
  const effStatus = (id: string, server: string): "accepted" | "rejected" | undefined => {
    const local = desk.suggStatus[id];
    if (local) return local;
    return server === "accepted" || server === "rejected" ? server : undefined;
  };

  // Accept/reject → POST /suggestions/{id}/decision, plus the optimistic local UI state.
  const decideSugg = (id: string, status: "accepted" | "rejected") => {
    void api.decideSuggestion(id, { status }).catch(() => {
      /* optimistic: the local state already reflects the choice; reloads reconcile with the server */
    });
    if (status === "accepted") desk.acceptSugg(id);
    else desk.rejectSugg(id);
  };

  // --- create a margin note (quote-anchored) → POST /scenes/{id}/annotations --------------------
  const [noteDraft, setNoteDraft] = useState("");
  const [quoteDraft, setQuoteDraft] = useState("");
  const addNote = () => {
    if (!sceneId || !noteDraft.trim()) return;
    void api
      .createAnnotation(sceneId, {
        quote: quoteDraft.trim() || null,
        note: noteDraft.trim(),
        author: "You",
        version: scene?.version ?? null,
      })
      .then(() => {
        setNoteDraft("");
        setQuoteDraft("");
        annFetch.reload();
      })
      .catch(() => {
        /* surface nothing on failure; the draft stays so the user can retry */
      });
  };

  // active scene meta (live, with graceful fallbacks while the detail loads from the queue row).
  const head = scene ?? queued;
  const cur = {
    no: head?.scene_no ?? 0,
    title: head ? head.title?.trim() || `Scene ${head.scene_no}` : "Scene",
    words: head ? String(wordCount(head.prose)) : "—",
    version: head?.version ?? 1,
    status: head?.status ?? "awaiting",
  };

  const statusMap: Record<string, { label: string; color: string }> = {
    awaiting_review: { label: "Awaiting review", color: t.warn },
    awaiting: { label: "Awaiting review", color: t.warn },
    note: { label: "Reviewer note", color: t.info },
    approved: { label: "Approved", color: t.good },
    revision_requested: { label: "Revising", color: t.bad },
    revising: { label: "Revising", color: t.bad },
  };
  const cst = statusMap[cur.status] || statusMap.awaiting;
  const sceneStatusStyle = `display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:${cst.color};background:color-mix(in srgb,${cst.color} 14%,transparent);border:1px solid color-mix(in srgb,${cst.color} 40%,transparent);border-radius:999px;padding:4px 11px`;
  const queuePos = `${queue.length ? idx + 1 : 0} / ${queue.length}`;

  const editing = desk.mode === "editing";
  const suggesting = desk.mode === "suggesting";
  const showMarks = !editing;

  // The prose the desk renders. Live scene.prose is the rendered form; desk.rawProse is the editing
  // buffer (empty until the user edits). Inline markers below anchor against this text.
  const proseText = scene?.prose ?? desk.rawProse;

  // Live continuity conflicts keyed by critique id; the inline card + rail share these.
  const conflictById = new Map(conflicts.map((c) => [c.id, c]));

  // canon hover-card model — entities from the live ledger, conflicts from live critiques.
  const makeCard = (kind: "entity" | "conflict", id: string): CardModel => {
    if (kind === "entity") {
      const e = entityMap.get(id);
      return {
        title: e?.name ?? id,
        subtitle: e?.role ?? "",
        rows: (e?.rows ?? []).map((r) => ({ k: r.k, v: r.v })),
        hasFlag: false,
        open: false,
        resolved: false,
        flagProse: "",
        flagLedger: "",
        resolvedLabel: "",
        keepProse: () => {},
        keepLedger: () => {},
      };
    }
    const cf = conflictById.get(id);
    const resolved = !!desk.resolved[id];
    return {
      title: cf?.attribute ?? "continuity conflict",
      subtitle: "continuity conflict",
      rows: [],
      hasFlag: true,
      open: !resolved,
      resolved,
      flagProse: cf?.proseValue ?? "",
      flagLedger: cf?.ledgerValue ?? "",
      resolvedLabel: resolved ? (desk.resolved[id] === "prose" ? "prose kept" : "ledger kept") : "",
      keepProse: () => resolveConflict(id, "prose"),
      keepLedger: () => resolveConflict(id, "ledger"),
    };
  };

  // Per-paragraph inline markers, all from live data: entity names (ledger), continuity conflict
  // spans (critique.span/context_sentence), annotation quotes, and suggestion old-text. tokenize
  // anchors each by substring within the paragraph and silently skips any that aren't present.
  const conflictMarkers: Marker[] = scene
    ? scene.critiques.flatMap((c) => {
        const cf = conflictById.get(c.id);
        if (!cf) return [];
        const find = conflictSpan(c);
        return find ? [{ find, kind: "conflict", id: c.id } as Marker] : [];
      })
    : [];
  const allMarkers: Marker[] = [...entMarks, ...conflictMarkers, ...annoMarks, ...suggMarks];

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

    // suggestion (track-changes) — live. Renders del/ins per its effective status.
    const s = suggById.get(tok.id);
    if (!s) return <span key={key} style={css("color:inherit")}>{tok.text}</span>;
    const st = effStatus(tok.id, s.status);
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
  const blocks = seg(proseText).map((b, bi) => {
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
    const parts = tokenize(text, allMarkers).map((tok) => renderToken(tok, "tk" + pkey++));
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

  // gutter suggestion cards — live, with effective (local-over-server) status.
  const suggList = suggCards.map((s) => {
    const st = effStatus(s.id, s.status);
    return {
      id: s.id,
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

  // tabs — badges reflect live counts.
  const unresolved = conflicts.filter((c) => !desk.resolved[c.id]).length;
  const tabDefs: { id: "continuity" | "notes" | "changes"; label: string; badge: string | null; badgeBg: string; badgeFg: string }[] = [
    { id: "continuity", label: "Continuity", badge: unresolved > 0 ? String(unresolved) : null, badgeBg: t.bad, badgeFg: "#fff" },
    { id: "notes", label: "Notes", badge: notes.length > 0 ? String(notes.length) : null, badgeBg: t.accentSoft, badgeFg: t.accent },
    { id: "changes", label: "Changes", badge: null, badgeBg: "", badgeFg: "" },
  ];

  // continuity conflicts (rail)
  const railConflicts = conflicts.map((c) => {
    const r = desk.resolved[c.id];
    const resolved = !!r;
    return {
      id: c.id,
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

  // --- decision footer → POST /scenes/{id}/decision ---------------------------------------------
  // Send edited prose only when in editing mode and the buffer actually differs from the live prose.
  const submitDecision = (d: "approve" | "revise" | "deny") => {
    if (sceneId) {
      const editedProse =
        editing && scene && desk.rawProse !== scene.prose ? desk.rawProse : null;
      void api
        .decide(sceneId, {
          decision: d,
          feedback: desk.feedback || null,
          edited_prose: editedProse,
        })
        .catch(() => {
          /* the DecisionToast still confirms the action; the queue refetches on next visit */
        });
    }
    desk.decide(d); // keep the existing UI-state call so the DecisionToast shows
  };

  // --- loading / error / empty -------------------------------------------------------------------
  const note = (msg: string) => (
    <div style={css("text-align:center;color:var(--dim);font-family:var(--mono);font-size:12px;letter-spacing:.04em;padding:80px 0")}>
      {msg}
    </div>
  );

  if (loading && !scene) return <div>{note("Loading scene…")}</div>;
  if (error) return <div>{note(`Could not load scene — ${error}`)}</div>;
  if (!scene) return <div>{note("No scenes awaiting review.")}</div>;

  return (
    <div>
      {/* breadcrumb / status row */}
      <div style={css("display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:20px")}>
        <div>
          <div style={css("font-family:var(--mono);font-size:11px;letter-spacing:.06em;color:var(--dim);margin-bottom:8px")}>INBOX / SCENE {cur.no}</div>
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
                defaultValue={proseText}
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
                    {suggList.length === 0 && (
                      <span style={css("font-size:11.5px;color:var(--dim);font-style:italic")}>No suggestions on this scene.</span>
                    )}
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
                            <button onClick={() => decideSugg(g.id, "accepted")} style={css("flex:1;padding:6px;border-radius:6px;border:1px solid color-mix(in srgb,var(--good) 45%,var(--line));background:color-mix(in srgb,var(--good) 12%,var(--bg3));color:var(--good);font-size:11px;cursor:pointer;font-family:var(--ui)")}>Accept</button>
                            <button onClick={() => decideSugg(g.id, "rejected")} style={css("flex:1;padding:6px;border-radius:6px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:11px;cursor:pointer;font-family:var(--ui)")}>Reject</button>
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
                {noteCards.length === 0 && (
                  <span style={css("font-size:11.5px;color:var(--dim);font-style:italic")}>No margin notes yet.</span>
                )}
                {noteCards.map((n) => (
                  <div
                    key={n.id}
                    onClick={() => desk.highlightAnn(n.id)}
                    style={css(`background:${desk.selectedAnn === n.id ? "var(--accentSoft)" : "var(--bg2)"};border:1px solid ${desk.selectedAnn === n.id ? "var(--accentLine)" : "var(--line)"};border-radius:9px;padding:11px 13px;cursor:pointer`)}
                  >
                    {n.quote && <div style={css("font-family:var(--prose);font-size:12.5px;color:var(--accent);font-style:italic;margin-bottom:6px")}>"{n.quote}"</div>}
                    <p style={css("margin:0 0 6px;font-size:12px;line-height:1.5;color:var(--ink)")}>{n.note}</p>
                    <div style={css("font-family:var(--mono);font-size:9.5px;color:var(--dim)")}>— {n.author}</div>
                  </div>
                ))}
                {/* add a margin note */}
                <div style={css("display:flex;flex-direction:column;gap:6px;border:1px dashed var(--line);border-radius:9px;padding:10px 11px")}>
                  <input
                    value={quoteDraft}
                    onChange={(e) => setQuoteDraft(e.target.value)}
                    placeholder="anchor quote (optional)"
                    style={css("background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:11.5px;font-family:var(--prose)")}
                  />
                  <textarea
                    value={noteDraft}
                    onChange={(e) => setNoteDraft(e.target.value)}
                    placeholder="add a margin note…"
                    style={css("background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;line-height:1.5;min-height:48px;resize:vertical")}
                  />
                  <button
                    onClick={addNote}
                    disabled={!noteDraft.trim()}
                    style={css(`align-self:flex-start;padding:5px 12px;border-radius:6px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--accent);font-size:11px;cursor:${noteDraft.trim() ? "pointer" : "default"};opacity:${noteDraft.trim() ? "1" : ".5"};font-family:var(--ui)`)}
                  >
                    Add note
                  </button>
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
              <button onClick={() => submitDecision("approve")} style={css("flex:1;display:flex;align-items:center;justify-content:center;gap:9px;padding:12px;border-radius:8px;border:1px solid color-mix(in srgb,var(--good) 50%,var(--line));background:color-mix(in srgb,var(--good) 13%,var(--bg3));color:var(--good);font-size:14px;font-weight:500;cursor:pointer")}>
                Approve <span style={css("font-family:var(--mono);font-size:10px;opacity:.7;border:1px solid currentColor;border-radius:4px;padding:0 5px")}>A</span>
              </button>
              <button onClick={() => submitDecision("revise")} style={css("flex:1;display:flex;align-items:center;justify-content:center;gap:9px;padding:12px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:14px;cursor:pointer")}>
                Request revision <span style={css("font-family:var(--mono);font-size:10px;opacity:.6;border:1px solid currentColor;border-radius:4px;padding:0 5px")}>R</span>
              </button>
              <button onClick={() => submitDecision("deny")} style={css("flex:none;display:flex;align-items:center;justify-content:center;gap:9px;padding:12px 16px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:14px;cursor:pointer")}>
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
              <span>model</span><span style={css("color:var(--ink)")}>{scene.model ?? "—"}</span>
            </div>
            <div style={css("display:flex;justify-content:space-between;font-family:var(--mono);font-size:11.5px;color:var(--dim);line-height:2.1")}>
              <span>tokens</span><span style={css("color:var(--ink)")}>{scene.token_count ?? "—"}</span>
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
              {railConflicts.length === 0 && (
                <p style={css("margin:0;font-size:12.5px;color:var(--dim);font-style:italic")}>No continuity conflicts on this scene.</p>
              )}
              {railConflicts.map((c) => (
                <div key={c.id} style={css(c.cardStyle)}>
                  <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:9px")}>
                    <span style={css("width:6px;height:6px;border-radius:50%;background:var(--bad)")} />
                    <span style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--bad)")}>{c.attribute}</span>
                    {c.isResolved && <span style={css("margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--good)")}>✓ {c.resolvedLabel}</span>}
                  </div>
                  {c.context && <p style={css("margin:0 0 11px;font-size:13.5px;font-style:italic;line-height:1.5;color:var(--ink)")}>"{c.context}"</p>}
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
                      <button onClick={() => resolveConflict(c.id, "prose")} style={css(KEEP_BTN)}>Keep prose · fix ledger</button>
                      <button onClick={() => resolveConflict(c.id, "ledger")} style={css(KEEP_BTN)}>Keep ledger · fix prose</button>
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
              {notes.length === 0 && (
                <p style={css("margin:0;font-size:12.5px;color:var(--dim);font-style:italic")}>No advisory flags.</p>
              )}
              {notes.map((n) => (
                <div key={n.id} style={css(`border-left:2px solid ${n.color};background:var(--bg2);border-radius:0 7px 7px 0;padding:10px 13px`)}>
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
              {changes.length === 0 && (
                <p style={css("margin:0;font-size:12.5px;color:var(--dim);font-style:italic")}>No state changes recorded for this scene's beat.</p>
              )}
              {changes.map((ch) => (
                <div key={ch.key} style={css("display:flex;align-items:center;gap:11px;padding:11px 13px;background:var(--bg2);border:1px solid var(--line);border-radius:8px")}>
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
