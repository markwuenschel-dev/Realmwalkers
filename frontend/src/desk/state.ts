"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { themes } from "./theme";
import type { ThemeId, ThemeTokens } from "./theme";
import { CHORD_TO_HREF } from "./routes";
import type {
  ChaptersView,
  DecisionKind,
  Mode,
  Resolved,
  SuggStatus,
  Tab,
} from "./types";

// The whole interactive surface — the prototype's `state` object, its methods, and its keyboard
// handler — rebuilt as a single hook. Screens read it through DeskContext via useDesk().
//
// Page identity now lives in the URL: there is no `screen` field and no `go(screen)`. Navigation
// happens through Next's router (here) or <Link>/usePathname (in components). A focused, out-of-queue
// scene is the route param at /scene/[sceneId], not a `focusSceneId` field.
export interface DeskValue {
  // state
  themeId: ThemeId;
  tab: Tab;
  mode: Mode;
  paletteOpen: boolean;
  feedback: string;
  decision: DecisionKind | null;
  resolved: Resolved;
  suggStatus: SuggStatus;
  hoveredKey: string | null;
  ledgerCat: string;
  selectedAnn: string | null;
  chaptersView: ChaptersView;
  selectedThread: string;
  activeScene: number;
  rawProse: string;
  // theme
  t: ThemeTokens;
  isManu: boolean;
  isConsole: boolean;
  isGrim: boolean;
  // actions
  setTheme: (id: ThemeId) => void;
  setTab: (t: Tab) => void;
  togglePalette: () => void;
  closePalette: () => void;
  setMode: (m: Mode) => void;
  acceptSugg: (id: string) => void;
  rejectSugg: (id: string) => void;
  undoSugg: (id: string) => void;
  setHover: (key: string | null) => void;
  clearHover: () => void;
  prevScene: () => void;
  nextScene: () => void;
  openScene: (index: number) => void;
  openSceneId: (id: string) => void; // navigate to /scene/[id] (any scene, incl. approved)
  decide: (d: DecisionKind) => void;
  undoDecision: () => void;
  resolve: (id: string, choice: "prose" | "ledger") => void;
  unresolve: (id: string) => void;
  selectAnn: (id: string) => void; // inline span: jump to Notes tab + highlight
  highlightAnn: (id: string) => void; // gutter card: highlight only
  setLedgerCat: (c: string) => void;
  setChaptersView: (v: ChaptersView) => void;
  selectThread: (id: string) => void;
  setFeedback: (v: string) => void;
  setProse: (v: string) => void;
}

