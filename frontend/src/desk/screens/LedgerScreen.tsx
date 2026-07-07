"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { invalidateCanonBodies } from "../api/hooks/useDeskCollections";
import { statValue } from "../lib/format";
import { useSelection } from "../lib/useSelection";
import BulkBar, { BulkButton } from "../components/BulkBar";
import { Button, Chip, Eyebrow, Panel } from "../components/ui";
import type { ChipTone } from "../components/ui";
import type {
  CanonCleanupPreviewOut,
  CanonEntityOut,
  CharacterStateOut,
  RuleProposalOut,
  ThreadBeatIn,
  ThreadIn,
} from "../api/types";

// Canon provenance/lifecycle filter option lists (Workstream H). `status` defaults to "active" so the
// Ledger hides retired/stale rows until you ask for them; `all` shows everything.
const STATUS_OPTIONS = ["active", "stale", "retired", "superseded", "all"] as const;
const SOURCE_OPTIONS = [
  "all",
  "repo_ingested",
  "manual",
  "packet_derived",
  "draft_derived",
  "legacy",
] as const;

// Provenance/lifecycle → Chip tone. Same hues the old local Badge used, expressed semantically.
const STATUS_TONE: Record<string, ChipTone> = {
  active: "good",
  stale: "warn",
  retired: "neutral",
  superseded: "bad",
};
const SOURCE_TONE: Record<string, ChipTone> = {
  manual: "accent",
  repo_ingested: "info",
  packet_derived: "neutral",
  draft_derived: "neutral",
  legacy: "neutral",
};

