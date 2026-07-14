"use client";

import { useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import type { EnrichOut } from "../api/types";
import { Button, Eyebrow, Panel, Spinner } from "../components/ui";

// Inject — paste prose you already wrote and run one enrichment lane over it. This is deliberately
// the whole feature: the enrichment passes take a plain string, so none of the contract-first
// apparatus (packets, beats, approval) applies — that machinery exists to tell the DRAFTER what to
// write from scratch, and injected prose is already written. Nothing persists; the result is text on
// screen for the author to read and take. Landing it as a scene is a separate, later decision.

const LANES: { id: string; label: string; hint: string }[] = [
  { id: "combat", label: "Combat", hint: "spatial clarity — who is where, what connects, in what order" },
  { id: "sensory", label: "Sensory", hint: "replace abstraction with what the POV actually senses" },
  { id: "dialogue", label: "Dialogue", hint: "voice and subtext — distinct speakers, weighted silence" },
];

const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";
const WRAP = "width:min(96vw,1800px);margin:0 auto;padding:0 clamp(12px,2vw,32px)";
const FIELD =
  "width:100%;background:var(--boxbg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;color:var(--ink);font-family:var(--ui);font-size:13px";
const PROSE =
  "width:100%;min-height:56vh;resize:vertical;background:var(--boxbg);border:1px solid var(--line);border-radius:8px;padding:14px;color:var(--ink);font-family:var(--prose,var(--ui));font-size:14.5px;line-height:1.7";

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export default function EnrichScreen() {
  const [prose, setProse] = useState("");
  const [pov, setPov] = useState("");
  const [lane, setLane] = useState("combat");
  const [beat, setBeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<EnrichOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  // POV is optional: a prologue or omniscient interlude has none, and inventing one ("none") tells
  // the lane to stay in a character that doesn't exist — it resolves that by changing nothing.
  const ready = prose.trim().length > 0 && !busy;

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await api.enrich({
          prose,
          pov: pov.trim(),
          lane,
          beat_text: beat.trim() || null,
        }),
      );
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const copy = () => {
    if (result) void navigator.clipboard.writeText(result.enriched);
  };

  const delta = result ? result.enriched_chars - result.source_chars : 0;

  return (
    <div style={css(`${WRAP};padding-top:26px;padding-bottom:60px`)}>
      <header style={css("margin-bottom:20px")}>
        <Eyebrow>Inject</Eyebrow>
        <h1 style={css(TITLE_XL)}>Enrich prose you wrote</h1>
        <p
          style={css(
            "margin:8px 0 0;font-family:var(--ui);font-size:13px;color:var(--ink3);max-width:70ch",
          )}
        >
          Paste a scene, pick a lane, and the pass deepens that one dimension — preserving your voice,
          your events, and the beat&rsquo;s outcome. Nothing is saved and your text is never altered:
          the result is a copy for you to read and take.
        </p>
      </header>

      <div style={css("display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start")}>
        <Panel eyebrow="Source" title="Your prose">
          <div style={css("display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap")}>
            <label style={css("flex:1;min-width:140px")}>
              <Eyebrow>POV character — optional</Eyebrow>
              <input
                value={pov}
                onChange={(e) => setPov(e.target.value)}
                placeholder="Marcus — or blank for prologue / omniscient"
                style={css(`${FIELD};margin-top:5px`)}
              />
            </label>
            <label style={css("flex:1;min-width:140px")}>
              <Eyebrow>Lane</Eyebrow>
              <select
                value={lane}
                onChange={(e) => setLane(e.target.value)}
                style={css(`${FIELD};margin-top:5px`)}
              >
                {LANES.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <p style={css("margin:0 0 12px;font-family:var(--ui);font-size:12px;color:var(--ink3)")}>
            {LANES.find((l) => l.id === lane)?.hint}
            {!pov.trim() && (
              <>
                {" · "}
                <span style={css("color:var(--ink3);font-style:italic")}>
                  no POV — the pass will preserve the viewpoint already on the page
                </span>
              </>
            )}
          </p>

          <label>
            <Eyebrow>Beat (optional — what this scene is for)</Eyebrow>
            <input
              value={beat}
              onChange={(e) => setBeat(e.target.value)}
              placeholder="Marcus wins the duel but reveals the Aspect"
              style={css(`${FIELD};margin:5px 0 12px`)}
            />
          </label>

          <textarea
            value={prose}
            onChange={(e) => setProse(e.target.value)}
            placeholder="Paste your scene here…"
            spellCheck={false}
            style={css(PROSE)}
          />

          <div
            style={css(
              "display:flex;align-items:center;gap:12px;margin-top:12px;justify-content:space-between",
            )}
          >
            <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--ink3)")}>
              {prose.length.toLocaleString()} chars
            </span>
            <Button variant="primary" onClick={run} disabled={!ready}>
              {busy ? "Enriching…" : "Enrich"}
            </Button>
          </div>
        </Panel>

        <Panel
          eyebrow={result ? `${result.lane} · ${result.model}` : "Result"}
          title="Enriched"
          actions={
            result ? (
              <Button size="sm" onClick={copy}>
                Copy
              </Button>
            ) : null
          }
        >
          {busy && (
            <div
              style={css(
                "display:flex;align-items:center;gap:10px;min-height:56vh;justify-content:center;color:var(--ink3);font-family:var(--ui);font-size:13px",
              )}
            >
              <Spinner /> running the {lane} pass…
            </div>
          )}

          {!busy && error && (
            <div
              style={css(
                "border:1px solid var(--danger,#b4413c);border-radius:8px;padding:14px;background:var(--boxbg)",
              )}
            >
              <Eyebrow>Pass failed</Eyebrow>
              <p
                style={css(
                  "margin:6px 0 0;font-family:var(--mono);font-size:12px;color:var(--ink);white-space:pre-wrap;word-break:break-word",
                )}
              >
                {error}
              </p>
            </div>
          )}

          {!busy && !error && !result && (
            <div
              style={css(
                "display:flex;align-items:center;justify-content:center;min-height:56vh;color:var(--ink3);font-family:var(--ui);font-size:13px",
              )}
            >
              Your enriched scene will appear here.
            </div>
          )}

          {!busy && result && (
            <>
              <textarea
                readOnly
                value={result.enriched}
                spellCheck={false}
                style={css(PROSE)}
              />
              <div
                style={css(
                  "display:flex;gap:14px;margin-top:12px;font-family:var(--mono);font-size:11.5px;color:var(--ink3)",
                )}
              >
                <span>
                  {result.source_chars.toLocaleString()} → {result.enriched_chars.toLocaleString()} chars
                </span>
                <span>
                  {delta >= 0 ? "+" : ""}
                  {delta.toLocaleString()}
                </span>
                <span>{result.tokens_used.toLocaleString()} tokens</span>
                {result.pov_free && <span>pov-free</span>}
                {result.lane === "dialogue" && (
                  <span style={css(result.dialogue_rules_loaded ? "" : "color:var(--danger,#b4413c)")}>
                    {result.dialogue_rules_loaded ? "rules loaded" : "NO DIALOGUE RULES"}
                  </span>
                )}
              </div>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}
