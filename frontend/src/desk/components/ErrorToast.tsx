import { useEffect } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";

// Global surface for action failures. Every data action sets `error` on failure, but only the
// Planner used to render it — so a failed approve / revert / continuity-resolve elsewhere gave no
// feedback at all. This shows the latest error once, app-wide, and auto-clears it.
export default function ErrorToast() {
  const { t } = useDesk();
  const { error, clearError } = useDeskData();

  useEffect(() => {
    if (!error) return;
    const h = window.setTimeout(clearError, 6000);
    return () => window.clearTimeout(h);
  }, [error, clearError]);

  if (!error) return null;

  return (
    <div style={css(`position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:85;display:flex;align-items:center;gap:14px;max-width:min(560px,92vw);padding:13px 16px 13px 18px;background:var(--bg2);border:1px solid color-mix(in srgb,${t.bad} 55%,var(--line));border-radius:11px;box-shadow:0 20px 50px rgba(0,0,0,.4);animation:fadeUp .25s ease both`)}>
      <span style={css(`width:8px;height:8px;border-radius:50%;background:${t.bad};flex:none`)} />
      <span style={css("font-size:13.5px;color:var(--ink);min-width:0;overflow-wrap:anywhere")}>{error}</span>
      <button onClick={clearError} style={css("margin-left:auto;font-family:var(--mono);font-size:11.5px;color:var(--accent);background:none;border:none;cursor:pointer;flex:none")}>Dismiss</button>
    </div>
  );
}
