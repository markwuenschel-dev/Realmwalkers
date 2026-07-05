"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import CanonCard from "../components/CanonCard";
import { ProseInline } from "../components/ProseBlocks";
import type { CardModel } from "../components/CanonCard";
import { seg, tokenize } from "../prose";
import type { Token } from "../prose";
import type { Marker } from "../types";
import { applyAcceptedSuggestions, sceneLabel, statValue, wordCount } from "../lib/format";
import { buildSceneMarkdown, downloadMarkdown, sceneMarkdownFilename } from "../lib/sceneMarkdown";
import { resolveAuthorName, useAuthorName } from "../lib/authorName";
import { severityChipTone, severityLabel, severityVar } from "../lib/severity";
import { api } from "../api/client";
import { Button, Chip, Eyebrow, Panel } from "../components/ui";
import type { ChipTone } from "../components/ui";
import type { CritiqueOut, DecisionKind, DraftAttemptOut, LengthStatus } from "../api/types";
import type { ExportKind } from "../lib/docx";

// Scene review status → Chip tone (the review lifecycle, not the StatusPill axes).
const SCENE_STATUS_TONE: Record<string, ChipTone> = {
  approved: "good",
  pending_review: "warn",
  revision_requested: "bad",
  draft: "info",
  superseded: "neutral",
};

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
  const data = useDeskData();
  const router = useRouter();
  // /scene/[sceneId] focuses a specific scene; bare /scene shows the pending review queue.
  const params = useParams<{ sceneId?: string }>();
  const focusSceneId = params.sceneId ?? null;
  const [committing, setCommitting] = useState(false);
  const [restarting, setRestarting] = useState(false); // manual re-queue for a stuck revision_requested scene
  const [stagesOpen, setStagesOpen] = useState(false); // draft-attempt provenance expander
  // selection toolbar + inline markup composer (replace the old window.prompt flows)
  const [sel, setSel] = useState<{ text: string; x: number; y: number } | null>(null);
  const [composer, setComposer] = useState<{
    kind: "note" | "sugg";
    quote: string;
    x: number;
    y: number;
  } | null>(null);
  const [restored, setRestored] = useState(false); // an unsaved hand-edit was recovered from localStorage
  const [exportingAs, setExportingAs] = useState<ExportKind | null>(null);
  // Shared with every other export surface (Manuscript, Inbox, Chapters, Packets) so it's typed once.
  const [author, saveAuthor] = useAuthorName();
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

  useEffect(() => {
    if (focused && focusSceneId && data.missingSceneId === focusSceneId) {
      router.replace("/");
    }
  }, [focused, focusSceneId, data.missingSceneId, router]);

  // Keep the raw queue index reconciled to the clamped position. Without this, approving a scene
  // (which shrinks the pending queue) or paging past the end leaves activeScene drifted ABOVE the
  // queue — idx pins to the last item and the › / j "next" appears dead until you page back down.
  useEffect(() => {
    if (pending.length && desk.activeScene !== idx) desk.syncActiveScene(idx);
  }, [pending.length, desk.activeScene, idx, desk.syncActiveScene]);

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

  // Manual escape hatch for a scene stuck in Revising (its auto-queued revision job failed, or one
  // was never queued) — re-queues a fresh draft job via the same contract-first redraft path the
  // Chapters board's bulk "Redraft" action uses (POST /chapters/{id}/scenes/redraft). Guarded by
  // restartBlockedByActiveJob below against firing while a job for this exact scene is already live.
  const handleRestart = async () => {
    if (!cur || restarting) return;
    setRestarting(true);
    try {
      await data.restartRedraft(cur.chapter_id, cur.id);
    } finally {
      setRestarting(false);
    }
  };

  const chapter = useMemo(
    () => data.chapters.find((c) => c.id === cur?.chapter_id) ?? null,
    [data.chapters, cur?.chapter_id],
  );

  // Chapter-order navigation: the ‹ › arrows page through THIS chapter's scenes by scene_no (not the
  // pending-review queue), so you can advance through the whole chapter even when only one scene is
  // pending review. Falls back to the old queue nav only when the loaded scene has no chapter context.
  const chapterScenes = useMemo(
    () =>
      (data.latestScenes ?? [])
        .filter((s) => s.chapter_id === cur?.chapter_id)
        .sort((a, b) => a.scene_no - b.scene_no),
    [data.latestScenes, cur?.chapter_id],
  );
  const chapterPos = cur ? chapterScenes.findIndex((s) => s.id === cur.id) : -1;
  const goPrevScene = () => {
    if (chapterPos > 0) desk.openSceneId(chapterScenes[chapterPos - 1].id);
    else if (chapterPos < 0) desk.prevScene();
  };
  const goNextScene = () => {
    if (chapterPos >= 0 && chapterPos < chapterScenes.length - 1)
      desk.openSceneId(chapterScenes[chapterPos + 1].id);
    else if (chapterPos < 0) desk.nextScene();
  };

  const editing = desk.mode === "editing";
  const suggesting = desk.mode === "suggesting";
  const showMarks = !editing;

  const critiques = useMemo(() => cur?.critiques ?? [], [cur]);
  const conflicts = useMemo(() => critiques.filter(isConflict), [critiques]);
  const notes = critiques.filter((c) => !isConflict(c));
  // Continuity conflicts are STORED severity=block (reserved for the deterministic hard-number
  // check), but this review surface is explicitly non-gating ("advisory — nothing is blocked"):
  // resolving one is fixable repair work, so it wears the repair tone here. Deliberate — do NOT
  // "clean this up" to severityVar(c.severity); that would re-paint advisories as hard blockers.
  const conflictVar = severityVar("repair");
  const annotations = data.annotations;
  const suggestions = data.suggestions;
  const pendingSugg = suggestions.filter((s) => s.status === "pending").length;

  // Markers for a paragraph: entity names, conflict prose-values, annotation quotes, suggestion quotes.
  // useCallback so the tokenization memo below only recomputes when a marker SOURCE actually changes.
  const markersFor = useCallback(
    (text: string): Marker[] => {
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
    },
    [data.characters, conflicts, suggestions, annotations],
  );

  // The expensive part of the reader — split the prose and scan every paragraph for markers
  // (O(paragraphs × entities)) — depends only on prose + marker sources, NOT on selection/hover/mode.
  // Memoize it so dragging a selection or toggling a mode no longer re-tokenizes the whole scene; the
  // cheap per-token rendering (renderToken) still runs each render for live hover/mode styling.
  const tokenizedParas = useMemo(() => {
    if (!cur) return [];
    return seg(cur.prose ?? "").map((b) => {
      const isLead = b.n === 0;
      const lead = isLead ? b.text.charAt(0) : "";
      const text = isLead ? b.text.slice(1) : b.text;
      return { isLead, lead, tokens: tokenize(text, markersFor(text)) };
    });
  }, [cur, markersFor]);

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

  // ── empty / missing scene ──────────────────────────────────────────────────────────────────────
  if (focused && focusSceneId && data.missingSceneId === focusSceneId) {
    return (
      <div style={css("max-width:560px;margin:60px auto;text-align:center")}>
        <h1
          style={css(
            "margin:0 0 10px;font-family:var(--display);font-weight:500;font-size:26px;letter-spacing:-.01em;color:var(--ink)",
          )}
        >
          Scene deleted or unavailable
        </h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px;line-height:1.6")}>
          This scene was deleted or is no longer reachable.
        </p>
        <Button style="margin-top:18px" onClick={() => router.push("/")}>
          Go to inbox
        </Button>
      </div>
    );
  }

  if (!cur) {
    return (
      <div style={css("max-width:560px;margin:60px auto;text-align:center")}>
        <h1
          style={css(
            "margin:0 0 10px;font-family:var(--display);font-weight:500;font-size:26px;letter-spacing:-.01em;color:var(--ink)",
          )}
        >
          Nothing to review
        </h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px;line-height:1.6")}>
          {data.loadingScene
            ? "Loading scene…"
            : data.jobs.running
              ? "A scene is drafting — it'll land here shortly."
              : "Plan a chapter from the Inbox and approve its beats; drafted scenes show up here for review."}
        </p>
        <Button style="margin-top:18px" onClick={() => router.push("/inbox")}>
          Go to inbox
        </Button>
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

  const renderToken = (tok: Token, key: string): ReactNode => {
    if (tok.kind === "text")
      return (
        <span key={key} style={css("color:inherit")}>
          <ProseInline text={tok.text} />
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
            ? `border-bottom:1px dotted var(${conflictVar});background:color-mix(in srgb,var(${conflictVar}) 9%,transparent);border-radius:2px;cursor:help;color:inherit`
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
  const proseFontSize = layout === "columns" ? "16.5px" : "17px";
  const proseColStyle =
    layout === "page"
      ? "flex:1 1 380px;min-width:330px"
      : layout === "wide"
        ? "flex:1 1 100%;min-width:0;max-width:54rem;margin:0 auto"
        : "flex:1 1 100%;min-width:0";
  const blocksWrapStyle = layout === "columns" ? "column-count:2;column-gap:2.8rem" : "";

  let pkey = 0;
  const blocks = tokenizedParas.map(({ isLead, lead, tokens }, bi) => {
    const leadStyle = isLead
      ? desk.isLight
        ? "float:left;font-family:var(--display);font-size:60px;line-height:.74;padding:9px 12px 0 0;color:var(--accent)"
        : "font:inherit;color:inherit"
      : "";
    const parts = tokens.map((tok) => renderToken(tok, "tk" + pkey++));
    return (
      <p
        key={`b${bi}`}
        style={css(
          `font-family:var(--prose);font-size:${proseFontSize};line-height:1.75;color:var(--ink);margin:0 0 1.2em;break-inside:avoid-column`,
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
  // Badge tones follow what the count means: conflicts are repair-tier work (see conflictVar);
  // notes are only worth an amber nudge when any is above info. Never the old hardcoded alarm-red.
  const tabDefs: {
    id: "continuity" | "notes" | "changes";
    label: string;
    badge: string | null;
    badgeVar: string;
  }[] = [
    {
      id: "continuity",
      label: "Continuity",
      badge: conflicts.length ? String(conflicts.length) : null,
      badgeVar: conflictVar,
    },
    {
      id: "notes",
      label: "Notes",
      badge: notes.length ? String(notes.length) : null,
      badgeVar: notes.some((n) => severityChipTone(n.severity) !== "info") ? "--warn" : "--dim",
    },
    { id: "changes", label: "Changes", badge: null, badgeVar: "--dim" },
  ];

  const deltas =
    cur && data.activeBeat?.expected_state_changes
      ? Object.entries(data.activeBeat.expected_state_changes).flatMap(([who, attrs]) =>
          Object.entries(attrs as Record<string, unknown>).map(([k, v]) => ({
            label: `${who} · ${k}`,
            detail: statValue(v),
          })),
        )
      : [];

  const sevColor = (s: string) => `var(${severityVar(s, "--info")})`;

  // Best-effort guard: hide/disable Restart while a job for this exact scene is the one actively
  // drafting right now, so a click can't queue a redundant concurrent redraft of the same original
  // scene (a stale click is still safe otherwise — the redraft endpoint dedupes repeat DRAFT jobs).
  const restartBlockedByActiveJob =
    data.jobs.active_scene != null &&
    chapter != null &&
    data.jobs.active_scene.chapter_no === chapter.chapter_no &&
    data.jobs.active_scene.scene_no === cur.scene_no;

  // Manuscript-style single-scene export: the same Markdown / Reader-DOCX / Shunn-DOCX builders the
  // Manuscript tab uses, wrapping just this scene as a one-chapter ManuscriptOut — so the output is
  // byte-for-byte the same format (fonts, structure, front matter) no matter which screen produced it.
  // The "⬇ Markdown" link above bundles reviewer feedback too; these are the plain manuscript exports.
  const sceneChapterInput = () => [
    {
      chapter_no: chapter?.chapter_no ?? 0,
      title: chapter?.title ?? null,
      pov: chapter?.pov ?? "",
      scenes: [{ scene_no: cur.scene_no, prose: cur.prose }],
    },
  ];
  const sceneExportStem = `scene_ch${chapter?.chapter_no ?? "x"}_s${cur.scene_no}_v${cur.version}`;

  const exportSceneMarkdown = async () => {
    setExportingAs("md");
    try {
      const exp = await import("../lib/docx");
      const ms = exp.buildManuscriptFrom(sceneLabel(cur), sceneChapterInput());
      exp.saveMarkdown(exp.buildManuscriptMarkdown(ms), exp.markdownFilename(sceneExportStem));
    } finally {
      setExportingAs(null);
    }
  };

  const exportSceneDocx = async () => {
    setExportingAs("docx");
    try {
      const exp = await import("../lib/docx");
      const ms = exp.buildManuscriptFrom(sceneLabel(cur), sceneChapterInput());
      await exp.saveDocx(
        exp.buildManuscriptDoc(ms, `Chapter ${chapter?.chapter_no ?? "?"} · Scene ${cur.scene_no}`),
        exp.docxFilename(sceneExportStem),
      );
    } finally {
      setExportingAs(null);
    }
  };

  const exportSceneShunn = async () => {
    setExportingAs("shunn");
    try {
      const exp = await import("../lib/docx");
      const ms = exp.buildManuscriptFrom(sceneLabel(cur), sceneChapterInput());
      const name = resolveAuthorName(author, saveAuthor);
      if (!name) return;
      await exp.saveDocx(
        exp.buildShunnDoc(ms, name, exp.manuscriptWordCount(ms)),
        exp.docxFilename(`${sceneExportStem}_shunn`),
      );
    } finally {
      setExportingAs(null);
    }
  };

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
                "margin:0;font-family:var(--display);font-weight:500;font-size:28px;line-height:36px;letter-spacing:-.01em;color:var(--ink)",
              )}
            >
              {sceneLabel(cur)}
            </h1>
            <Chip
              label={cur.status.replace(/_/g, " ")}
              tone={SCENE_STATUS_TONE[cur.status] ?? "neutral"}
            />
            <LengthBadge status={cur.length_status} wordCount={cur.word_count} />
          </div>
        </div>
        <div
          style={css(
            "display:flex;align-items:center;flex-wrap:wrap;row-gap:8px;gap:18px;font-family:var(--mono);font-size:12px;color:var(--dim)",
          )}
        >
          <div style={css("display:flex;align-items:center;gap:8px")}>
            <button
              onClick={goPrevScene}
              title="Previous scene"
              disabled={chapterPos === 0}
              style={css(
                `width:26px;height:26px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer${chapterPos === 0 ? ";opacity:.4;cursor:default" : ""}`,
              )}
            >
              ‹
            </button>
            <span>
              {chapterPos >= 0
                ? `Scene ${cur.scene_no} · ${chapterPos + 1}/${chapterScenes.length}`
                : focused
                  ? "out of queue"
                  : `${idx + 1} / ${pending.length}`}
            </span>
            <button
              onClick={goNextScene}
              title="Next scene"
              disabled={chapterPos >= 0 && chapterPos === chapterScenes.length - 1}
              style={css(
                `width:26px;height:26px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer${chapterPos >= 0 && chapterPos === chapterScenes.length - 1 ? ";opacity:.4;cursor:default" : ""}`,
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
            onClick={() => setStagesOpen((v) => !v)}
            title="Preserved prose stages: raw draft → enrichment → length guard → final"
            style={css(
              "cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft)",
            )}
          >
            stages {stagesOpen ? "▴" : "▾"}
          </span>
          <span style={css("opacity:.4")}>·</span>
          <span
            onClick={() => router.push(`/packets?chapter=${cur.chapter_id}&scene=${cur.scene_no}`)}
            title="Open this scene's contract on the Packets tab"
            style={css(
              "cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft)",
            )}
          >
            Scene packet →
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
            ⬇ Markdown + feedback
          </span>
          <span style={css("opacity:.4")}>·</span>
          <span
            onClick={() => void exportSceneMarkdown()}
            title="Semantic Markdown — same format the Manuscript tab exports"
            style={css(
              `cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft);opacity:${exportingAs ? 0.6 : 1}`,
            )}
          >
            ⬇ {exportingAs === "md" ? "Exporting…" : "Export Markdown"}
          </span>
          <span style={css("opacity:.4")}>·</span>
          <span
            onClick={() => void exportSceneDocx()}
            title="Reader DOCX — styled book format, same as the Manuscript tab"
            style={css(
              `cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft);opacity:${exportingAs ? 0.6 : 1}`,
            )}
          >
            ⬇ {exportingAs === "docx" ? "Exporting…" : "Export Reader DOCX"}
          </span>
          <span style={css("opacity:.4")}>·</span>
          <span
            onClick={() => void exportSceneShunn()}
            title="Shunn DOCX — plain submission format, same as the Manuscript tab"
            style={css(
              `cursor:pointer;color:var(--accent);border-bottom:1px solid var(--accentSoft);opacity:${exportingAs ? 0.6 : 1}`,
            )}
          >
            ⬇ {exportingAs === "shunn" ? "Exporting…" : "Export Shunn DOCX"}
          </span>
        </div>
      </div>

      {stagesOpen && <StagesPanel sceneId={cur.id} />}

      {focused && cur.status !== "pending_review" && (
        <div
          style={css(
            "display:flex;align-items:center;gap:10px;margin-bottom:16px;padding:10px 14px;border-radius:9px;border:1px solid color-mix(in srgb,var(--warn) 45%,var(--line));background:color-mix(in srgb,var(--warn) 12%,transparent);font-size:13px;color:var(--ink);line-height:1.5",
          )}
        >
          <span
            style={css(
              "font-family:var(--mono);font-size:10.5px;text-transform:uppercase;color:var(--warn)",
            )}
          >
            {cur.status.replace(/_/g, " ")}
          </span>
          {cur.status === "revision_requested" ? (
            <span>
              Waiting to redraft against your feedback. If it's been stuck a while (a failed or
              missed job), <b>Restart</b> re-queues drafting now.
              {data.jobs.queue_paused
                ? " The queue is paused, so a restarted job waits until you resume."
                : ""}
            </span>
          ) : (
            <span>
              You're editing an already-decided scene. Switch to <b>Editing</b> to change the prose
              — <b>Approve</b> saves your changes; <b>Request revision</b> re-drafts it. Use the
              queue arrows to return to the review queue.
            </span>
          )}
          {cur.status === "revision_requested" && (
            <Button
              size="sm"
              style="margin-left:auto;flex:none"
              disabled={restarting || restartBlockedByActiveJob}
              onClick={() => void handleRestart()}
              title={
                restartBlockedByActiveJob
                  ? "Already drafting — wait for it to finish"
                  : data.jobs.queue_paused
                    ? "Queue is paused — Restart queues the redraft; drafting starts when you resume"
                    : "Re-queue drafting for this scene now"
              }
            >
              {restarting ? "Restarting…" : "Restart"}
            </Button>
          )}
        </div>
      )}

      {restored && (
        <div
          style={css(
            "display:flex;align-items:center;gap:10px;margin-bottom:16px;padding:10px 14px;border-radius:9px;border:1px solid color-mix(in srgb,var(--info) 45%,var(--line));background:color-mix(in srgb,var(--info) 12%,transparent);font-size:13px;color:var(--ink);line-height:1.5",
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
          <Button size="sm" style="margin-left:auto" onClick={discardDraft}>
            Discard edits
          </Button>
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
              <Button
                variant="primary"
                disabled={committing}
                onClick={() => void commit("approve")}
                style="flex:1;height:40px;font-size:13.5px"
              >
                Approve
              </Button>
              <Button
                variant="secondary"
                disabled={committing}
                onClick={() => void commit("revise")}
                style="flex:1;height:40px;font-size:13.5px;color:var(--accent2)"
              >
                Request revision
              </Button>
              <Button
                variant="danger"
                disabled={committing}
                onClick={() => void commit("deny")}
                style="flex:none;height:40px;font-size:13.5px;padding:0 16px"
              >
                Reject
              </Button>
            </div>
          </div>
        </section>

        {/* ── REVIEW RAIL ── (drops below the prose, full-width-but-capped, in wide/two-column) */}
        <aside
          style={css(
            `${wideRead ? "" : "position:sticky;top:84px;"}display:flex;flex-direction:column;gap:16px${wideRead ? ";width:100%;max-width:760px;margin:0 auto" : ""}`,
          )}
        >
          <Panel eyebrow="Provenance" pad="16px 18px">
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
            <Eyebrow style="margin-bottom:9px">Passes run</Eyebrow>
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
          </Panel>

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
                        `margin-left:6px;font-family:var(--mono);font-size:10px;padding:0 5px;border-radius:999px;background:color-mix(in srgb,var(${tb.badgeVar}) 18%,var(--bg3));color:var(${tb.badgeVar})`,
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
            <Panel eyebrow="Continuity" pad="16px 18px">
              <div style={css("display:flex;flex-direction:column;gap:12px")}>
                <p style={css("margin:0;font-size:12.5px;color:var(--dim);line-height:1.55")}>
                  Advisory — nothing is blocked. You decide which source is canon; resolving updates
                  the world ledger or queues a prose fix.
                </p>
                {conflicts.length === 0 && (
                  <p
                    style={css("margin:0;font-family:var(--mono);font-size:12px;color:var(--good)")}
                  >
                    ✓ no continuity flags
                  </p>
                )}
                {conflicts.map((c) => (
                  <div
                    key={c.id}
                    style={css(
                      `background:var(--bg2);border:1px solid color-mix(in srgb,var(${conflictVar}) 32%,var(--line));border-radius:10px;padding:14px`,
                    )}
                  >
                    <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:9px")}>
                      <span
                        style={css(
                          `width:6px;height:6px;border-radius:50%;background:var(${conflictVar})`,
                        )}
                      />
                      <span
                        style={css(
                          `font-family:var(--mono);font-size:10.5px;text-transform:uppercase;color:var(${conflictVar})`,
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
                      <Button
                        size="sm"
                        style="flex:1"
                        onClick={() =>
                          data.resolveContinuity(cur.id, { critique_id: c.id, choice: "use_prose" })
                        }
                      >
                        Keep prose · fix ledger
                      </Button>
                      <Button
                        size="sm"
                        style="flex:1"
                        onClick={() =>
                          data.resolveContinuity(cur.id, {
                            critique_id: c.id,
                            choice: "use_ledger",
                          })
                        }
                      >
                        Keep ledger · fix prose
                      </Button>
                    </div>
                    {pstr(c, "character") && (
                      <div style={css("margin-top:9px")}>
                        <span
                          onClick={() =>
                            router.push(
                              `/ledger?cat=characters&focus=${encodeURIComponent(pstr(c, "character"))}`,
                            )
                          }
                          style={css(
                            "cursor:pointer;font-size:12px;color:var(--accent);border-bottom:1px solid var(--accentSoft)",
                          )}
                        >
                          View {pstr(c, "character")} in ledger →
                        </span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {desk.tab === "notes" && (
            <Panel eyebrow="Notes" pad="16px 18px">
              <div style={css("display:flex;flex-direction:column;gap:12px")}>
                <p style={css("margin:0;font-size:12.5px;color:var(--dim);line-height:1.55")}>
                  Advisory flags from the review passes.
                </p>
                {notes.length === 0 && (
                  <p
                    style={css("margin:0;font-family:var(--mono);font-size:12px;color:var(--dim)")}
                  >
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
                        {severityLabel(n.severity)}
                      </span>
                    </div>
                    <p style={css("margin:0;font-size:13px;line-height:1.5;color:var(--dim)")}>
                      {n.note}
                    </p>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {desk.tab === "changes" && (
            <Panel eyebrow="Changes" pad="16px 18px">
              <div style={css("display:flex;flex-direction:column;gap:9px")}>
                <p style={css("margin:0 0 3px;font-size:12.5px;color:var(--dim);line-height:1.55")}>
                  Ledger deltas this scene's beat declares, committed on approval.
                </p>
                {deltas.length === 0 && (
                  <p
                    style={css("margin:0;font-family:var(--mono);font-size:12px;color:var(--dim)")}
                  >
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
                    <span style={css("font-family:var(--mono);font-size:15px;color:var(--good)")}>
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
            </Panel>
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

// --- length budget badge + draft-attempt provenance (scene-packet contract system) ---

const LENGTH_META: Record<string, { label: string; tone: string }> = {
  within_budget: { label: "within budget", tone: "--good" },
  under_min: { label: "under min", tone: "--warn" },
  over_max: { label: "over max", tone: "--warn" },
  over_hard_max_compressed: { label: "compressed", tone: "--info" },
  over_hard_max_quarantined: { label: "over hard max", tone: "--bad" },
};

function LengthBadge({
  status,
  wordCount: wc,
}: {
  status?: LengthStatus | string | null;
  wordCount?: number | null;
}) {
  if (!status) return null;
  const meta = LENGTH_META[status] ?? { label: String(status).replace(/_/g, " "), tone: "--dim" };
  return (
    <Chip
      label={`${meta.label}${wc != null ? ` · ${wc}w` : ""}`}
      colorVar={meta.tone}
      title="How the draft's length landed against its ScenePacket word budget"
    />
  );
}

// The preserved prose stages for this scene (raw draft → enrichment passes → length guard → final),
// so a compression/expansion or an enrichment pass is auditable. Fetched lazily on first open.
function StagesPanel({ sceneId }: { sceneId: string }) {
  const [stages, setStages] = useState<DraftAttemptOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setStages(null);
    setError(null);
    api
      .draftAttempts(sceneId)
      .then((s) => alive && setStages(s))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [sceneId]);

  return (
    <Panel eyebrow="Draft stages · provenance" pad="12px 14px" style="margin-bottom:16px">
      {error && <div style={css("font-size:12px;color:var(--bad)")}>{error}</div>}
      {!stages && !error && (
        <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>loading…</div>
      )}
      {stages && stages.length === 0 && (
        <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
          No recorded stages (drafted before provenance tracking).
        </div>
      )}
      {stages && stages.length > 0 && (
        <div style={css("display:flex;flex-direction:column;gap:5px")}>
          {stages.map((s) => (
            <div
              key={s.id}
              style={css(
                "display:flex;align-items:baseline;gap:10px;font-family:var(--mono);font-size:11.5px",
              )}
            >
              <span style={css("color:var(--ink);min-width:170px")}>
                {s.stage.replace(/_/g, " ")}
              </span>
              <span style={css("color:var(--dim)")}>
                {s.word_count != null ? `${s.word_count}w` : "—"}
                {s.model ? ` · ${s.model}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
