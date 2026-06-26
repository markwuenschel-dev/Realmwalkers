"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { api } from "../api/client";
import type { PacketBody, PacketClaim, PacketOut, PacketRisk, PacketSceneSeed } from "../api/types";

// The Packet review panel (contract-first drafting, Phase 1). Per chapter, it runs the Packet Author
// + Packet QA agents, then shows the proposed chapter knowledge packet for the human to adjudicate
// and approve BEFORE any prose is drafted. Nothing here touches the drafter — that's a later phase.

const CONFIDENCE_VAR: Record<string, string> = { green: "--good", yellow: "--warn", red: "--bad" };

export default function PacketsScreen() {
  const { t } = useDesk();
  const data = useDeskData();
  const chapters = useMemo(
    () => [...data.chapters].sort((a, b) => a.chapter_no - b.chapter_no),
    [data.chapters],
  );

  const [chapterId, setChapterId] = useState<string | null>(null);
  const [packet, setPacket] = useState<PacketOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Default to the first chapter once chapters load (without clobbering an explicit pick).
  useEffect(() => {
    if (chapterId === null && chapters.length) setChapterId(chapters[0].id);
  }, [chapters, chapterId]);

  const chapter = chapters.find((c) => c.id === chapterId) ?? null;
  const hasOutline = !!(chapter?.outline || "").trim();

  // Fetch the chapter's packet (404 = none yet) whenever the selected chapter changes.
  useEffect(() => {
    if (!chapterId) return;
    let alive = true;
    setLoading(true);
    setError(null);
    setPacket(null);
    api
      .packet(chapterId)
      .then((p) => { if (alive) setPacket(p); })
      .catch(() => { if (alive) setPacket(null); /* 404: no packet yet */ })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [chapterId]);

  const run = useCallback(
    async (label: string, fn: () => Promise<PacketOut>) => {
      setBusy(label);
      setError(null);
      try {
        setPacket(await fn());
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const openItems = (packet?.open_questions?.items ?? []).filter(Boolean);
  const canApprove =
    !!packet && packet.status !== "blocked" && packet.confidence !== "red" && openItems.length === 0;

  const resolveQuestion = (idx: number) => {
    if (!packet || !chapterId) return;
    const items = openItems.filter((_, i) => i !== idx);
    void run("resolve", () => api.updatePacket(chapterId, { open_questions: { items } }));
  };

  return (
    <div>
      <div style={css("display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px")}>
        <div>
          <h1 style={css("margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)")}>Chapter packets</h1>
          <p style={css("margin:0;color:var(--dim);font-size:14.5px;max-width:640px")}>
            The contract the drafting agents obey — allowed vs forbidden knowledge, reveal timing, roster and canon locks, scene jobs, and known drift risks. Author it, adjudicate the flags, approve it before any prose is written.
          </p>
        </div>
        <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
          <select
            value={chapterId ?? ""}
            onChange={(e) => setChapterId(e.target.value || null)}
            style={css("height:34px;padding:0 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:13px")}
          >
            {chapters.length === 0 && <option value="">No chapters yet</option>}
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>
                Ch {c.chapter_no}{c.title ? ` · ${c.title}` : ""} ({c.pov})
              </option>
            ))}
          </select>
          <button
            disabled={!chapterId || !hasOutline || busy === "propose"}
            title={hasOutline ? undefined : "Outline this chapter first (Inbox → plan a chapter)"}
            onClick={() => chapterId && run("propose", () => api.proposePacket(chapterId))}
            style={btn(!!chapterId && hasOutline && busy !== "propose", t.accent, t.onAccent)}
          >
            {busy === "propose" ? "Authoring…" : packet ? "Re-propose" : "Propose packet"}
          </button>
        </div>
      </div>

      {error && (
        <div style={css(`margin-bottom:16px;border:1px solid color-mix(in srgb,${t.bad} 40%,var(--line));background:color-mix(in srgb,${t.bad} 8%,var(--bg2));border-radius:9px;padding:11px 13px;color:${t.bad};font-size:13px`)}>
          {error}
        </div>
      )}

      {loading && <Muted text="Loading packet…" />}

      {!loading && !packet && (
        <div style={css("border:1px dashed var(--line);border-radius:12px;padding:30px;text-align:center;color:var(--dim)")}>
          <div style={css("font-family:var(--display);font-size:17px;color:var(--ink);margin-bottom:6px")}>No packet yet</div>
          <div style={css("font-size:13.5px;max-width:420px;margin:0 auto")}>
            {hasOutline
              ? "Propose a packet to have the Packet Author + QA agents draft this chapter's drafting contract."
              : "This chapter has no outline yet. Plan it from the Inbox first, then return here to author its packet."}
          </div>
        </div>
      )}

      {!loading && packet && <PacketView packet={packet} openItems={openItems} onResolve={resolveQuestion} />}

      {!loading && packet && (
        <div style={css("display:flex;align-items:center;gap:14px;margin-top:22px;flex-wrap:wrap")}>
          <button
            disabled={!canApprove || busy === "approve"}
            onClick={() => chapterId && run("approve", () => api.approvePacket(chapterId))}
            style={btn(canApprove && busy !== "approve", t.good, t.onAccent)}
          >
            {busy === "approve" ? "Approving…" : packet.status === "approved" ? "Approved ✓" : "Approve packet"}
          </button>
          {!canApprove && packet.status !== "approved" && (
            <span style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim)")}>
              {packet.status === "blocked"
                ? "Blocked — re-propose or edit before approving."
                : packet.confidence === "red"
                  ? "Red confidence — resolve before approving."
                  : openItems.length
                    ? `Resolve ${openItems.length} open question${openItems.length > 1 ? "s" : ""} to approve.`
                    : ""}
            </span>
          )}
          {packet.status === "approved" && (
            <>
              <button
                disabled={busy === "draft"}
                onClick={async () => {
                  if (!chapterId) return;
                  setBusy("draft");
                  setError(null);
                  try {
                    await api.draftChapter(chapterId);
                    await data.draftNext();   // kick the worker drain
                    await data.refreshAll();
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(null);
                  }
                }}
                style={btn(busy !== "draft", t.accent, t.onAccent)}
              >
                {busy === "draft" ? "Queuing…" : "Draft this chapter →"}
              </button>
              <span style={css(`font-family:var(--mono);font-size:11.5px;color:var(${CONFIDENCE_VAR.green})`)}>
                {(packet.body?.scene_seeds?.length ?? 0)} scene contract{(packet.body?.scene_seeds?.length ?? 0) === 1 ? "" : "s"} now scope the drafter.
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function PacketView({
  packet, openItems, onResolve,
}: { packet: PacketOut; openItems: string[]; onResolve: (i: number) => void }) {
  const b: PacketBody = packet.body ?? {};
  const confVar = CONFIDENCE_VAR[packet.confidence ?? ""] ?? "--dim";
  const blockedReason = packet.qa_warnings?.blocked_reason ?? b.blocked_reason;
  const residual = packet.qa_warnings?.residual_risks ?? [];
  const issues = packet.qa_warnings?.issues ?? [];

  return (
    <div style={css("display:flex;flex-direction:column;gap:16px")}>
      {/* status / confidence / verdict */}
      <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
        <Chip label={`status: ${packet.status}`} colorVar={packet.status === "approved" ? "--good" : packet.status === "blocked" ? "--bad" : "--warn"} />
        {packet.confidence && <Chip label={`confidence: ${packet.confidence}`} colorVar={confVar} />}
        {packet.qa_verdict && <Chip label={`QA: ${packet.qa_verdict.replace(/_/g, " ")}`} colorVar="--info" />}
      </div>

      {blockedReason && (
        <Panel accentVar="--bad" title="Blocked">
          <div style={css("font-size:13px;color:var(--ink)")}>{blockedReason}</div>
          <div style={css("font-size:12px;color:var(--dim);margin-top:6px")}>
            The packet failed closed — no prose may be drafted from it. Re-propose, or edit the chapter outline and try again.
          </div>
        </Panel>
      )}

      {openItems.length > 0 && (
        <Panel accentVar="--warn" title={`Open questions · ${openItems.length}`}>
          <div style={css("display:flex;flex-direction:column;gap:8px")}>
            {openItems.map((q, i) => (
              <div key={i} style={css("display:flex;align-items:flex-start;justify-content:space-between;gap:12px")}>
                <span style={css("font-size:13px;color:var(--ink);line-height:1.45")}>{q}</span>
                <button onClick={() => onResolve(i)} style={miniBtn()}>Resolve</button>
              </div>
            ))}
          </div>
          <div style={css("font-size:11.5px;color:var(--dim);margin-top:9px;font-family:var(--mono)")}>
            Adjudicate each (edit the outline/canon as needed), then resolve to clear it. All must be resolved to approve.
          </div>
        </Panel>
      )}

      {(b.chapter_job || b.one_sentence_spine || b.entry_state || b.exit_state || b.emotional_spine) && (
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
          {residual.length > 0 && <PillList label="Residual risks the writer must avoid" items={residual} tone="warn" />}
          {issues.length > 0 && (
            <div style={css("margin-top:10px")}>
              <Label text="Issues raised" />
              <div style={css("display:flex;flex-direction:column;gap:6px")}>
                {issues.map((iss, i) => (
                  <div key={i} style={css("font-size:12.5px;color:var(--ink)")}>
                    <span style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}>{iss.kind || "issue"}: </span>
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
                {r.why_dangerous && <div style={css("font-size:12.5px;color:var(--dim);margin-top:2px")}>Why: {r.why_dangerous}</div>}
                {r.prevention && <div style={css("font-size:12.5px;color:var(--ink);margin-top:2px")}>Prevent: {r.prevention}</div>}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {/* roster */}
      {(b.characters_present?.length || b.characters_absent?.length || b.characters_mentioned_only?.length || b.characters_forbidden?.length) ? (
        <Panel title="Roster">
          {b.characters_present?.length ? <PillList label="Present" items={b.characters_present} tone="good" /> : null}
          {b.characters_absent?.length ? <PillList label="Absent" items={b.characters_absent} tone="dim" /> : null}
          {b.characters_mentioned_only?.length ? <PillList label="Mentioned only" items={b.characters_mentioned_only} tone="info" /> : null}
          {b.characters_forbidden?.length ? <PillList label="Forbidden" items={b.characters_forbidden} tone="bad" /> : null}
        </Panel>
      ) : null}

      {/* knowledge + reveals */}
      {(b.allowed_knowledge?.length || b.forbidden_knowledge?.length || b.required_reveals?.length || b.forbidden_reveals?.length) ? (
        <Panel title="Knowledge & reveals">
          {b.allowed_knowledge?.length ? <PillList label="Reader MAY know" items={b.allowed_knowledge} tone="good" /> : null}
          {b.forbidden_knowledge?.length ? <PillList label="Reader may NOT know yet" items={b.forbidden_knowledge} tone="bad" /> : null}
          {b.required_reveals?.length ? <PillList label="Required reveals" items={b.required_reveals} tone="info" /> : null}
          {b.forbidden_reveals?.length ? <PillList label="Forbidden reveals" items={b.forbidden_reveals} tone="bad" /> : null}
        </Panel>
      ) : null}

      {/* locks */}
      {(b.canon_locks?.length || b.roster_locks?.length || b.relationship_locks?.length || b.timeline_locks?.length) ? (
        <Panel title="Locks">
          {b.canon_locks?.length ? <PillList label="Canon" items={b.canon_locks} tone="info" /> : null}
          {b.roster_locks?.length ? <PillList label="Roster" items={b.roster_locks} tone="info" /> : null}
          {b.relationship_locks?.length ? <PillList label="Relationship" items={b.relationship_locks} tone="info" /> : null}
          {b.timeline_locks?.length ? <PillList label="Timeline" items={b.timeline_locks} tone="info" /> : null}
        </Panel>
      ) : null}

      {/* scene seeds */}
      {(b.scene_seeds?.length ?? 0) > 0 && (
        <Panel title={`Scene seeds · ${b.scene_seeds!.length}${packet.status === "approved" ? " · scoping the drafter" : ""}`}>
          <div style={css("display:flex;flex-direction:column;gap:12px")}>
            {b.scene_seeds!.map((s: PacketSceneSeed) => (
              <div key={s.seed_id} style={css("border:1px solid var(--line);border-radius:9px;padding:11px 13px;background:var(--bg3)")}>
                <div style={css("display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:5px")}>
                  <span style={css("font-family:var(--display);font-size:14.5px;color:var(--ink)")}>Scene {s.scene_no}{s.scene_type ? <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}> · {s.scene_type}</span> : null}</span>
                  {s.word_budget?.target ? <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>~{s.word_budget.target}w{s.word_budget.hard_max ? ` (≤${s.word_budget.hard_max})` : ""}</span> : null}
                </div>
                {s.scene_job && <div style={css("font-size:13px;color:var(--ink);margin-bottom:5px")}>{s.scene_job}</div>}
                {s.required_beats?.length ? <PillList label="Required" items={s.required_beats} tone="good" /> : null}
                {s.forbidden_beats?.length ? <PillList label="Forbidden" items={s.forbidden_beats} tone="bad" /> : null}
                {s.exit_state && <div style={css("font-size:12px;color:var(--dim);margin-top:4px")}>Exit: {s.exit_state}</div>}
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
              <div key={i} style={css("display:flex;align-items:flex-start;gap:10px;padding-bottom:8px;border-bottom:1px solid var(--line)")}>
                <SourceBadge strength={c.source_strength} />
                <div style={css("min-width:0;flex:1")}>
                  <div style={css("font-size:13px;color:var(--ink);line-height:1.4")}>{c.claim}</div>
                  <div style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:2px")}>
                    {c.source_title_or_file ? `source: ${c.source_title_or_file}` : "no canonical source"}
                    {c.confidence ? ` · ${c.confidence}` : ""}
                    {c.excerpt ? ` — “${c.excerpt.slice(0, 120)}${c.excerpt.length > 120 ? "…" : ""}”` : ""}
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

// --- small presentational helpers -----------------------------------------------------------------

function Panel({ title, children, accentVar }: { title: string; children: ReactNode; accentVar?: string }) {
  return (
    <div style={css(`background:var(--bg2);border:1px solid var(--line);${accentVar ? `border-left:3px solid var(${accentVar});` : ""}border-radius:11px;padding:15px 17px`)}>
      <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:11px")}>{title}</div>
      {children}
    </div>
  );
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div style={css("margin-bottom:8px")}>
      <div style={css("font-family:var(--mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim);margin-bottom:2px")}>{k}</div>
      <div style={css("font-size:13.5px;color:var(--ink);line-height:1.45")}>{v}</div>
    </div>
  );
}

function Label({ text }: { text: string }) {
  return <div style={css("font-family:var(--mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim);margin-bottom:6px")}>{text}</div>;
}

function PillList({ label, items, tone }: { label: string; items: string[]; tone: "good" | "bad" | "info" | "warn" | "dim" }) {
  const v = `--${tone}`;
  return (
    <div style={css("margin-bottom:10px")}>
      <Label text={label} />
      <div style={css("display:flex;flex-wrap:wrap;gap:6px")}>
        {items.map((it, i) => (
          <span key={i} style={css(`font-size:12px;color:var(--ink);background:color-mix(in srgb,var(${v}) 10%,var(--bg3));border:1px solid color-mix(in srgb,var(${v}) 30%,var(--line));border-radius:6px;padding:3px 8px`)}>{it}</span>
        ))}
      </div>
    </div>
  );
}

function Chip({ label, colorVar }: { label: string; colorVar: string }) {
  return (
    <span style={css(`font-family:var(--mono);font-size:11px;color:var(${colorVar});background:color-mix(in srgb,var(${colorVar}) 12%,var(--bg2));border:1px solid color-mix(in srgb,var(${colorVar}) 35%,var(--line));border-radius:999px;padding:3px 10px`)}>{label}</span>
  );
}

function SourceBadge({ strength }: { strength: string }) {
  const s = (strength || "").toUpperCase();
  const v = s === "LOCKED_CANON" ? "--good"
    : s === "FORBIDDEN" ? "--bad"
    : s === "UNRESOLVED" ? "--warn"
    : s === "DERIVED_FROM_OUTLINE" ? "--info"
    : "--dim";
  const short = s.replace("DERIVED_FROM_OUTLINE", "OUTLINE").replace("PLAUSIBLE_INFERENCE", "INFERENCE").replace("LOCKED_CANON", "CANON").replace(/_/g, " ");
  return (
    <span style={css(`flex:none;font-family:var(--mono);font-size:9.5px;letter-spacing:.04em;color:var(${v});border:1px solid color-mix(in srgb,var(${v}) 40%,var(--line));border-radius:5px;padding:2px 6px;margin-top:1px;white-space:nowrap`)}>{short}</span>
  );
}

function Muted({ text }: { text: string }) {
  return <div style={css("font-family:var(--mono);font-size:12px;color:var(--dim);padding:18px 2px")}>{text}</div>;
}

function btn(enabled: boolean, bg: string, fg: string): CSSProperties {
  return css(`height:34px;padding:0 16px;border-radius:8px;border:none;font-family:var(--ui);font-size:13px;font-weight:500;cursor:${enabled ? "pointer" : "default"};background:${enabled ? bg : "var(--bg3)"};color:${enabled ? fg : "var(--dim)"};opacity:${enabled ? 1 : 0.7}`);
}

function miniBtn(): CSSProperties {
  return css("flex:none;height:26px;padding:0 11px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:12px;cursor:pointer");
}
