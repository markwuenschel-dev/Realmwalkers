import { Fragment } from "react";
import { css } from "../../css";

export type StepState = "done" | "active" | "blocked" | "pending";

export interface Step {
  id: string;
  label: string;
  state: StepState;
  /** Short annotation under the active/blocked step ("provider rate limited", elapsed, …). */
  note?: string;
}

const DOT: Record<StepState, { bg: string; border: string; ink: string }> = {
  done: { bg: "var(--good)", border: "var(--good)", ink: "var(--onAccent)" },
  active: { bg: "var(--accentSoft)", border: "var(--accent)", ink: "var(--accent)" },
  blocked: {
    bg: "color-mix(in srgb,var(--bad) 12%,transparent)",
    border: "var(--bad)",
    ink: "var(--bad)",
  },
  pending: { bg: "transparent", border: "var(--line)", ink: "var(--dim)" },
};

/** Pipeline stage stepper — dots joined by hairlines, done stages filled, the active stage
 *  ringed in gold, a blocked stage ringed in red with its note. Used for production run stages
 *  and anywhere a fixed sequence needs to say "you are here". */
export default function Stepper({ steps }: { steps: Step[] }) {
  return (
    <div style={css("display:flex;align-items:flex-start;gap:0;overflow-x:auto;padding:2px 0")}>
      {steps.map((step, i) => {
        const d = DOT[step.state];
        return (
          <Fragment key={step.id}>
            {i > 0 && (
              <div
                style={css(
                  `flex:1;min-width:18px;height:1px;margin-top:9px;background:${steps[i].state === "pending" ? "var(--line)" : "var(--accentLine)"}`,
                )}
              />
            )}
            <div
              style={css(
                "display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0",
              )}
            >
              <div
                style={css(
                  `width:18px;height:18px;border-radius:50%;border:1.5px solid ${d.border};background:${d.bg};display:flex;align-items:center;justify-content:center;flex:none`,
                )}
              >
                {step.state === "done" && (
                  <svg width="9" height="9" viewBox="0 0 10 10" fill="none" aria-hidden>
                    <path
                      d="M1.5 5.5l2.3 2.3L8.5 2.5"
                      stroke="var(--bg2)"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
                {step.state === "active" && (
                  <div
                    style={css(
                      "width:6px;height:6px;border-radius:50%;background:var(--accent);animation:pulseDot 1.4s ease-in-out infinite",
                    )}
                  />
                )}
                {step.state === "blocked" && (
                  <span
                    style={css(
                      "font-family:var(--mono);font-size:10px;color:var(--bad);line-height:1",
                    )}
                  >
                    !
                  </span>
                )}
              </div>
              <div
                style={css(
                  `font-family:var(--mono);font-size:9.5px;letter-spacing:.04em;text-transform:uppercase;text-align:center;max-width:96px;line-height:1.35;color:${d.ink};font-weight:${step.state === "active" || step.state === "blocked" ? "700" : "400"}`,
                )}
              >
                {step.label}
              </div>
              {step.note && (step.state === "active" || step.state === "blocked") && (
                <div
                  style={css(
                    `font-family:var(--mono);font-size:9px;color:${d.ink};text-align:center;max-width:110px`,
                  )}
                >
                  {step.note}
                </div>
              )}
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}
