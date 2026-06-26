"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import CanonCard from "../components/CanonCard";
import type { CardModel } from "../components/CanonCard";
import { seg, tokenize } from "../prose";
import type { Token } from "../prose";
import type { Marker } from "../types";
import { applyAcceptedSuggestions, sceneLabel, statValue, wordCount } from "../lib/format";
import { buildSceneMarkdown, downloadMarkdown, sceneMarkdownFilename } from "../lib/sceneMarkdown";
import type { CritiqueOut, DecisionKind } from "../api/types";

const KEEP_BTN =
  "flex:1;padding:8px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:11.5px;cursor:pointer";

// Reading layouts for a single scene, mirroring the Manuscript screen: a comfortable measure beside
// the review rail (Page), a full-width single measure (Wide), or a true two-column book spread.
// In Wide/Two-column the prose reclaims the full width and the review rail drops beneath it.
type SceneLayout = "page" | "wide" | "columns";
const SCENE_LAYOUTS: { id: SceneLayout; label: string }[] = [
  { id: "page", label: "Page" },
  { id: "wide", label: "Wide" },
  { id: "columns", label: "Two-column" },
];
const LAYOUT_KEY = "dominion:sceneLayout";

// Continuity critiques carry a prose↔ledger mismatch in their payload; everything else is an advisory note.
const isConflict = (c: CritiqueOut): boolean =>
  !!c.payload && c.payload.prose_value != null && c.payload.ledger_value != null;
const pstr = (c: CritiqueOut, key: string): string => {
  const v = c.payload?.[key];
  return v == null ? "" : String(v);
};

