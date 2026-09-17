"use client";

// Notes — an editor's read-through of chapters the author supplies (ADR 0035).
//
// Unlike Edit, the work is saved. A run is created on the server, works in the background and
// outlives this screen, so the screen is a client of the run rather than its owner: it re-attaches to
// an active run on mount, polls a slim status, and loads the full result once the run is terminal.
//
// Load-bearing for money or trust:
// - `client_request_id` is minted whenever the composition changes and reused when Run is retried,
//   so a Run whose response was lost cannot start (and bill) a second read-through.
// - The coverage banner is always shown with results: a partial run, or cross-chapter notes built
//   from summaries, must never read as a complete edit of the book.
// - Highlights use the server's offsets only (lib/anchorSpans). Nothing here searches the text.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import { ApiError, api } from "../api/client";
import { useDeskData } from "../api/data";
import type {
  ReadThroughNoteOut,
  ReadThroughOut,
  ReadThroughStatusOut,
  ReadThroughSummaryOut,
} from "../api/types";
import Composer, {
  chapterProblem,
  type DraftChapter,
  type DraftUpdate,
} from "../components/readThrough/Composer";
import CoverageBanner from "../components/readThrough/CoverageBanner";
import NotesPane, { type NoteStatus } from "../components/readThrough/NotesPane";
import RunBanner from "../components/readThrough/RunBanner";
import TextPane from "../components/readThrough/TextPane";
import { Button, Eyebrow, Panel, Spinner } from "../components/ui";
import { css } from "../css";
import { anchorHighlightChapter, anchorKey, anchorsForChapter } from "../lib/anchorSpans";
import {
  isActiveStatus,
  isTerminalStatus,
  readThroughMarkdown,
  snapshotDate,
  sortNotes,
  sortedChapters,
} from "../lib/readThroughMarkdown";

const POLL_MS = 1500;

const WRAP = "width:min(96vw,1800px);margin:0 auto;padding:0 clamp(12px,2vw,32px)";
const TITLE_XL =
  "margin:0;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)";
const MONO = "font-family:var(--mono);font-size:11px;color:var(--dim)";
const ERROR_BOX =
  "border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));border-radius:var(--r);" +
  "background:color-mix(in srgb,var(--bad) 8%,transparent);padding:10px 14px;font-size:13px";
const SELECT =
  "background:var(--bg3);border:1px solid var(--line);border-radius:var(--r);padding:6px 8px;" +
  "color:var(--ink);font-family:var(--ui);font-size:13px;max-width:100%";

function mintRequestId(): string {
  const c = globalThis.crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID();
  // randomUUID needs a secure context; getRandomValues does not (e.g. the Desk over a LAN IP).
  const bytes = new Uint8Array(16);
  if (typeof c?.getRandomValues === "function") c.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The server's `detail` when it sent one (409 / 422), else the error's own message. */
function errorDetail(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e.data as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) =>
          typeof d === "string" ? d : ((d as { msg?: string })?.msg ?? JSON.stringify(d)),
        )
        .join("; ");
    }
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      return typeof message === "string" ? message : JSON.stringify(detail);
    }
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

function replaceUrlId(id: string | null) {
  try {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("id", id);
    else url.searchParams.delete("id");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  } catch {
    /* no window (SSR) — the URL is a convenience only */
  }
}

