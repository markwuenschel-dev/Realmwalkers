import { css } from "../../css";

/** Loading shimmer for cache-miss states — a quiet gradient sweep, never a spinner wall.
 *  `lines` renders a stack of text-shaped bars; `height` renders one block. */
export default function Skeleton({
  lines,
  height = "120px",
  width = "100%",
}: {
  lines?: number;
  height?: string;
  width?: string;
}) {
  const bar = (w: string, h: string, key?: number) => (
    <div
      key={key}
      style={css(
        `width:${w};height:${h};border-radius:6px;background:linear-gradient(90deg,var(--bg3) 25%,color-mix(in srgb,var(--line) 55%,var(--bg3)) 37%,var(--bg3) 63%);background-size:200% 100%;animation:shimmer 1.6s linear infinite`,
      )}
    />
  );
  if (lines && lines > 0) {
    return (
      <div style={css("display:flex;flex-direction:column;gap:9px")} aria-busy="true">
        {Array.from({ length: lines }, (_, i) => bar(i === lines - 1 ? "62%" : "100%", "13px", i))}
      </div>
    );
  }
  return <div aria-busy="true">{bar(width, height)}</div>;
}
