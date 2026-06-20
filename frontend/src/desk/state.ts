import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { themes } from "./theme";
import type { ThemeId, ThemeTokens } from "./theme";
import type {
  ChaptersView,
  DecisionKind,
  Mode,
  Resolved,
  Screen,
  SuggStatus,
  Tab,
} from "./types";
import { INITIAL_BOARD, INITIAL_PROSE, QUEUE } from "./data";

// The whole interactive surface — the prototype's `state` object, its methods, and its keyboard
// handler — rebuilt as a single hook. Screens read it through DeskContext via useDesk().
export interface DeskValue {
  // state
  screen: Screen;
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
  board: string[];
  dragId: string | null;
  rawProse: string;
  // theme
  t: ThemeTokens;
  isManu: boolean;
  isConsole: boolean;
  isGrim: boolean;
  // actions
  go: (s: Screen) => void;
  setTheme: (id: ThemeId) => void;
  setTab: (t: Tab) => void;
  togglePalette: () => void;
  setMode: (m: Mode) => void;
  acceptSugg: (id: string) => void;
  rejectSugg: (id: string) => void;
  undoSugg: (id: string) => void;
  setHover: (key: string | null) => void;
  clearHover: () => void;
  prevScene: () => void;
  nextScene: () => void;
  openScene: (index: number) => void;
  onDragStart: (id: string) => void;
  onDragEnter: (id: string) => void;
  onDragEnd: () => void;
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
  const [screen, setScreen] = useState<Screen>("scene");
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
  const [selectedThread, setSelectedThread] = useState("t1");
  const [activeScene, setActiveScene] = useState(0);
  const [board, setBoard] = useState<string[]>(INITIAL_BOARD);
  const [dragId, setDragId] = useState<string | null>(null);
  const [rawProse, setRawProse] = useState(INITIAL_PROSE);

  const go = useCallback((s: Screen) => {
    setScreen(s);
    setPaletteOpen(false);
  }, []);
  const setTheme = useCallback((id: ThemeId) => setThemeId(id), []);
  const setTab = useCallback((tb: Tab) => setTabState(tb), []);
  const togglePalette = useCallback(() => setPaletteOpen((p) => !p), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const toggleEditing = useCallback(
    () => setMode((m) => (m === "editing" ? "reading" : "editing")),
    [],
  );
  const toggleSuggesting = useCallback(
    () => setMode((m) => (m === "suggesting" ? "reading" : "suggesting")),
    [],
  );
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
  const prevScene = useCallback(() => {
    setScreen("scene");
    setActiveScene((a) => Math.max(0, a - 1));
  }, []);
  const nextScene = useCallback(() => {
    setScreen("scene");
    setActiveScene((a) => Math.min(QUEUE.length - 1, a + 1));
  }, []);
  const openScene = useCallback((index: number) => {
    setScreen("scene");
    setActiveScene(index);
    setPaletteOpen(false);
  }, []);

  // Drag-to-reorder reads the live drag id from a ref so onDragEnter never closes over a stale value.
  const dragIdRef = useRef<string | null>(null);
  const onDragStart = useCallback((id: string) => {
    dragIdRef.current = id;
    setDragId(id);
  }, []);
  const onDragEnter = useCallback((id: string) => {
    const d = dragIdRef.current;
    if (!d || d === id) return;
    setBoard((prev) => {
      const arr = prev.slice();
      arr.splice(arr.indexOf(id), 0, arr.splice(arr.indexOf(d), 1)[0]);
      return arr;
    });
  }, []);
  const onDragEnd = useCallback(() => {
    dragIdRef.current = null;
    setDragId(null);
  }, []);

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

  // Global keyboard shortcuts (⌘K palette, g-chord screen nav, j/k queue, a/r/x/e/s on the scene).
  const screenRef = useRef(screen);
  screenRef.current = screen;
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
      if (tag === "textarea" || tag === "input") return;
      if (chord.active) {
        chord.active = false;
        const map: Record<string, Screen> = {
          i: "inbox", s: "scene", c: "chapters", v: "diff", m: "manuscript", l: "ledger",
        };
        if (map[k]) {
          go(map[k]);
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
      if (screenRef.current === "scene") {
        if (k === "a") decide("approve");
        else if (k === "r") decide("revise");
        else if (k === "x") decide("deny");
        else if (k === "e") toggleEditing();
        else if (k === "s") toggleSuggesting();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [togglePalette, closePalette, go, nextScene, prevScene, decide, toggleEditing, toggleSuggesting]);

  const t = themes[themeId];

  return {
    screen, themeId, tab, mode, paletteOpen, feedback, decision, resolved, suggStatus, hoveredKey,
    ledgerCat, selectedAnn, chaptersView, selectedThread, activeScene, board, dragId, rawProse,
    t, isManu: themeId === "manuscript", isConsole: themeId === "console", isGrim: themeId === "grimoire",
    go, setTheme, setTab, togglePalette, setMode, acceptSugg, rejectSugg, undoSugg, setHover, clearHover,
    prevScene, nextScene, openScene, onDragStart, onDragEnter, onDragEnd, decide, undoDecision, resolve, unresolve,
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