export default function NotesScreen() {
  const { bookId } = useDeskData();
  const searchParams = useSearchParams();
  const requestedId = useRef<string | null>(searchParams?.get("id") ?? null);

  // --- the book's runs and the one being looked at ---------------------------------------------
  const [runs, setRuns] = useState<ReadThroughSummaryOut[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const [rt, setRt] = useState<ReadThroughOut | null>(null);
  // Notes live apart from `rt` so a status PATCH replaces one note without touching `rt.notes`,
  // which the text pane's anchors are derived from (keeps that placement memoized across patches).
  const [notes, setNotes] = useState<ReadThroughNoteOut[]>([]);
  const [loadingRun, setLoadingRun] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const loadSeq = useRef(0);

  // --- the book's active run (at most one) -------------------------------------------------------
  const [activeId, setActiveId] = useState<string | null>(null);
  const [status, setStatus] = useState<ReadThroughStatusOut | null>(null);
  const [pollNotice, setPollNotice] = useState<string | null>(null);
  const pollingId = useRef<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [stopBusy, setStopBusy] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const stopInFlight = useRef(false);

  // --- composer -------------------------------------------------------------------------------
  const [draft, setDraft] = useState<DraftChapter[]>([]);
  const [requestId, setRequestId] = useState<string>(mintRequestId);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submitInFlight = useRef(false);

  // --- results interaction --------------------------------------------------------------------
  const [chapterId, setChapterId] = useState<string | null>(null);
  const [activeAnchor, setActiveAnchor] = useState<string | null>(null);
  const [scrollNonce, setScrollNonce] = useState(0);
  const [patching, setPatching] = useState<Record<string, boolean>>({});
  const [patchErrors, setPatchErrors] = useState<Record<string, string>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  const loadRun = useCallback(async (id: string) => {
    const seq = ++loadSeq.current;
    setLoadingRun(true);
    setRunError(null);
    try {
      const full = await api.readThrough(id);
      if (seq !== loadSeq.current) return;
      const ordered = sortedChapters(full);
      const firstWithNotes = ordered.find((c) => full.notes.some((n) => n.chapter_id === c.id));
      setRt(full);
      setNotes(full.notes);
      setChapterId((firstWithNotes ?? ordered[0])?.id ?? null);
      setActiveAnchor(null);
    } catch (e) {
      if (seq === loadSeq.current) setRunError(errorDetail(e));
    } finally {
      if (seq === loadSeq.current) setLoadingRun(false);
    }
  }, []);

  const select = useCallback(
    (run: { id: string; status: string } | null) => {
      ++loadSeq.current; // a load still in flight belongs to the previous selection
      selectedRef.current = run?.id ?? null;
      setSelectedId(run?.id ?? null);
      setRt(null);
      setNotes([]);
      setChapterId(null);
      setActiveAnchor(null);
      setRunError(null);
      setLoadingRun(false);
      setPatchErrors({});
      setConfirmDelete(false);
      setDeleteError(null);
      setCopyState("idle");
      replaceUrlId(run?.id ?? null);
      // An active run has no result to load yet; the poller loads it when it turns terminal.
      if (run && isTerminalStatus(run.status)) void loadRun(run.id);
    },
    [loadRun],
  );

  const refreshList = useCallback(async () => {
    if (!bookId) return;
    try {
      setRuns(await api.readThroughs(bookId));
      setListError(null);
    } catch (e) {
      setListError(errorDetail(e));
    }
  }, [bookId]);

  const stopPolling = useCallback(() => {
    pollingId.current = null;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    pollTimer.current = null;
  }, []);

  const finishActive = useCallback(
    async (id: string) => {
      setActiveId(null);
      setStopError(null);
      if (selectedRef.current === id) await loadRun(id);
      await refreshList();
    },
    [loadRun, refreshList],
  );

  // Chained setTimeout, never setInterval: the next poll is scheduled only after this one settles,
  // so a slow status call can never stack requests.
  const startPolling = useCallback(
    (id: string) => {
      stopPolling();
      pollingId.current = id;
      setActiveId(id);
      setStatus(null);
      setPollNotice(null);
      const tick = async () => {
        if (pollingId.current !== id) return;
        try {
          const s = await api.readThroughStatus(id);
          if (pollingId.current !== id) return;
          setStatus(s);
          setPollNotice(null);
          if (isTerminalStatus(s.status)) {
            stopPolling();
            await finishActive(id);
            return;
          }
        } catch (e) {
          if (pollingId.current !== id) return;
          if (e instanceof ApiError && e.status === 404) {
            stopPolling();
            setActiveId(null);
            setStatus(null);
            setPollNotice("That read-through no longer exists — it may have been deleted.");
            if (selectedRef.current === id) select(null);
            await refreshList();
            return;
          }
          setPollNotice(`Couldn't check progress (${errorDetail(e)}). Retrying…`);
        }
        if (pollingId.current === id) pollTimer.current = setTimeout(() => void tick(), POLL_MS);
      };
      void tick();
    },
    [stopPolling, finishActive, refreshList, select],
  );

  // On mount (and on a book switch): list the runs, re-attach to an active one, show the latest.
  useEffect(() => {
    if (!bookId) return;
    let cancelled = false;
    setActiveId(null);
    setStatus(null);
    (async () => {
      try {
        const list = await api.readThroughs(bookId);
        if (cancelled) return;
        setRuns(list);
        setListError(null);
        const active = list.find((r) => isActiveStatus(r.status));
        if (active) startPolling(active.id);
        const wanted = requestedId.current
          ? list.find((r) => r.id === requestedId.current)
          : undefined;
        requestedId.current = null;
        select(wanted ?? active ?? list[0] ?? null);
      } catch (e) {
        if (!cancelled) setListError(errorDetail(e));
      }
    })();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [bookId, select, startPolling, stopPolling]);

  const updateDraft = useCallback((update: DraftUpdate) => {
    setDraft(update);
    setRequestId(mintRequestId());
    setSubmitError(null);
  }, []);

  async function run() {
    if (!bookId || submitInFlight.current || activeId) return;
    if (draft.length === 0 || draft.some((c) => chapterProblem(c) !== null)) return;
    submitInFlight.current = true;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const out = await api.startReadThrough(bookId, {
        client_request_id: requestId,
        chapters: draft.map((c) => ({ label: c.label.trim(), text: c.text })),
      });
      setDraft([]);
      setRequestId(mintRequestId());
      setRuns((prev) => [out, ...prev.filter((r) => r.id !== out.id)]);
      if (isActiveStatus(out.status)) startPolling(out.id);
      select(out);
    } catch (e) {
      // 409 / 422 carry a detail worth showing verbatim. Anything else (network, 5xx) may or may not
      // have reached the server — the same request id makes a retry safe either way.
      setSubmitError(
        e instanceof ApiError && e.status < 500
          ? errorDetail(e)
          : `Couldn't reach the server (${errorDetail(e)}). Run again to retry — it resends the same request, so it can't start a second read-through.`,
      );
    } finally {
      submitInFlight.current = false;
      setSubmitting(false);
    }
  }

  async function stop() {
    if (!activeId || stopInFlight.current) return;
    const id = activeId;
    stopInFlight.current = true;
    setStopBusy(true);
    setStopError(null);
    try {
      const s = await api.stopReadThrough(id);
      setStatus(s);
      if (isTerminalStatus(s.status)) {
        stopPolling();
        await finishActive(id);
      }
    } catch (e) {
      setStopError(errorDetail(e));
    } finally {
      stopInFlight.current = false;
      setStopBusy(false);
    }
  }

  async function setNoteStatus(note: ReadThroughNoteOut, next: NoteStatus) {
    if (patching[note.id]) return;
    setPatching((p) => ({ ...p, [note.id]: true }));
    setPatchErrors((p) => {
      const rest = { ...p };
      delete rest[note.id];
      return rest;
    });
    try {
      const out = await api.patchReadThroughNote(note.id, { status: next });
      // Replace from the response; no refetch of the (heavy) run.
      setNotes((prev) => prev.map((n) => (n.id === out.id ? out : n)));
    } catch (e) {
      setPatchErrors((p) => ({ ...p, [note.id]: errorDetail(e) }));
    } finally {
      setPatching((p) => {
        const rest = { ...p };
        delete rest[note.id];
        return rest;
      });
    }
  }

  async function copyMarkdown() {
    if (!rt) return;
    try {
      await navigator.clipboard.writeText(readThroughMarkdown({ ...rt, notes }));
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  async function remove() {
    if (!selectedId) return;
    const id = selectedId;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await api.deleteReadThrough(id);
      setRuns((prev) => prev.filter((r) => r.id !== id));
      select(null);
    } catch (e) {
      setDeleteError(errorDetail(e));
    } finally {
      setDeleteBusy(false);
    }
  }

  const onAnchor = useCallback((note: ReadThroughNoteOut, index: number) => {
    const a = note.anchors[index];
    // Each per-chapter anchor of a repeated book-note quote carries its own chapter: switch to it.
    const target = a ? anchorHighlightChapter(note, a) : null;
    if (!target) return;
    setChapterId(target);
    setActiveAnchor(anchorKey(note.id, index));
    setScrollNonce((n) => n + 1);
  }, []);

  const chapters = useMemo(() => (rt ? sortedChapters(rt) : []), [rt]);
  const chapterLabel = useMemo(() => new Map(chapters.map((c) => [c.id, c.label])), [chapters]);
  const textAnchors = useMemo(
    () => (rt && chapterId ? anchorsForChapter(rt.notes, chapterId) : []),
    [rt, chapterId],
  );
  const bookNotes = useMemo(() => sortNotes(notes.filter((n) => n.chapter_id == null)), [notes]);
  const chapterNotes = useMemo(
    () => (chapterId ? sortNotes(notes.filter((n) => n.chapter_id === chapterId)) : []),
    [notes, chapterId],
  );
  const noteCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of notes) if (n.chapter_id) m.set(n.chapter_id, (m.get(n.chapter_id) ?? 0) + 1);
    return m;
  }, [notes]);

  const header = (
    <>
      <Eyebrow>Notes</Eyebrow>
      <h1 style={css(TITLE_XL)}>An editor&rsquo;s read-through</h1>
      <p style={css("color:var(--dim);font-size:14px;margin:0 0 18px;max-width:72ch")}>
        Upload or paste chapters. An editor model reads each one, then reads across them, and leaves
        notes that quote where they apply. It recommends — it never rewrites your text. The run and
        its notes are saved, so you can leave and come back.
      </p>
    </>
  );

  if (!bookId) {
    return (
      <div style={css(`${WRAP};padding-top:26px;padding-bottom:60px`)}>
        {header}
        <p style={css("color:var(--dim);font-size:14px")}>
          Choose a book first — read-throughs are saved per book.
        </p>
      </div>
    );
  }

  const selectedSummary = runs.find((r) => r.id === selectedId) ?? null;
  const selectedStatus =
    rt?.status ??
    (selectedId !== null && selectedId === activeId ? status?.status : undefined) ??
    selectedSummary?.status ??
    null;
  const canDelete =
    selectedStatus !== null && isTerminalStatus(selectedStatus) && selectedId !== activeId;
  const current = chapters.find((c) => c.id === chapterId) ?? null;
  const activeTitle = runs.find((r) => r.id === activeId)?.title;
  const chapterMeta = current
    ? [
        current.status !== "done"
          ? `not read — ${current.status}${current.error ? `: ${current.error}` : ""}`
          : null,
        current.notes_dropped > 0
          ? `${current.notes_dropped} note${current.notes_dropped === 1 ? "" : "s"} dropped (no quote could be located)`
          : null,
        current.notes_capped ? "more notes existed than were kept" : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  return (
    <div style={css(`${WRAP};padding-top:26px;padding-bottom:60px`)}>
      {header}

      <div style={css("display:flex;flex-direction:column;gap:18px")}>
        {activeId && status && isActiveStatus(status.status) && (
          <RunBanner
            status={status}
            title={activeTitle}
            onStop={() => void stop()}
            stopBusy={stopBusy}
            stopError={stopError}
          />
        )}
        {activeId && !status && (
          <Panel>
            <span style={css("display:inline-flex;align-items:center;gap:10px;color:var(--dim)")}>
              <Spinner /> Checking the running read-through…
            </span>
          </Panel>
        )}
        {pollNotice && <p style={css("margin:0;font-size:13px;color:var(--warn)")}>{pollNotice}</p>}

        <Composer
          chapters={draft}
          onChange={updateDraft}
          onRun={() => void run()}
          submitting={submitting}
          blocked={
            activeId
              ? "A read-through is already running for this book — wait for it to finish, or stop it."
              : null
          }
          error={submitError}
        />

        {listError && (
          <div style={css(ERROR_BOX)}>{`Couldn't list read-throughs: ${listError}`}</div>
        )}

        {runs.length > 0 && (
          <section style={css("display:flex;flex-direction:column;gap:14px")}>
            <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
              <Eyebrow>Read-through</Eyebrow>
              <select
                aria-label="Read-through"
                value={selectedId ?? ""}
                onChange={(e) => select(runs.find((r) => r.id === e.target.value) ?? null)}
                style={css(SELECT)}
              >
                {selectedId === null && <option value="">Choose a read-through…</option>}
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    {`${r.title} · ${r.status} · ${snapshotDate(r.created_at)}`}
                  </option>
                ))}
              </select>
              <span style={css("flex:1")} />
              <Button
                size="sm"
                variant="secondary"
                disabled={!rt}
                onClick={() => void copyMarkdown()}
              >
                {copyState === "copied" ? "Copied" : "Copy as Markdown"}
              </Button>
              {confirmDelete ? (
                <>
                  <span style={css("font-size:12.5px;color:var(--bad)")}>
                    Delete this read-through and all its notes?
                  </span>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={deleteBusy}
                    onClick={() => void remove()}
                  >
                    Confirm delete
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
                    Cancel
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  variant="danger"
                  disabled={!canDelete}
                  title={canDelete ? undefined : "Only a finished read-through can be deleted"}
                  onClick={() => setConfirmDelete(true)}
                >
                  Delete
                </Button>
              )}
            </div>
            {copyState === "failed" && (
              <p style={css("margin:0;font-size:12.5px;color:var(--warn)")}>
                The clipboard is not available here.
              </p>
            )}
            {deleteError && <div style={css(ERROR_BOX)}>{deleteError}</div>}

            {selectedStatus && isActiveStatus(selectedStatus) && (
              <p style={css("margin:0;color:var(--dim);font-size:13.5px")}>
                Notes appear here when this read-through finishes.
              </p>
            )}
            {loadingRun && (
              <span style={css("display:inline-flex;align-items:center;gap:10px;color:var(--dim)")}>
                <Spinner /> Loading notes…
              </span>
            )}
            {runError && <div style={css(ERROR_BOX)}>{runError}</div>}

            {rt && (
              <>
                <CoverageBanner rt={rt} />

                <Panel eyebrow="Across chapters" title="Book notes">
                  <NotesPane
                    notes={bookNotes}
                    chapterLabel={chapterLabel}
                    bookNotes
                    activeAnchorId={activeAnchor}
                    onAnchor={onAnchor}
                    onStatus={(n, s) => void setNoteStatus(n, s)}
                    busy={patching}
                    errors={patchErrors}
                    empty="No cross-chapter notes for this read-through."
                  />
                </Panel>

                {chapters.length > 0 && (
                  <div
                    role="group"
                    aria-label="Chapter"
                    style={css("display:flex;gap:6px;flex-wrap:wrap")}
                  >
                    {chapters.map((c) => (
                      <Button
                        key={c.id}
                        size="sm"
                        variant={c.id === chapterId ? "primary" : "secondary"}
                        onClick={() => {
                          setChapterId(c.id);
                          setActiveAnchor(null);
                        }}
                      >
                        {`${c.label} · ${noteCounts.get(c.id) ?? 0}`}
                      </Button>
                    ))}
                  </div>
                )}

                {current && (
                  <div
                    style={css(
                      "display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:18px;align-items:start",
                    )}
                  >
                    <Panel eyebrow="Chapter notes" title={current.label}>
                      {chapterMeta && <p style={css(`${MONO};margin:0 0 10px`)}>{chapterMeta}</p>}
                      <NotesPane
                        notes={chapterNotes}
                        chapterLabel={chapterLabel}
                        activeAnchorId={activeAnchor}
                        onAnchor={onAnchor}
                        onStatus={(n, s) => void setNoteStatus(n, s)}
                        busy={patching}
                        errors={patchErrors}
                        empty={
                          current.status === "done"
                            ? "No notes for this chapter."
                            : "This chapter was not read, so it has no notes."
                        }
                      />
                    </Panel>
                    <TextPane
                      label={current.label}
                      text={current.text}
                      anchors={textAnchors}
                      activeAnchorId={activeAnchor}
                      scrollNonce={scrollNonce}
                      onSelectAnchor={setActiveAnchor}
                    />
                  </div>
                )}
              </>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
