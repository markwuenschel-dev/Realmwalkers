import type { MouseEvent } from "react";
import { css } from "../css";
import { useDesk } from "../state";

export default function CommandPalette() {
  const { go, nextScene, prevScene, togglePalette } = useDesk();

  const commands = [
    { icon: "◧", label: "Go to Inbox", hint: "G I", onClick: () => go("inbox") },
    { icon: "❖", label: "Open Scene · review queue", hint: "G S", onClick: () => go("scene") },
    { icon: "▦", label: "Open Chapter board & progress", hint: "G C", onClick: () => go("chapters") },
    { icon: "⇄", label: "Compare versions", hint: "G V", onClick: () => go("diff") },
    { icon: "❡", label: "Open Manuscript", hint: "G M", onClick: () => go("manuscript") },
    { icon: "◍", label: "Open World ledger", hint: "G L", onClick: () => go("ledger") },
    { icon: "❡", label: "Open Canon docs", hint: "G D", onClick: () => go("docs") },
    { icon: "⤓", label: "Next scene in queue", hint: "J", onClick: nextScene },
    { icon: "⤒", label: "Previous scene in queue", hint: "K", onClick: prevScene },
  ];

  const stop = (e: MouseEvent) => e.stopPropagation();

  return (
    <div onClick={togglePalette} style={css("position:fixed;inset:0;z-index:90;background:rgba(0,0,0,.62);display:flex;align-items:flex-start;justify-content:center;padding-top:13vh;animation:fadeIn .14s ease both")}>
      <div onClick={stop} style={css("width:min(560px,92vw);background:var(--bg2);border:1px solid var(--line);border-radius:13px;box-shadow:0 30px 80px rgba(0,0,0,.5);overflow:hidden;animation:fadeUp .18s ease both")}>
        <div style={css("display:flex;align-items:center;gap:11px;padding:15px 18px;border-bottom:1px solid var(--line)")}>
          <span style={css("color:var(--dim)")}>⌕</span>
          <input autoFocus placeholder="Jump to a scene, screen, or action…" style={css("flex:1;background:transparent;border:none;color:var(--ink);font-size:15px")} />
          <span style={css("font-family:var(--mono);font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:5px;padding:1px 6px")}>esc</span>
        </div>
        <div style={css("padding:8px;max-height:50vh;overflow-y:auto")}>
          {commands.map((cmd, i) => (
            <button
              key={i}
              onClick={cmd.onClick}
              style={css("display:flex;align-items:center;gap:13px;width:100%;padding:11px 13px;border:none;background:transparent;color:var(--ink);border-radius:8px;cursor:pointer;text-align:left;font-size:13.5px")}
              onMouseOver={(e) => (e.currentTarget.style.background = "var(--bg3)")}
              onMouseOut={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <span style={css("width:22px;text-align:center;color:var(--accent)")}>{cmd.icon}</span>
              <span style={css("flex:1")}>{cmd.label}</span>
              <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>{cmd.hint}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
