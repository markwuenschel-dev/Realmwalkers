import { Fragment, type ReactNode } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { lineDiff } from "../lib/diff";

export default function DiffScreen() {
  const { go } = useDesk();
  const data = useDeskData();
  const versions = data.versions;
  const cur = data.detail;

  if (!cur) {
    return (
      <Centered>
        Open a scene from the inbox, then compare its versions here.
        <Back go={() => go("inbox")} />
      </Centered>
    );
  }
  if (versions.length < 2) {
    return (
      <Centered>
        Scene {cur.scene_no} has only one version — request a revision to create the next one.
        <Back go={() => go("scene")} label="Back to scene" />
      </Centered>
    );
  }

  const base = versions[versions.length - 2];
  const target = versions[versions.length - 1];
  const ops = lineDiff(base.prose ?? "", target.prose ?? "");

  const cell = (side: "l" | "r", type: "same" | "add" | "del") => {
    const b = "font-family:var(--mono);font-size:12.5px;line-height:1.65;padding:5px 14px;background:var(--bg2);white-space:pre-wrap;word-break:break-word;";
    if (type === "same") return b + "color:var(--ink)";
    if (type === "add") return b + (side === "r" ? "background:color-mix(in srgb,var(--good) 14%,var(--bg2));color:var(--good)" : "opacity:.35");
    return b + (side === "l" ? "background:color-mix(in srgb,var(--bad) 14%,var(--bg2));color:var(--bad)" : "opacity:.35");
  };

  let ln = 0;

  return (
    <div>
      <div style={css("display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:16px;margin-bottom:22px")}>
        <div>
          <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-bottom:7px")}>SCENE {cur.scene_no}</div>
          <h1 style={css("margin:0;font-family:var(--display);font-weight:600;font-size:28px;color:var(--ink)")}>Version history</h1>
        </div>
        <div style={css("display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:12px;color:var(--dim)")}>
          <span style={css("padding:6px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2)")}>v{base.version}</span>
          <span style={css("color:var(--accent)")}>→</span>
          <span style={css("padding:6px 12px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink)")}>v{target.version}</span>
        </div>
      </div>

      <div style={css("display:flex;gap:18px;font-family:var(--mono);font-size:11.5px;color:var(--dim);margin-bottom:14px")}>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--good) 30%,transparent)")} />added</span>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--bad) 30%,transparent)")} />removed</span>
      </div>

      <div style={css("display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r);overflow:hidden")}>
        <div style={css("background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;text-transform:uppercase;color:var(--dim)")}>v{base.version}{base.agent_original ? " — agent original" : ""}</div>
        <div style={css("background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;text-transform:uppercase;color:var(--dim)")}>v{target.version} — current</div>
        {ops.map((d, i) => {
          if (d.type !== "add") ln++;
          const leftNum = d.type === "add" ? "" : String(ln);
          const rightNum = d.type === "del" ? "" : String(ln);
          return (
            <Fragment key={i}>
              <div style={css(cell("l", d.type))}><span style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}>{leftNum}</span>{d.type === "add" ? "" : d.text}</div>
              <div style={css(cell("r", d.type))}><span style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}>{rightNum}</span>{d.type === "del" ? "" : d.text}</div>
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div style={css("max-width:560px;margin:60px auto;text-align:center;color:var(--dim);font-size:14.5px;line-height:1.6")}>{children}</div>
  );
}

function Back({ go, label = "Go to inbox" }: { go: () => void; label?: string }) {
  return (
    <div>
      <button onClick={go} style={css("margin-top:18px;padding:9px 16px;border-radius:8px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer;font-family:var(--ui);font-size:13.5px")}>{label}</button>
    </div>
  );
}
