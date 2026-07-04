import { css } from "../../css";
import Eyebrow from "./Eyebrow";

/** Stat tile — eyebrow label over a display-serif value. Hoisted from ProductionScreen's
 *  local MetricCard so Inbox/Settings/Production share one. */
export default function MetricCard({
  label,
  value,
  hint,
  tone = "var(--ink)",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div
      style={css(
        "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px",
      )}
    >
      <Eyebrow>{label}</Eyebrow>
      <div
        style={css(
          `margin-top:8px;font-family:var(--display);font-weight:500;font-size:24px;line-height:1.15;color:${tone}`,
        )}
      >
        {value}
      </div>
      {hint && (
        <div
          style={css("margin-top:4px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
        >
          {hint}
        </div>
      )}
    </div>
  );
}
