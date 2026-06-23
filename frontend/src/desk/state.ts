import { createContext, useCallback, useContext, useEffect, useState } from "react";
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
  focusSceneId: string | null; // a specific scene to edit (e.g. an approved one), outside the pending queue
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
  openSceneId: (id: string) => void; // open any scene (incl. approved) in the editor
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
  const [selectedThread, setSelectedThread] = useState("");
  const [activeScene, setActiveScene] = useState(0);
  const [focusSceneId, setFocusSceneId] = useState<string | null>(null);
  const [rawProse, setRawProse] = useState("");

  const go = useCallback((s: Screen) => {
    setScreen(s);
    setPaletteOpen(false);
  }, []);
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
  // Queue navigation always returns to the pending review queue — clear any focused (out-of-queue) scene.
  const prevScene = useCallback(() => {
    setScreen("scene");
    setFocusSceneId(null);
    setActiveScene((a) => Math.max(0, a - 1));
  }, []);
  const nextScene = useCallback(() => {
    setScreen("scene");
    setFocusSceneId(null);
    setActiveScene((a) => a + 1); // upper bound is clamped against the live queue in SceneScreen
  }, []);
  const openScene = useCallback((index: number) => {
    setScreen("scene");
    setFocusSceneId(null);
    setActiveScene(index);
    setPaletteOpen(false);
  }, []);
  const openSceneId = useCallback((id: string) => {
    setScreen("scene");
    setFocusSceneId(id);
    setPaletteOpen(false);
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

  // Global keyboard shortcuts (⌘K palette, g-chord screen nav, j/k queue). Scene actions live in
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
      if (tag === "textarea" || tag === "input") return;
      if (chord.active) {
        chord.active = false;
        const map: Record<string, Screen> = {
          i: "inbox", s: "scene", c: "chapters", v: "diff", m: "manuscript", l: "ledger", d: "docs",
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
      // a/r/x/e/s on the Scene screen are handled there (they commit to the API), not here.
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [togglePalette, closePalette, go, nextScene, prevScene]);

  const t = themes[themeId];

  return {
    screen, themeId, tab, mode, paletteOpen, feedback, decision, resolved, suggStatus, hoveredKey,
    ledgerCat, selectedAnn, chaptersView, selectedThread, activeScene, focusSceneId, rawProse,
    t, isManu: themeId === "manuscript", isConsole: themeId === "console", isGrim: themeId === "grimoire",
    go, setTheme, setTab, togglePalette, setMode, acceptSugg, rejectSugg, undoSugg, setHover, clearHover,
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
