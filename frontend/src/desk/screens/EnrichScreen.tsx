"use client";

import { useEffect, useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import type { EnrichOut } from "../api/types";
import { Button, Eyebrow, Panel, Spinner } from "../components/ui";

// Inject — paste prose you already wrote and run one enrichment lane over it. This is deliberately
// the whole feature: the enrichment passes take a plain string, so none of the contract-first
// apparatus (packets, beats, approval) applies — that machinery exists to tell the DRAFTER what to
// write from scratch, and injected prose is already written. Landing a result as a scene is a
// separate, later decision.
//
// The panel keeps NOTHING server-side, which makes the browser the only copy — so this screen is
// responsible for not losing it. Next unmounts the component on any nav (Inbox, a chord, a stray
// click), and plain useState dies with it, silently discarding a result the machine just spent ~12s
// and real tokens producing. Mirror every field to sessionStorage so navigating away and back is
// lossless. sessionStorage (not local): it survives navigation and reload but not the tab, which
// matches "this is a scratch surface, not a store".

const STORAGE_KEY = "desk.inject.v1";

interface Persisted {
  prose: string;
  pov: string;
  lanes: string[];
  /** v1 stored a single lane. Read-only migration path — never written. */
  lane?: string;
  beat: string;
  result: EnrichOut | null;
}

const loadPersisted = (): Partial<Persisted> => {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "{}") as Partial<Persisted>;
  } catch {
    return {}; // corrupt/unparseable — start clean rather than crash the screen
  }
};

// Canonical run order — the same fixed order the server chains passes in (workers/router.py). Keep
// these aligned: this list is what the panel PROMISES will happen, so a different order here would
// describe a chain the machine never runs.
const LANES: { id: string; label: string; hint: string }[] = [
  {
    id: "combat",
    label: "Combat",
    hint: "spatial clarity — who is where, what connects, in what order",
  },
  {
    id: "sensory",
    label: "Sensory",
    hint: "replace abstraction with what the POV actually senses",
  },
  {
    id: "dialogue",
    label: "Dialogue",
    hint: "voice and subtext — distinct speakers, weighted silence",
  },
];

const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";
const WRAP = "width:min(96vw,1800px);margin:0 auto;padding:0 clamp(12px,2vw,32px)";
const FIELD =
  "width:100%;background:var(--boxbg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;color:var(--ink);font-family:var(--ui);font-size:13px";
const PROSE =
  "width:100%;min-height:56vh;resize:vertical;background:var(--boxbg);border:1px solid var(--line);border-radius:8px;padding:14px;color:var(--ink);font-family:var(--prose,var(--ui));font-size:14.5px;line-height:1.7";

