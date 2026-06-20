import { Fragment } from "react";
import { css } from "../css";
import { DIFF_ROWS } from "../data";
import type { DiffType } from "../types";

export default function DiffScreen() {
  const tone = (side: "l" | "r", type: DiffType) => {
    const base = "font-family:var(--mono);font-size:12.5px;line-height:1.65;padding:5px 14px;background:var(--bg2);white-space:pre-wrap;word-break:break-word;";
    if (type === "same") return base + "color:var(--ink)";
    if (type === "change") return base + "background:color-mix(in srgb,var(--warn) 12%,var(--bg2));color:var(--ink)";
    if (type === "add") return base + (side === "r" ? "background:color-mix(in srgb,var(--good) 14%,var(--bg2));color:var(--good)" : "color:var(--dim);opacity:.4");
    if (type === "del") return base + (side === "l" ? "background:color-mix(in srgb,var(--bad) 14%,var(--bg2));color:var(--bad)" : "color:var(--dim);opacity:.4");
    return base;
  };

  let ln = 0;
  const diffRows = DIFF_ROWS.map((d) => {
    const showNum = d.l || d.r;
    if (showNum) ln++;
    return {
      left: d.l,
      right: d.r,
      leftGutter: d.type === "add" ? "" : (d.l || d.type === "del" ? String(ln) : ""),
      rightGutter: d.type === "del" ? "" : (d.r || d.type === "add" ? String(ln) : ""),
      leftStyle: tone("l", d.type),
      rightStyle: tone("r", d.type),
    };
  });

  return (
    <div>
      <div style={css("display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:16px;margin-bottom:22px")}>
        <div>
          <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-bottom:7px")}>SCENE 7 · THE WARDED DOOR</div>
          <h1 style={css("margin:0;font-family:var(--display);font-weight:600;font-size:28px;color:var(--ink)")}>Version history</h1>
        </div>
        <div style={css("display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:12px;color:var(--dim)")}>
          <span style={css("padding:6px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2)")}>v2 <span style={css("color:var(--dim)")}>· 11 Jun</span></span>
          <span style={css("color:var(--accent)")}>→</span>
          <span style={css("padding:6px 12px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink)")}>v3 <span style={css("color:var(--dim)")}>· 14 Jun</span></span>
        </div>
      </div>

      <div style={css("display:flex;gap:18px;font-family:var(--mono);font-size:11.5px;color:var(--dim);margin-bottom:14px")}>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--good) 30%,transparent)")} />added</span>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--bad) 30%,transparent)")} />removed</span>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--warn) 28%,transparent)")} />changed</span>
      </div>

      <div style={css("display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r);overflow:hidden")}>
        <div style={css("background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)")}>v2 — agent original</div>
        <div style={css("background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)")}>v3 — current</div>
        {diffRows.map((d, i) => (
          <Fragment key={i}>
            <div style={css(d.leftStyle)}><span style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}>{d.leftGutter}</span>{d.left}</div>
            <div style={css(d.rightStyle)}><span style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}>{d.rightGutter}</span>{d.right}</div>
          </Fragment>
        ))}
      </div>
    </div>
  );
}