export function useDeskState(): DeskValue {
  const router = useRouter();
  const [themeId, setThemeId] = useState<ThemeId>("manuscript");
  const [tab, setTabState] = useState<Tab>("continuity");
  const [mode, setMode] = useState<Mode>("reading");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [feedback, setFeedbackState] = useState("");
  const [decision, setDecision] = useState<DecisionKind | null>(null);
  const [resolved, setResolved] = useState<Resolved>({});
  const [suggStatus, setSuggStatus] = useState<SuggStatus>({});
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [ledgerCat, setLedgerCatState] = useState("characters");
  const [selectedAnn, setSelectedAnn] = useState<string | null>(null);
  const [chaptersView, setChaptersViewState] = useState<ChaptersView>("board");
  const [selectedThread, setSelectedThread] = useState("");
  const [activeScene, setActiveScene] = useState(0);
  const [rawProse, setRawProse] = useState("");

  const setTheme = useCallback((id: ThemeId) => setThemeId(id), []);
  const setTab = useCallback((tb: Tab) => setTabState(tb), []);
  const togglePalette = useCallback(() => setPaletteOpen((p) => !p), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const acceptSugg = useCallback(
    (id: string) => setSuggStatus((s) => ({ ...s, [id]: "accepted" })),
    [],
  );
  const rejectSugg = useCallback(
    (id: string) => setSuggStatus((s) => ({ ...s, [id]: "rejected" })),
    [],
  );
  const undoSugg = useCallback((id: string) => {
    setSuggStatus((s) => {
      const m = { ...s };
      delete m[id];
      return m;
    });
  }, []);
  const setHover = useCallback((key: string | null) => setHoveredKey(key), []);
  const clearHover = useCallback(() => setHoveredKey(null), []);
  // Queue navigation always returns to the pending review queue at /scene — the focused-scene route
  // param drops away as soon as we push there.
  const prevScene = useCallback(() => {
    setActiveScene((a) => Math.max(0, a - 1));
    setPaletteOpen(false);
    router.push("/scene");
  }, [router]);
  const nextScene = useCallback(() => {
    setActiveScene((a) => a + 1); // upper bound is clamped against the live queue in SceneScreen
    setPaletteOpen(false);
    router.push("/scene");
  }, [router]);
  const openScene = useCallback((index: number) => {
    setActiveScene(index);
    setPaletteOpen(false);
    router.push("/scene");
  }, [router]);
  const openSceneId = useCallback((id: string) => {
    setPaletteOpen(false);
    router.push(`/scene/${id}`);
  }, [router]);

  const decide = useCallback((d: DecisionKind) => setDecision(d), []);
  const undoDecision = useCallback(() => setDecision(null), []);
  const resolve = useCallback(
    (id: string, choice: "prose" | "ledger") => setResolved((r) => ({ ...r, [id]: choice })),
    [],
  );
  const unresolve = useCallback((id: string) => {
    setResolved((r) => {
      const m = { ...r };
      delete m[id];
      return m;
    });
  }, []);
  const selectAnn = useCallback((id: string) => {
    setTabState("notes");
    setSelectedAnn(id);
  }, []);
  const highlightAnn = useCallback((id: string) => setSelectedAnn(id), []);
  const setLedgerCat = useCallback((c: string) => setLedgerCatState(c), []);
  const setChaptersView = useCallback((v: ChaptersView) => setChaptersViewState(v), []);
  const setFeedback = useCallback((v: string) => setFeedbackState(v), []);
  const setProse = useCallback((v: string) => setRawProse(v), []);

  // Global keyboard shortcuts (⌘K palette, g-chord route nav, j/k queue). Scene actions live in
  // SceneScreen (they commit to the API).
  useEffect(() => {
    const chord = { active: false, timer: 0 as number | undefined };
    const onKey = (e: KeyboardEvent) => {
      const k = (e.key || "").toLowerCase();
      if ((e.metaKey || e.ctrlKey) && k === "k") {
        e.preventDefault();
        togglePalette();
        return;
      }
      if (e.key === "Escape") {
        closePalette();
        return;
      }
      const tag = ((e.target as HTMLElement | null)?.tagName || "").toLowerCase();
      const editable = (e.target as HTMLElement | null)?.isContentEditable;
      if (tag === "textarea" || tag === "input" || tag === "select" || editable) return;
      if (chord.active) {
        chord.active = false;
        const href = CHORD_TO_HREF[k];
        if (href) {
          setPaletteOpen(false);
          router.push(href);
          return;
        }
      }
      if (k === "g") {
        chord.active = true;
        clearTimeout(chord.timer);
        chord.timer = window.setTimeout(() => {
          chord.active = false;
        }, 900);
        return;
      }
      if (k === "j") {
        nextScene();
        return;
      }
      if (k === "k") {
        prevScene();
        return;
      }
      // a/r/x/e/s on the Scene screen are handled there (they commit to the API), not here.
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [togglePalette, closePalette, router, nextScene, prevScene]);

  const t = themes[themeId];

  return {
    themeId, tab, mode, paletteOpen, feedback, decision, resolved, suggStatus, hoveredKey,
    ledgerCat, selectedAnn, chaptersView, selectedThread, activeScene, rawProse,
    t, isManu: themeId === "manuscript", isConsole: themeId === "console", isGrim: themeId === "grimoire",
    setTheme, setTab, togglePalette, closePalette, setMode, acceptSugg, rejectSugg, undoSugg, setHover, clearHover,
    prevScene, nextScene, openScene, openSceneId, decide, undoDecision, resolve, unresolve,
    selectAnn, highlightAnn, setLedgerCat, setChaptersView, selectThread: setSelectedThread,
    setFeedback, setProse,
  };
}

const DeskContext = createContext<DeskValue | null>(null);
export const DeskProvider = DeskContext.Provider;

export function useDesk(): DeskValue {
  const ctx = useContext(DeskContext);
  if (!ctx) throw new Error("useDesk must be used inside <DeskProvider>");
  return ctx;
}