// A lane toggle. Selected reads as gilt (the Atelier accent); unselected stays quiet — the chips are
// a filter on a sensible default, not a required choice, so an untouched row shouldn't shout.
const chipStyle = (on: boolean): string =>
  "padding:6px 13px;border-radius:999px;font-family:var(--ui);font-size:13px;cursor:pointer;" +
  (on
    ? "border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--accent)"
    : "border:1px solid var(--line);background:var(--bg2);color:var(--dim)");

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export default function EnrichScreen() {
  const [prose, setProse] = useState("");
  const [pov, setPov] = useState("");
  // Empty = every lane. The server reads it the same way, so "nothing picked" is a real default
  // ("deepen all of it") rather than a no-op that would make Enrich do nothing.
  const [lanes, setLanes] = useState<string[]>([]);
  const [beat, setBeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<EnrichOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restored, setRestored] = useState(false);

  // Rehydrate after mount, not via useState initializers: this is a server-rendered route, so reading
  // sessionStorage during render would mismatch the server's HTML.
  useEffect(() => {
    const p = loadPersisted();
    if (p.prose) setProse(p.prose);
    if (p.pov) setPov(p.pov);
    // A v1 payload holds `lane: "combat"`. Read it as that one lane rather than ignoring it — an
    // ignored value would silently widen a stored single-lane choice to all three, and the author
    // would pay 3x the tokens on a click they thought they'd already configured.
    if (p.lanes) setLanes(p.lanes);
    else if (p.lane) setLanes([p.lane]);
    if (p.beat) setBeat(p.beat);
    if (p.result) setResult(p.result);
    setRestored(true);
  }, []);

  // Mirror on every change. Guarded on `restored` so the empty first render can't clobber a stored
  // result before the effect above has read it back.
  useEffect(() => {
    if (!restored) return;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ prose, pov, lanes, beat, result }));
    } catch {
      /* quota exceeded on a very large scene — keep working in memory rather than break the panel */
    }
  }, [restored, prose, pov, lanes, beat, result]);

  // POV is optional: a prologue or omniscient interlude has none, and inventing one ("none") tells
  // the lane to stay in a character that doesn't exist — it resolves that by changing nothing.
  const ready = prose.trim().length > 0 && !busy;

  // What will actually run, in the order it will run. Selection is a SET — clicking dialogue first
  // doesn't run it first, so show the real chain rather than the click order.
  const chain = LANES.filter((l) => lanes.length === 0 || lanes.includes(l.id));

  const toggleLane = (id: string) =>
    setLanes((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await api.enrich({
          prose,
          pov: pov.trim(),
          lanes,
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

  // No removeItem here: the mirror effect fires on these state changes and would rewrite the key
  // immediately, so deleting it is dead code. Clearing the state IS clearing the store.
  const clear = () => {
    setProse("");
    setPov("");
    setBeat("");
    setResult(null);
    setError(null);
  };

  const delta = result ? result.enriched_chars - result.source_chars : 0;

  return (
    <div style={css(`${WRAP};padding-top:26px;padding-bottom:60px`)}>
      <header style={css("margin-bottom:20px")}>
        <Eyebrow>Inject</Eyebrow>
        <h1 style={css(TITLE_XL)}>Enrich prose you wrote</h1>
        <p
          style={css(
            "margin:8px 0 0;font-family:var(--ui);font-size:13px;color:var(--dim);max-width:70ch",
          )}
        >
          Paste a scene and each lane you pick deepens one dimension of it — preserving your voice,
          your events, and the beat&rsquo;s outcome. Pick none and every lane runs, in order, each
          reading the last one&rsquo;s work. Your text is never altered: the result is a copy for
          you to read and take. Nothing is stored on the server, but this panel keeps what you left
          here, so you can navigate away and come back.
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
          </div>

          <Eyebrow>Lanes — pick none to run all {LANES.length}</Eyebrow>
          <div style={css("display:flex;gap:7px;flex-wrap:wrap;margin:6px 0 8px")}>
            {LANES.map((l) => {
              const on = lanes.includes(l.id);
              return (
                <button
                  key={l.id}
                  type="button"
                  onClick={() => toggleLane(l.id)}
                  aria-pressed={on}
                  title={l.hint}
                  style={css(chipStyle(on))}
                >
                  {l.label}
                </button>
              );
            })}
          </div>

          {/* Always spell out the run. "None = all" is a default worth having, but only if the
              author never has to remember it — and the chain makes the cost legible too. */}
          <p style={css("margin:0 0 12px;font-family:var(--ui);font-size:12px;color:var(--dim)")}>
            {chain.map((l) => l.label).join(" → ")}
            {chain.length === 1
              ? ` — ${chain[0].hint}`
              : " · one pass per lane, each deepening the last one's output"}
            {!pov.trim() && (
              <>
                {" · "}
                <span style={css("font-style:italic")}>
                  no POV — the passes will preserve the viewpoint already on the page
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
            <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
              {prose.length.toLocaleString()} chars
            </span>
            <div style={css("display:flex;gap:8px;align-items:center")}>
              {(prose || result) && (
                <Button
                  onClick={clear}
                  disabled={busy}
                  title="Clear the panel and forget what's stored"
                >
                  Clear
                </Button>
              )}
              <Button variant="primary" onClick={run} disabled={!ready}>
                {busy ? "Enriching…" : "Enrich"}
              </Button>
            </div>
          </div>
        </Panel>

        <Panel
          eyebrow={result ? `${result.lanes_run.join(" → ")} · ${result.model}` : "Result"}
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
                "display:flex;align-items:center;gap:10px;min-height:56vh;justify-content:center;color:var(--dim);font-family:var(--ui);font-size:13px",
              )}
            >
              <Spinner /> running {chain.map((l) => l.label.toLowerCase()).join(" → ")}…
            </div>
          )}

          {!busy && error && (
            <div
              style={css(
                "border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));border-radius:8px;padding:14px;background:color-mix(in srgb,var(--bad) 8%,var(--boxbg))",
              )}
            >
              <Eyebrow>Every lane failed</Eyebrow>
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
                "display:flex;align-items:center;justify-content:center;min-height:56vh;color:var(--dim);font-family:var(--ui);font-size:13px",
              )}
            >
              Your enriched scene will appear here.
            </div>
          )}

          {!busy && result && (
            <>
              {/* A partial chain is still a real result — but it must never read as a complete one.
                  A lane the author asked for and did not get is the loudest thing on this panel. */}
              {result.lanes_failed.length > 0 && (
                <div
                  style={css(
                    "margin-bottom:11px;border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));background:color-mix(in srgb,var(--bad) 9%,var(--boxbg));border-radius:8px;padding:10px 12px",
                  )}
                >
                  <div
                    style={css(
                      "font-family:var(--ui);font-size:12.5px;font-weight:600;color:var(--bad)",
                    )}
                  >
                    Partial result — {result.lanes_failed.map((f) => f.lane).join(", ")} did not run
                  </div>
                  {result.lanes_failed.map((f) => (
                    <p
                      key={f.lane}
                      style={css(
                        "margin:5px 0 0;font-family:var(--mono);font-size:11px;color:var(--bad);white-space:pre-wrap;word-break:break-word",
                      )}
                    >
                      {f.reason}
                    </p>
                  ))}
                </div>
              )}

              <textarea readOnly value={result.enriched} spellCheck={false} style={css(PROSE)} />
              <div
                style={css(
                  "display:flex;gap:14px;flex-wrap:wrap;margin-top:12px;font-family:var(--mono);font-size:11.5px;color:var(--dim)",
                )}
              >
                <span>
                  {result.source_chars.toLocaleString()} → {result.enriched_chars.toLocaleString()}{" "}
                  chars
                </span>
                <span>
                  {delta >= 0 ? "+" : ""}
                  {delta.toLocaleString()}
                </span>
                <span>{result.tokens_used.toLocaleString()} tokens</span>
                {result.pov_free && <span>pov-free</span>}
                {result.lanes_run.includes("dialogue") && (
                  <span style={css(result.dialogue_rules_loaded ? "" : "color:var(--bad)")}>
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
