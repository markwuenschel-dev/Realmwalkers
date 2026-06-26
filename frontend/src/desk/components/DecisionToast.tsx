"use client";

import { useRouter } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";

export default function DecisionToast() {
  const router = useRouter();
  const { decision, t, undoDecision } = useDesk();
  if (!decision) return null;

  const decMap = {
    approve: { color: t.good, msg: "Scene approved — committed to the manuscript and the world ledger." },
    revise: { color: t.warn, msg: "Revision requested — the Oracle will redraft from your notes." },
    deny: { color: t.bad, msg: "Scene rejected — removed from the review queue." },
  };
  const dec = decMap[decision];

  return (
    <div style={css("position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:80;display:flex;align-items:center;gap:16px;padding:13px 16px 13px 18px;background:var(--bg2);border:1px solid var(--line);border-radius:11px;box-shadow:0 20px 50px rgba(0,0,0,.4);animation:fadeUp .25s ease both")}>
      <span style={css(`width:8px;height:8px;border-radius:50%;background:${dec.color}`)} />
      <span style={css("font-size:13.5px;color:var(--ink)")}>{dec.msg}</span>
      <button onClick={undoDecision} style={css("font-family:var(--mono);font-size:11.5px;color:var(--accent);background:none;border:none;cursor:pointer")}>Dismiss</button>
      <button onClick={() => { undoDecision(); router.push("/inbox"); }} style={css("font-size:12.5px;color:var(--ink);background:var(--bg3);border:1px solid var(--line);border-radius:7px;padding:6px 12px;cursor:pointer")}>Back to inbox</button>
    </div>
  );
}
