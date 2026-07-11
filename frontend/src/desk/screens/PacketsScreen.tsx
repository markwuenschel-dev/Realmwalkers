"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { css } from "../css";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import { formatElapsed } from "../components/DraftActivity";
import { Button, Chip, Eyebrow, Panel as UiPanel, Spinner, StatusPill } from "../components/ui";
import { ScenePacketsPanel } from "../components/ScenePacketsPanel";
import { ViolationGroups } from "../components/ViolationGroups";
import ClearFailedPanel from "../components/ClearFailedPanel";
import { resolveAuthorName, useAuthorName } from "../lib/authorName";
import { downloadBlob } from "../lib/download";
import {
  normalizePacketViolation,
  packetBlockedGuidance,
  packetRepairTasks,
} from "../lib/packetBlockers";
import type {
  PacketBody,
  PacketClaim,
  PacketOut,
  PacketRisk,
  PacketSceneSeed,
  ResolvedQuestion,
} from "../api/types";
import type { ExportKind } from "../lib/docx";

// The Packet review panel (contract-first drafting, Phase 1). Per chapter, it runs the Packet Author
// + Packet QA agents, then shows the proposed chapter knowledge packet for the human to adjudicate
// and approve BEFORE any prose is drafted. The human can: resolve each open question WITH a recorded
// ruling, edit the packet's key fields inline, and leave packet-level adjudication notes. Nothing here
// touches the drafter — that's a later phase.

const CONFIDENCE_VAR: Record<string, string> = { green: "--good", yellow: "--warn", red: "--bad" };

