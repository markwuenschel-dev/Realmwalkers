"use client";

// Edit — audit prose you wrote against your own style rules.
//
// The sibling of Inject, pointed the other way. Inject rewrites the passage and hands back prose;
// this hands back suggestions, each citing the rule it rests on, and the text on the page changes
// only when you accept one. Nothing is written server-side: the endpoint is stateless like
// `/enrich`, so decisions live in this screen until you copy the result out.
//
// The highlighting is not new machinery — `seg` + `tokenize` (prose.ts) and the `sugg` marker kind
// are what SceneScreen already uses for human tracked-changes. A suggestion anchors by substring,
// which is why the backend returns `quote` rather than character offsets.

import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { StyleReviewOut } from "../api/types";
import { Button, Eyebrow, Panel, Spinner } from "../components/ui";
import { css } from "../css";
import { applyAcceptedSuggestions } from "../lib/format";
import { severityLabel, severityVar } from "../lib/severity";
import { seg, tokenize } from "../prose";
import type { Marker } from "../types";

type Decision = "accepted" | "rejected";

const STORAGE_KEY = "desk.edit.v1";

interface Persisted {
  prose?: string;
  pov?: string;
}

const loadPersisted = (): Persisted => {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Persisted) : {};
  } catch {
    return {};
  }
};

const WRAP = "width:min(96vw,1800px);margin:0 auto;padding:0 clamp(12px,2vw,32px)";
const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";
const FIELD =
  "width:100%;background:var(--bg3);border:1px solid var(--line);border-radius:var(--r);" +
  "padding:8px 10px;color:var(--ink);font-family:var(--ui);font-size:14px";
const PROSE =
  "width:100%;min-height:56vh;resize:vertical;background:var(--bg3);border:1px solid var(--line);" +
  "border-radius:var(--r);padding:12px 14px;color:var(--ink);font-family:var(--prose,var(--ui));" +
  "font-size:15px;line-height:1.7";