export default function SceneScreen() {
  const desk = useDesk();
  const { t } = desk;
  const data = useDeskData();
  const router = useRouter();
  // /scene/[sceneId] focuses a specific scene; bare /scene shows the pending review queue.
  const params = useParams<{ sceneId?: string }>();
  const focusSceneId = params.sceneId ?? null;
  const [committing, setCommitting] = useState(false);
  // selection toolbar + inline markup composer (replace the old window.prompt flows)
  const [sel, setSel] = useState<{ text: string; x: number; y: number } | null>(null);
  const [composer, setComposer] = useState<{
    kind: "note" | "sugg";
    quote: string;
    x: number;
    y: number;
  } | null>(null);
  const [restored, setRestored] = useState(false); // an unsaved hand-edit was recovered from localStorage
  const proseRef = useRef<HTMLDivElement>(null);
  // Reading layout (page / wide / two-column) — a per-user preference that sticks across sessions.
  const [layout, setLayout] = useState<SceneLayout>(() => {
    try {
      const v = localStorage.getItem(LAYOUT_KEY);
      if (v === "page" || v === "wide" || v === "columns") return v;
    } catch {
      /* localStorage unavailable */
    }
    return "page";
  });
  useEffect(() => {
    try {
      localStorage.setItem(LAYOUT_KEY, layout);
    } catch {
      /* ignore */
    }
  }, [layout]);

  const pending = data.pending;
  const idx = pending.length ? Math.min(Math.max(desk.activeScene, 0), pending.length - 1) : -1;
  const queueId = idx >= 0 ? pending[idx].id : null;
  // A focused scene (e.g. an approved one opened from the board) takes precedence over the queue.
  const focused = focusSceneId != null;
  const loadId = focusSceneId ?? queueId;

  // Load the active scene when it (or the queue position / contents) changes.
  const loadedRef = useRef<string | null>(null);
  useEffect(() => {
    if (loadId !== loadedRef.current) {
      loadedRef.current = loadId;
      data.openSceneById(loadId);
    }
  }, [loadId, data]);

  const cur = data.detail;
  const draftKey = (s: { id: string; version: number }) => `dominion:draft:${s.id}:${s.version}`;

  // Seed the edit buffer from the loaded scene — but if an unsaved hand-edit was autosaved for this
  // exact (scene, version), recover it so navigating away never loses work.
  useEffect(() => {
    if (!cur) return;
    let initial = cur.prose ?? "";
    let didRestore = false;
    try {
      const saved = localStorage.getItem(draftKey(cur));
      if (saved != null && saved !== (cur.prose ?? "")) {
        initial = saved;
        didRestore = true;
      }
    } catch {
      /* localStorage unavailable — fall back to the server prose */
    }
    desk.setProse(initial);
    setRestored(didRestore);
  }, [cur?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Autosave the hand-edit buffer per (scene, version); clear the draft once it matches the server.
  useEffect(() => {
    if (!cur) return;
    try {
      if (desk.rawProse !== (cur.prose ?? "")) localStorage.setItem(draftKey(cur), desk.rawProse);
      else localStorage.removeItem(draftKey(cur));
    } catch {
      /* ignore */
    }
  }, [desk.rawProse, cur?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const discardDraft = () => {
    if (!cur) return;
    desk.setProse(cur.prose ?? "");
    try {
      localStorage.removeItem(draftKey(cur));
    } catch {
      /* ignore */
    }
    setRestored(false);
  };

  const chapter = useMemo(
    () => data.chapters.find((c) => c.id === cur?.chapter_id) ?? null,
    [data.chapters, cur?.chapter_id],
  );

  const editing = desk.mode === "editing";
  const suggesting = desk.mode === "suggesting";
  const showMarks = !editing;

  const critiques = cur?.critiques ?? [];
  const conflicts = critiques.filter(isConflict);
  const notes = critiques.filter((c) => !isConflict(c));
  const annotations = data.annotations;
  const suggestions = data.suggestions;
  const pendingSugg = suggestions.filter((s) => s.status === "pending").length;

  const commit = async (kind: DecisionKind) => {
    if (!cur || committing) return;
    setCommitting(true);
    desk.decide(kind); // instant feedback (the toast) — the API call below now returns fast
    // fold accepted tracked-changes (on top of any hand-edit) into the canonical text
    const finalProse = applyAcceptedSuggestions(desk.rawProse, suggestions);
    const edited = finalProse !== (cur.prose ?? "") ? finalProse : null;
    const body =
      kind === "revise"
        ? { decision: "revise" as const, feedback: desk.feedback || null, edited_prose: edited }
        : kind === "approve"
          ? { decision: "approve" as const, edited_prose: edited }
          : { decision: "deny" as const };
    await data.decide(cur.id, body);
    try {
      localStorage.removeItem(draftKey(cur));
    } catch {
      /* ignore */
    }
    setRestored(false);
    desk.setFeedback("");
    setCommitting(false);
  };

  // Capture a text selection inside the prose to anchor the inline note/suggest toolbar.
  const onProseMouseUp = () => {
    const s = window.getSelection();
    if (!s || s.isCollapsed) {
      setSel(null);
      return;
    }
    const text = s.toString().trim();
    const node = s.anchorNode;
    if (!text || !proseRef.current || !node || !proseRef.current.contains(node)) {
      setSel(null);
      return;
    }
    const rect = s.getRangeAt(0).getBoundingClientRect();
    setSel({ text, x: rect.left + rect.width / 2, y: rect.top });
  };

  // ── empty state ────────────────────────────────────────────────────────────────────────────────
  if (!cur) {
    return (
      <div style={css("max-width:560px;margin:60px auto;text-align:center")}>
        <h1
          style={css(
            "margin:0 0 10px;font-family:var(--display);font-weight:600;font-size:26px;color:var(--ink)",
          )}
        >
          Nothing to review
        </h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px;line-height:1.6")}>
          {data.jobs.running
            ? "A scene is drafting — it'll land here shortly."
            : "Plan a chapter from the Inbox and approve its beats; drafted scenes show up here for review."}
        </p>
        <button
          onClick={() => router.push("/inbox")}
          style={css(
            "margin-top:18px;padding:9px 16px;border-radius:8px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer;font-family:var(--ui);font-size:13.5px",
          )}
        >
          Go to inbox
        </button>
      </div>
    );
  }

  // ── canon hover-card model ───────────────────────────────────────────────────────────────────
  const makeCard = (kind: "entity" | "conflict", id: string): CardModel => {
    if (kind === "entity") {
      const ch = data.characters.find((c) => c.character === id);
      return {
        title: id,
        subtitle: ch?.is_pov ? "POV character" : ch?.body ? "canon" : "character",
        rows: ch ? Object.entries(ch.stats).map(([k, v]) => ({ k, v: statValue(v) })) : [],
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
    const c = conflicts.find((x) => x.id === id);
    return {
      title: c ? pstr(c, "attribute") || "continuity" : "continuity",
      subtitle: "continuity conflict",
      rows: [],
      hasFlag: true,
      open: true,
      resolved: false,
      flagProse: c ? pstr(c, "prose_value") : "",
      flagLedger: c ? pstr(c, "ledger_value") : "",
      resolvedLabel: "",
      keepProse: () => data.resolveContinuity(cur.id, { critique_id: id, choice: "use_prose" }),
      keepLedger: () => data.resolveContinuity(cur.id, { critique_id: id, choice: "use_ledger" }),
    };
  };

  // markers for a paragraph: entity names, conflict prose-values, annotation quotes, suggestion quotes
  const markersFor = (text: string): Marker[] => {
    const ms: Marker[] = [];
    for (const ch of data.characters) {
      if (ch.character && text.includes(ch.character)) {
        ms.push({ find: ch.character, kind: "entity", id: ch.character });
      }
    }
    for (const c of conflicts) {
      const pv = pstr(c, "prose_value");
      if (pv && text.includes(pv)) ms.push({ find: pv, kind: "conflict", id: c.id });
    }
    for (const s of suggestions) {
      if (s.quote && text.includes(s.quote)) ms.push({ find: s.quote, kind: "sugg", id: s.id });
    }
    for (const a of annotations) {
      if (a.quote && text.includes(a.quote)) ms.push({ find: a.quote, kind: "anno", id: a.id });
    }
    return ms;
  };

  const renderToken = (tok: Token, key: string): ReactNode => {
    if (tok.kind === "text")
      return (
        <span key={key} style={css("color:inherit")}>
          {tok.text}
        </span>
      );

    if (tok.kind === "anno") {
      const style = showMarks
        ? "background:var(--accentSoft);border-bottom:1px dashed var(--accent);border-radius:2px;cursor:pointer;color:inherit"
        : "color:inherit;cursor:pointer";
      return (
        <span key={key} onClick={() => desk.selectAnn(tok.id)} style={css(style)}>
          {tok.text}
        </span>
      );
    }

    if (tok.kind === "sugg") {
      const s = suggestions.find((x) => x.id === tok.id);
      const neu = s?.new_text ?? "";
      if (s?.status === "accepted")
        return (
          <span key={key} style={css("color:inherit")}>
            {neu}
          </span>
        );
      if (s?.status === "rejected" || !suggesting)
        return (
          <span key={key} style={css("color:inherit")}>
            {tok.text}
          </span>
        );
      // pending + suggesting mode: show the tracked change
      return (
        <span key={key}>
          <span
            style={css(
              "text-decoration:line-through;color:var(--bad);background:color-mix(in srgb,var(--bad) 9%,transparent)",
            )}
          >
            {tok.text}
          </span>
          {neu && (
            <span
              style={css(
                "text-decoration:underline;color:var(--good);background:color-mix(in srgb,var(--good) 13%,transparent)",
              )}
            >
              {neu}
            </span>
          )}
        </span>
      );
    }

    if (tok.kind === "entity" || tok.kind === "conflict") {
      const hovered = desk.hoveredKey === key;
      const span =
        tok.kind === "entity"
          ? showMarks
            ? "border-bottom:1px dotted var(--accent);cursor:help;color:inherit"
            : "color:inherit"
          : showMarks
            ? "border-bottom:1px dotted var(--bad);background:color-mix(in srgb,var(--bad) 9%,transparent);border-radius:2px;cursor:help;color:inherit"
            : "color:inherit";
      return (
        <span
          key={key}
          style={css("position:relative;display:inline")}
          onMouseEnter={() => desk.setHover(key)}
          onMouseLeave={desk.clearHover}
        >
          <span onClick={() => desk.setHover(hovered ? null : key)} style={css(span)}>
            {tok.text}
          </span>
          {hovered && <CanonCard card={makeCard(tok.kind, tok.id)} />}
        </span>
      );
    }
    return (
      <span key={key} style={css("color:inherit")}>
        {tok.text}
      </span>
    );
  };

  // Gutter affordances open the inline composer (select prose for a pre-filled quote, or add manually).
  const center = () => ({ x: window.innerWidth / 2, y: 150 });
  const addNote = () => setComposer({ kind: "note", quote: "", ...center() });
  const addSuggestion = () => setComposer({ kind: "sugg", quote: "", ...center() });
  const saveComposer = async (p: { quote: string; note: string; newText: string; why: string }) => {
    if (!composer) return;
    if (composer.kind === "note") {
      await data.addAnnotation({
        note: p.note.trim(),
        quote: p.quote.trim() || null,
        author: "You",
      });
    } else {
      await data.addSuggestion({
        quote: p.quote.trim(),
        new_text: p.newText,
        why: p.why.trim() || null,
        author: "You",
      });
      desk.setMode("suggesting"); // surface the tracked change you just made
    }
    setComposer(null);
  };

  // Wide and two-column read full-width with the review rail beneath; page keeps the side-by-side rail.
  const wideRead = layout !== "page";
  const proseFontSize = layout === "columns" ? "16.5px" : "18px";
  const proseColStyle =
    layout === "page"
      ? "flex:1 1 380px;min-width:330px"
      : layout === "wide"
        ? "flex:1 1 100%;min-width:0;max-width:54rem;margin:0 auto"
        : "flex:1 1 100%;min-width:0";
  const blocksWrapStyle = layout === "columns" ? "column-count:2;column-gap:2.8rem" : "";

  let pkey = 0;
  const blocks = seg(cur.prose ?? "").map((b, bi) => {
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
    const parts = tokenize(text, markersFor(text)).map((tok) => renderToken(tok, "tk" + pkey++));
    return (
      <p
        key={`b${bi}`}
        style={css(
          `font-family:var(--prose);font-size:${proseFontSize};line-height:1.86;color:var(--ink);margin:0 0 1.05em;break-inside:avoid-column`,
        )}
      >
        {isLead && <span style={css(leadStyle)}>{lead}</span>}
        {parts}
      </p>
    );
  });

  const modeList: { id: "reading" | "suggesting" | "editing"; label: string }[] = [
    { id: "reading", label: "Reading" },
    { id: "suggesting", label: "Suggesting" },
    { id: "editing", label: "Editing" },
  ];

  const passes = cur.passes_run ?? [];
  const tabDefs: { id: "continuity" | "notes" | "changes"; label: string; badge: string | null }[] =
    [
      {
        id: "continuity",
        label: "Continuity",
        badge: conflicts.length ? String(conflicts.length) : null,
      },
      { id: "notes", label: "Notes", badge: notes.length ? String(notes.length) : null },
      { id: "changes", label: "Changes", badge: null },
    ];

  const deltas =
    cur && data.activeBeat?.expected_state_changes
      ? Object.entries(data.activeBeat.expected_state_changes).flatMap(([who, attrs]) =>
          Object.entries(attrs).map(([k, v]) => ({ label: `${who} · ${k}`, detail: statValue(v) })),
        )
      : [];

  const sevColor = (s: string) => (s === "hard" ? t.bad : s === "warn" ? t.warn : t.info);

  return (
    <div>
      {/* breadcrumb / status row */}
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:20px",
        )}
      >
        <div>
          <div
            style={css(
              "font-family:var(--mono);font-size:11px;letter-spacing:.06em;color:var(--dim);margin-bottom:8px",
            )}
          >
            INBOX / CHAPTER {chapter?.chapter_no ?? "?"} · {chapter?.pov ?? "—"} / SCENE{" "}
            {cur.scene_no}
          </div>
          <div style={css("display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap")}>
            <h1
              style={css(
                "margin:0;font-family:var(--display);font-weight:600;font-size:28px;letter-spacing:.01em;color:var(--ink)",
              )}
            >
              {sceneLabel(cur)}
            </h1>
            <span
              style={css(
                `display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;text-transform:uppercase;color:${t.warn};background:color-mix(in srgb,${t.warn} 14%,transparent);border:1px solid color-mix(in srgb,${t.warn} 40%,transparent);border-radius:999px;padding:4px 11px`,
              )}
            >
              ● {cur.status.replace(/_/g, " ")}
            </span>
          </div>
        </div>
        <div
          style={css(
            "display:flex;align-items:center;gap:18px;font-family:var(--mono);font-size:12px;color:var(--dim)",
          )}
        >
          <div style={css("display:flex;align-items:center;gap:8px")}>
            <button
              onClick={desk.prevScene}
              title="Previous (k)"
              style={css(
                "width:26px;height:26px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer",
              )}
            >
              ‹
            </button>
            <span>{focused ? "out of queue" : `${idx + 1} / ${pending.length}`}</span>
            <button
              onClick={desk.nextScene}
              title="Next (j)"
              style={css(
                "width:26px;height:26px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer",
              )}
            >
              ›
            </button>
          </div>
          <span style={css("opacity:.4")}>·</span>
          <span>
            <b style={css("color:var(--ink)")}>{wordCount(cur.prose)}</b> words
          </span>
          <span style={css("opacity:.4")}>·</span>
          <span
            onClick={() => router.push(`/diff/${cur.id}`)}
            style={css(
              "cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft)",
            )}
          >
            v{cur.version} · compare ▾
          </span>
          <span style={css("opacity:.4")}>·</span>
          <span
            onClick={() =>
              downloadMarkdown(
                sceneMarkdownFilename(cur, chapter),
                buildSceneMarkdown(cur, chapter, annotations, suggestions),
              )
            }
            title="Download this scene + reviewer feedback as Markdown"
            style={css(
              "cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft)",
            )}
          >
            ⬇ Markdown
          </span>
        </div>
      </div>

      {focused && cur.status !== "pending_review" && (
        <div
          style={css(
            `display:flex;align-items:center;gap:10px;margin-bottom:16px;padding:10px 14px;border-radius:9px;border:1px solid ${t.warn};background:color-mix(in srgb,${t.warn} 12%,transparent);font-size:13px;color:var(--ink);line-height:1.5`,
          )}
        >
          <span
            style={css(
              "font-family:var(--mono);font-size:10.5px;text-transform:uppercase;color:var(--warn)",
            )}
          >
            {cur.status.replace(/_/g, " ")}
          </span>
          <span>
            You're editing an already-decided scene. Switch to <b>Editing</b> to change the prose —{" "}
            <b>Approve</b> saves your changes; <b>Request revision</b> re-drafts it. Use the queue
            arrows to return to the review queue.
          </span>
        </div>
      )}

      {restored && (
        <div
          style={css(
            `display:flex;align-items:center;gap:10px;margin-bottom:16px;padding:10px 14px;border-radius:9px;border:1px solid ${t.info};background:color-mix(in srgb,${t.info} 12%,transparent);font-size:13px;color:var(--ink);line-height:1.5`,
          )}
        >
          <span
            style={css(
              "font-family:var(--mono);font-size:10.5px;text-transform:uppercase;color:var(--info)",
            )}
          >
            recovered
          </span>
          <span>
            Restored unsaved edits to this scene from a previous session. <b>Approve</b> to keep
            them, or
          </span>
          <button
            onClick={discardDraft}
            style={css(
              "margin-left:auto;padding:5px 11px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);font-size:12px;cursor:pointer;font-family:var(--ui)",
            )}
          >
            Discard edits
          </button>
        </div>
      )}

      <div
        style={css(
          `display:grid;grid-template-columns:${wideRead ? "minmax(0,1fr)" : "minmax(0,1fr) 388px"};gap:22px;align-items:start`,
        )}
      >
        {/* ── PROSE COLUMN ── */}
        <section
          style={css(
            "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow)",
          )}
        >
          <div
            style={css(
              "display:flex;align-items:center;justify-content:space-between;padding:11px 14px;border-bottom:1px solid var(--line);background:var(--bg2b);border-radius:var(--r) var(--r) 0 0",
            )}
          >
            <div
              style={css(
                "display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px",
              )}
            >
              {modeList.map((m) => {
                const active = desk.mode === m.id;
                return (
                  <button
                    key={m.id}
                    onClick={() => desk.setMode(m.id)}
                    style={css(
                      `padding:5px 12px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12.5px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`,
                    )}
                  >
                    {m.label}
                  </button>
                );
              })}
            </div>
            <div style={css("display:flex;align-items:center;gap:12px")}>
              {!editing && (
                <div
                  style={css(
                    "display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px",
                  )}
                  title="Reading layout"
                >
                  {SCENE_LAYOUTS.map((l) => {
                    const active = layout === l.id;
                    return (
                      <button
                        key={l.id}
                        onClick={() => setLayout(l.id)}
                        style={css(
                          `padding:5px 11px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`,
                        )}
                      >
                        {l.label}
                      </button>
                    );
                  })}
                </div>
              )}
              <div style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                {editing ? (
                  "editing — your text becomes canonical on approve"
                ) : suggesting ? (
                  <span style={css("color:var(--accent)")}>
                    {pendingSugg} open suggestion{pendingSugg === 1 ? "" : "s"}
                  </span>
                ) : (
                  "hover a name for canon"
                )}
              </div>
            </div>
          </div>

          {editing ? (
            <div style={css("padding:24px 30px")}>
              <textarea
                onChange={(e) => desk.setProse(e.target.value)}
                value={desk.rawProse}
                spellCheck
                style={css(
                  "width:100%;min-height:52vh;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:18px;font-family:var(--mono);font-size:13.5px;line-height:1.75;resize:vertical",
                )}
              />
            </div>
          ) : (
            <div style={css("display:flex;flex-wrap:wrap;gap:30px;padding:34px 32px 14px 42px")}>
              <div ref={proseRef} onMouseUp={onProseMouseUp} style={css(proseColStyle)}>
                {!editing && (
                  <div
                    style={css(
                      "font-family:var(--mono);font-size:10px;color:var(--dim);margin-bottom:10px;opacity:.8",
                    )}
                  >
                    Select any text to add a note or a tracked change.
                  </div>
                )}
                <div style={css(blocksWrapStyle)}>
                  {blocks.length ? blocks : <p style={css("color:var(--dim)")}>No prose.</p>}
                </div>
              </div>

              <div
                style={css(
                  "flex:0 1 244px;display:flex;flex-direction:column;gap:11px;padding-top:2px",
                )}
              >
                {suggesting && (
                  <div style={css("display:flex;flex-direction:column;gap:9px;margin-bottom:6px")}>
                    <div
                      style={css("display:flex;align-items:center;justify-content:space-between")}
                    >
                      <span
                        style={css(
                          "font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)",
                        )}
                      >
                        Suggestions
                      </span>
                      <button
                        onClick={addSuggestion}
                        style={css(
                          "background:none;border:none;color:var(--accent);font-size:11px;cursor:pointer;font-family:var(--ui)",
                        )}
                      >
                        + add
                      </button>
                    </div>
                    {suggestions.length === 0 && (
                      <p style={css("margin:0;font-size:11.5px;color:var(--dim);line-height:1.5")}>
                        No tracked changes yet. Add one, or switch to Editing to revise directly.
                      </p>
                    )}
                    {suggestions.map((g) => (
                      <div
                        key={g.id}
                        style={css(
                          `background:var(--bg2);border:1px solid ${g.status === "accepted" ? "color-mix(in srgb,var(--good) 42%,var(--line))" : g.status === "rejected" ? "var(--line)" : "var(--accentLine)"};border-radius:9px;padding:11px 12px`,
                        )}
                      >
                        <div
                          style={css(
                            "display:flex;align-items:center;justify-content:space-between;margin-bottom:6px",
                          )}
                        >
                          <span
                            style={css("font-family:var(--mono);font-size:9.5px;color:var(--dim)")}
                          >
                            {g.author ?? "—"}
                          </span>
                          <button
                            onClick={() => data.deleteSuggestion(g.id)}
                            title="remove this suggestion"
                            style={css(
                              "background:none;border:none;color:var(--dim);font-size:13px;cursor:pointer;line-height:1",
                            )}
                          >
                            ×
                          </button>
                        </div>
                        <div style={css("font-size:12.5px;line-height:1.4;margin-bottom:7px")}>
                          <span style={css("text-decoration:line-through;color:var(--bad)")}>
                            {g.quote}
                          </span>{" "}
                          <span style={css("color:var(--good)")}>
                            {g.new_text?.trim() || "(delete)"}
                          </span>
                        </div>
                        {g.why && (
                          <div
                            style={css(
                              "font-size:11px;color:var(--dim);font-style:italic;margin-bottom:9px",
                            )}
                          >
                            {g.why}
                          </div>
                        )}
                        {g.status === "pending" && (
                          <div style={css("display:flex;gap:6px")}>
                            <button
                              onClick={() => data.decideSuggestion(g.id, "accepted")}
                              style={css(
                                "flex:1;padding:6px;border-radius:6px;border:1px solid color-mix(in srgb,var(--good) 45%,var(--line));background:color-mix(in srgb,var(--good) 12%,var(--bg3));color:var(--good);font-size:11px;cursor:pointer;font-family:var(--ui)",
                              )}
                            >
                              Accept
                            </button>
                            <button
                              onClick={() => data.decideSuggestion(g.id, "rejected")}
                              style={css(
                                "flex:1;padding:6px;border-radius:6px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:11px;cursor:pointer;font-family:var(--ui)",
                              )}
                            >
                              Reject
                            </button>
                          </div>
                        )}
                        {g.status !== "pending" && (
                          <div
                            style={css(
                              "display:flex;align-items:center;justify-content:space-between",
                            )}
                          >
                            <span
                              style={css(
                                `font-family:var(--mono);font-size:10px;color:${g.status === "accepted" ? "var(--good)" : "var(--dim)"}`,
                              )}
                            >
                              {g.status === "accepted" ? "✓ accepted" : "rejected"}
                            </span>
                            <button
                              onClick={() => data.decideSuggestion(g.id, "pending")}
                              style={css(
                                "background:none;border:none;color:var(--dim);font-size:10.5px;cursor:pointer",
                              )}
                            >
                              undo
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <div style={css("display:flex;align-items:center;justify-content:space-between")}>
                  <span
                    style={css(
                      "font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)",
                    )}
                  >
                    Margin notes
                  </span>
                  <button
                    onClick={addNote}
                    style={css(
                      "background:none;border:none;color:var(--accent);font-size:11px;cursor:pointer;font-family:var(--ui)",
                    )}
                  >
                    + note
                  </button>
                </div>
                {annotations.length === 0 && (
                  <p style={css("margin:0;font-size:11.5px;color:var(--dim);line-height:1.5")}>
                    No notes. Add one, or click a name in the prose.
                  </p>
                )}
                {annotations.map((a) => {
                  const sel = desk.selectedAnn === a.id;
                  return (
                    <div
                      key={a.id}
                      onClick={() => desk.highlightAnn(a.id)}
                      style={css(
                        `background:${sel ? "var(--accentSoft)" : "var(--bg2)"};border:1px solid ${sel ? "var(--accentLine)" : "var(--line)"};border-radius:9px;padding:11px 13px;cursor:pointer`,
                      )}
                    >
                      {a.quote && (
                        <div
                          style={css(
                            "font-family:var(--prose);font-size:12.5px;color:var(--accent);font-style:italic;margin-bottom:6px",
                          )}
                        >
                          "{a.quote}"
                        </div>
                      )}
                      <p
                        style={css(
                          "margin:0 0 6px;font-size:12px;line-height:1.5;color:var(--ink)",
                        )}
                      >
                        {a.note}
                      </p>
                      <div
                        style={css("display:flex;align-items:center;justify-content:space-between")}
                      >
                        <span
                          style={css("font-family:var(--mono);font-size:9.5px;color:var(--dim)")}
                        >
                          — {a.author ?? "you"}
                        </span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            data.deleteAnnotation(a.id);
                          }}
                          style={css(
                            "background:none;border:none;color:var(--dim);font-size:10.5px;cursor:pointer",
                          )}
                        >
                          delete
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* decision footer */}
          <div
            style={css(
              "border-top:1px solid var(--line);padding:18px;background:var(--bg2b);border-radius:0 0 var(--r) var(--r)",
            )}
          >
            <textarea
              onChange={(e) => desk.setFeedback(e.target.value)}
              value={desk.feedback}
              placeholder="Revision notes for the drafter (used when you request a revision)…"
              style={css(
                "width:100%;min-height:58px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:12px 14px;font-size:13.5px;line-height:1.6;resize:vertical;margin-bottom:12px",
              )}
            />
            <div style={css("display:flex;gap:10px;align-items:center")}>
              <button
                disabled={committing}
                onClick={() => commit("approve")}
                style={css(
                  "flex:1;padding:12px;border-radius:8px;border:1px solid color-mix(in srgb,var(--good) 50%,var(--line));background:color-mix(in srgb,var(--good) 13%,var(--bg3));color:var(--good);font-size:14px;font-weight:500;cursor:pointer",
                )}
              >
                Approve
              </button>
              <button
                disabled={committing}
                onClick={() => commit("revise")}
                style={css(
                  "flex:1;padding:12px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:14px;cursor:pointer",
                )}
              >
                Request revision
              </button>
              <button
                disabled={committing}
                onClick={() => commit("deny")}
                style={css(
                  "flex:none;padding:12px 16px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:14px;cursor:pointer",
                )}
              >
                Reject
              </button>
            </div>
          </div>
        </section>

        {/* ── REVIEW RAIL ── (drops below the prose, full-width-but-capped, in wide/two-column) */}
        <aside
          style={css(
            `${wideRead ? "" : "position:sticky;top:84px;"}display:flex;flex-direction:column;gap:16px${wideRead ? ";width:100%;max-width:760px;margin:0 auto" : ""}`,
          )}
        >
          <div
            style={css(
              "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px",
            )}
          >
            <div
              style={css(
                "display:flex;justify-content:space-between;font-family:var(--mono);font-size:11.5px;color:var(--dim);line-height:2.1",
              )}
            >
              <span>model</span>
              <span style={css("color:var(--ink)")}>{cur.model ?? "—"}</span>
            </div>
            <div
              style={css(
                "display:flex;justify-content:space-between;font-family:var(--mono);font-size:11.5px;color:var(--dim);line-height:2.1",
              )}
            >
              <span>source</span>
              <span style={css("color:var(--ink)")}>{cur.prose_source}</span>
            </div>
            <div style={css("height:1px;background:var(--line);margin:11px 0")} />
            <div
              style={css(
                "font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin-bottom:9px",
              )}
            >
              Passes run
            </div>
            <div style={css("display:flex;flex-wrap:wrap;gap:6px")}>
              {passes.length ? (
                passes.map((p) => (
                  <span
                    key={p}
                    style={css(
                      "font-family:var(--mono);font-size:11px;color:var(--ink);background:var(--bg3);border:1px solid var(--line);border-radius:999px;padding:3px 9px",
                    )}
                  >
                    {p}
                  </span>
                ))
              ) : (
                <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                  —
                </span>
              )}
            </div>
            <div style={css("height:1px;background:var(--line);margin:12px 0")} />
            <button
              onClick={() => data.setExemplar(!cur.is_exemplar)}
              title={`Few-shot future ${chapter?.pov ?? "POV"} drafts on this scene's prose so the voice matches yours`}
              style={css(
                `width:100%;display:flex;align-items:center;justify-content:center;gap:7px;padding:8px;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12px;border:1px solid ${cur.is_exemplar ? "color-mix(in srgb,var(--accent) 50%,var(--line))" : "var(--line)"};background:${cur.is_exemplar ? "var(--accentSoft)" : "var(--bg3)"};color:${cur.is_exemplar ? "var(--accent)" : "var(--dim)"}`,
              )}
            >
              {cur.is_exemplar
                ? "★ Voice exemplar — drafts learn from this"
                : "☆ Use as voice exemplar"}
            </button>
          </div>

          <div
            style={css(
              "display:flex;gap:2px;padding:3px;background:var(--bg3);border:1px solid var(--line);border-radius:999px",
            )}
          >
            {tabDefs.map((tb) => {
              const active = desk.tab === tb.id;
              return (
                <button
                  key={tb.id}
                  onClick={() => desk.setTab(tb.id)}
                  style={css(
                    `flex:1;padding:7px;border:none;border-radius:999px;cursor:pointer;font-family:var(--ui);font-size:12.5px;background:${active ? "var(--bg2)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-weight:${active ? "500" : "400"}`,
                  )}
                >
                  {tb.label}
                  {tb.badge && (
                    <span
                      style={css(
                        `margin-left:6px;font-family:var(--mono);font-size:10px;padding:0 5px;border-radius:999px;background:${t.bad};color:#fff`,
                      )}
                    >
                      {tb.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {desk.tab === "continuity" && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <p style={css("margin:0;font-size:12.5px;color:var(--dim);line-height:1.55")}>
                Advisory — nothing is blocked. You decide which source is canon; resolving updates
                the world ledger or queues a prose fix.
              </p>
              {conflicts.length === 0 && (
                <p style={css("margin:0;font-family:var(--mono);font-size:12px;color:var(--good)")}>
                  ✓ no continuity flags
                </p>
              )}
              {conflicts.map((c) => (
                <div
                  key={c.id}
                  style={css(
                    "background:var(--bg2);border:1px solid color-mix(in srgb,var(--bad) 32%,var(--line));border-radius:10px;padding:14px",
                  )}
                >
                  <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:9px")}>
                    <span
                      style={css("width:6px;height:6px;border-radius:50%;background:var(--bad)")}
                    />
                    <span
                      style={css(
                        "font-family:var(--mono);font-size:10.5px;text-transform:uppercase;color:var(--bad)",
                      )}
                    >
                      {pstr(c, "attribute") || c.reviewer}
                    </span>
                  </div>
                  {pstr(c, "context_sentence") && (
                    <p
                      style={css(
                        "margin:0 0 11px;font-size:13.5px;font-style:italic;line-height:1.5;color:var(--ink)",
                      )}
                    >
                      "{pstr(c, "context_sentence")}"
                    </p>
                  )}
                  <div style={css("display:flex;gap:8px;margin-bottom:11px")}>
                    <div
                      style={css(
                        "flex:1;padding:8px 10px;border-radius:7px;background:var(--bg3);border:1px solid var(--line)",
                      )}
                    >
                      <div
                        style={css(
                          "font-family:var(--mono);font-size:9.5px;text-transform:uppercase;color:var(--dim);margin-bottom:3px",
                        )}
                      >
                        Prose
                      </div>
                      <div style={css("font-family:var(--mono);font-size:13px;color:var(--ink)")}>
                        {pstr(c, "prose_value")}
                      </div>
                    </div>
                    <div
                      style={css(
                        "flex:1;padding:8px 10px;border-radius:7px;background:var(--bg3);border:1px solid var(--line)",
                      )}
                    >
                      <div
                        style={css(
                          "font-family:var(--mono);font-size:9.5px;text-transform:uppercase;color:var(--dim);margin-bottom:3px",
                        )}
                      >
                        Ledger
                      </div>
                      <div style={css("font-family:var(--mono);font-size:13px;color:var(--ink)")}>
                        {pstr(c, "ledger_value")}
                      </div>
                    </div>
                  </div>
                  <div style={css("display:flex;gap:7px")}>
                    <button
                      onClick={() =>
                        data.resolveContinuity(cur.id, { critique_id: c.id, choice: "use_prose" })
                      }
                      style={css(KEEP_BTN)}
                    >
                      Keep prose · fix ledger
                    </button>
                    <button
                      onClick={() =>
                        data.resolveContinuity(cur.id, { critique_id: c.id, choice: "use_ledger" })
                      }
                      style={css(KEEP_BTN)}
                    >
                      Keep ledger · fix prose
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {desk.tab === "notes" && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <p style={css("margin:0;font-size:12.5px;color:var(--dim);line-height:1.55")}>
                Advisory flags from the review passes.
              </p>
              {notes.length === 0 && (
                <p style={css("margin:0;font-family:var(--mono);font-size:12px;color:var(--dim)")}>
                  no reviewer notes
                </p>
              )}
              {notes.map((n) => (
                <div
                  key={n.id}
                  style={css(
                    `border-left:2px solid ${sevColor(n.severity)};background:var(--bg2);border-radius:0 7px 7px 0;padding:10px 13px`,
                  )}
                >
                  <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:5px")}>
                    <span style={css("font-family:var(--mono);font-size:11px;color:var(--ink)")}>
                      {n.reviewer}
                    </span>
                    <span
                      style={css(
                        `font-family:var(--mono);font-size:9.5px;text-transform:uppercase;color:${sevColor(n.severity)}`,
                      )}
                    >
                      {n.severity}
                    </span>
                  </div>
                  <p style={css("margin:0;font-size:13px;line-height:1.5;color:var(--dim)")}>
                    {n.note}
                  </p>
                </div>
              ))}
            </div>
          )}

          {desk.tab === "changes" && (
            <div style={css("display:flex;flex-direction:column;gap:9px")}>
              <p style={css("margin:0 0 3px;font-size:12.5px;color:var(--dim);line-height:1.55")}>
                Ledger deltas this scene's beat declares, committed on approval.
              </p>
              {deltas.length === 0 && (
                <p style={css("margin:0;font-family:var(--mono);font-size:12px;color:var(--dim)")}>
                  no declared deltas
                </p>
              )}
              {deltas.map((ch) => (
                <div
                  key={ch.label}
                  style={css(
                    "display:flex;align-items:center;gap:11px;padding:11px 13px;background:var(--bg2);border:1px solid var(--line);border-radius:8px",
                  )}
                >
                  <span style={css(`font-family:var(--mono);font-size:15px;color:${t.good}`)}>
                    ▲
                  </span>
                  <div style={css("min-width:0")}>
                    <div style={css("font-size:13px;color:var(--ink)")}>{ch.label}</div>
                    <div
                      style={css(
                        "font-family:var(--mono);font-size:11.5px;color:var(--dim);margin-top:2px",
                      )}
                    >
                      {ch.detail}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>

      {/* selection toolbar — appears over a highlighted passage in reading/suggesting mode */}
      {!editing && sel && !composer && (
        <div
          style={css(
            `position:fixed;left:${sel.x}px;top:${Math.max(sel.y - 46, 8)}px;transform:translateX(-50%);z-index:70;display:flex;gap:3px;background:var(--bg2);border:1px solid var(--line);border-radius:9px;box-shadow:var(--shadow);padding:4px`,
          )}
        >
          <button
            onClick={() => {
              setComposer({ kind: "note", quote: sel.text, x: sel.x, y: sel.y });
              setSel(null);
            }}
            style={css(
              "padding:5px 10px;border:none;border-radius:6px;background:transparent;color:var(--ink);font-size:12px;cursor:pointer;font-family:var(--ui)",
            )}
          >
            ＋ Note
          </button>
          <button
            onClick={() => {
              setComposer({ kind: "sugg", quote: sel.text, x: sel.x, y: sel.y });
              setSel(null);
            }}
            style={css(
              "padding:5px 10px;border:none;border-radius:6px;background:transparent;color:var(--accent);font-size:12px;cursor:pointer;font-family:var(--ui)",
            )}
          >
            ✎ Suggest
          </button>
        </div>
      )}

      {composer && (
        <MarkupComposer
          composer={composer}
          onCancel={() => setComposer(null)}
          onSave={saveComposer}
        />
      )}
    </div>
  );
}

// Inline composer for a margin note or a tracked-change suggestion, anchored near the selection.
function MarkupComposer({
  composer,
  onCancel,
  onSave,
}: {
  composer: { kind: "note" | "sugg"; quote: string; x: number; y: number };
  onCancel: () => void;
  onSave: (p: { quote: string; note: string; newText: string; why: string }) => void;
}) {
  const isNote = composer.kind === "note";
  const [quote, setQuote] = useState(composer.quote);
  const [note, setNote] = useState("");
  const [newText, setNewText] = useState("");
  const [why, setWhy] = useState("");
  const canSave = isNote ? note.trim().length > 0 : quote.trim().length > 0;

  const left = Math.min(Math.max(composer.x, 190), window.innerWidth - 190);
  // Anchor below the selection; the composer itself scrolls within the viewport (note vs. suggest
  // modes differ in height), so a short viewport never clips the action buttons.
  const top = Math.max(composer.y + 14, 20);
  const field =
    "width:100%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 10px;font-size:13px;font-family:var(--ui)";
  const lbl =
    "display:block;font-family:var(--mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin:0 0 4px";

  return (
    <>
      <div onClick={onCancel} style={css("position:fixed;inset:0;z-index:80")} />
      <div
        style={css(
          `position:fixed;left:${left}px;top:${top}px;transform:translateX(-50%);z-index:81;width:340px;max-height:calc(100vh - 40px);overflow-y:auto;background:var(--bg2);border:1px solid var(--accentLine);border-radius:12px;box-shadow:var(--shadow);padding:15px 16px`,
        )}
      >
        <div
          style={css(
            "font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin-bottom:11px",
          )}
        >
          {isNote ? "Margin note" : "Tracked change"}
        </div>

        <label style={css("display:block;margin-bottom:10px")}>
          <span style={css(lbl)}>
            {isNote ? "Anchor quote (optional)" : "Text to replace (must match the prose)"}
          </span>
          <input
            value={quote}
            onChange={(e) => setQuote(e.target.value)}
            placeholder={isNote ? "the exact words to pin to…" : "the exact words to change…"}
            style={css(field)}
          />
        </label>

        {isNote ? (
          <label style={css("display:block;margin-bottom:12px")}>
            <span style={css(lbl)}>Note</span>
            <textarea
              autoFocus
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="your note to self / the drafter…"
              style={css(field + ";min-height:70px;line-height:1.5;resize:vertical")}
            />
          </label>
        ) : (
          <>
            <label style={css("display:block;margin-bottom:10px")}>
              <span style={css(lbl)}>
                Replace with{" "}
                <span style={css("text-transform:none;letter-spacing:0")}>
                  (leave blank to delete)
                </span>
              </span>
              <textarea
                autoFocus
                value={newText}
                onChange={(e) => setNewText(e.target.value)}
                placeholder="the replacement text…"
                style={css(field + ";min-height:54px;line-height:1.5;resize:vertical")}
              />
            </label>
            <label style={css("display:block;margin-bottom:12px")}>
              <span style={css(lbl)}>Why (optional)</span>
              <input
                value={why}
                onChange={(e) => setWhy(e.target.value)}
                placeholder="reason for the change…"
                style={css(field)}
              />
            </label>
          </>
        )}

        <div style={css("display:flex;gap:9px;justify-content:flex-end")}>
          <button
            onClick={onCancel}
            style={css(
              "padding:7px 13px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-size:12.5px;cursor:pointer;font-family:var(--ui)",
            )}
          >
            Cancel
          </button>
          <button
            disabled={!canSave}
            onClick={() => onSave({ quote, note, newText, why })}
            style={css(
              `padding:7px 14px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:12.5px;cursor:${canSave ? "pointer" : "default"};opacity:${canSave ? "1" : ".5"};font-family:var(--ui)`,
            )}
          >
            {isNote ? "Add note" : "Add change"}
          </button>
        </div>
      </div>
    </>
  );
}