export default function PacketsScreen() {
  const data = useDeskData();
  const searchParams = useSearchParams();
  const chapters = useMemo(
    () =>
      [...data.chapters].sort(
        (a, b) => (a.position ?? a.chapter_no ?? 0) - (b.position ?? b.chapter_no ?? 0),
      ),
    [data.chapters],
  );

  const [chapterId, setChapterId] = useState<string | null>(null);
  const [packet, setPacket] = useState<PacketOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  // Collapsible raw canonical JSON view of the chapter packet body (client-side pretty-print).
  const [jsonOpen, setJsonOpen] = useState(false);
  // Background-proposal state. `proposing` drives the poll loop; phase/elapsed are the live status the
  // author+QA report from the server, so the work survives tab switches (it runs in the API process)
  // and any tab can rejoin it.
  const [proposing, setProposing] = useState(false);
  const [phase, setPhase] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);

  // Batch panel: fire chapter-packet authoring for several EXISTING chapters at once (e.g. a whole
  // arc that already has outlines but no packets yet). Reuses the same fire-and-forget per-chapter
  // proposePacket() the single-chapter flow above uses -- no new backend endpoint, since each call
  // already just kicks off a background author+QA run and returns immediately.
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchSelected, setBatchSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResults, setBatchResults] = useState<
    { chapterId: string; chapterNo: number | null; ok: boolean; error?: string }[] | null
  >(null);

  // Export the current chapter's approved manuscript content — same Markdown / Reader-DOCX /
  // Shunn-DOCX builders the Manuscript tab uses. Shared author name with every other export surface.
  const [author, saveAuthor] = useAuthorName();
  const [exportingChapter, setExportingChapter] = useState<ExportKind | null>(null);

  // Default to the first chapter once chapters load (without clobbering an explicit pick).
  useEffect(() => {
    const fromUrl = searchParams.get("chapter");
    if (fromUrl && chapters.some((c) => c.id === fromUrl)) {
      setChapterId(fromUrl);
      return;
    }
    if (chapterId === null && chapters.length) setChapterId(chapters[0].id);
  }, [chapters, chapterId, searchParams]);

  // Deep-link target from Production's issue rows (?chapter=…&scene=N): the scene-packet card to
  // auto-expand and scroll to. Same query-param convention as ?chapter=.
  const focusSceneRaw = searchParams.get("scene");
  const focusScene = focusSceneRaw ? Number.parseInt(focusSceneRaw, 10) || undefined : undefined;

  const chapter = chapters.find((c) => c.id === chapterId) ?? null;
  const hasOutline = !!(chapter?.outline || "").trim();

  // On chapter change: fetch its packet (404 = none yet) AND its proposal status, so landing on (or
  // returning to) a chapter with an in-flight author+QA rejoins that run instead of looking idle.
  useEffect(() => {
    if (!chapterId) return;
    let alive = true;
    setLoading(true);
    setError(null);
    setPacket(null);
    setEditing(false);
    setJsonOpen(false);
    Promise.allSettled([api.packet(chapterId), api.packetStatus(chapterId)])
      .then(([pkt, st]) => {
        if (!alive) return;
        setPacket(pkt.status === "fulfilled" ? pkt.value : null); // 404: no packet yet
        const running = st.status === "fulfilled" && st.value.running;
        setProposing(running);
        setPhase(running && st.status === "fulfilled" ? (st.value.phase ?? "authoring") : null);
        setElapsed(running && st.status === "fulfilled" ? (st.value.elapsed_s ?? null) : null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [chapterId]);

  // Poll the background proposal while one is running. Server is the source of truth; when it finishes
  // (running -> false) we refetch the now-persisted packet and stop. Interval clears on tab switch but
  // the run keeps going server-side, so the chapter-change effect above re-attaches on return.
  useEffect(() => {
    if (!proposing || !chapterId) return;
    let alive = true;
    const tick = async () => {
      try {
        const st = await api.packetStatus(chapterId);
        if (!alive) return;
        if (st.running) {
          setPhase(st.phase ?? "authoring");
          setElapsed(st.elapsed_s ?? null);
        } else {
          const pkt = await api.packet(chapterId).catch(() => null);
          if (!alive) return;
          setPacket(pkt);
          setProposing(false);
          setPhase(null);
          setElapsed(null);
        }
      } catch {
        /* transient — keep polling */
      }
    };
    void tick();
    const id = window.setInterval(tick, 1500);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [proposing, chapterId]);

  const startPropose = async () => {
    if (!chapterId) return;
    setError(null);
    try {
      const st = await api.proposePacket(chapterId);
      setProposing(true);
      setPhase(st.phase ?? "authoring");
      setElapsed(st.elapsed_s ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const run = useCallback(async (label: string, fn: () => Promise<PacketOut>) => {
    setBusy(label);
    setError(null);
    try {
      setPacket(await fn());
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  }, []);

  // Chapters eligible for batch packet generation: same "has an outline" gate the single-chapter
  // button already uses (author_packet needs an outline to work from). Re-propose is safe here too
  // (it's the same button/endpoint the single-chapter flow calls "Re-propose"), so a chapter that
  // already has a packet isn't excluded -- selecting it just re-authors it.
  const batchEligible = useMemo(() => chapters.filter((c) => (c.outline ?? "").trim()), [chapters]);

  const toggleBatchSelected = (id: string) =>
    setBatchSelected((sel) => {
      const next = new Set(sel);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const runBatchGenerate = async () => {
    if (batchSelected.size === 0) return;
    setBatchBusy(true);
    setBatchResults(null);
    const targets = batchEligible.filter((c) => batchSelected.has(c.id));
    const results: { chapterId: string; chapterNo: number | null; ok: boolean; error?: string }[] =
      [];
    for (const c of targets) {
      try {
        await api.proposePacket(c.id);
        results.push({ chapterId: c.id, chapterNo: c.chapter_no ?? null, ok: true });
      } catch (e) {
        results.push({
          chapterId: c.id,
          chapterNo: c.chapter_no ?? null,
          ok: false,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    }
    setBatchResults(results);
    setBatchBusy(false);
  };

  const viewBatchChapter = (id: string) => {
    setChapterId(id);
    setBatchOpen(false);
  };

  const openItems = (packet?.open_questions?.items ?? []).filter(Boolean);
  const resolvedItems = packet?.open_questions?.resolved ?? [];
  // Approval is the SERVER's gate: repair/warn issues never disable it locally (approve-with-repairs
  // — the repairs still gate final export). Only `blocked` packets refuse approval.
  const canApprove = packet?.can_approve ?? false;
  // The server guarantees a reason for every non-approvable state; the keyed fallback only covers
  // pre-approval_state payloads so a greyed Approve can never be silent again.
  const disabledReason =
    packet?.approval_blockers[0] ??
    (packet?.approval_state === "already_approved"
      ? "Packet already approved — edit or re-propose to make changes."
      : "Packet is not approvable right now — derive or re-propose it first.");
  const repairCount = packet ? packetRepairTasks(packet.qa_warnings).length : 0;

  // Raw canonical JSON of the chapter packet body — the exact contract the drafting agents receive.
  const packetJson = useMemo(
    () => (packet ? JSON.stringify(packet.body ?? {}, null, 2) : ""),
    [packet],
  );
  const downloadPacketJson = () => {
    if (!packet || !chapter) return;
    downloadBlob(`chapter_${chapter.chapter_no}_packet.json`, packetJson, "application/json");
  };

  // Resolve a question WITH the human's ruling: drop it from `items`, append it to `resolved` so the
  // adjudication is recorded (not just cleared). Both lists are sent together — the server replaces the
  // whole open_questions object, and the approve gate only counts `items`.
  const resolveQuestion = (idx: number, resolution: string) => {
    if (!packet || !chapterId) return;
    const items = openItems.filter((_, i) => i !== idx);
    const entry: ResolvedQuestion = {
      q: openItems[idx],
      resolution: resolution.trim(),
      at: new Date().toISOString(),
    };
    void run("resolve", () =>
      api.updatePacket(chapterId, {
        open_questions: { items, resolved: [...resolvedItems, entry] },
      }),
    );
  };

  // Undo a ruling: move it back into `items` (re-gating approval) and drop it from `resolved`.
  const unresolveQuestion = (idx: number) => {
    if (!packet || !chapterId) return;
    const entry = resolvedItems[idx];
    if (!entry) return;
    const resolved = resolvedItems.filter((_, i) => i !== idx);
    void run("resolve", () =>
      api.updatePacket(chapterId, { open_questions: { items: [...openItems, entry.q], resolved } }),
    );
  };

  const saveBody = async (body: PacketBody) => {
    if (!chapterId) return;
    const ok = await run("save", () => api.updatePacket(chapterId, { body }));
    if (ok) setEditing(false);
  };

  // A packet is pre-prose planning JSON — nothing to export until the chapter has approved, drafted
  // scenes. data.manuscript is already the approved compile, so find this chapter's slice of it.
  const manuscriptChapter =
    data.manuscript?.chapters.find((mc) => mc.chapter_no === chapter?.chapter_no) ?? null;
  const chapterHasProse = !!manuscriptChapter?.scenes.some((s) => (s.prose ?? "").trim());

  const exportChapterAs = async (kind: ExportKind) => {
    if (!chapter || !manuscriptChapter) return;
    setExportingChapter(kind);
    try {
      const exp = await import("../lib/docx");
      const { exportAndSave } = await import("../manuscript/exportActions");
      const title = `Chapter ${chapter.chapter_no}${chapter.title ? `: ${chapter.title}` : ""}`;
      const ms = exp.buildManuscriptFrom(title, [manuscriptChapter]);
      const stem = `chapter_${chapter.chapter_no}${chapter.title ? `_${chapter.title}` : ""}`;
      if (kind === "md") {
        await exportAndSave(ms, { preset: "editorial_review", filenameStem: stem, override: true });
      } else if (kind === "docx") {
        const bookTitle = data.books.find((b) => b.id === data.bookId)?.title;
        await exportAndSave(ms, {
          preset: "reader_proof",
          filenameStem: stem,
          renderSubtitle: bookTitle ? `from ${bookTitle}` : undefined,
          override: true,
        });
      } else {
        const name = resolveAuthorName(author, saveAuthor);
        if (!name) return;
        await exportAndSave(ms, {
          preset: "submission_shunn",
          filenameStem: `${stem}_shunn`,
          author: name,
          override: true,
        });
      }
    } finally {
      setExportingChapter(null);
    }
  };

  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px",
        )}
      >
        <div>
          <Eyebrow style="margin-bottom:6px">Contracts</Eyebrow>
          <h1
            style={css(
              "margin:0 0 6px;font-family:var(--display);font-weight:500;font-size:30px;line-height:38px;letter-spacing:-.01em;color:var(--ink)",
            )}
          >
            Chapter packets
          </h1>
          <p style={css("margin:0;color:var(--dim);font-size:14.5px;max-width:640px")}>
            The contract the drafting agents obey — allowed vs forbidden knowledge, reveal timing,
            roster and canon locks, scene jobs, and known drift risks. Author it, adjudicate the
            flags, approve it before any prose is written.
          </p>
        </div>
        <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
          <select
            value={chapterId ?? ""}
            onChange={(e) => setChapterId(e.target.value || null)}
            style={css(
              "height:34px;padding:0 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:13px",
            )}
          >
            {chapters.length === 0 && <option value="">No chapters yet</option>}
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>
                Ch {c.chapter_no}
                {c.title ? ` · ${c.title}` : ""} ({c.pov})
              </option>
            ))}
          </select>
          {/* Same three exports the Manuscript tab offers, scoped to this chapter's approved scenes
              (data.manuscript is already the approved compile) — a packet has no prose of its own. */}
          <Button
            disabled={!chapterHasProse || exportingChapter != null}
            title={
              chapterHasProse
                ? "Semantic Markdown — same format the Manuscript tab exports"
                : "This chapter has no approved prose yet"
            }
            onClick={() => void exportChapterAs("md")}
          >
            {exportingChapter === "md" ? "Exporting…" : "Export Markdown"}
          </Button>
          <Button
            disabled={!chapterHasProse || exportingChapter != null}
            title={
              chapterHasProse
                ? "Reader DOCX — styled book format, same as the Manuscript tab"
                : "This chapter has no approved prose yet"
            }
            onClick={() => void exportChapterAs("docx")}
          >
            {exportingChapter === "docx" ? "Exporting…" : "Export Reader DOCX"}
          </Button>
          <Button
            disabled={!chapterHasProse || exportingChapter != null}
            title={
              chapterHasProse
                ? "Shunn DOCX — plain submission format, same as the Manuscript tab"
                : "This chapter has no approved prose yet"
            }
            onClick={() => void exportChapterAs("shunn")}
          >
            {exportingChapter === "shunn" ? "Exporting…" : "Export Shunn DOCX"}
          </Button>
          {packet && !editing && !proposing && (
            <Button onClick={() => setEditing(true)}>Edit packet</Button>
          )}
          {packet && !editing && (
            <Button
              onClick={() => setJsonOpen((o) => !o)}
              title="Raw canonical JSON of the chapter packet body — view or download it"
            >
              {jsonOpen ? "Hide JSON" : "Packet JSON"}
            </Button>
          )}
          {packet && !editing && !proposing && (
            <Button
              disabled={busy === "clear"}
              style="color:var(--warn)"
              onClick={() => {
                void (async () => {
                  if (!chapterId) return;
                  if (
                    !confirm(
                      "Clear this chapter packet and all derived scene packets? You will need to re-propose and re-derive before drafting.",
                    )
                  )
                    return;
                  setBusy("clear");
                  setError(null);
                  try {
                    await api.deletePacket(chapterId);
                    setPacket(null);
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(null);
                  }
                })();
              }}
            >
              {busy === "clear" ? "Clearing…" : "Clear packet"}
            </Button>
          )}
          <Button
            variant="primary"
            disabled={!chapterId || !hasOutline || proposing || editing}
            title={hasOutline ? undefined : "Outline this chapter first (Inbox → plan a chapter)"}
            onClick={startPropose}
          >
            {proposing
              ? `${phase === "qa" ? "QA reviewing" : "Authoring"}…${formatElapsed(elapsed) ? ` ${formatElapsed(elapsed)}` : ""}`
              : packet
                ? "Re-propose"
                : "Propose packet"}
          </Button>
        </div>
      </div>

      <div style={css("margin-bottom:18px")}>
        <Button variant="ghost" size="sm" onClick={() => setBatchOpen((o) => !o)}>
          {batchOpen ? "Hide batch generate" : "Batch · generate packets for multiple chapters"}
        </Button>

        {batchOpen && (
          <div data-testid="batch-panel" style={css("margin-top:12px")}>
            <UiPanel eyebrow="Batch generate" pad="14px 16px">
              <div style={css("display:flex;flex-direction:column;gap:12px")}>
                <p style={css("margin:0;color:var(--dim);font-size:12.5px;line-height:1.55")}>
                  Pick several chapters that already have an outline and generate a chapter packet
                  for each — every chapter authors concurrently in the background, same as a single
                  propose. Selecting a chapter that already has a packet re-proposes it.
                </p>

                {batchEligible.length === 0 ? (
                  <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
                    No chapters have an outline yet — plan chapters from the Inbox first.
                  </div>
                ) : (
                  <div
                    style={css(
                      "display:flex;flex-direction:column;gap:6px;max-height:260px;overflow-y:auto",
                    )}
                  >
                    {batchEligible.map((c) => (
                      <label
                        key={c.id}
                        className="dk-row"
                        style={css(
                          "display:flex;align-items:center;gap:9px;padding:6px 8px;border-radius:7px;background:var(--bg3);font-size:13px;color:var(--ink);cursor:pointer",
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={batchSelected.has(c.id)}
                          onChange={() => toggleBatchSelected(c.id)}
                        />
                        <span
                          style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}
                        >
                          Ch {c.chapter_no}
                        </span>
                        <span>
                          {c.title || "(untitled)"} · {c.pov}
                        </span>
                      </label>
                    ))}
                  </div>
                )}

                <div style={css("display:flex;gap:14px;align-items:center;flex-wrap:wrap")}>
                  <Button
                    variant="primary"
                    style="background:var(--good);border-color:transparent"
                    disabled={batchBusy || batchSelected.size === 0}
                    onClick={() => void runBatchGenerate()}
                  >
                    {batchBusy
                      ? "Generating…"
                      : `Generate ${batchSelected.size || ""} packet${batchSelected.size === 1 ? "" : "s"}`}
                  </Button>
                </div>

                {batchResults && (
                  <div style={css("display:flex;flex-direction:column;gap:6px")}>
                    {batchResults.map((r) => (
                      <div
                        key={r.chapterId}
                        style={css(
                          `display:flex;gap:10px;align-items:center;flex-wrap:wrap;border:1px solid var(--line);border-radius:8px;background:var(--boxbg);padding:8px 11px;font-family:var(--mono);font-size:11.5px;color:${r.ok ? "var(--ink)" : "var(--bad)"}`,
                        )}
                      >
                        <span style={css("color:var(--accent)")}>Ch {r.chapterNo}</span>
                        <span>{r.ok ? "authoring started" : `failed: ${r.error}`}</span>
                        {r.ok && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => viewBatchChapter(r.chapterId)}
                          >
                            View →
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </UiPanel>
          </div>
        )}
      </div>

      {error && (
        <div
          style={css(
            "margin-bottom:16px;border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--bg2));border-radius:var(--r);padding:11px 13px;color:var(--bad);font-size:13px",
          )}
        >
          {error}
        </div>
      )}

      {proposing && (
        <div
          style={css(
            "display:flex;align-items:center;gap:12px;margin-bottom:16px;border:1px solid color-mix(in srgb,var(--info) 35%,var(--line));background:color-mix(in srgb,var(--info) 7%,var(--bg2));border-radius:9px;padding:11px 14px",
          )}
        >
          <Spinner />
          <div style={css("display:flex;flex-direction:column;gap:2px")}>
            <span style={css("font-size:13px;color:var(--ink)")}>
              {phase === "qa"
                ? "Packet QA is attacking the draft contract…"
                : "Packet Author is drafting the chapter contract…"}
            </span>
            <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
              Step {phase === "qa" ? "2 of 2 · QA" : "1 of 2 · authoring"}
              {formatElapsed(elapsed) ? ` · ${formatElapsed(elapsed)} elapsed` : ""} · runs
              server-side — you can switch tabs and come back.
            </span>
          </div>
        </div>
      )}

      {loading && <Muted text="Loading packet…" />}

      {!loading && !packet && (
        <div
          style={css(
            "border:1px dashed var(--line);border-radius:var(--rLg);padding:40px 30px;text-align:center;color:var(--dim)",
          )}
        >
          <div aria-hidden style={css("font-size:18px;color:var(--accent);margin-bottom:10px")}>
            ✦
          </div>
          <div
            style={css(
              "font-family:var(--display);font-size:18px;font-style:italic;color:var(--ink);margin-bottom:6px",
            )}
          >
            No packet yet
          </div>
          <div style={css("font-size:13.5px;max-width:420px;margin:0 auto;line-height:1.55")}>
            {hasOutline
              ? "Propose a packet to have the Packet Author + QA agents draft this chapter's drafting contract."
              : "This chapter has no outline yet. Plan it from the Inbox first, then return here to author its packet."}
          </div>
        </div>
      )}

      {!loading && packet && !editing && jsonOpen && (
        <div style={css("margin-bottom:16px")}>
          <UiPanel
            eyebrow="Canonical packet JSON"
            actions={<Button onClick={downloadPacketJson}>Download JSON</Button>}
          >
            <div style={css("margin-bottom:10px")}>
              <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                chapter_{chapter?.chapter_no}_packet.json — the exact body the drafting agents
                receive.
              </span>
            </div>
            <pre
              data-testid="packet-json"
              style={css(
                "margin:0;padding:12px 14px;border:1px solid var(--line);border-radius:var(--r);background:var(--boxbg);font-family:var(--mono);font-size:11.5px;line-height:1.5;color:var(--ink);white-space:pre;overflow-x:auto;max-height:420px;overflow-y:auto",
              )}
            >
              {packetJson}
            </pre>
          </UiPanel>
        </div>
      )}

      {!loading && packet && editing && (
        <PacketEditor
          packet={packet}
          busy={busy === "save"}
          onSave={saveBody}
          onCancel={() => setEditing(false)}
        />
      )}

      {!loading && packet && !editing && (
        <PacketView
          packet={packet}
          openItems={openItems}
          resolvedItems={resolvedItems}
          resolving={busy === "resolve"}
          onResolve={resolveQuestion}
          onUnresolve={unresolveQuestion}
        />
      )}

      {!loading && packet && !editing && (
        <div style={css("display:flex;align-items:center;gap:14px;margin-top:22px;flex-wrap:wrap")}>
          <Button
            variant="primary"
            style="background:var(--good);border-color:transparent"
            disabled={!canApprove || busy === "approve"}
            title={!canApprove ? disabledReason : undefined}
            onClick={() => {
              if (chapterId) void run("approve", () => api.approvePacket(chapterId));
            }}
          >
            {busy === "approve"
              ? "Approving…"
              : packet.status === "approved"
                ? "Approved ✓"
                : repairCount > 0
                  ? `Approve (${repairCount} repair task${repairCount === 1 ? "" : "s"} outstanding)`
                  : "Approve packet"}
          </Button>
          {canApprove && packet.status !== "approved" && repairCount > 0 && (
            <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
              repair tasks gate final export, not drafting — approving proceeds with them
              outstanding
            </span>
          )}
          {!canApprove && packet.status !== "approved" && (
            <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
              {disabledReason}
            </span>
          )}
          {packet.status === "approved" && (
            <span
              style={css(
                `font-family:var(--mono);font-size:11.5px;color:var(${CONFIDENCE_VAR.green})`,
              )}
            >
              {packet.body?.scene_seeds?.length ?? 0} scene seed
              {(packet.body?.scene_seeds?.length ?? 0) === 1 ? "" : "s"} — derive scene packets
              below.
            </span>
          )}
        </div>
      )}

      {/* Scene packets: the scene-local contract, available once the chapter packet is approved. */}
      {!loading && packet && !editing && packet.status === "approved" && chapterId && chapter && (
        <>
          {(data.failedJobs.some((f) => f.chapter_no === chapter.chapter_no) ||
            data.jobs.failed > 0) && (
            <div style={css("margin-top:18px")}>
              <ClearFailedPanel
                failedCount={
                  data.failedJobs.filter((f) => f.chapter_no === chapter.chapter_no).length ||
                  data.jobs.failed
                }
                failedJobs={
                  data.failedJobs.filter((f) => f.chapter_no === chapter.chapter_no).length > 0
                    ? data.failedJobs.filter((f) => f.chapter_no === chapter.chapter_no)
                    : data.failedJobs
                }
                onClear={() => data.clearFailed(chapterId)}
                scopeLabel="this chapter"
              />
            </div>
          )}
          <ScenePacketsPanel chapterId={chapterId} focusScene={focusScene} />
        </>
      )}
    </div>
  );
}

function PacketView({
  packet,
  openItems,
  resolvedItems,
  resolving,
  onResolve,
  onUnresolve,
}: {
  packet: PacketOut;
  openItems: string[];
  resolvedItems: ResolvedQuestion[];
  resolving: boolean;
  onResolve: (i: number, resolution: string) => void;
  onUnresolve: (i: number) => void;
}) {
  const b: PacketBody = packet.body ?? {};
  const confVar = CONFIDENCE_VAR[packet.confidence ?? ""] ?? "--dim";
  const blockedGuidance = packet.status === "blocked" ? packetBlockedGuidance(packet) : null;
  const residual = packet.qa_warnings?.residual_risks ?? [];
  const issues = packet.qa_warnings?.issues ?? [];
  // Deterministic-validation channel (distinct from QA `issues`): decidable roster contradictions
  // (double-bucketed character, forbidden name in a scene seed) caught before QA ever runs.
  // Normalized so old rows without persisted blocks_* booleans still get the severity-derived gates.
  const violations = (packet.qa_warnings?.violations ?? []).map(normalizePacketViolation);

  return (
    <div style={css("display:flex;flex-direction:column;gap:16px")}>
      {/* status / confidence / verdict — status and QA are independent axes, never merged */}
      <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
        <StatusPill axis="contract" state={packet.status} />
        {packet.confidence && (
          <Chip label={`confidence: ${packet.confidence}`} colorVar={confVar} />
        )}
        {packet.qa_verdict && <StatusPill axis="qa" state={packet.qa_verdict} />}
      </div>

      {blockedGuidance && (
        <Panel accentVar="--bad" title={blockedGuidance.title}>
          <div style={css("font-size:13px;color:var(--ink);line-height:1.45")}>
            {blockedGuidance.reason ?? "Chapter packet is blocked but no reason was recorded."}
          </div>
          <div style={css("font-size:12px;color:var(--dim);margin-top:6px;line-height:1.45")}>
            {blockedGuidance.detail}
          </div>
          {blockedGuidance.actions.length > 0 && (
            <div style={css("display:flex;flex-direction:column;gap:4px;margin-top:8px")}>
              {blockedGuidance.actions.map((action, i) => (
                <div key={i} style={css("font-size:12px;color:var(--ink);line-height:1.4")}>
                  · {action}
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      {violations.length > 0 && (
        <Panel accentVar="--warn" title={`Deterministic validation · ${violations.length}`}>
          <ViolationGroups violations={violations} />
        </Panel>
      )}

      {openItems.length > 0 && (
        <Panel accentVar="--warn" title={`Open questions · ${openItems.length}`}>
          <div style={css("display:flex;flex-direction:column;gap:14px")}>
            {openItems.map((q, i) => (
              <QuestionResolver
                key={i}
                question={q}
                disabled={resolving}
                onResolve={(text) => onResolve(i, text)}
              />
            ))}
          </div>
          <div
            style={css("font-size:11.5px;color:var(--dim);margin-top:11px;font-family:var(--mono)")}
          >
            Type your ruling for each (edit the outline/canon or the packet itself as needed), then
            resolve. Your ruling is recorded. All must be resolved to approve.
          </div>
        </Panel>
      )}

      {resolvedItems.length > 0 && (
        <Panel accentVar="--good" title={`Resolved rulings · ${resolvedItems.length}`}>
          <div style={css("display:flex;flex-direction:column;gap:10px")}>
            {resolvedItems.map((r, i) => (
              <div
                key={i}
                style={css(
                  "display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding-bottom:9px;border-bottom:1px solid var(--line)",
                )}
              >
                <div style={css("min-width:0;flex:1")}>
                  <div style={css("font-size:12.5px;color:var(--dim);line-height:1.45")}>{r.q}</div>
                  <div
                    style={css("font-size:13px;color:var(--ink);line-height:1.45;margin-top:3px")}
                  >
                    {r.resolution ? (
                      <>→ {r.resolution}</>
                    ) : (
                      <span style={css("color:var(--dim);font-style:italic")}>
                        → resolved (no note)
                      </span>
                    )}
                  </div>
                </div>
                <Button size="sm" onClick={() => onUnresolve(i)} disabled={resolving}>
                  Unresolve
                </Button>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {b.adjudication_notes && (
        <Panel accentVar="--info" title="Adjudication notes">
          <div style={css("font-size:13px;color:var(--ink);line-height:1.5;white-space:pre-wrap")}>
            {b.adjudication_notes}
          </div>
        </Panel>
      )}

      {(b.chapter_job ||
        b.one_sentence_spine ||
        b.entry_state ||
        b.exit_state ||
        b.emotional_spine) && (
        <Panel title="Spine">
          {b.one_sentence_spine && <Field k="Spine" v={b.one_sentence_spine} />}
          {b.chapter_job && <Field k="Chapter job" v={b.chapter_job} />}
          {b.entry_state && <Field k="Entry state" v={b.entry_state} />}
          {b.exit_state && <Field k="Exit state" v={b.exit_state} />}
          {b.emotional_spine && <Field k="Emotional spine" v={b.emotional_spine} />}
        </Panel>
      )}

      {(residual.length > 0 || issues.length > 0) && (
        <Panel accentVar="--info" title="Packet QA">
          {residual.length > 0 && (
            <PillList label="Residual risks the writer must avoid" items={residual} tone="warn" />
          )}
          {issues.length > 0 && (
            <div style={css("margin-top:10px")}>
              <Label text="Issues raised" />
              <div style={css("display:flex;flex-direction:column;gap:6px")}>
                {issues.map((iss, i) => (
                  <div key={i} style={css("font-size:12.5px;color:var(--ink)")}>
                    <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
                      {iss.kind || "issue"}:{" "}
                    </span>
                    {iss.detail}
                  </div>
                ))}
              </div>
            </div>
          )}
        </Panel>
      )}

      {(b.known_risks?.length ?? 0) > 0 && (
        <Panel accentVar="--bad" title="Known drift risks">
          <div style={css("display:flex;flex-direction:column;gap:12px")}>
            {b.known_risks!.map((r: PacketRisk, i: number) => (
              <div key={i}>
                <div style={css("font-size:13.5px;color:var(--ink);font-weight:500")}>{r.risk}</div>
                {r.why_dangerous && (
                  <div style={css("font-size:12.5px;color:var(--dim);margin-top:2px")}>
                    Why: {r.why_dangerous}
                  </div>
                )}
                {r.prevention && (
                  <div style={css("font-size:12.5px;color:var(--ink);margin-top:2px")}>
                    Prevent: {r.prevention}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {/* roster */}
      {b.characters_present?.length ||
      b.characters_absent?.length ||
      b.characters_mentioned_only?.length ||
      b.characters_forbidden?.length ? (
        <Panel title="Roster">
          {b.characters_present?.length ? (
            <PillList label="Present" items={b.characters_present} tone="good" />
          ) : null}
          {b.characters_absent?.length ? (
            <PillList label="Absent" items={b.characters_absent} tone="dim" />
          ) : null}
          {b.characters_mentioned_only?.length ? (
            <PillList label="Mentioned only" items={b.characters_mentioned_only} tone="info" />
          ) : null}
          {b.characters_forbidden?.length ? (
            <PillList label="Forbidden" items={b.characters_forbidden} tone="bad" />
          ) : null}
        </Panel>
      ) : null}

      {/* knowledge + reveals */}
      {b.allowed_knowledge?.length ||
      b.forbidden_knowledge?.length ||
      b.required_reveals?.length ||
      b.forbidden_reveals?.length ? (
        <Panel title="Knowledge & reveals">
          {b.allowed_knowledge?.length ? (
            <PillList label="Reader MAY know" items={b.allowed_knowledge} tone="good" />
          ) : null}
          {b.forbidden_knowledge?.length ? (
            <PillList label="Reader may NOT know yet" items={b.forbidden_knowledge} tone="bad" />
          ) : null}
          {b.required_reveals?.length ? (
            <PillList label="Required reveals" items={b.required_reveals} tone="info" />
          ) : null}
          {b.forbidden_reveals?.length ? (
            <PillList label="Forbidden reveals" items={b.forbidden_reveals} tone="bad" />
          ) : null}
        </Panel>
      ) : null}

      {/* locks */}
      {b.canon_locks?.length ||
      b.roster_locks?.length ||
      b.relationship_locks?.length ||
      b.timeline_locks?.length ? (
        <Panel title="Locks">
          {b.canon_locks?.length ? (
            <PillList label="Canon" items={b.canon_locks} tone="info" />
          ) : null}
          {b.roster_locks?.length ? (
            <PillList label="Roster" items={b.roster_locks} tone="info" />
          ) : null}
          {b.relationship_locks?.length ? (
            <PillList label="Relationship" items={b.relationship_locks} tone="info" />
          ) : null}
          {b.timeline_locks?.length ? (
            <PillList label="Timeline" items={b.timeline_locks} tone="info" />
          ) : null}
        </Panel>
      ) : null}

      {/* scene seeds */}
      {(b.scene_seeds?.length ?? 0) > 0 && (
        <Panel
          title={`Scene seeds · ${b.scene_seeds!.length}${packet.status === "approved" ? " · scoping the drafter" : ""}`}
        >
          <div style={css("display:flex;flex-direction:column;gap:12px")}>
            {b.scene_seeds!.map((s: PacketSceneSeed) => (
              <div
                key={s.seed_id}
                style={css(
                  "border:1px solid var(--line);border-radius:9px;padding:11px 13px;background:var(--bg3)",
                )}
              >
                <div
                  style={css(
                    "display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:5px",
                  )}
                >
                  <span style={css("font-family:var(--display);font-size:14.5px;color:var(--ink)")}>
                    Scene {s.scene_no}
                    {s.scene_type ? (
                      <span
                        style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
                      >
                        {" "}
                        · {s.scene_type}
                      </span>
                    ) : null}
                  </span>
                  {s.word_budget?.target ? (
                    <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
                      ~{s.word_budget.target}w
                      {s.word_budget.hard_max ? ` (≤${s.word_budget.hard_max})` : ""}
                    </span>
                  ) : null}
                </div>
                {s.scene_job && (
                  <div style={css("font-size:13px;color:var(--ink);margin-bottom:5px")}>
                    {s.scene_job}
                  </div>
                )}
                {s.required_beats?.length ? (
                  <PillList label="Required" items={s.required_beats} tone="good" />
                ) : null}
                {s.forbidden_beats?.length ? (
                  <PillList label="Forbidden" items={s.forbidden_beats} tone="bad" />
                ) : null}
                {s.exit_state && (
                  <div style={css("font-size:12px;color:var(--dim);margin-top:4px")}>
                    Exit: {s.exit_state}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {/* claims with provenance */}
      {(b.claims?.length ?? 0) > 0 && (
        <Panel title={`Claims · ${b.claims!.length}`}>
          <div style={css("display:flex;flex-direction:column;gap:8px")}>
            {b.claims!.map((c: PacketClaim, i: number) => (
              <div
                key={i}
                style={css(
                  "display:flex;align-items:flex-start;gap:10px;padding-bottom:8px;border-bottom:1px solid var(--line)",
                )}
              >
                <SourceBadge strength={c.source_strength} />
                <div style={css("min-width:0;flex:1")}>
                  <div style={css("font-size:13px;color:var(--ink);line-height:1.4")}>
                    {c.claim}
                  </div>
                  <div
                    style={css(
                      "font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:2px",
                    )}
                  >
                    {c.source_title_or_file
                      ? `source: ${c.source_title_or_file}`
                      : "no canonical source"}
                    {c.confidence ? ` · ${c.confidence}` : ""}
                    {c.excerpt
                      ? ` — “${c.excerpt.slice(0, 120)}${c.excerpt.length > 120 ? "…" : ""}”`
                      : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}

// One open question with its own resolution text box. Local state so typing is decoupled from the
// persisted packet; submitting lifts the ruling up to be recorded.
function QuestionResolver({
  question,
  disabled,
  onResolve,
}: {
  question: string;
  disabled: boolean;
  onResolve: (resolution: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <div style={css("display:flex;flex-direction:column;gap:7px")}>
      <span style={css("font-size:13px;color:var(--ink);line-height:1.45")}>{question}</span>
      <div style={css("display:flex;align-items:flex-end;gap:9px")}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Your ruling — how this resolves (recorded with the packet)…"
          rows={2}
          style={inputStyle()}
        />
        <Button
          variant="primary"
          style="background:var(--good);border-color:transparent"
          onClick={() => onResolve(text)}
          disabled={disabled}
        >
          Resolve
        </Button>
      </div>
    </div>
  );
}

// --- editor ---------------------------------------------------------------------------------------

// Edit the packet's high-leverage fields and PUT the whole body. Works on a local draft so edits are
// atomic (Save persists, Cancel discards); the server preserves scene seed ids and re-stamps any new
// ones. Read-only review surfaces (QA, claims, provenance) stay out of the editor.
function PacketEditor({
  packet,
  busy,
  onSave,
  onCancel,
}: {
  packet: PacketOut;
  busy: boolean;
  onSave: (body: PacketBody) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<PacketBody>(() => structuredClone(packet.body ?? {}));

  const setField = (k: keyof PacketBody, v: unknown) => setDraft((d) => ({ ...d, [k]: v }));

  const setSeed = (idx: number, patch: Partial<PacketSceneSeed>) =>
    setDraft((d) => {
      const seeds = [...(d.scene_seeds ?? [])];
      seeds[idx] = { ...seeds[idx], ...patch };
      return { ...d, scene_seeds: seeds };
    });

  return (
    <div style={css("display:flex;flex-direction:column;gap:16px")}>
      <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
        <Chip label="editing packet" colorVar="--info" />
        <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          Edit the contract directly. Save replaces the packet body; Cancel discards your changes.
        </span>
      </div>

      <Panel title="Spine">
        <EditText
          label="Spine (one sentence)"
          value={draft.one_sentence_spine}
          onChange={(v) => setField("one_sentence_spine", v)}
        />
        <EditText
          label="Chapter job"
          value={draft.chapter_job}
          onChange={(v) => setField("chapter_job", v)}
          multiline
        />
        <EditText
          label="Entry state"
          value={draft.entry_state}
          onChange={(v) => setField("entry_state", v)}
          multiline
        />
        <EditText
          label="Exit state"
          value={draft.exit_state}
          onChange={(v) => setField("exit_state", v)}
          multiline
        />
        <EditText
          label="Emotional spine"
          value={draft.emotional_spine}
          onChange={(v) => setField("emotional_spine", v)}
          multiline
        />
      </Panel>

      <Panel title="Adjudication notes">
        <EditText
          label="Your packet-level rulings / context"
          value={draft.adjudication_notes}
          onChange={(v) => setField("adjudication_notes", v)}
          multiline
          rows={4}
        />
      </Panel>

      <Panel title="Roster">
        <EditList
          label="Present"
          value={draft.characters_present}
          onChange={(v) => setField("characters_present", v)}
        />
        <EditList
          label="Absent"
          value={draft.characters_absent}
          onChange={(v) => setField("characters_absent", v)}
        />
        <EditList
          label="Mentioned only"
          value={draft.characters_mentioned_only}
          onChange={(v) => setField("characters_mentioned_only", v)}
        />
        <EditList
          label="Forbidden"
          value={draft.characters_forbidden}
          onChange={(v) => setField("characters_forbidden", v)}
        />
      </Panel>

      <Panel title="Knowledge & reveals">
        <EditList
          label="Reader MAY know"
          value={draft.allowed_knowledge}
          onChange={(v) => setField("allowed_knowledge", v)}
        />
        <EditList
          label="Reader may NOT know yet"
          value={draft.forbidden_knowledge}
          onChange={(v) => setField("forbidden_knowledge", v)}
        />
        <EditList
          label="Required reveals"
          value={draft.required_reveals}
          onChange={(v) => setField("required_reveals", v)}
        />
        <EditList
          label="Forbidden reveals"
          value={draft.forbidden_reveals}
          onChange={(v) => setField("forbidden_reveals", v)}
        />
      </Panel>

      <Panel title="Locks">
        <EditList
          label="Canon"
          value={draft.canon_locks}
          onChange={(v) => setField("canon_locks", v)}
        />
        <EditList
          label="Roster"
          value={draft.roster_locks}
          onChange={(v) => setField("roster_locks", v)}
        />
        <EditList
          label="Relationship"
          value={draft.relationship_locks}
          onChange={(v) => setField("relationship_locks", v)}
        />
        <EditList
          label="Timeline"
          value={draft.timeline_locks}
          onChange={(v) => setField("timeline_locks", v)}
        />
      </Panel>

      {(draft.scene_seeds?.length ?? 0) > 0 && (
        <Panel title={`Scene seeds · ${draft.scene_seeds!.length}`}>
          <div style={css("display:flex;flex-direction:column;gap:14px")}>
            {draft.scene_seeds!.map((s, i) => (
              <div
                key={s.seed_id ?? i}
                style={css(
                  "border:1px solid var(--line);border-radius:9px;padding:13px;background:var(--bg3)",
                )}
              >
                <div
                  style={css(
                    "font-family:var(--display);font-size:14px;color:var(--ink);margin-bottom:9px",
                  )}
                >
                  Scene {s.scene_no}
                </div>
                <EditText
                  label="Scene job"
                  value={s.scene_job}
                  onChange={(v) => setSeed(i, { scene_job: v })}
                  multiline
                />
                <EditText
                  label="Scene type"
                  value={s.scene_type}
                  onChange={(v) => setSeed(i, { scene_type: v })}
                />
                <EditList
                  label="Required beats"
                  value={s.required_beats}
                  onChange={(v) => setSeed(i, { required_beats: v })}
                />
                <EditList
                  label="Forbidden beats"
                  value={s.forbidden_beats}
                  onChange={(v) => setSeed(i, { forbidden_beats: v })}
                />
                <EditText
                  label="Exit state"
                  value={s.exit_state}
                  onChange={(v) => setSeed(i, { exit_state: v })}
                  multiline
                />
                <div style={css("display:flex;gap:12px;flex-wrap:wrap")}>
                  <EditNum
                    label="Target words"
                    value={s.word_budget?.target}
                    onChange={(v) => setSeed(i, { word_budget: { ...s.word_budget, target: v } })}
                  />
                  <EditNum
                    label="Hard max words"
                    value={s.word_budget?.hard_max}
                    onChange={(v) => setSeed(i, { word_budget: { ...s.word_budget, hard_max: v } })}
                  />
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <div style={css("display:flex;align-items:center;gap:12px;flex-wrap:wrap")}>
        <Button
          variant="primary"
          style="background:var(--good);border-color:transparent"
          disabled={busy}
          onClick={() => onSave(draft)}
        >
          {busy ? "Saving…" : "Save changes"}
        </Button>
        <Button disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// --- small presentational + editor helpers --------------------------------------------------------

// Section panel — the shared Atelier Panel with an optional severity edge (left border accent).
function Panel({
  title,
  children,
  accentVar,
}: {
  title: string;
  children: ReactNode;
  accentVar?: string;
}) {
  return (
    <UiPanel
      eyebrow={title}
      pad="15px 17px"
      style={accentVar ? `border-left:3px solid var(${accentVar})` : ""}
    >
      {children}
    </UiPanel>
  );
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div style={css("margin-bottom:8px")}>
      <div
        style={css(
          "font-family:var(--mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim);margin-bottom:2px",
        )}
      >
        {k}
      </div>
      <div style={css("font-size:13.5px;color:var(--ink);line-height:1.45")}>{v}</div>
    </div>
  );
}

function Label({ text }: { text: string }) {
  return (
    <div
      style={css(
        "font-family:var(--mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim);margin-bottom:6px",
      )}
    >
      {text}
    </div>
  );
}

function EditText({
  label,
  value,
  onChange,
  multiline,
  rows,
}: {
  label: string;
  value?: string;
  onChange: (v: string) => void;
  multiline?: boolean;
  rows?: number;
}) {
  return (
    <div style={css("margin-bottom:10px")}>
      <Label text={label} />
      {multiline ? (
        <textarea
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          rows={rows ?? 2}
          style={inputStyle()}
        />
      ) : (
        <input
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          style={inputStyle()}
        />
      )}
    </div>
  );
}

// A string-array field edited as one item per line. Keeps a local text buffer so blank/intermediate
// lines don't fight the cursor; the normalized (trimmed, non-empty) list is pushed up on every change.
function EditList({
  label,
  value,
  onChange,
}: {
  label: string;
  value?: string[];
  onChange: (v: string[]) => void;
}) {
  const [text, setText] = useState(() => (value ?? []).join("\n"));
  return (
    <div style={css("margin-bottom:10px")}>
      <Label text={`${label} · one per line`} />
      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          onChange(
            e.target.value
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean),
          );
        }}
        rows={Math.max(2, value?.length ?? 0)}
        style={inputStyle()}
      />
    </div>
  );
}

function EditNum({
  label,
  value,
  onChange,
}: {
  label: string;
  value?: number;
  onChange: (v: number | undefined) => void;
}) {
  return (
    <div style={css("margin-bottom:10px")}>
      <Label text={label} />
      <input
        value={value ?? ""}
        inputMode="numeric"
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          onChange(Number.isFinite(n) ? n : undefined);
        }}
        style={css(`${inputBase};width:120px`)}
      />
    </div>
  );
}

function PillList({
  label,
  items,
  tone,
}: {
  label: string;
  items: string[];
  tone: "good" | "bad" | "info" | "warn" | "dim";
}) {
  const v = `--${tone}`;
  return (
    <div style={css("margin-bottom:10px")}>
      <Label text={label} />
      <div style={css("display:flex;flex-wrap:wrap;gap:6px")}>
        {items.map((it, i) => (
          <span
            key={i}
            style={css(
              `font-size:12px;color:var(--ink);background:color-mix(in srgb,var(${v}) 10%,var(--bg3));border:1px solid color-mix(in srgb,var(${v}) 30%,var(--line));border-radius:6px;padding:3px 8px`,
            )}
          >
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}

function SourceBadge({ strength }: { strength: string }) {
  const s = (strength || "").toUpperCase();
  const v =
    s === "LOCKED_CANON"
      ? "--good"
      : s === "FORBIDDEN"
        ? "--bad"
        : s === "UNRESOLVED"
          ? "--warn"
          : s === "DERIVED_FROM_OUTLINE"
            ? "--info"
            : "--dim";
  const short = s
    .replace("DERIVED_FROM_OUTLINE", "OUTLINE")
    .replace("PLAUSIBLE_INFERENCE", "INFERENCE")
    .replace("LOCKED_CANON", "CANON")
    .replace(/_/g, " ");
  return (
    <span
      style={css(
        `flex:none;font-family:var(--mono);font-size:9.5px;letter-spacing:.04em;color:var(${v});border:1px solid color-mix(in srgb,var(${v}) 40%,var(--line));border-radius:5px;padding:2px 6px;margin-top:1px;white-space:nowrap`,
      )}
    >
      {short}
    </span>
  );
}

function Muted({ text }: { text: string }) {
  return (
    <div style={css("font-family:var(--mono);font-size:12px;color:var(--dim);padding:18px 2px")}>
      {text}
    </div>
  );
}

const inputBase =
  "box-sizing:border-box;padding:7px 9px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:13px;line-height:1.45";

function inputStyle(): CSSProperties {
  return css(`${inputBase};width:100%;resize:vertical`);
}
