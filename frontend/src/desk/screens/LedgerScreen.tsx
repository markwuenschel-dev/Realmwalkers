"use client";

import { useEffect, useState, type ReactNode } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { statValue } from "../lib/format";
import { useSelection } from "../lib/useSelection";
import BulkBar, { BulkButton } from "../components/BulkBar";
import type {
  CanonEntityOut,
  CanonIngestOut,
  CharacterStateOut,
  RuleProposalOut,
  ThreadBeatIn,
  ThreadIn,
} from "../api/types";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// Round-trip a stat value to/from an editable string: objects/arrays as JSON, scalars as text.
const rawOf = (v: unknown): string =>
  typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
// Coerce typed input back to a value: JSON first (numbers, booleans, arrays), else plain string.
const coerce = (s: string): unknown => {
  const t = s.trim();
  if (t === "") return "";
  try {
    return JSON.parse(t);
  } catch {
    return s;
  }
};

const btn =
  "padding:7px 12px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:12.5px;cursor:pointer;font-family:var(--ui);white-space:nowrap";
const ghost =
  "padding:6px 11px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-size:11.5px;cursor:pointer;font-family:var(--ui);white-space:nowrap";
const input =
  "width:100%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13px;font-family:var(--ui)";
const fieldLabel =
  "display:block;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:4px";

type CanonEdit = { mode: "new"; kind?: string } | { mode: "edit"; entity: CanonEntityOut } | null;

