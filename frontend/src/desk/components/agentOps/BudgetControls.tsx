"use client";

import { useEffect, useState } from "react";
import { css } from "../../css";
import type { AgentGlobalsOut } from "../../api/types";
import { Button, Panel } from "../ui";

interface BudgetControlsProps {
  globals: AgentGlobalsOut;
  busy: boolean;
  onSave: (patch: { scene_token_budget?: number; scene_time_budget_s?: number }) => void;
}

export function BudgetControls({ globals, busy, onSave }: BudgetControlsProps) {
  const [tokens, setTokens] = useState(String(globals.scene_token_budget));
  const [seconds, setSeconds] = useState(String(globals.scene_time_budget_s));

  useEffect(() => {
    setTokens(String(globals.scene_token_budget));
    setSeconds(String(globals.scene_time_budget_s));
  }, [globals.scene_token_budget, globals.scene_time_budget_s]);

  const dirty =
    Number(tokens) !== globals.scene_token_budget ||
    Number(seconds) !== globals.scene_time_budget_s;

  return (
    <Panel eyebrow="Global scene budgets" style="margin-bottom:18px" pad="16px 18px">
      <div style={css("display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end")}>
        <label style={css("font-size:13px;color:var(--dim)")}>
          Token budget
          <input
            type="number"
            min={5000}
            max={500000}
            step={1000}
            disabled={busy}
            value={tokens}
            onChange={(e) => setTokens(e.target.value)}
            style={css(
              "display:block;margin-top:4px;padding:6px 10px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--mono);font-size:12px;width:140px",
            )}
          />
        </label>
        <label style={css("font-size:13px;color:var(--dim)")}>
          Wall-clock limit (sec)
          <input
            type="number"
            min={30}
            max={3600}
            step={30}
            disabled={busy}
            value={seconds}
            onChange={(e) => setSeconds(e.target.value)}
            style={css(
              "display:block;margin-top:4px;padding:6px 10px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--mono);font-size:12px;width:120px",
            )}
          />
        </label>
        <Button
          size="sm"
          disabled={busy || !dirty}
          onClick={() =>
            onSave({
              scene_token_budget: Number(tokens),
              scene_time_budget_s: Number(seconds),
            })
          }
        >
          Save budgets
        </Button>
      </div>
      <div style={css("margin-top:8px;font-size:11.5px;color:var(--dim)")}>
        Applied to new scene jobs and worker timeouts. Existing queued jobs keep their run budget.
      </div>
    </Panel>
  );
}
