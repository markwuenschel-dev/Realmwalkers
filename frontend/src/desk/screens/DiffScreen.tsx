"use client";

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { lineDiff } from "../lib/diff";
import type { SceneVersionOut } from "../api/types";

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

  const cell = (side: "l" | "r", type: "same" | "add" | "del") => {
    const b =
      "font-family:var(--mono);font-size:12.5px;line-height:1.65;padding:5px 14px;background:var(--bg2);white-space:pre-wrap;word-break:break-word;";
    if (type === "same") return b + "color:var(--ink)";
    if (type === "add")
      return (
        b +
        (side === "r"
          ? "background:color-mix(in srgb,var(--good) 14%,var(--bg2));color:var(--good)"
          : "opacity:.35")
      );
    return (
      b +
      (side === "l"
        ? "background:color-mix(in srgb,var(--bad) 14%,var(--bg2));color:var(--bad)"
        : "opacity:.35")
    );
  };

  const versionLabel = (v: SceneVersionOut) =>
    `v${v.version} · ${v.status.replace(/_/g, " ")}${v.id === latest.id ? " · current" : ""}`;
  const selectStyle = css(
    "background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font-family:var(--mono);font-size:12px;cursor:pointer",
  );

  // Left/right line numbers advance independently: an `add` exists only on the right, a `del` only
  // on the left. Sharing one counter mislabels consecutive adds with the previous line's number.
  let leftLn = 0;
  let rightLn = 0;

  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:16px;margin-bottom:18px",
        )}
      >
        <div>
          <div
            style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-bottom:7px")}
          >
            SCENE {cur.scene_no}
          </div>
          <h1
            style={css(
              "margin:0;font-family:var(--display);font-weight:600;font-size:28px;color:var(--ink)",
            )}
          >
            Version history
          </h1>
        </div>
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
      </div>

      <div
        style={css(
          "display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:14px",
        )}
      >
        <div
          style={css(
            "display:flex;gap:18px;font-family:var(--mono);font-size:11.5px;color:var(--dim)",
          )}
        >
          <span style={css("display:flex;align-items:center;gap:6px")}>
            <span
              style={css(
                "width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--good) 30%,transparent)",
              )}
            />
            added
          </span>
          <span style={css("display:flex;align-items:center;gap:6px")}>
            <span
              style={css(
                "width:10px;height:10px;border-radius:3px;background:color-mix(in srgb,var(--bad) 30%,transparent)",
              )}
            />
            removed
          </span>
        </div>
        {base.id !== latest.id &&
          (confirmFor === base.id ? (
            <div style={css("display:flex;align-items:center;gap:9px;flex-wrap:wrap")}>
              <span style={css("font-size:12.5px;color:var(--dim)")}>
                Revert scene {cur.scene_no} to v{base.version}? A new current version is created
                with that text.
              </span>
              <button
                onClick={() => revertTo(base)}
                disabled={reverting}
                style={css(
                  "padding:7px 13px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:12.5px;cursor:pointer;font-family:var(--ui)",
                )}
              >
                {reverting ? "Reverting…" : "Confirm revert"}
              </button>
              <button
                onClick={() => setConfirmFor(null)}
                disabled={reverting}
                style={css(
                  "padding:7px 13px;border-radius:7px;border:1px solid var(--line);background:var(--bg2);color:var(--dim);font-size:12.5px;cursor:pointer;font-family:var(--ui)",
                )}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmFor(base.id)}
              disabled={reverting}
              title={`Make v${base.version}'s text the current version`}
              style={css(
                "padding:7px 13px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:12.5px;cursor:pointer;font-family:var(--ui)",
              )}
            >
              ⟲ Revert to v{base.version}
            </button>
          ))}
      </div>

      <div
        style={css(
          "display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r);overflow:hidden",
        )}
      >
        <div
          style={css(
            "background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;text-transform:uppercase;color:var(--dim)",
          )}
        >
          v{base.version}
          {base.agent_original ? " — agent original" : ""}
        </div>
        <div
          style={css(
            "background:var(--bg2b);padding:11px 16px;font-family:var(--mono);font-size:11px;text-transform:uppercase;color:var(--dim)",
          )}
        >
          v{target.version}
          {target.id === latest.id ? " — current" : ""}
        </div>
        {ops.map((d, i) => {
          const leftNum = d.type === "add" ? "" : String(++leftLn);
          const rightNum = d.type === "del" ? "" : String(++rightLn);
          return (
            <Fragment key={i}>
              <div style={css(cell("l", d.type))}>
                <span
                  style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}
                >
                  {leftNum}
                </span>
                {d.type === "add" ? "" : d.text}
              </div>
              <div style={css(cell("r", d.type))}>
                <span
                  style={css("display:inline-block;width:1.6em;color:var(--dim);user-select:none")}
                >
                  {rightNum}
                </span>
                {d.type === "del" ? "" : d.text}
              </div>
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div
      style={css(
        "max-width:560px;margin:60px auto;text-align:center;color:var(--dim);font-size:14.5px;line-height:1.6",
      )}
    >
      {children}
    </div>
  );
}

function Back({ go, label = "Go to inbox" }: { go: () => void; label?: string }) {
  return (
    <div>
      <button
        onClick={go}
        style={css(
          "margin-top:18px;padding:9px 16px;border-radius:8px;border:1px solid var(--line);background:var(--bg2);color:var(--ink);cursor:pointer;font-family:var(--ui);font-size:13.5px",
        )}
      >
        {label}
      </button>
    </div>
  );
}
