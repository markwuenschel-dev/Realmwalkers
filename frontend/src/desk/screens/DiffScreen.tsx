"use client";

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { lineDiff } from "../lib/diff";
import type { SceneVersionOut } from "../api/types";
import { Button, Chip, Eyebrow, Panel } from "../components/ui";

// Two comparison modes: version-vs-version across the lineage, or agent-original-vs-final within
// ONE version — the only place the agent-draft ↔ human-edit diff is visible anywhere in the Desk.
type DiffMode = "versions" | "agent";

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

  const [mode, setMode] = useState<DiffMode>("versions");
  // Which two versions to compare. Default to the last two; reset whenever the lineage changes
  // (e.g. after a revert or revision adds a version).
  const [baseId, setBaseId] = useState<string | null>(null);
  const [targetId, setTargetId] = useState<string | null>(null);
  const [reverting, setReverting] = useState(false);
  const [confirmFor, setConfirmFor] = useState<string | null>(null); // version id awaiting revert confirmation
  const versionKey = versions.map((v) => v.id).join(",");
  useEffect(() => {
    if (versions.length === 0) return;
    setBaseId(versions[Math.max(0, versions.length - 2)].id);
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
  if (versions.length === 0) {
    return (
      <Centered>
        Scene {cur.scene_no} has no loaded versions yet.
        <Back go={() => router.push(`/scene/${cur.id}`)} label="Back to scene" />
      </Centered>
    );
  }

  // A single-version lineage still has a story to tell: the agent's original vs the final text.
  // Only version-vs-version needs two rows, so that mode alone is disabled.
  const singleVersion = versions.length === 1;
  const effectiveMode: DiffMode = singleVersion ? "agent" : mode;
  const agentMode = effectiveMode === "agent";

  const latest = versions[versions.length - 1];
  const base = versions.find((v) => v.id === baseId) ?? versions[Math.max(0, versions.length - 2)];
  const target = versions.find((v) => v.id === targetId) ?? latest;
  // In agent mode the target's preserved agent draft is the "before"; hand-written scenes (and rows
  // predating provenance tracking) have none — that gets an honest empty state, not a blank diff.
  const agentMissing = agentMode && target.agent_original == null;
  const noHumanEdits = agentMode && !agentMissing && target.agent_original === (target.prose ?? "");
  const ops = agentMissing
    ? []
    : agentMode
      ? lineDiff(target.agent_original ?? "", target.prose ?? "")
      : lineDiff(base.prose ?? "", target.prose ?? "");

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
  const openLink = (v: SceneVersionOut) => (
    <span
      onClick={() => router.push(`/scene/${v.id}`)}
      title="Open this version as a scene page"
      style={css(
        "cursor:pointer;font-family:var(--mono);font-size:11.5px;color:var(--accent);border-bottom:1px solid var(--accentSoft)",
      )}
    >
      open →
    </span>
  );

  // Left/right line numbers advance independently: an `add` exists only on the right, a `del` only
  // on the left. Sharing one counter mislabels consecutive adds with the previous line's number.
  let leftLn = 0;
  let rightLn = 0;

  return (
    <div>
      <header
        style={css("margin-bottom:20px;display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap")}
      >
        <div>
          <Eyebrow style="margin-bottom:6px">
            Scene {cur.scene_no} · {versions.length} version{versions.length === 1 ? "" : "s"}
          </Eyebrow>
          <h1
            style={css(
              "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)",
            )}
          >
            Version history
          </h1>
        </div>
        <div style={css("margin-left:auto")}>
          <Button size="sm" onClick={() => router.push(`/scene/${latest.id}`)}>
            ← Back to scene
          </Button>
        </div>
      </header>

      {/* sticky picker bar — rides just under the 60px top bar while the diff scrolls */}
      <Panel
        pad="12px"
        style="position:sticky;top:68px;z-index:30;display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px"
      >
        <div
          style={css(
            "display:flex;background:var(--bg3);border:1px solid var(--line);border-radius:999px;padding:3px",
          )}
          title="Compare two versions, or the agent's original draft against the final text within one version"
        >
          {(
            [
              { id: "versions" as const, label: "Versions" },
              { id: "agent" as const, label: "Agent vs final" },
            ] satisfies { id: DiffMode; label: string }[]
          ).map((m) => {
            const active = effectiveMode === m.id;
            const disabled = m.id === "versions" && singleVersion;
            return (
              <button
                key={m.id}
                onClick={() => !disabled && setMode(m.id)}
                disabled={disabled}
                title={
                  disabled
                    ? "Only one version exists — request a revision to compare versions"
                    : undefined
                }
                style={css(
                  `padding:5px 13px;border:none;border-radius:999px;cursor:${disabled ? "default" : "pointer"};font-family:var(--ui);font-size:12.5px;background:${active ? "var(--bg2)" : "transparent"};color:${disabled ? "var(--dim)" : active ? "var(--ink)" : "var(--dim)"};opacity:${disabled ? ".55" : "1"}`,
                )}
              >
                {m.label}
              </button>
            );
          })}
        </div>
        <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
          {!agentMode && (
            <>
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
              {openLink(base)}
              <span style={css("color:var(--accent)")}>→</span>
            </>
          )}
          <select
            value={target.id}
            onChange={(e) => setTargetId(e.target.value)}
            style={selectStyle}
            title={agentMode ? "version to inspect" : "compare to"}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {versionLabel(v)}
              </option>
            ))}
          </select>
          {openLink(target)}
        </div>
        <div style={css("display:flex;align-items:center;gap:8px")}>
          <Chip label="added" tone="good" />
          <Chip label="removed" tone="bad" />
        </div>
        {!agentMode && base.id !== latest.id && (
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

      {agentMissing ? (
        <Panel pad="34px 24px" style="text-align:center">
          <div
            style={css(
              "font-family:var(--display);font-style:italic;font-size:16px;line-height:1.6;color:var(--dim)",
            )}
          >
            v{target.version} has no preserved agent original — it was written by hand or predates
            provenance tracking. Pick another version, or compare versions instead.
          </div>
        </Panel>
      ) : (
        <>
          {noHumanEdits && (
            <div
              style={css(
                "margin-bottom:10px;font-family:var(--mono);font-size:12px;color:var(--dim)",
              )}
            >
              No human edits — the final text matches the agent's original.
            </div>
          )}
          {/* Before/After panes share one grid so the diff rows stay height-aligned across the split */}
          <Panel pad="0" style="overflow:hidden">
            <div
              style={css(
                "display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line)",
              )}
            >
              <div style={css("background:var(--bg2b);padding:12px 16px")}>
                <Eyebrow>
                  Before · v{agentMode ? target.version : base.version}
                  {agentMode ? " — agent original" : ""}
                </Eyebrow>
              </div>
              <div style={css("background:var(--bg2b);padding:12px 16px")}>
                <Eyebrow>
                  After · v{target.version}
                  {agentMode ? " — your final" : target.id === latest.id ? " — current" : ""}
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
        </>
      )}
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