const READER = "min-height:56vh;font-family:var(--prose,var(--ui));font-size:15px;line-height:1.8";
const MONO = "font-family:var(--mono);font-size:11px;color:var(--dim)";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function EditScreen() {
  const [prose, setProse] = useState("");
  const [pov, setPov] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<StyleReviewOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<number, Decision>>({});
  const [active, setActive] = useState<number | null>(null);
  const [restored, setRestored] = useState(false);

  // Rehydrate after mount, never during render: sessionStorage does not exist on the server, and
  // reading it in the initial state would desync the first client render from the SSR markup.
  useEffect(() => {
    const p = loadPersisted();
    if (p.prose) setProse(p.prose);
    if (p.pov) setPov(p.pov);
    setRestored(true);
  }, []);

  useEffect(() => {
    if (!restored) return;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ prose, pov } satisfies Persisted));
    } catch {
      /* private mode / quota — the draft is a convenience, never load-bearing */
    }
  }, [prose, pov, restored]);

  const suggestions = result?.suggestions ?? [];
  const ready = prose.trim().length > 0 && !busy;

  // The prose the audit actually ran against. Editing the box afterwards would leave every quote
  // anchored to text that no longer exists, so the rendered view is pinned to the audited copy and
  // the decisions stay meaningful until the next run.
  const [audited, setAudited] = useState<string | null>(null);

  const markers: Marker[] = useMemo(
    () => suggestions.map((s, i) => ({ find: s.quote, kind: "sugg" as const, id: String(i) })),
    [suggestions],
  );

  const paragraphs = useMemo(
    () => (audited ? seg(audited).map((b) => ({ n: b.n, tokens: tokenize(b.text, markers) })) : []),
    [audited, markers],
  );

  const acceptedCount = Object.values(decisions).filter((d) => d === "accepted").length;
  const openCount = suggestions.length - Object.keys(decisions).length;

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    setDecisions({});
    setActive(null);
    try {
      const out = await api.styleReview({ prose, pov: pov.trim() || null });
      setResult(out);
      setAudited(prose);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function decide(i: number, d: Decision) {
    setDecisions((prev) => {
      const next = { ...prev };
      if (next[i] === d) delete next[i];
      else next[i] = d;
      return next;
    });
  }

  function copyResult() {
    const text = applyAcceptedSuggestions(
      audited ?? prose,
      suggestions.map((s, i) => ({
        quote: s.quote,
        new_text: s.new_text,
        status: decisions[i] ?? "pending",
      })),
    );
    void navigator.clipboard?.writeText(text);
  }

  function reset() {
    setResult(null);
    setAudited(null);
    setDecisions({});
    setActive(null);
    setError(null);
  }

  return (
    <div style={css(`${WRAP};padding-top:26px;padding-bottom:60px`)}>
      <Eyebrow>Edit</Eyebrow>
      <h1 style={css(TITLE_XL)}>Audit prose against your rules</h1>
      <p style={css("color:var(--dim);font-size:14px;margin:0 0 18px;max-width:70ch")}>
        Every suggestion cites the rule it rests on — a clarity rule, a contract clause, or a named
        drift pattern — and quotes the exact text it objects to. Nothing is saved and nothing is
        changed for you; accept the ones you want and copy the result out.
      </p>

      <div style={css("display:grid;grid-template-columns:1.35fr 1fr;gap:18px;align-items:start")}>
        {/* ---------- left: the passage ---------- */}
        <Panel
          eyebrow="Passage"
          title={audited ? "Audited prose" : "Your prose"}
          actions={
            audited ? (
              <Button size="sm" variant="ghost" onClick={reset}>
                Edit again
              </Button>
            ) : undefined
          }
        >
          {!audited && (
            <label style={css("display:block;margin-bottom:12px")}>
              <Eyebrow>POV character (optional)</Eyebrow>
              <input
                value={pov}
                onChange={(e) => setPov(e.target.value)}
                placeholder="Marcus"
                style={css(`${FIELD};margin-top:5px`)}
              />
              <span style={css(`${MONO};display:block;margin-top:5px`)}>
                Scopes which drift patterns apply. Leave blank for a POV-free passage. Cast-scoped
                rules are activated by characters named in the prose, never guessed from pronouns.
              </span>
            </label>
          )}

          {audited ? (
            <div style={css(READER)}>
              {paragraphs.map((p) => (
                <p key={p.n} style={css("margin:0 0 14px")}>
                  {p.tokens.map((t, ti) => {
                    if (t.kind === "text") return <span key={ti}>{t.text}</span>;
                    const i = Number(t.id);
                    const s = suggestions[i];
                    if (!s) return <span key={ti}>{t.text}</span>;
                    const d = decisions[i];
                    const tone = severityVar(s.severity);
                    if (d === "accepted" && s.new_text !== null) {
                      return (
                        <span
                          key={ti}
                          style={css(
                            `background:color-mix(in srgb,var(--good) 13%,transparent);` +
                              `border-bottom:1px solid var(--good);cursor:pointer`,
                          )}
                          onClick={() => setActive(i)}
                        >
                          {s.new_text}
                        </span>
                      );
                    }
                    if (d === "rejected") return <span key={ti}>{t.text}</span>;
                    return (
                      <span
                        key={ti}
                        onClick={() => setActive(i)}
                        title={`${s.rule} — ${s.why}`}
                        style={css(
                          `background:color-mix(in srgb,var(${tone}) ${active === i ? 22 : 10}%,transparent);` +
                            `border-bottom:1px solid var(${tone});cursor:pointer`,
                        )}
                      >
                        {t.text}
                      </span>
                    );
                  })}
                </p>
              ))}
            </div>
          ) : (
            <textarea
              value={prose}
              onChange={(e) => setProse(e.target.value)}
              spellCheck={false}
              placeholder="Paste the passage you want audited."
              style={css(PROSE)}
            />
          )}

          <div
            style={css(
              "display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px",
            )}
          >
            <span style={css(MONO)}>
              {(audited ?? prose).length.toLocaleString()} characters
              {acceptedCount > 0 ? ` · ${acceptedCount} accepted` : ""}
            </span>
            <span style={css("display:flex;gap:8px")}>
              {audited && (
                <Button size="sm" variant="secondary" onClick={copyResult}>
                  Copy with accepted edits
                </Button>
              )}
              {!audited && (
                <Button variant="primary" disabled={!ready} onClick={run}>
                  {busy ? "Auditing…" : "Audit"}
                </Button>
              )}
            </span>
          </div>
        </Panel>

        {/* ---------- right: the suggestions ---------- */}
        <Panel
          eyebrow="Findings"
          title={
            result
              ? `${suggestions.length} suggestion${suggestions.length === 1 ? "" : "s"}`
              : "Suggestions"
          }
        >
          {busy && (
            <div
              style={css(
                "display:flex;align-items:center;justify-content:center;gap:10px;min-height:40vh;color:var(--dim)",
              )}
            >
              <Spinner /> reading your rules…
            </div>
          )}

          {!busy && error && (
            <div
              style={css(
                "border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));border-radius:var(--r);" +
                  "background:color-mix(in srgb,var(--bad) 8%,transparent);padding:12px 14px;font-size:13px",
              )}
            >
              {error}
            </div>
          )}

          {!busy && !error && !result && (
            <p style={css("color:var(--dim);font-size:14px;min-height:40vh")}>
              Paste a passage and run the audit. Findings appear here, each anchored to the sentence
              it objects to.
            </p>
          )}

          {!busy && !error && result && suggestions.length === 0 && (
            <p style={css("color:var(--dim);font-size:14px")}>
              No rule was broken in this passage. That is a real answer, not an empty one — the
              audit reports only what a named rule condemns.
            </p>
          )}

          {!busy && !error && result && suggestions.length > 0 && (
            <div style={css("display:flex;flex-direction:column;gap:10px")}>
              {suggestions.map((s, i) => {
                const d = decisions[i];
                const tone = severityVar(s.severity);
                return (
                  <div
                    key={i}
                    onMouseEnter={() => setActive(i)}
                    style={css(
                      `border:1px solid ${
                        active === i ? `var(${tone})` : "var(--line)"
                      };border-radius:var(--r);padding:11px 13px;` +
                        `background:${d === "rejected" ? "transparent" : "var(--bg2)"};` +
                        `opacity:${d === "rejected" ? ".55" : "1"}`,
                    )}
                  >
                    <div style={css("display:flex;align-items:baseline;gap:8px;flex-wrap:wrap")}>
                      <span
                        style={css(`font-family:var(--mono);font-size:11px;color:var(${tone})`)}
                      >
                        {s.rule}
                      </span>
                      <span style={css(MONO)}>
                        {s.rule_source.replace(/_/g, " ")} · {severityLabel(s.severity)}
                      </span>
                    </div>

                    <p style={css("margin:7px 0 0;font-size:13.5px")}>{s.why}</p>

                    {s.new_text !== null && (
                      <div
                        style={css(
                          "margin-top:8px;font-family:var(--prose,var(--ui));font-size:13px",
                        )}
                      >
                        <div
                          style={css(
                            "text-decoration:line-through;color:var(--dim);" +
                              "background:color-mix(in srgb,var(--bad) 8%,transparent);padding:2px 5px;border-radius:3px",
                          )}
                        >
                          {s.quote}
                        </div>
                        <div
                          style={css(
                            "margin-top:4px;background:color-mix(in srgb,var(--good) 10%,transparent);" +
                              "padding:2px 5px;border-radius:3px",
                          )}
                        >
                          {s.new_text === "" ? (
                            <em style={css("color:var(--dim)")}>(delete)</em>
                          ) : (
                            s.new_text
                          )}
                        </div>
                      </div>
                    )}

                    <div style={css("display:flex;gap:6px;margin-top:9px")}>
                      {s.new_text !== null && (
                        <Button
                          size="sm"
                          variant={d === "accepted" ? "primary" : "secondary"}
                          onClick={() => decide(i, "accepted")}
                        >
                          {d === "accepted" ? "Accepted" : "Accept"}
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant={d === "rejected" ? "primary" : "ghost"}
                        onClick={() => decide(i, "rejected")}
                      >
                        {d === "rejected" ? "Dismissed" : "Dismiss"}
                      </Button>
                    </div>
                  </div>
                );
              })}

              <p style={css(`${MONO};margin:4px 0 0`)}>
                {openCount} undecided · judged against{" "}
                {result.standards_loaded.join(", ") || "nothing"}
                {result.drift_scope_characters.length > 0
                  ? ` · cast-scoped rules for ${result.drift_scope_characters.join(", ")}`
                  : ""}
              </p>
              {result.standards_loaded.includes("forbidden_drift") &&
                result.drift_scope_characters.length === 0 && (
                  <p style={css(`${MONO};margin:0;color:var(--warn)`)}>
                    no character was named in this passage, so only the always-on drift rules ran —
                    cast-scoped rules need a name on the page and are never inferred from pronouns
                  </p>
                )}
              {!result.telemetry_recorded && (
                <p style={css(`${MONO};margin:0;color:var(--warn)`)}>
                  this run&rsquo;s cost was not recorded — it will not appear in Agent Operations
                </p>
              )}
              {result.standards_missing.length > 0 && (
                <p style={css(`${MONO};margin:0;color:var(--warn)`)}>
                  not loaded: {result.standards_missing.join(", ")} — this audit was weaker than it
                  looks
                </p>
              )}
              {result.fabricated_dropped > 0 && (
                <p style={css(`${MONO};margin:0;color:var(--warn)`)}>
                  {result.fabricated_dropped} finding(s) dropped: quoted text that is not in your
                  passage
                </p>
              )}
              <p style={css(`${MONO};margin:0`)}>
                {result.model} · {result.tokens_used.toLocaleString()} tokens
              </p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
