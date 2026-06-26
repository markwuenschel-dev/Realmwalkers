import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { DraftPill } from "./DraftActivity";
import type { ThemeId } from "../theme";
import type { Screen } from "../types";

const SCREENS: { id: Screen; label: string }[] = [
  { id: "inbox", label: "Inbox" },
  { id: "scene", label: "Scene" },
  { id: "chapters", label: "Chapters" },
  { id: "packets", label: "Packets" },
  { id: "diff", label: "Versions" },
  { id: "manuscript", label: "Manuscript" },
  { id: "ledger", label: "Ledger" },
  { id: "docs", label: "Canon" },
  { id: "settings", label: "Models" },
];

const THEMES: { id: ThemeId; label: string; title: string }[] = [
  { id: "grimoire", label: "Grimoire", title: "Dark fantasy — refined" },
  { id: "manuscript", label: "Manuscript", title: "Light editorial" },
  { id: "console", label: "Console", title: "Dense pro tool" },
];

export default function TopBar() {
  const { screen, themeId, go, setTheme, togglePalette } = useDesk();
  const { pending, books, bookId } = useDeskData();
  const badgeFor = (id: Screen): string | null =>
    id === "inbox" && pending.length ? String(pending.length) : null;
  // Initial of the active book (the desk is book-scoped); fall back to the Dominion mark.
  const bookInitial = (books.find((b) => b.id === bookId)?.title ?? "D").trim().charAt(0).toUpperCase() || "D";

  return (
    <header className="no-print" style={css("position:sticky;top:0;z-index:40;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 18px;height:60px;background:var(--bg2b);border-bottom:1px solid var(--line)")}>
      <div style={css("display:flex;align-items:center;gap:20px;min-width:0")}>
        <div onClick={() => go("inbox")} style={css("display:flex;align-items:center;gap:11px;cursor:pointer;flex:none")}>
          <div style={css("width:26px;height:26px;border-radius:6px;border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-family:var(--display);font-size:15px;color:var(--accent);background:var(--accentSoft)")}>D</div>
          <div style={css("font-family:var(--display);font-size:15.5px;letter-spacing:.02em;color:var(--ink);white-space:nowrap")}>
            The Dominion Realm <span style={css("color:var(--dim)")}>· Writers' Desk</span>
          </div>
        </div>
        <nav style={css("display:flex;gap:2px")}>
          {SCREENS.map((n) => {
            const active = screen === n.id;
            const badge = badgeFor(n.id);
            return (
              <button
                key={n.id}
                onClick={() => go(n.id)}
                style={css(`padding:7px 10px;border:none;border-radius:8px;cursor:pointer;font-family:var(--ui);font-size:13px;white-space:nowrap;background:${active ? "var(--accentSoft)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-weight:${active ? "500" : "400"}`)}
              >
                {n.label}
                {badge && (
                  <span style={css("margin-left:7px;font-family:var(--mono);font-size:10px;padding:1px 6px;border-radius:999px;background:var(--accent);color:var(--onAccent)")}>{badge}</span>
                )}
              </button>
            );
          })}
        </nav>
        <DraftPill />
      </div>
      <div style={css("display:flex;align-items:center;gap:10px;flex:none")}>
        <div style={css("display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:999px")}>
          {THEMES.map((th) => {
            const active = themeId === th.id;
            return (
              <button
                key={th.id}
                onClick={() => setTheme(th.id)}
                title={th.title}
                style={css(`padding:5px 11px;border:none;border-radius:999px;cursor:pointer;font-family:var(--ui);font-size:12px;background:${active ? "var(--accent)" : "transparent"};color:${active ? "var(--onAccent)" : "var(--dim)"};font-weight:${active ? "600" : "400"}`)}
              >
                {th.label}
              </button>
            );
          })}
        </div>
        <button onClick={togglePalette} style={css("display:flex;align-items:center;gap:8px;height:34px;padding:0 12px;border-radius:999px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:12.5px;cursor:pointer;font-family:var(--ui)")}>
          Search
          <span style={css("font-family:var(--mono);font-size:11px;border:1px solid var(--line);border-radius:5px;padding:1px 5px;color:var(--dim)")}>⌘K</span>
        </button>
        <div title={books.find((b) => b.id === bookId)?.title ?? undefined} style={css("width:30px;height:30px;border-radius:50%;background:var(--accentSoft);border:1px solid var(--accent);display:flex;align-items:center;justify-content:center;font-family:var(--display);font-size:13px;color:var(--accent)")}>{bookInitial}</div>
      </div>
    </header>
  );
}
