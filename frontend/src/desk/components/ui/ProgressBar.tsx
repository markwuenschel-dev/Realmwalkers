import { css } from "../../css";

/** Progress track. With `value` (0–1) it renders determinate with a width transition; without,
 *  the indeterminate sweep (the old IndeterminateBar). Width is rounded to integer percent so
 *  dynamic values cannot churn the css() string cache. */
export default function ProgressBar({
  value,
  color = "var(--info)",
  height = 3,
}: {
  value?: number | null;
  color?: string;
  height?: number;
}) {
  const track = css(
    `position:relative;height:${height}px;border-radius:${height}px;background:var(--line);overflow:hidden`,
  );
  if (value == null) {
    return (
      <div style={track}>
        <div
          style={css(
            `position:absolute;top:0;bottom:0;border-radius:${height}px;background:${color};animation:indeterminate 1.1s ease-in-out infinite`,
          )}
        />
      </div>
    );
  }
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div style={track} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div
        style={css(
          `position:absolute;top:0;bottom:0;left:0;width:${pct}%;border-radius:${height}px;background:${color};transition:width 300ms var(--ease)`,
        )}
      />
    </div>
  );
}
