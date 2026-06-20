import { css } from "./desk/css";
import { DeskProvider, useDesk, useDeskState } from "./desk/state";
import { themeRootStyle } from "./desk/theme";
import TopBar from "./desk/components/TopBar";
import CommandPalette from "./desk/components/CommandPalette";
import DecisionToast from "./desk/components/DecisionToast";
import SceneScreen from "./desk/screens/SceneScreen";
import InboxScreen from "./desk/screens/InboxScreen";
import ChaptersScreen from "./desk/screens/ChaptersScreen";
import DiffScreen from "./desk/screens/DiffScreen";
import ManuscriptScreen from "./desk/screens/ManuscriptScreen";
import LedgerScreen from "./desk/screens/LedgerScreen";

export default function App() {
  const desk = useDeskState();
  return (
    <DeskProvider value={desk}>
      <Desk />
    </DeskProvider>
  );
}

function Desk() {
  const { t, isGrim, isConsole, screen, paletteOpen, decision } = useDesk();
  return (
    <div style={themeRootStyle(t)}>
      {isGrim && (
        <div style={css("position:fixed;inset:0;pointer-events:none;background:radial-gradient(120% 90% at 50% -10%, rgba(201,162,83,.07), transparent 55%);z-index:0")} />
      )}
      {isConsole && (
        <div style={css("position:fixed;inset:0;pointer-events:none;background:radial-gradient(100% 70% at 50% -5%, rgba(79,214,224,.06), transparent 60%);z-index:0")} />
      )}

      <TopBar />

      <div style={css("position:relative;z-index:1;max-width:1480px;margin:0 auto;padding:30px 26px 80px")}>
        {screen === "scene" && <SceneScreen />}
        {screen === "inbox" && <InboxScreen />}
        {screen === "chapters" && <ChaptersScreen />}
        {screen === "diff" && <DiffScreen />}
        {screen === "manuscript" && <ManuscriptScreen />}
        {screen === "ledger" && <LedgerScreen />}
      </div>

      {paletteOpen && <CommandPalette />}
      {decision && <DecisionToast />}
    </div>
  );
}
