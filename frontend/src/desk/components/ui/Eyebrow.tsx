import type { ReactNode } from "react";
import { css } from "../../css";

/** The Atelier eyebrow — small-caps mono section label. Replaces the uppercase-mono "SMALL"
 *  pattern that was re-inlined across every screen. */
export default function Eyebrow({
  children,
  tone = "var(--dim)",
  style = "",
}: {
  children: ReactNode;
  tone?: string;
  style?: string;
}) {
  return (
    <div
      style={css(
        `font-family:var(--mono);font-size:10.5px;line-height:16px;letter-spacing:.08em;text-transform:uppercase;color:${tone};${style}`,
      )}
    >
      {children}
    </div>
  );
}