export default function LedgerScreen() {
  const { t, ledgerCat, selectedThread, setLedgerCat, selectThread } = useDesk();
  const data = useDeskData();

  const [charEdit, setCharEdit] = useState<CharacterStateOut | "new" | null>(null);
  const [canonEdit, setCanonEdit] = useState<CanonEdit>(null);
  const [ingesting, setIngesting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [threadAdding, setThreadAdding] = useState(false); // inline new-thread form open
  const [beatFor, setBeatFor] = useState<string | null>(null); // thread id whose add-beat form is open

  const canonKinds = [...new Set(data.canon.map((c) => c.kind ?? "other"))].filter(
    (k) => k !== "character",
  );
  const pendingRules = data.ruleProposals.filter((r) => r.status === "pending");
  const cats = [
    { id: "characters", label: "Characters", count: data.characters.length },
    { id: "threads", label: "Threads", count: data.threads.length },
    { id: "voice-rules", label: "Voice rules", count: pendingRules.length },
    ...canonKinds.map((k) => ({
      id: `canon:${k}`,
      label: cap(k),
      count: data.canon.filter((c) => (c.kind ?? "other") === k).length,
    })),
  ];

  const threadKinds: Record<string, string> = {
    relationship: t.bad,
    mentorship: t.info,
    system: t.accent,
    power: t.warn,
  };

  const rebuildIndex = async () => {
    setIngesting(true);
    setNotice(null);
    const out = await data.ingestCanon();
    setIngesting(false);
    if (out == null) {
      setNotice("Rebuild failed — check the API logs.");
    } else {
      const o = out as CanonIngestOut;
      const tot = o.total ?? o.indexed;
      const ret = o.retired ?? 0;
      setNotice(
        `Clean rebuild complete: ${tot} live repo passage(s), ${ret} stale indexed row(s) purged.`,
      );
    }
  };

  const isChars = ledgerCat === "characters";
  const isThreads = ledgerCat === "threads";
  const isRules = ledgerCat === "voice-rules";
  const canonKind = ledgerCat.startsWith("canon:") ? ledgerCat.slice("canon:".length) : null;

  // Bulk-delete selection. One picker, reset when you switch category (the ids/meaning change: a
  // character is keyed by name, threads + canon by id), so a stale tick can't delete the wrong thing.
  const bulk = useSelection();
  useEffect(() => {
    bulk.clear();
  }, [ledgerCat, bulk.clear]);
  const canonHere = canonKind ? data.canon.filter((c) => (c.kind ?? "other") === canonKind) : [];
  const selectableIds = isChars
    ? data.characters.map((c) => c.character)
    : isThreads
      ? data.threads.map((th) => th.id)
      : canonHere.map((c) => c.id);
  const bulkDelete = () => {
    const what = isChars ? "character" : isThreads ? "thread" : "entry";
    if (!confirm(`Delete ${bulk.count} ${what}${bulk.count === 1 ? "" : "s"}?`)) return;
    const fn = isChars
      ? (name: string) => api.deleteCharacter(data.bookId ?? "", name)
      : isThreads
        ? (id: string) => api.deleteThread(id)
        : (id: string) => api.deleteCanon(id);
    void data.runBulk(bulk.ids, fn);
    bulk.clear();
  };

  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px",
        )}
      >
        <div>
          <h1
            style={css(
              "margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:28px;color:var(--ink)",
            )}
          >
            World ledger
          </h1>
          <p style={css("margin:0;color:var(--dim);font-size:14px")}>
            The Oracle's canon — the hard numbers and lore the continuity passes check prose
            against. Author it here before you write.
          </p>
        </div>
        <div style={css("display:flex;gap:9px;align-items:center;flex-wrap:wrap")}>
          <button
            onClick={() => {
              setCanonEdit({ mode: "new" });
              setCharEdit(null);
            }}
            style={css(btn)}
          >
            + Canon entry
          </button>
          <button
            onClick={rebuildIndex}
            disabled={ingesting}
            title="Ledger “Clean rebuild from docs” deletes stale repo-ingested canon chunks (doc_path IS NOT NULL) and rebuilds from current series/canon while preserving hand-authored entries (doc_path IS NULL)"
            style={css(ghost)}
          >
            {ingesting ? "Cleaning…" : "⟳ Clean rebuild from docs"}
          </button>
        </div>
      </div>

      {notice && (
        <div
          style={css(
            "margin-bottom:16px;padding:9px 12px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:12.5px",
          )}
        >
          {notice}
        </div>
      )}

      <div style={css("display:grid;grid-template-columns:184px 1fr;gap:22px;align-items:start")}>
        <div style={css("display:flex;flex-direction:column;gap:3px;position:sticky;top:84px")}>
          {cats.map((cat) => {
            const active = ledgerCat === cat.id;
            return (
              <button
                key={cat.id}
                onClick={() => setLedgerCat(cat.id)}
                style={css(
                  `display:flex;align-items:center;width:100%;padding:9px 12px;border:1px solid ${active ? "var(--accentLine)" : "transparent"};border-radius:8px;background:${active ? "var(--accentSoft)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-family:var(--ui);font-size:13.5px;cursor:pointer`,
                )}
              >
                {cat.label}
                <span
                  style={css(
                    "margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)",
                  )}
                >
                  {cat.count}
                </span>
              </button>
            );
          })}
        </div>

        <div style={css("min-width:0")}>
          {/* canon editor (general "+ Canon entry", per-kind add, or edit) renders above whatever's selected */}
          {canonEdit && (
            <CanonForm
              key={canonEdit.mode === "edit" ? canonEdit.entity.id : `new:${canonEdit.kind ?? ""}`}
              initial={canonEdit.mode === "edit" ? canonEdit.entity : null}
              fixedKind={canonEdit.mode === "new" ? canonEdit.kind : undefined}
              onCancel={() => setCanonEdit(null)}
              onSave={async (body) => {
                if (canonEdit.mode === "edit") await data.updateCanon(canonEdit.entity.id, body);
                else await data.createCanon(body);
                setCanonEdit(null);
                if (body.kind && body.kind !== "character") setLedgerCat(`canon:${body.kind}`);
              }}
            />
          )}

          {isChars && (
            <div style={css("display:flex;flex-direction:column;gap:14px")}>
              {charEdit ? (
                <CharacterForm
                  key={charEdit === "new" ? "new" : charEdit.character}
                  initial={charEdit === "new" ? null : charEdit}
                  onCancel={() => setCharEdit(null)}
                  onSave={async (name, body) => {
                    await data.upsertCharacter(name, body);
                    setCharEdit(null);
                  }}
                />
              ) : (
                <div style={css("display:flex;align-items:center;justify-content:space-between")}>
                  <p style={css("margin:0;font-size:13px;color:var(--dim);line-height:1.5")}>
                    Hard numbers the Oracle tracks; continuity flags prose that disagrees. Seed a
                    character's starting stats here.
                  </p>
                  <button
                    onClick={() => {
                      setCharEdit("new");
                      setCanonEdit(null);
                    }}
                    style={css(btn)}
                  >
                    + Add character
                  </button>
                </div>
              )}

              {data.characters.length === 0 && !charEdit ? (
                <Empty>
                  No character state yet — add one to set a baseline, or it accrues as you approve
                  scenes whose beats declare stat changes.
                </Empty>
              ) : (
                <div style={css("display:grid;grid-template-columns:1fr 1fr;gap:14px")}>
                  {data.characters.map((ch) => (
                    <div
                      key={ch.character}
                      style={css(
                        "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);overflow:hidden",
                      )}
                    >
                      <div
                        style={css(
                          "display:flex;align-items:center;gap:12px;padding:15px 16px;border-bottom:1px solid var(--line);background:var(--bg2b)",
                        )}
                      >
                        <div
                          style={css(
                            "width:38px;height:38px;border-radius:9px;background:var(--accentSoft);border:1px solid var(--accentLine);display:flex;align-items:center;justify-content:center;font-family:var(--display);font-size:17px;color:var(--accent);flex:none",
                          )}
                        >
                          {ch.character.charAt(0)}
                        </div>
                        <div style={css("min-width:0;flex:1")}>
                          <div
                            style={css(
                              "font-family:var(--display);font-size:16px;color:var(--ink)",
                            )}
                          >
                            {ch.character}
                          </div>
                          <div
                            style={css(
                              "font-family:var(--mono);font-size:10.5px;text-transform:uppercase;color:var(--dim);margin-top:2px",
                            )}
                          >
                            {ch.is_pov ? "POV" : "character"}
                            {ch.provisional ? " · provisional" : ""}
                          </div>
                        </div>
                        <div style={css("display:flex;gap:6px;flex:none;align-items:center")}>
                          <input
                            type="checkbox"
                            checked={bulk.has(ch.character)}
                            onChange={() => bulk.toggle(ch.character)}
                            title="select"
                            style={css(
                              "width:15px;height:15px;cursor:pointer;accent-color:var(--accent);margin-right:2px",
                            )}
                          />
                          <button
                            onClick={() => {
                              setCharEdit(ch);
                              setCanonEdit(null);
                            }}
                            style={css(ghost)}
                          >
                            edit
                          </button>
                          <button
                            onClick={() => {
                              if (confirm(`Delete ${ch.character}'s tracked stats?`))
                                data.deleteCharacter(ch.character);
                            }}
                            style={css(ghost)}
                          >
                            ×
                          </button>
                        </div>
                      </div>
                      <div style={css("padding:13px 16px")}>
                        {Object.keys(ch.stats).length === 0 && (
                          <div
                            style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}
                          >
                            no tracked stats
                          </div>
                        )}
                        {Object.entries(ch.stats).map(([k, v]) => (
                          <div
                            key={k}
                            style={css(
                              "display:flex;justify-content:space-between;gap:12px;padding:5px 0;font-size:13px;border-bottom:1px solid var(--hairline)",
                            )}
                          >
                            <span
                              style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}
                            >
                              {k}
                            </span>
                            <span style={css("color:var(--ink);text-align:right")}>
                              {statValue(v)}
                            </span>
                          </div>
                        ))}
                        {ch.body && (
                          <p
                            style={css(
                              "margin:10px 0 0;font-size:12.5px;color:var(--dim);line-height:1.5",
                            )}
                          >
                            {ch.body}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {isThreads && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <div style={css("display:flex;align-items:center;justify-content:space-between")}>
                <p style={css("margin:0;font-size:13px;color:var(--dim);line-height:1.5")}>
                  Follow a relationship or plot thread across the scenes it touches.
                </p>
                <button onClick={() => setThreadAdding(true)} style={css(btn)}>
                  + New thread
                </button>
              </div>
              {threadAdding && (
                <ThreadForm
                  kinds={Object.keys(threadKinds)}
                  onCancel={() => setThreadAdding(false)}
                  onSave={async (body) => {
                    await data.createThread(body);
                    setThreadAdding(false);
                  }}
                />
              )}
              {data.threads.length === 0 && !threadAdding && (
                <Empty>No threads yet — add one to track an arc across scenes.</Empty>
              )}
              {data.threads.map((th) => {
                const sel = selectedThread === th.id;
                const kindColor = threadKinds[th.kind ?? ""] ?? t.dim;
                return (
                  <div
                    key={th.id}
                    onClick={() => selectThread(th.id)}
                    style={css(
                      `background:var(--bg2);border:1px solid ${sel ? "var(--accentLine)" : "var(--line)"};border-radius:var(--r);padding:16px 18px;cursor:pointer;box-shadow:${sel ? "var(--shadow)" : "none"}`,
                    )}
                  >
                    <div
                      style={css(
                        "display:flex;align-items:center;gap:11px;margin-bottom:8px;flex-wrap:wrap",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={bulk.has(th.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => bulk.toggle(th.id)}
                        style={css(
                          "width:15px;height:15px;cursor:pointer;accent-color:var(--accent);flex:none",
                        )}
                      />
                      <span
                        style={css("font-family:var(--display);font-size:18px;color:var(--ink)")}
                      >
                        {th.name}
                      </span>
                      {th.kind && (
                        <span
                          style={css(
                            `font-family:var(--mono);font-size:9.5px;text-transform:uppercase;color:${kindColor};background:color-mix(in srgb,${kindColor} 13%,transparent);border-radius:999px;padding:3px 9px`,
                          )}
                        >
                          {th.kind}
                        </span>
                      )}
                      <span
                        style={css(
                          "margin-left:auto;font-family:var(--mono);font-size:10.5px;color:var(--dim)",
                        )}
                      >
                        {th.state ? `state · ${th.state}` : ""}
                      </span>
                    </div>
                    {th.note && (
                      <p
                        style={css(
                          "margin:0 0 14px;font-size:13.5px;color:var(--dim);line-height:1.55",
                        )}
                      >
                        {th.note}
                      </p>
                    )}
                    <div style={css("display:flex;align-items:center;flex-wrap:wrap;row-gap:10px")}>
                      {th.beats.map((b, i) => (
                        <div key={b.id} style={css("display:flex;align-items:center")}>
                          <div
                            style={css(
                              `display:flex;flex-direction:column;gap:2px;padding:7px 11px;border-radius:8px;border:1px solid ${b.flag ? "color-mix(in srgb,var(--bad) 40%,var(--line))" : "var(--line)"};background:${b.flag ? "color-mix(in srgb,var(--bad) 9%,var(--bg3))" : "var(--bg3)"};white-space:nowrap`,
                            )}
                          >
                            <span
                              style={css("font-family:var(--mono);font-size:9px;color:var(--dim)")}
                            >
                              SCENE {b.scene_no}
                            </span>
                            <span style={css("font-size:12.5px;color:var(--ink)")}>
                              {b.label ?? "—"}
                            </span>
                          </div>
                          {i !== th.beats.length - 1 && (
                            <span style={css("margin:0 9px;color:var(--dim);font-size:13px")}>
                              →
                            </span>
                          )}
                        </div>
                      ))}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setBeatFor(beatFor === th.id ? null : th.id);
                        }}
                        style={css(
                          "margin-left:10px;padding:6px 10px;border-radius:7px;border:1px dashed var(--line);background:transparent;color:var(--dim);font-size:11.5px;cursor:pointer;font-family:var(--ui)",
                        )}
                      >
                        + beat
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (confirm(`Delete thread "${th.name}"?`)) data.deleteThread(th.id);
                        }}
                        style={css(
                          "margin-left:auto;padding:6px 10px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--dim);font-size:11.5px;cursor:pointer;font-family:var(--ui)",
                        )}
                      >
                        delete
                      </button>
                    </div>
                    {beatFor === th.id && (
                      <BeatForm
                        onCancel={() => setBeatFor(null)}
                        onSave={async (body) => {
                          await data.addThreadBeat(th.id, body);
                          setBeatFor(null);
                        }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {isRules && <RulesSection />}

          {canonKind && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <div style={css("display:flex;align-items:center;justify-content:space-between")}>
                <p style={css("margin:0;font-size:13px;color:var(--dim)")}>
                  {cap(canonKind)} the drafter & planner can retrieve.
                </p>
                <button
                  onClick={() => {
                    setCanonEdit({ mode: "new", kind: canonKind });
                    setCharEdit(null);
                  }}
                  style={css(btn)}
                >
                  + Add to {canonKind}
                </button>
              </div>
              {data.canon.filter((c) => (c.kind ?? "other") === canonKind).length === 0 && (
                <Empty>Nothing in this section yet.</Empty>
              )}
              {data.canon
                .filter((c) => (c.kind ?? "other") === canonKind)
                .map((e) => (
                  <div
                    key={e.id}
                    style={css(
                      "background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:15px 18px",
                    )}
                  >
                    <div
                      style={css(
                        "display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:5px",
                      )}
                    >
                      <div
                        style={css("font-family:var(--display);font-size:16px;color:var(--ink)")}
                      >
                        {e.name ?? "—"}
                      </div>
                      <div style={css("display:flex;gap:6px;flex:none;align-items:center")}>
                        <input
                          type="checkbox"
                          checked={bulk.has(e.id)}
                          onChange={() => bulk.toggle(e.id)}
                          title="select"
                          style={css(
                            "width:15px;height:15px;cursor:pointer;accent-color:var(--accent);margin-right:2px",
                          )}
                        />
                        <button
                          onClick={() => {
                            setCanonEdit({ mode: "edit", entity: e });
                            setCharEdit(null);
                          }}
                          style={css(ghost)}
                        >
                          edit
                        </button>
                        <button
                          onClick={() => {
                            if (confirm(`Delete "${e.name ?? "entry"}"?`)) data.deleteCanon(e.id);
                          }}
                          style={css(ghost)}
                        >
                          ×
                        </button>
                      </div>
                    </div>
                    {e.body && (
                      <p
                        style={css(
                          "margin:0;font-size:13px;color:var(--dim);line-height:1.55;white-space:pre-wrap",
                        )}
                      >
                        {e.body}
                      </p>
                    )}
                  </div>
                ))}
            </div>
          )}
        </div>
      </div>

      <BulkBar
        count={bulk.count}
        noun={isChars ? "character" : isThreads ? "thread" : "entry"}
        onClear={bulk.clear}
      >
        {bulk.count < selectableIds.length && (
          <BulkButton onClick={() => bulk.toggleAll(selectableIds)}>
            Select all {selectableIds.length}
          </BulkButton>
        )}
        <BulkButton tone="bad" onClick={bulkDelete}>
          Delete selected
        </BulkButton>
      </BulkBar>
    </div>
  );
}

function CharacterForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: CharacterStateOut | null;
  onSave: (name: string, body: { stats: Record<string, unknown>; body: string | null }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.character ?? "");
  const [desc, setDesc] = useState(initial?.body ?? "");
  const [rows, setRows] = useState<{ k: string; v: string }[]>(
    initial
      ? Object.entries(initial.stats).map(([k, v]) => ({ k, v: rawOf(v) }))
      : [{ k: "", v: "" }],
  );

  const setRow = (i: number, patch: Partial<{ k: string; v: string }>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const save = () => {
    if (!name.trim()) return;
    const stats: Record<string, unknown> = {};
    for (const { k, v } of rows) {
      const key = k.trim();
      if (key) stats[key] = coerce(v);
    }
    onSave(name.trim(), { stats, body: desc.trim() || null });
  };

  return (
    <div
      style={css(
        "background:var(--bg2);border:1px solid var(--accentLine);border-radius:var(--r);padding:16px 18px",
      )}
    >
      <div
        style={css(
          "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:12px",
        )}
      >
        {initial ? `Edit ${initial.character}` : "New character"}
      </div>
      <label style={css("display:block;margin-bottom:10px")}>
        <span style={css(fieldLabel)}>Name</span>
        <input
          value={name}
          disabled={!!initial}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Soren"
          style={css(input + (initial ? ";opacity:.6" : ""))}
        />
      </label>
      <div style={css(fieldLabel)}>Stats</div>
      <div style={css("display:flex;flex-direction:column;gap:7px;margin-bottom:10px")}>
        {rows.map((r, i) => (
          <div key={i} style={css("display:flex;gap:8px;align-items:center")}>
            <input
              value={r.k}
              onChange={(e) => setRow(i, { k: e.target.value })}
              placeholder="stat (e.g. level)"
              style={css(
                "flex:1 1 40%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12.5px;font-family:var(--mono)",
              )}
            />
            <input
              value={r.v}
              onChange={(e) => setRow(i, { v: e.target.value })}
              placeholder="value (e.g. 5)"
              style={css(
                "flex:1 1 50%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12.5px;font-family:var(--mono)",
              )}
            />
            <button
              onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
              title="remove"
              style={css(
                "flex:none;background:none;border:none;color:var(--dim);font-size:15px;cursor:pointer",
              )}
            >
              ×
            </button>
          </div>
        ))}
        <button
          onClick={() => setRows((rs) => [...rs, { k: "", v: "" }])}
          style={css(ghost + ";align-self:flex-start")}
        >
          + stat
        </button>
      </div>
      <label style={css("display:block;margin-bottom:12px")}>
        <span style={css(fieldLabel)}>
          Description{" "}
          <span style={css("text-transform:none;letter-spacing:0")}>
            (optional — canon body, fed to retrieval)
          </span>
        </span>
        <textarea
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="Who they are, appearance, role…"
          style={css(
            "width:100%;min-height:60px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:9px 11px;font-size:13px;line-height:1.5;resize:vertical;font-family:var(--ui)",
          )}
        />
      </label>
      <div style={css("display:flex;gap:9px")}>
        <button onClick={save} disabled={!name.trim()} style={css(btn)}>
          {initial ? "Save" : "Add character"}
        </button>
        <button onClick={onCancel} style={css(ghost)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function CanonForm({
  initial,
  fixedKind,
  onSave,
  onCancel,
}: {
  initial: CanonEntityOut | null;
  fixedKind?: string;
  onSave: (body: { kind: string | null; name: string | null; body: string | null }) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState(initial?.kind ?? fixedKind ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const save = () =>
    onSave({ kind: kind.trim() || null, name: name.trim() || null, body: body.trim() || null });

  return (
    <div
      style={css(
        "background:var(--bg2);border:1px solid var(--accentLine);border-radius:var(--r);padding:16px 18px;margin-bottom:16px",
      )}
    >
      <div
        style={css(
          "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:12px",
        )}
      >
        {initial ? "Edit canon entry" : "New canon entry"}
      </div>
      <div style={css("display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px")}>
        <label style={css("flex:1 1 160px")}>
          <span style={css(fieldLabel)}>Kind</span>
          <input
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            placeholder="location / faction / item / lore…"
            style={css(input)}
          />
        </label>
        <label style={css("flex:2 1 220px")}>
          <span style={css(fieldLabel)}>Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Eriadne"
            style={css(input)}
          />
        </label>
      </div>
      <label style={css("display:block;margin-bottom:12px")}>
        <span style={css(fieldLabel)}>
          Body{" "}
          <span style={css("text-transform:none;letter-spacing:0")}>
            (what the drafter & planner retrieve)
          </span>
        </span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="The lore/description…"
          style={css(
            "width:100%;min-height:96px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:9px 11px;font-size:13px;line-height:1.55;resize:vertical;font-family:var(--ui)",
          )}
        />
      </label>
      <div style={css("display:flex;gap:9px")}>
        <button onClick={save} style={css(btn)}>
          {initial ? "Save" : "Add entry"}
        </button>
        <button onClick={onCancel} style={css(ghost)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function ThreadForm({
  kinds,
  onSave,
  onCancel,
}: {
  kinds: string[];
  onSave: (body: ThreadIn) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("");
  const [note, setNote] = useState("");
  const save = () => {
    if (!name.trim()) return;
    onSave({
      name: name.trim(),
      kind: kind.trim() || null,
      state: "active",
      note: note.trim() || null,
    });
  };

  return (
    <div
      style={css(
        "background:var(--bg2);border:1px solid var(--accentLine);border-radius:var(--r);padding:16px 18px",
      )}
    >
      <div
        style={css(
          "font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:12px",
        )}
      >
        New thread
      </div>
      <div style={css("display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px")}>
        <label style={css("flex:2 1 200px")}>
          <span style={css(fieldLabel)}>Name</span>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Soren ⇄ Lyra"
            style={css(input)}
          />
        </label>
        <label style={css("flex:1 1 150px")}>
          <span style={css(fieldLabel)}>Kind</span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            style={css(input + ";cursor:pointer")}
          >
            <option value="">—</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {cap(k)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label style={css("display:block;margin-bottom:12px")}>
        <span style={css(fieldLabel)}>
          Note <span style={css("text-transform:none;letter-spacing:0")}>(optional)</span>
        </span>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="what this thread tracks…"
          style={css(input)}
        />
      </label>
      <div style={css("display:flex;gap:9px")}>
        <button onClick={save} disabled={!name.trim()} style={css(btn)}>
          Add thread
        </button>
        <button onClick={onCancel} style={css(ghost)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function BeatForm({
  onSave,
  onCancel,
}: {
  onSave: (body: ThreadBeatIn) => void;
  onCancel: () => void;
}) {
  const [sceneNo, setSceneNo] = useState("");
  const [label, setLabel] = useState("");
  const n = Number(sceneNo);
  const valid = sceneNo.trim() !== "" && Number.isFinite(n);
  const save = () => {
    if (valid) onSave({ scene_no: n, label: label.trim() || null });
  };

  return (
    <div
      onClick={(e) => e.stopPropagation()}
      style={css(
        "margin-top:12px;padding:12px 14px;border:1px solid var(--accentLine);border-radius:9px;background:var(--bg2b);display:flex;gap:9px;align-items:flex-end;flex-wrap:wrap",
      )}
    >
      <label>
        <span style={css(fieldLabel)}>Scene</span>
        <input
          type="number"
          min={1}
          autoFocus
          value={sceneNo}
          onChange={(e) => setSceneNo(e.target.value)}
          placeholder="5"
          style={css(
            "width:80px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13px;font-family:var(--ui)",
          )}
        />
      </label>
      <label style={css("flex:1 1 160px")}>
        <span style={css(fieldLabel)}>
          Label <span style={css("text-transform:none;letter-spacing:0")}>(optional)</span>
        </span>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. threadbound"
          style={css(input)}
        />
      </label>
      <button onClick={save} disabled={!valid} style={css(btn)}>
        Add beat
      </button>
      <button onClick={onCancel} style={css(ghost)}>
        Cancel
      </button>
    </div>
  );
}

function RulesSection() {
  const data = useDeskData();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const pending = data.ruleProposals.filter((r) => r.status === "pending");
  const decided = data.ruleProposals.filter((r) => r.status !== "pending");

  const distill = async () => {
    setBusy(true);
    setNotice(null);
    const n = await data.distillRules();
    setBusy(false);
    setNotice(
      n === 0
        ? "No new rules — distillation found nothing durable in recent edits (or there are no edits yet)."
        : `Proposed ${n} new rule${n === 1 ? "" : "s"} from your recent edits — review below.`,
    );
  };

  return (
    <div style={css("display:flex;flex-direction:column;gap:12px")}>
      <div
        style={css(
          "display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap",
        )}
      >
        <p style={css("margin:0;font-size:13px;color:var(--dim);line-height:1.5;max-width:560px")}>
          Rules distilled from your hand-edits. Accepting one appends it to that POV's voice spec,
          which the drafter reads on the next scene — so the agent learns your style without
          retraining.
        </p>
        <button
          onClick={distill}
          disabled={busy}
          title="Read recent agent→author edit pairs and propose durable voice/dialogue rules"
          style={css(btn)}
        >
          {busy ? "Distilling…" : "⟳ Distill from edits"}
        </button>
      </div>
      {notice && (
        <div
          style={css(
            "padding:9px 12px;border-radius:7px;border:1px solid var(--accentLine);background:var(--accentSoft);color:var(--ink);font-size:12.5px",
          )}
        >
          {notice}
        </div>
      )}

      {pending.length === 0 && decided.length === 0 && (
        <Empty>
          No proposed rules yet — approve a few hand-edited scenes, then distill to turn those edits
          into voice rules.
        </Empty>
      )}

      {pending.map((r) => (
        <RuleCard key={r.id} rule={r} />
      ))}

      {decided.length > 0 && (
        <>
          <div
            style={css(
              "margin-top:6px;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)",
            )}
          >
            Reviewed
          </div>
          {decided.map((r) => (
            <RuleCard key={r.id} rule={r} />
          ))}
        </>
      )}
    </div>
  );
}

function RuleCard({ rule }: { rule: RuleProposalOut }) {
  const data = useDeskData();
  const [text, setText] = useState(rule.rule_text);
  const pending = rule.status === "pending";
  const accepted = rule.status === "accepted";
  const kindColor = rule.kind === "dialogue" ? "var(--info)" : "var(--accent)";
  const statusColor = accepted
    ? "var(--good)"
    : rule.status === "rejected"
      ? "var(--bad)"
      : "var(--dim)";

  return (
    <div
      style={css(
        `background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;opacity:${pending ? 1 : 0.72}`,
      )}
    >
      <div style={css("display:flex;align-items:center;gap:9px;margin-bottom:9px;flex-wrap:wrap")}>
        <span
          style={css(
            "font-family:var(--mono);font-size:10px;text-transform:uppercase;color:var(--dim);background:var(--bg3);border:1px solid var(--line);border-radius:999px;padding:2px 8px",
          )}
        >
          {rule.pov}
        </span>
        <span
          style={css(
            `font-family:var(--mono);font-size:9.5px;text-transform:uppercase;color:${kindColor};background:color-mix(in srgb,${kindColor} 13%,transparent);border-radius:999px;padding:3px 9px`,
          )}
        >
          {rule.kind}
        </span>
        {!pending && (
          <span
            style={css(
              `margin-left:auto;font-family:var(--mono);font-size:10px;text-transform:uppercase;color:${statusColor}`,
            )}
          >
            {rule.status}
          </span>
        )}
      </div>
      {pending ? (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={css(
            "width:100%;min-height:42px;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13.5px;line-height:1.5;resize:vertical;font-family:var(--ui)",
          )}
        />
      ) : (
        <div style={css("font-size:13.5px;color:var(--ink);line-height:1.5")}>{rule.rule_text}</div>
      )}
      {rule.rationale && (
        <p
          style={css(
            "margin:8px 0 0;font-size:12px;color:var(--dim);line-height:1.5;font-style:italic",
          )}
        >
          {rule.rationale}
        </p>
      )}
      {pending && (
        <div style={css("display:flex;gap:9px;margin-top:11px")}>
          <button
            onClick={() =>
              data.decideRuleProposal(rule.id, {
                status: "accepted",
                rule_text: text.trim() || null,
              })
            }
            disabled={!text.trim()}
            style={css(btn)}
          >
            Accept
          </button>
          <button
            onClick={() => data.decideRuleProposal(rule.id, { status: "rejected" })}
            style={css(ghost)}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div
      style={css(
        "background:var(--bg2);border:1px dashed var(--line);border-radius:var(--r);padding:40px;text-align:center;font-family:var(--mono);font-size:12.5px;color:var(--dim)",
      )}
    >
      {children}
    </div>
  );
}
