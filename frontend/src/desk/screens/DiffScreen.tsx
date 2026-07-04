"use client";

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { lineDiff } from "../lib/diff";
import type { SceneVersionOut } from "../api/types";
import { Button, Chip, Eyebrow, Panel } from "../components/ui";

export default function DiffScreen() {
  const router = useRouter();
  const params = useParams<{ sceneId?: string }>();
  const sceneId = params.sceneId ?? null;
  const data = useDeskData();
  const versions = data.versions;
  const cur = data.detail;

  // /diff/[sceneId] is shareable and refresh-safe: load that scene into the provider if it isn't the
  // one already open. Bare /diff just compares whatever scene is currently loaded.
  const loadedRef = useRef<string | null>(null);
  useEffect(() => {
    if (sceneId && sceneId !== loadedRef.current) {
      loadedRef.current = sceneId;
      data.openSceneById(sceneId);
    }
  }, [sceneId, data]);

  // Which two versions to compare. Default to the last two; reset whenever the lineage changes
  // (e.g. after a revert or revision adds a version).
  const [baseId, setBaseId] = useState<string | null>(null);
  const [targetId, setTargetId] = useState<string | null>(null);
  const [reverting, setReverting] = useState(false);
  const [confirmFor, setConfirmFor] = useState<string | null>(null); // version id awaiting revert confirmation
  const versionKey = versions.map((v) => v.id).join(",");
  useEffect(() => {
    if (versions.length < 2) return;
    setBaseId(versions[versions.length - 2].id);
    setTargetId(versions[versions.length - 1].id);
  }, [versionKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!cur) {
    return (
      <Centered>
        Open a scene from the inbox, then compare its versions here.
        <Back go={() => router.push("/inbox")} />
      </Centered>
    );
  }
  if (versions.length < 2) {
    return (
      <Centered>
        Scene {cur.scene_no} has only one version — request a revision to create the next one.
        <Back go={() => router.push(`/scene/${cur.id}`)} label="Back to scene" />
      </Centered>
    );
  }

  const base = versions.find((v) => v.id === baseId) ?? versions[versions.length - 2];
  const target = versions.find((v) => v.id === targetId) ?? versions[versions.length - 1];
  const ops = lineDiff(base.prose ?? "", target.prose ?? "");
  const latest = versions[versions.length - 1];

  const revertTo = async (v: SceneVersionOut) => {
    if (reverting) return;
    setReverting(true);
    await data.revertScene(v.id);
    setReverting(false);
    setConfirmFor(null);
  };

  // Changed lines get a soft 10% wash of the semantic color over the panel surface — the grid
  // backing between cells is var(--line), so mixing over var(--bg2) (not transparent) keeps the
  // cells opaque while reading as the same quiet tint in both Ink and Vellum.
  const cell = (side: "l" | "r", type: "same" | "add" | "del") => {
    const b =
      "font-family:var(--mono);font-size:12.5px;line-height:1.65;padding:5px 14px;background:var(--bg2);white-space:pre-wrap;word-break:break-word;";
    if (type === "same") return b + "color:var(--ink)";
    if (type === "add")
      return (
        b +
        (side === "r"
          ? "background:color-mix(in srgb,var(--good) 10%,var(--bg2));color:var(--good)"
          : "opacity:.3")
      );
    return (
      b +
      (side === "l"
        ? "background:color-mix(in srgb,var(--bad) 10%,var(--bg2));color:var(--bad)"
        : "opacity:.3")
    );
  };

  const versionLabel = (v: SceneVersionOut) =>
    `v${v.version} · ${v.status.replace(/_/g, " ")}${v.id === latest.id ? " · current" : ""}`;
  const selectStyle = css(
    "background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:9px;height:30px;padding:0 10px;font-family:var(--mono);font-size:12px;cursor:pointer",
  );

  // Left/right line numbers advance independently: an `add` exists only on the right, a `del` only
  // on the left. Sharing one counter mislabels consecutive adds with the previous line's number.
  let leftLn = 0;
  let rightLn = 0;

  return (
    <div>
      <header style={css("margin-bottom:20px")}>
        <Eyebrow style="margin-bottom:6px">
          Scene {cur.scene_no} · {versions.length} versions
        </Eyebrow>
        <h1
          style={css(
            "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)",
          )}
        >
          Version history
        </h1>
      </header>

      {/* sticky picker bar — rides just under the 60px top bar while the diff scrolls */}
      <Panel
        pad="12px"
        style="position:sticky;top:68px;z-index:30;display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px"
      >
        <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
          <select
            value={base.id}
            onChange={(e) => setBaseId(e.target.value)}
            style={selectStyle}
            title="compare from"
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {versionLabel(v)}
              </option>
            ))}
          </select>
          <span style={css("color:var(--accent)")}>→</span>
          <select
            value={target.id}
            onChange={(e) => setTargetId(e.target.value)}
            style={selectStyle}
            title="compare to"
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {versionLabel(v)}
              </option>
            ))}
          </select>
        </div>
        <div style={css("display:flex;align-items:center;gap:8px")}>
          <Chip label="added" tone="good" />
          <Chip label="removed" tone="bad" />
        </div>
        {base.id !== latest.id && (
          <div
            style={css("margin-left:auto;display:flex;align-items:center;gap:9px;flex-wrap:wrap")}
          >
            {confirmFor === base.id ? (
              <>
                <span style={css("font-family:var(--ui);font-size:12.5px;color:var(--dim)")}>
                  Revert scene {cur.scene_no} to v{base.version}? A new current version is created
                  with that text.
                </span>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => revertTo(base)}
                  disabled={reverting}
                >
                  {reverting ? "Reverting…" : "Confirm revert"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmFor(null)}
                  disabled={reverting}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                variant="danger"
                size="sm"
                onClick={() => setConfirmFor(base.id)}
                disabled={reverting}
                title={`Make v${base.version}'s text the current version`}
              >
                ⟲ Revert to v{base.version}
              </Button>
            )}
          </div>
        )}
      </Panel>

      {/* Before/After panes share one grid so the diff rows stay height-aligned across the split */}
      <Panel pad="0" style="overflow:hidden">
        <div
          style={css("display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line)")}
        >
          <div style={css("background:var(--bg2b);padding:12px 16px")}>
            <Eyebrow>
              Before · v{base.version}
              {base.agent_original ? " — agent original" : ""}
            </Eyebrow>
          </div>
          <div style={css("background:var(--bg2b);padding:12px 16px")}>
            <Eyebrow>
              After · v{target.version}
              {target.id === latest.id ? " — current" : ""}
            </Eyebrow>
          </div>
          {ops.map((d, i) => {
            const leftNum = d.type === "add" ? "" : String(++leftLn);
            const rightNum = d.type === "del" ? "" : String(++rightLn);
            return (
              <Fragment key={i}>
                <div style={css(cell("l", d.type))}>
                  <span
                    style={css(
                      "display:inline-block;width:1.6em;color:var(--dim);user-select:none",
                    )}
                  >
                    {leftNum}
                  </span>
                  {d.type === "add" ? "" : d.text}
                </div>
                <div style={css(cell("r", d.type))}>
                  <span
                    style={css(
                      "display:inline-block;width:1.6em;color:var(--dim);user-select:none",
                    )}
                  >
                    {rightNum}
                  </span>
                  {d.type === "del" ? "" : d.text}
                </div>
              </Fragment>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div style={css("max-width:560px;margin:80px auto;text-align:center")}>
      <div aria-hidden style={css("font-size:20px;color:var(--accent);margin-bottom:16px")}>
        ✦
      </div>
      <div
        style={css(
          "font-family:var(--display);font-style:italic;font-size:18px;line-height:1.6;color:var(--dim)",
        )}
      >
        {children}
      </div>
    </div>
  );
}

function Back({ go, label = "Go to inbox" }: { go: () => void; label?: string }) {
  return (
    <div style={css("margin-top:20px;font-style:normal")}>
      <Button variant="secondary" onClick={go}>
        {label}
      </Button>
    </div>
  );
}