// Thread kind → raw theme var for the legacy Chip colorVar escape hatch (kept 1:1 with the old
// hex-from-theme colors).
const THREAD_KIND_VAR: Record<string, string> = {
  relationship: "--bad",
  mentorship: "--info",
  system: "--accent",
  power: "--warn",
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// Where a repo-ingested canon row came from: `doc_path › heading_path` (heading only when present).
const provenanceOf = (e: CanonEntityOut): string =>
  `${e.doc_path}${e.heading_path ? ` › ${e.heading_path}` : ""}`;

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

// Atelier display-XL screen title.
const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";
const input =
  "width:100%;background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 11px;font-size:13px;font-family:var(--ui)";
const fieldLabel =
  "display:block;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:4px";
const filterSelect =
  "background:var(--bg3);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:12.5px;font-family:var(--ui);cursor:pointer";

type CanonEdit = { mode: "new"; kind?: string } | { mode: "edit"; entity: CanonEntityOut } | null;

export default function LedgerScreen() {
  const { ledgerCat, selectedThread, setLedgerCat, selectThread } = useDesk();
  const data = useDeskData();
  const searchParams = useSearchParams();

  // Deep-link consumption (?cat=…&focus=<character name>), e.g. from a Scene continuity conflict.
  // One-shot on mount: the URL selects the category and names the character panel to highlight;
  // navigating within the tab afterwards clears the highlight.
  const [focusChar, setFocusChar] = useState<string | null>(null);
  const consumedLink = useRef(false);
  useEffect(() => {
    if (consumedLink.current) return;
    consumedLink.current = true;
    const cat = searchParams.get("cat");
    const focus = searchParams.get("focus");
    if (cat) setLedgerCat(cat);
    if (focus) setFocusChar(focus);
  }, [searchParams, setLedgerCat]);
  // The highlight belongs to the characters view; switching category ends it.
  useEffect(() => {
    if (ledgerCat !== "characters") setFocusChar(null);
  }, [ledgerCat]);
  const focusScrolled = useRef(false);

  const bookId = data.bookId;
  const [charEdit, setCharEdit] = useState<CharacterStateOut | "new" | null>(null);
  const [canonEdit, setCanonEdit] = useState<CanonEdit>(null);
  const [ingesting, setIngesting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [threadAdding, setThreadAdding] = useState(false); // inline new-thread form open
  const [beatFor, setBeatFor] = useState<string | null>(null); // thread id whose add-beat form is open

  // Stale-canon cleanup (Workstream H). The canon view reads its own filtered rows (server-side
  // status/source filters surface retired/stale canon that data.canon — active-only — omits); `query`
  // is a client-side text filter over name/summary. `cleanup` holds the preview awaiting confirmation.
  const [statusFilter, setStatusFilter] = useState<string>("active");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [canonRows, setCanonRows] = useState<CanonEntityOut[]>([]);
  const [cleanup, setCleanup] = useState<{
    action: "retire" | "delete";
    ids: string[];
    preview: CanonCleanupPreviewOut;
  } | null>(null);
  const [cleanupBusy, setCleanupBusy] = useState(false);

  const reloadCanon = useCallback(async () => {
    if (!bookId) return;
    try {
      const rows = await api.listCanon(bookId, { status: statusFilter, source: sourceFilter });
      setCanonRows(rows);
    } catch {
      /* leave the prior rows in place; a fetch blip shouldn't blank the ledger */
    }
  }, [bookId, statusFilter, sourceFilter]);
  useEffect(() => {
    void reloadCanon();
  }, [reloadCanon]);

  const canonKinds = [...new Set(canonRows.map((c) => c.kind ?? "other"))].filter(
    (k) => k !== "character",
  );
  const pendingRules = data.ruleProposals.filter((r) => r.status === "pending");
  // The rail groups: the story ledgers, then the canon corpus by kind.
  const storyCats = [
    { id: "characters", label: "Characters", count: data.characters.length },
    { id: "threads", label: "Threads", count: data.threads.length },
    { id: "voice-rules", label: "Voice rules", count: pendingRules.length },
  ];
  const canonCats = canonKinds.map((k) => ({
    id: `canon:${k}`,
    label: cap(k),
    count: canonRows.filter((c) => (c.kind ?? "other") === k).length,
  }));

  const rebuildIndex = async () => {
    if (!bookId) return;
    setIngesting(true);
    setNotice(null);
    try {
      // The rebuild replaces the corpus wholesale (new entity ids) — the once-per-session canon
      // body upgrade must re-run on the next full load or palette body search goes dark.
      invalidateCanonBodies(bookId);
      // The re-index now runs in the BACKGROUND (a full corpus re-embed used to 499-time out inside the
      // request). The endpoint returns immediately; completion — 'Canon rebuilt · N new, …' — appears in
      // the Activity drawer. Reload the ledger so any already-committed changes show; the fresh corpus
      // lands on the next load once the background pass finishes.
      await api.rebuildCanon(bookId);
      setNotice("Canon rebuild started — progress and completion appear in the Activity drawer.");
      await reloadCanon();
    } catch {
      setNotice("Couldn't start the rebuild — check the API logs.");
    } finally {
      setIngesting(false);
    }
  };

  const isChars = ledgerCat === "characters";
  const isThreads = ledgerCat === "threads";
  const isRules = ledgerCat === "voice-rules";
  const canonKind = ledgerCat.startsWith("canon:") ? ledgerCat.slice("canon:".length) : null;

  // Bulk-delete selection. One picker, reset when you switch category (the ids/meaning change: a
  // character is keyed by name, threads + canon by id), so a stale tick can't delete the wrong thing.
  const bulk = useSelection();
  // Reset the tick set when the category OR the canon filters change — the visible ids (and their
  // meaning) shift, so a stale tick must never carry over into a retire/delete.
  useEffect(() => {
    bulk.clear();
  }, [ledgerCat, statusFilter, sourceFilter, bulk.clear]);

  // Client-side text filter over the loaded canon rows (name + body/summary).
  const matchesQuery = (c: CanonEntityOut): boolean => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return `${c.name ?? ""} ${c.body ?? ""}`.toLowerCase().includes(q);
  };
  const canonHere = canonKind
    ? canonRows.filter((c) => (c.kind ?? "other") === canonKind).filter(matchesQuery)
    : [];
  const selectableIds = isChars
    ? data.characters.map((c) => c.character)
    : isThreads
      ? data.threads.map((th) => th.id)
      : canonHere.map((c) => c.id);

  // Chars/threads keep the plain confirm+delete; canon routes through the preview-before-destroy flow.
  const bulkDelete = () => {
    const what = isChars ? "character" : "thread";
    if (!confirm(`Delete ${bulk.count} ${what}${bulk.count === 1 ? "" : "s"}?`)) return;
    const fn = isChars
      ? (name: string) => api.deleteCharacter(bookId ?? "", name)
      : (id: string) => api.deleteThread(id);
    void data.runBulk(bulk.ids, fn);
    bulk.clear();
  };

  // Preview-before-destroy: dry-run the selection, show what would be retired/deleted (and which manual
  // rows are protected), and only fire the real retire/delete once the author confirms in the dialog.
  const openCleanup = async (action: "retire" | "delete") => {
    if (!bookId || bulk.count === 0) return;
    const ids = bulk.ids;
    try {
      const preview = await api.canonCleanupPreview(bookId, { ids, dry_run: true });
      setCleanup({ action, ids, preview });
    } catch {
      setNotice("Preview failed — check the API logs.");
    }
  };
  const confirmCleanup = async () => {
    if (!bookId || !cleanup) return;
    setCleanupBusy(true);
    try {
      if (cleanup.action === "retire") {
        const out = await api.retireCanon(bookId, { ids: cleanup.ids, dry_run: false });
        setNotice(
          `Retired ${out.retired} row(s)${out.protected_manual ? ` · ${out.protected_manual} manual protected` : ""}.`,
        );
      } else {
        const out = await api.bulkDeleteCanon(bookId, { ids: cleanup.ids, dry_run: false });
        setNotice(
          `Deleted ${out.deleted} row(s)${out.protected_manual ? ` · ${out.protected_manual} manual protected` : ""}.`,
        );
      }
      setCleanup(null);
      bulk.clear();
      await reloadCanon();
    } catch {
      setNotice("Action failed — check the API logs.");
    } finally {
      setCleanupBusy(false);
    }
  };

  return (
    <div>
      <header
        style={css(
          "display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:26px",
        )}
      >
        <div>
          <Eyebrow style="margin-bottom:6px">Continuity</Eyebrow>
          <h1 style={css(TITLE_XL)}>World ledger</h1>
          <p style={css("margin:6px 0 0;color:var(--dim);font-size:14px")}>
            The Oracle's canon — the hard numbers and lore the continuity passes check prose
            against. Author it here before you write.
          </p>
        </div>
        <div style={css("display:flex;gap:9px;align-items:center;flex-wrap:wrap")}>
          <Button
            variant="primary"
            onClick={() => {
              setCanonEdit({ mode: "new" });
              setCharEdit(null);
            }}
          >
            + Canon entry
          </Button>
          <Button
            variant="danger"
            onClick={rebuildIndex}
            disabled={ingesting}
            title="Ledger “Clean rebuild from docs” deletes stale repo-ingested canon chunks (doc_path IS NOT NULL) and rebuilds from current series/canon while preserving hand-authored entries (doc_path IS NULL)"
          >
            {ingesting ? "Cleaning…" : "⟳ Clean rebuild from docs"}
          </Button>
        </div>
      </header>

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
        <nav style={css("position:sticky;top:84px")}>
          <Eyebrow style="margin:0 0 8px 10px">Story</Eyebrow>
          {storyCats.map((cat) => (
            <NavLink
              key={cat.id}
              label={cat.label}
              count={cat.count}
              active={ledgerCat === cat.id}
              onClick={() => setLedgerCat(cat.id)}
            />
          ))}
          {canonCats.length > 0 && (
            <>
              <Eyebrow style="margin:20px 0 8px 10px">Canon</Eyebrow>
              {canonCats.map((cat) => (
                <NavLink
                  key={cat.id}
                  label={cat.label}
                  count={cat.count}
                  active={ledgerCat === cat.id}
                  onClick={() => setLedgerCat(cat.id)}
                />
              ))}
            </>
          )}
        </nav>

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
                await reloadCanon();
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
                  <Button
                    variant="primary"
                    onClick={() => {
                      setCharEdit("new");
                      setCanonEdit(null);
                    }}
                  >
                    + Add character
                  </Button>
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
                      id={`ledger-char-${ch.character}`}
                      ref={
                        focusChar === ch.character
                          ? (el) => {
                              if (el && !focusScrolled.current) {
                                focusScrolled.current = true;
                                el.scrollIntoView?.({ behavior: "smooth", block: "start" });
                              }
                            }
                          : undefined
                      }
                    >
                      <Panel
                        pad="0"
                        style={`overflow:hidden${
                          focusChar === ch.character ? ";border-color:var(--accentLine)" : ""
                        }`}
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
                                "font-family:var(--display);font-weight:500;font-size:16px;color:var(--ink)",
                              )}
                            >
                              {ch.character}
                            </div>
                            <Eyebrow style="margin-top:2px;font-size:10px">
                              {ch.is_pov ? "POV" : "character"}
                              {ch.provisional ? " · provisional" : ""}
                            </Eyebrow>
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
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                setCharEdit(ch);
                                setCanonEdit(null);
                              }}
                            >
                              edit
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                if (confirm(`Delete ${ch.character}'s tracked stats?`))
                                  data.deleteCharacter(ch.character);
                              }}
                            >
                              ×
                            </Button>
                          </div>
                        </div>
                        <div style={css("padding:13px 16px")}>
                          {Object.keys(ch.stats).length === 0 && (
                            <div
                              style={css(
                                "font-family:var(--mono);font-size:11.5px;color:var(--dim)",
                              )}
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
                                style={css(
                                  "font-family:var(--mono);font-size:11px;color:var(--dim)",
                                )}
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
                      </Panel>
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
                <Button variant="primary" onClick={() => setThreadAdding(true)}>
                  + New thread
                </Button>
              </div>
              {threadAdding && (
                <ThreadForm
                  kinds={Object.keys(THREAD_KIND_VAR)}
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
                return (
                  <div
                    key={th.id}
                    onClick={() => selectThread(th.id)}
                    style={css("cursor:pointer")}
                  >
                    <Panel
                      interactive
                      pad="16px 18px"
                      style={sel ? "border-color:var(--accentLine)" : ""}
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
                          style={css(
                            "font-family:var(--display);font-weight:500;font-size:18px;color:var(--ink)",
                          )}
                        >
                          {th.name}
                        </span>
                        {th.kind && (
                          <Chip
                            label={th.kind}
                            size="sm"
                            colorVar={THREAD_KIND_VAR[th.kind] ?? "--dim"}
                          />
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
                      <div
                        style={css("display:flex;align-items:center;flex-wrap:wrap;row-gap:10px")}
                      >
                        {th.beats.map((b, i) => (
                          <div key={b.id} style={css("display:flex;align-items:center")}>
                            <div
                              style={css(
                                `display:flex;flex-direction:column;gap:2px;padding:7px 11px;border-radius:8px;border:1px solid ${b.flag ? "color-mix(in srgb,var(--bad) 40%,var(--line))" : "var(--line)"};background:${b.flag ? "color-mix(in srgb,var(--bad) 9%,var(--bg3))" : "var(--bg3)"};white-space:nowrap`,
                              )}
                            >
                              <span
                                style={css(
                                  "font-family:var(--mono);font-size:9px;color:var(--dim)",
                                )}
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
                        <span
                          onClick={(e) => e.stopPropagation()}
                          style={css("margin-left:10px;display:inline-flex")}
                        >
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setBeatFor(beatFor === th.id ? null : th.id)}
                          >
                            + beat
                          </Button>
                        </span>
                        <span
                          onClick={(e) => e.stopPropagation()}
                          style={css("margin-left:auto;display:inline-flex")}
                        >
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              if (confirm(`Delete thread "${th.name}"?`)) data.deleteThread(th.id);
                            }}
                          >
                            delete
                          </Button>
                        </span>
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
                    </Panel>
                  </div>
                );
              })}
            </div>
          )}

          {isRules && <RulesSection />}

          {canonKind && (
            <div style={css("display:flex;flex-direction:column;gap:12px")}>
              <div
                style={css(
                  "display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap",
                )}
              >
                <p style={css("margin:0;font-size:13px;color:var(--dim)")}>
                  {cap(canonKind)} the drafter & planner can retrieve. Only{" "}
                  <strong style={css("color:var(--ink);font-weight:600")}>active</strong> canon
                  reaches the drafter — retire or delete stale rows below.
                </p>
                <Button
                  variant="primary"
                  onClick={() => {
                    setCanonEdit({ mode: "new", kind: canonKind });
                    setCharEdit(null);
                  }}
                >
                  + Add to {canonKind}
                </Button>
              </div>

              {/* status / source / search filters (Workstream H) */}
              <Panel pad="10px 14px">
                <div style={css("display:flex;gap:9px;flex-wrap:wrap;align-items:center")}>
                  <label style={css("display:flex;flex-direction:column;gap:3px")}>
                    <span style={css(fieldLabel + ";margin-bottom:0")}>Status</span>
                    <select
                      aria-label="status filter"
                      value={statusFilter}
                      onChange={(e) => setStatusFilter(e.target.value)}
                      style={css(filterSelect)}
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label style={css("display:flex;flex-direction:column;gap:3px")}>
                    <span style={css(fieldLabel + ";margin-bottom:0")}>Source</span>
                    <select
                      aria-label="source filter"
                      value={sourceFilter}
                      onChange={(e) => setSourceFilter(e.target.value)}
                      style={css(filterSelect)}
                    >
                      {SOURCE_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label style={css("display:flex;flex-direction:column;gap:3px;flex:1 1 180px")}>
                    <span style={css(fieldLabel + ";margin-bottom:0")}>Search</span>
                    <input
                      aria-label="search canon"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="name or text…"
                      style={css(input)}
                    />
                  </label>
                </div>
              </Panel>

              {canonHere.length === 0 && <Empty>Nothing in this section yet.</Empty>}
              {canonHere.map((e) => (
                <Panel key={e.id} pad="15px 18px">
                  <div
                    style={css(
                      "display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:5px",
                    )}
                  >
                    <div
                      style={css(
                        "display:flex;align-items:center;gap:9px;min-width:0;flex-wrap:wrap",
                      )}
                    >
                      <span
                        style={css(
                          "font-family:var(--display);font-weight:500;font-size:16px;color:var(--ink)",
                        )}
                      >
                        {e.name ?? "—"}
                      </span>
                      <Chip
                        label={e.status ?? "active"}
                        size="sm"
                        tone={STATUS_TONE[e.status ?? "active"] ?? "neutral"}
                      />
                      <Chip
                        label={e.source ?? "manual"}
                        size="sm"
                        tone={SOURCE_TONE[e.source ?? "manual"] ?? "neutral"}
                      />
                      {e.owner_topic && <Chip label={e.owner_topic} size="sm" tone="info" />}
                      {e.source_priority != null && (
                        <Chip label={`prio ${e.source_priority}`} size="sm" tone="neutral" />
                      )}
                      {e.embedding_stale && (
                        <Chip
                          label="embedding stale"
                          size="sm"
                          tone="warn"
                          title="embedded under a different backend — re-ingest or edit to re-embed"
                        />
                      )}
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
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setCanonEdit({ mode: "edit", entity: e });
                          setCharEdit(null);
                        }}
                      >
                        edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={async () => {
                          if (confirm(`Delete "${e.name ?? "entry"}"?`)) {
                            await data.deleteCanon(e.id);
                            await reloadCanon();
                          }
                        }}
                      >
                        ×
                      </Button>
                    </div>
                  </div>
                  {e.doc_path && (
                    <div
                      title={provenanceOf(e)}
                      style={css(
                        "margin:0 0 5px;font-family:var(--mono);font-size:10.5px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis",
                      )}
                    >
                      {provenanceOf(e)}
                    </div>
                  )}
                  {e.body && (
                    <p
                      style={css(
                        "margin:0;font-size:13px;color:var(--dim);line-height:1.55;white-space:pre-wrap",
                      )}
                    >
                      {e.body}
                    </p>
                  )}
                </Panel>
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
        {canonKind ? (
          <>
            <BulkButton onClick={() => void openCleanup("retire")}>Retire selected</BulkButton>
            <BulkButton tone="bad" onClick={() => void openCleanup("delete")}>
              Delete selected
            </BulkButton>
          </>
        ) : (
          <BulkButton tone="bad" onClick={bulkDelete}>
            Delete selected
          </BulkButton>
        )}
      </BulkBar>

      {cleanup && (
        <CleanupDialog
          action={cleanup.action}
          preview={cleanup.preview}
          busy={cleanupBusy}
          onCancel={() => setCleanup(null)}
          onConfirm={() => void confirmCleanup()}
        />
      )}
    </div>
  );
}

// One category link in the rail — dk-navlink hover, accentSoft when active.
function NavLink({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className="dk-navlink"
      onClick={onClick}
      style={css(
        `display:flex;align-items:center;gap:8px;width:100%;text-align:left;padding:7px 10px;margin-bottom:1px;border:none;border-radius:7px;cursor:pointer;` +
          `font-family:var(--ui);font-size:13.5px;line-height:1.35;` +
          `background:${active ? "var(--accentSoft)" : "transparent"};color:${active ? "var(--ink)" : "var(--dim)"};font-weight:${active ? "500" : "400"}`,
      )}
    >
      {label}
      <span
        style={css("margin-left:auto;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
      >
        {count}
      </span>
    </button>
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
    <Panel
      eyebrow={initial ? `Edit ${initial.character}` : "New character"}
      pad="16px 18px"
      style="border-color:var(--accentLine)"
    >
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
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setRows((rs) => [...rs, { k: "", v: "" }])}
          style="align-self:flex-start"
        >
          + stat
        </Button>
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
        <Button variant="primary" onClick={save} disabled={!name.trim()}>
          {initial ? "Save" : "Add character"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </Panel>
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
    <Panel
      eyebrow={initial ? "Edit canon entry" : "New canon entry"}
      pad="16px 18px"
      style="border-color:var(--accentLine);margin-bottom:16px"
    >
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
        <Button variant="primary" onClick={save}>
          {initial ? "Save" : "Add entry"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </Panel>
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
    <Panel eyebrow="New thread" pad="16px 18px" style="border-color:var(--accentLine)">
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
        <Button variant="primary" onClick={save} disabled={!name.trim()}>
          Add thread
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </Panel>
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
        "margin-top:12px;padding:12px 14px;border:1px solid var(--accentLine);border-radius:var(--r);background:var(--bg2b);display:flex;gap:9px;align-items:flex-end;flex-wrap:wrap",
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
      <Button variant="primary" onClick={save} disabled={!valid}>
        Add beat
      </Button>
      <Button variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
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
        <Button
          variant="primary"
          onClick={distill}
          disabled={busy}
          title="Read recent agent→author edit pairs and propose durable voice/dialogue rules"
        >
          {busy ? "Distilling…" : "⟳ Distill from edits"}
        </Button>
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
          <Eyebrow style="margin-top:6px">Reviewed</Eyebrow>
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
  const statusTone: ChipTone = accepted ? "good" : rule.status === "rejected" ? "bad" : "neutral";

  return (
    <Panel pad="14px 16px" style={pending ? "" : "opacity:.72"}>
      <div style={css("display:flex;align-items:center;gap:9px;margin-bottom:9px;flex-wrap:wrap")}>
        <Chip label={rule.pov} size="sm" tone="neutral" />
        <Chip label={rule.kind} size="sm" tone={rule.kind === "dialogue" ? "info" : "accent"} />
        {!pending && (
          <span style={css("margin-left:auto;display:inline-flex")}>
            <Chip label={rule.status} size="sm" tone={statusTone} />
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
          <Button
            variant="primary"
            onClick={() =>
              data.decideRuleProposal(rule.id, {
                status: "accepted",
                rule_text: text.trim() || null,
              })
            }
            disabled={!text.trim()}
          >
            Accept
          </Button>
          <Button
            variant="ghost"
            onClick={() => data.decideRuleProposal(rule.id, { status: "rejected" })}
          >
            Reject
          </Button>
        </div>
      )}
    </Panel>
  );
}

// Preview-before-destroy confirmation. Shows the dry-run counts + the affected rows, spells out that
// manual-source canon is protected, and only fires the real retire/delete when the author confirms.
function CleanupDialog({
  action,
  preview,
  busy,
  onCancel,
  onConfirm,
}: {
  action: "retire" | "delete";
  preview: CanonCleanupPreviewOut;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const isDelete = action === "delete";
  const count = isDelete ? preview.would_delete : preview.would_retire;
  const stat =
    "display:flex;flex-direction:column;gap:2px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg3);min-width:96px";
  const statNum = "font-family:var(--display);font-weight:500;font-size:20px;color:var(--ink)";
  const statLbl =
    "font-family:var(--mono);font-size:9.5px;text-transform:uppercase;color:var(--dim)";
  return (
    <div
      role="dialog"
      aria-label={isDelete ? "confirm delete canon" : "confirm retire canon"}
      style={css(
        "position:fixed;inset:0;z-index:120;display:flex;align-items:center;justify-content:center;padding:24px;background:var(--scrim)",
      )}
    >
      <div
        style={css(
          "background:var(--bg2);border:1px solid var(--accentLine);border-radius:var(--rLg);box-shadow:var(--shadow);width:min(560px,94vw);max-height:86vh;display:flex;flex-direction:column;overflow:hidden;animation:fadeUp .18s ease both",
        )}
      >
        <div style={css("padding:18px 20px 12px")}>
          <div
            style={css(
              "font-family:var(--display);font-weight:500;font-size:19px;color:var(--ink)",
            )}
          >
            {isDelete ? "Delete canon rows?" : "Retire canon rows?"}
          </div>
          <p style={css("margin:6px 0 0;font-size:12.5px;color:var(--dim);line-height:1.5")}>
            {isDelete
              ? "Hard delete is permanent."
              : "Retire is a soft, reversible hide — retired rows drop out of retrieval and the active view."}{" "}
            Manual-source rows are protected — they are skipped unless you selected them
            individually.
          </p>
          <div style={css("display:flex;gap:10px;flex-wrap:wrap;margin-top:14px")}>
            <div style={css(stat)}>
              <span style={css(statNum)}>{preview.matched}</span>
              <span style={css(statLbl)}>matched</span>
            </div>
            <div style={css(stat)}>
              <span style={css(statNum)}>{preview.would_retire}</span>
              <span style={css(statLbl)}>would retire</span>
            </div>
            <div style={css(stat)}>
              <span style={css(statNum)}>{preview.would_delete}</span>
              <span style={css(statLbl)}>would delete</span>
            </div>
            <div style={css(stat)}>
              <span style={css(statNum + ";color:var(--accent)")}>{preview.protected_manual}</span>
              <span style={css(statLbl)}>manual protected</span>
            </div>
          </div>
        </div>
        <div style={css("overflow:auto;padding:0 20px;flex:1")}>
          {preview.items.length === 0 ? (
            <Empty>Nothing matched this selection.</Empty>
          ) : (
            <div style={css("display:flex;flex-direction:column;gap:6px;padding-bottom:6px")}>
              {preview.items.map((it) => (
                <div
                  key={it.id}
                  style={css(
                    "display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 10px;border:1px solid var(--hairline);border-radius:8px;background:var(--bg3)",
                  )}
                >
                  <span style={css("font-size:13px;color:var(--ink)")}>{it.name ?? "—"}</span>
                  <Chip
                    label={it.status ?? "active"}
                    size="sm"
                    tone={STATUS_TONE[it.status ?? "active"] ?? "neutral"}
                  />
                  <Chip
                    label={it.source ?? "manual"}
                    size="sm"
                    tone={SOURCE_TONE[it.source ?? "manual"] ?? "neutral"}
                  />
                  <span
                    style={css(
                      "margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--dim)",
                    )}
                  >
                    {it.reason}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div
          style={css(
            "display:flex;gap:9px;justify-content:flex-end;padding:14px 20px;border-top:1px solid var(--line)",
          )}
        >
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={isDelete ? "danger" : "primary"}
            onClick={onConfirm}
            disabled={busy || count === 0}
          >
            {busy
              ? "Working…"
              : isDelete
                ? `Delete ${preview.would_delete} row(s)`
                : `Retire ${preview.would_retire} row(s)`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div
      style={css(
        "background:var(--bg2);border:1px dashed var(--line);border-radius:var(--r);padding:44px 24px;text-align:center",
      )}
    >
      <div aria-hidden style={css("font-size:18px;color:var(--accent);margin-bottom:12px")}>
        ✦
      </div>
      <p
        style={css(
          "margin:0;font-family:var(--display);font-style:italic;font-size:15.5px;line-height:1.6;color:var(--dim)",
        )}
      >
        {children}
      </p>
    </div>
  );
}
