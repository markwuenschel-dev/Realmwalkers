import type { ReactNode } from "react";
import { css } from "../../css";
import Eyebrow from "./Eyebrow";

/** The Atelier surface card — raised paper on the page. Replaces the PANEL pattern
 *  (`background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding…`)
 *  previously re-inlined dozens of times. `interactive` adds the .dk-card hover treatment —
 *  reserve it for panels that are actually clickable. `inset` renders the recessed data-well
 *  variant (mono tables, JSON) on --boxbg. */
export default function Panel({
  eyebrow,
  title,
  actions,
  children,
  interactive = false,
  inset = false,
  pad = "20px",
  style = "",
}: {
  eyebrow?: ReactNode;
  title?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  interactive?: boolean;
  inset?: boolean;
  pad?: string;
  style?: string;
}) {
  const surface = inset ? "var(--boxbg)" : "var(--bg2)";
  return (
    <section
      className={interactive ? "dk-card" : undefined}
      style={css(
        `background:${surface};border:1px solid var(--line);border-radius:var(--r);padding:${pad};${style}`,
      )}
    >
      {(eyebrow || title || actions) && (
        <header
          style={css(
            `display:flex;align-items:baseline;gap:12px;${children ? "margin-bottom:14px" : ""}`,
          )}
        >
          <div style={css("min-width:0;flex:1")}>
            {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
            {title && (
              <div
                style={css(
                  `font-family:var(--display);font-weight:500;font-size:21px;line-height:28px;color:var(--ink);${eyebrow ? "margin-top:4px" : ""}`,
                )}
              >
                {title}
              </div>
            )}
          </div>
          {actions && (
            <div style={css("display:flex;gap:8px;align-items:center;flex:none")}>{actions}</div>
          )}
        </header>
      )}
      {children}
    </section>
  );
}
