import { Fragment } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { api } from "../api/client";
import { useFetch } from "../api/hooks";
import { sceneTitle } from "../api/adapters";
import { diffRows } from "../api/adapters.diff";
import type { DiffType } from "../types";

// One-line status frame so loading / error / empty all share the screen's outer chrome.
function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div style={css("padding:40px;text-align:center;font-family:var(--mono);font-size:13px;color:var(--dim)")}>{children}</div>
    </div>
  );
}

export default function DiffScreen() {
  const { activeScene } = useDesk();

  // Resolve the scene under review: the pending queue, indexed by the active selection (clamped).
  const pending = useFetch(() => api.pending(), []);
  const scenes = pending.data ?? [];
  const idx = scenes.length ? Math.min(Math.max(activeScene, 0), scenes.length - 1) : 0;
  const scene = scenes[idx] ?? null;

  const versions = useFetch(
    () => (scene ? api.sceneVersions(scene.id) : Promise.resolve([])),
    [scene?.id],
  );

  const tone = (side: "l" | "r", type: DiffType) => {
    const base = "font-family:var(--mono);font-size:12.5px;line-height:1.65;padding:5px 14px;background:var(--bg2);white-space:pre-wrap;word-break:break-word;";
    if (type === "same") return base + "color:var(--ink)";
    if (type === "change") return base + "background:color-mix(in srgb,var(--warn) 12%,var(--bg2));color:var(--ink)";
    if (type === "add") return base + (side === "r" ? "background:color-mix(in srgb,var(--good) 14%,var(--bg2));color:var(--good)" : "color:var(--dim);opacity:.4");
    if (type === "del") return base + (side === "l" ? "background:color-mix(in srgb,var(--bad) 14%,var(--bg2));color:var(--bad)" : "color:var(--dim);opacity:.4");
    return base;
  };

  if (pending.loading || versions.loading) return <Frame>Loading version history…</Frame>;
  if (pending.error) return <Frame>Couldn't load the review queue — {pending.error}</Frame>;
  if (versions.error) return <Frame>Couldn't load versions — {versions.error}</Frame>;
  if (!scene) return <Frame>No scene is awaiting review.</Frame>;

  // Versions arrive oldest→newest; diff the two latest.
  const vers = versions.data ?? [];
  if (vers.length < 2) return <Frame>Need at least two versions to diff — Scene {scene.scene_no} has {vers.length}.</Frame>;
  const older = vers[vers.length - 2];
  const newer = vers[vers.length - 1];
  const rows = diffRows(older.prose ?? "", newer.prose ?? "");

  let ln = 0;
  const builtRows = rows.map((d) => {
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
          <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-bottom:7px")}>SCENE {scene.scene_no} · {sceneTitle(scene).toUpperCase()}</div>
          <h1 style={css("margin:0;font-family:var(--display);font-weight:600;font-size:28px;color:var(--ink)")}>Version history</h1>
        </div>
        <div style={css("display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:12px;color:var(--dim)")}>
          <span style={css("padding:6px 12px;border-radius:7px;border:1px solid var(--line);background:var(--bg2)")}>v{older.version}</span>
          <span style={css("color:var(--accent)")}>→</span>
          <span style={css("padding:6px 12px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink)")}>v{newer.version}</span>
        </div>
      </div>

      <div style={css("display:flex;gap:18px;font-family:var(--mono);font-size:11.5px;color:var(--dim);margin-bottom:14px")}>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--good) 30%,transparent)")} />added</span>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--bad) 30%,transparent)")} />removed</span>
        <span style={css("display:flex;align-items:center;gap:6px")}><span style={css("width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--warn) 28%,transparent)")} />changed</span>
      </div>

      <div style={css("display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r);overflow:hidden")}>
        <div style={css("background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)")}>v{older.version} — earlier</div>
        <div style={css("background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)")}>v{newer.version} — current</div>
        {builtRows.map((d, i) => (
          <Fragment key={i}>
            <div style={css(d.leftStyle)}><span style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}>{d.leftGutter}</span>{d.left}</div>
            <div style={css(d.rightStyle)}><span style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}>{d.rightGutter}</span>{d.right}</div>
          </Fragment>
        ))}
      </div>
    </div>
  );
}
