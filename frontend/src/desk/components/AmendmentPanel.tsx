"use client";

import type { ReactNode } from "react";
import { css } from "../css";
import { conflictDetail, toDeskError } from "../api/hooks/shared";
import type { AmendmentEligibilityOut, PacketOut } from "../api/types";
import GateDisclosure from "./GateDisclosure";
import type { GateRow } from "./GateDisclosure";
import { Button, Chip, Panel as UiPanel } from "./ui";

// Amendment mode (#261) — the author-facing surface for replacing an APPROVED chapter contract that
// never seeded some imported scene. It exists to make ONE thing unmissable:
//
//   GET /chapters/{id}/packet resolves by RECENCY with no status filter. So the moment an amendment is
//   proposed, that endpoint returns the PROPOSAL — while the approved predecessor is still the
//   chapter's governing authority for drafting, derivation, and every gate.
//
// There is NO endpoint that returns "the active authority". This panel therefore never claims to show
// it: it names the predecessor by the id the proposal itself carries (`supersedes_packet_id`) and says
// so out loud. Everything else it renders comes from real fields — lineage, fingerprints, the
// eligibility verdict, and (after approval) the consequence set the locked transition recorded.

/** The closed set of `AmendmentEligibilityOut.reason` tokens (`workers/packet/amendment.py:137-141`).
 *  Switch on these — never on `message`, which is prose that may be reworded. */
export type AmendmentReason =
  | "unseeded_scenes_present"
  | "no_approved_packet"
  | "no_imported_scenes"
  | "all_scenes_seeded"
  | "amendment_already_open";

/** The ONE eligible token. Every other token is a refusal. */
export const ELIGIBLE_REASON: AmendmentReason = "unseeded_scenes_present";

/** True when the packet the Desk is showing is a PROPOSAL, not the chapter's live contract. This is the
 *  whole authority-split detection: `origin_mode` says it was authored as an amendment and `status`
 *  says it has not taken authority yet, so the predecessor still governs. */
export function isProposedAmendment(packet: PacketOut | null | undefined): boolean {
  return !!packet && packet.origin_mode === "amendment" && packet.status === "proposed";
}

/** True once an amendment has actually taken chapter authority (its lineage columns are then filled). */
export function isApprovedAmendment(packet: PacketOut | null | undefined): boolean {
  return !!packet && packet.origin_mode === "amendment" && packet.status === "approved";
}

/** Whether this panel has anything to render at all. Exported so a screen can skip its wrapper markup
 *  instead of laying out an empty box (the component itself returns null on the same condition). */
export function hasAmendmentSurface(
  packet: PacketOut | null | undefined,
  eligibility: AmendmentEligibilityOut | null | undefined,
  failure?: unknown,
): boolean {
  return (
    isProposedAmendment(packet) ||
    isApprovedAmendment(packet) ||
    eligibility != null ||
    failure != null
  );
}

/** First 8 of a uuid — enough to match against a log line or another panel, short enough to scan. */
export function shortId(v?: string | null): string {
  return v ? v.slice(0, 8) : "—";
}

/** First 12 of a sha256 hex fingerprint. Deliberately labelled as a fingerprint everywhere it is
 *  shown: it is an equality token, NOT a readable summary of what changed. */
export function shortFingerprint(v?: string | null): string {
  return v ? `${v.slice(0, 12)}…` : "none recorded";
}

// The eligibility gates in the exact order the server evaluates them (amendment.py:229-296). Order is
// load-bearing for honesty: assessment SHORT-CIRCUITS at the first refusal, so gates below the failing
// one were never evaluated and must not be rendered as "pass".
const GATE_SEQUENCE: { reason: AmendmentReason; label: string }[] = [
  { reason: "no_approved_packet", label: "Chapter has an approved contract to amend" },
  { reason: "no_imported_scenes", label: "Chapter has imported prose" },
  { reason: "all_scenes_seeded", label: "Some imported scene has no seed in that contract" },
  { reason: "amendment_already_open", label: "No amendment already in flight" },
];

const GATE_FAIL_DETAIL: Record<AmendmentReason, string> = {
  no_approved_packet:
    "amendment is copy-on-write FROM an approved packet; adopt an initial contract first",
  no_imported_scenes: "no imported prose, so no scene can be missing a seed",
  all_scenes_seeded: "every imported scene resolves to a seed; re-derive the scene packet instead",
  amendment_already_open: "a chapter may only have one amendment in flight",
  unseeded_scenes_present: "eligible",
};

const GATE_PASS_DETAIL: Record<AmendmentReason, (el: AmendmentEligibilityOut) => string> = {
  no_approved_packet: (el) => `approved packet ${shortId(el.approved_packet_id)}`,
  no_imported_scenes: (el) =>
    `${el.seeded_scene_ids.length + el.unseeded_scene_ids.length} imported scene(s)`,
  all_scenes_seeded: (el) => `${el.unseeded_scene_ids.length} scene(s) carry no seed`,
  amendment_already_open: () => "no proposed amendment for this chapter",
  unseeded_scenes_present: () => "eligible",
};

/** The refusal `reason` rendered as pass/fail gate rows for `GateDisclosure`. Rows stop AT the failing
 *  gate — the server never ran the ones after it, so claiming they passed would be a fabrication. */
export function eligibilityGateRows(el: AmendmentEligibilityOut): GateRow[] {
  const rows: GateRow[] = [];
  for (const gate of GATE_SEQUENCE) {
    const failed = el.reason === gate.reason;
    rows.push({
      label: gate.label,
      pass: !failed,
      detail: failed
        ? `${GATE_FAIL_DETAIL[gate.reason]} — later checks were not reached.`
        : GATE_PASS_DETAIL[gate.reason](el),
    });
    if (failed) break;
  }
  return rows;
}

/** Recovery instructions per failure. Keyed on the machine `reason`; the server's own sentence is always
 *  shown too, and an unrecognised or non-409 failure still gets a title, the raw message, and a stated
 *  next step — an opaque failure is never an acceptable render here. */
function recoveryFor(
  failure: unknown,
  el: AmendmentEligibilityOut | null,
): { title: string; reason: string | null; lines: string[] } | null {
  if (failure == null) return null;
  const detail = conflictDetail(failure);
  const reason = detail?.reason ?? null;
  const server = detail?.message ?? toDeskError(failure);

  if (reason === "amendment_source_drifted") {
    const prints =
      detail?.expected || detail?.actual
        ? [
            `Fingerprints: authored against ${shortFingerprint(detail?.expected)} · chapter now ${shortFingerprint(detail?.actual)}.`,
          ]
        : [];
    return {
      title: "Refused — NOTHING was changed",
      reason,
      lines: [
        server,
        "The approved contract and every scene packet are exactly as they were. This is a refusal under the chapter lock, not a partial write, so there is nothing to clean up.",
        "Fix: the chapter's prose moved after this amendment was authored. Re-run the amendment against the current prose (Start amendment), review that proposal, and approve that one instead.",
        ...prints,
      ],
    };
  }
  if (reason === "amendment_already_open") {
    return {
      title: "An amendment is already open",
      reason,
      lines: [
        server,
        `Go to the existing branch — packet ${shortId(el?.open_amendment_packet_id)} — and review or discard it. Starting a second one is refused, not queued.`,
      ],
    };
  }
  if (reason === "amendment_predecessor_missing") {
    return {
      title: "Refused — NOTHING was changed",
      reason,
      lines: [
        server,
        "Another operation changed this chapter's contract first, so the packet this amendment was written to replace no longer governs. Re-check eligibility and re-run the amendment.",
      ],
    };
  }
  if (reason === "amendment_requires_amendment_approval") {
    return {
      title: "Wrong approval route",
      reason,
      lines: [
        server,
        "Use Approve amendment in this panel — it supersedes the predecessor and stales its scene contracts in the same transaction, which the ordinary approve does not do.",
      ],
    };
  }
  if (reason === "chapter_workflow_busy") {
    return {
      title: "Chapter is busy — nothing was changed",
      reason,
      lines: [server, "Another workflow operation holds this chapter's lock. Retry in a moment."],
    };
  }
  return {
    title: reason ? "Refused" : "Failed",
    reason,
    lines: [
      server,
      reason
        ? `The server refused with reason \`${reason}\`, which this panel has no specific recovery for — the sentence above is the server's own.`
        : "This failure carried no machine-readable reason, so the raw error is shown above. If it is not a 409 the request may not have reached the amendment transition at all; re-check eligibility before retrying.",
    ],
  };
}

export default function AmendmentPanel({
  packet,
  authority = null,
  eligibility,
  busy = null,
  failure = null,
  notice = null,
  onStart,
  onApprove,
  onRefresh,
}: {
  /** The chapter's newest packet as `GET .../packet` resolved it — by recency, ANY status. */
  packet: PacketOut | null;
  /** The governing approved packet from `GET .../packet/authority`, or null if none / not fetched. */
  authority?: PacketOut | null;
  /** The read-only preflight verdict, or null when it has not been fetched / 404'd. */
  eligibility: AmendmentEligibilityOut | null;
  busy?: "start" | "approve" | null;
  /** The caught error from the last amendment action (any shape) — rendered as recovery, not a dump. */
  failure?: unknown;
  /** Post-start confirmation written by the screen (the adoption row's own status). */
  notice?: string | null;
  onStart?: () => void;
  onApprove?: () => void;
  onRefresh?: () => void;
}) {
  const proposed = isProposedAmendment(packet);
  const approved = isApprovedAmendment(packet);
  const recovery = recoveryFor(failure, eligibility);

  // Nothing to say: no amendment lineage on the packet, no verdict to explain, no failure to recover.
  if (!hasAmendmentSurface(packet, eligibility, failure)) return null;

  const eligible = eligibility?.eligible ?? false;
  // `amendment_already_open` naming THIS packet is not a blocker — it is the expected state while the
  // branch under review exists. Framing it as a refusal would tell the author to fix a non-problem.
  const openIsThisPacket =
    !!packet && !!eligibility?.open_amendment_packet_id
      ? eligibility.open_amendment_packet_id === packet.id
      : false;
  const scope = packet?.amendment_scope ?? null;
  const staled = scope?.staled_scene_packet_ids ?? [];
  const predecessorId =
    authority?.id ?? packet?.supersedes_packet_id ?? scope?.predecessor_packet_id ?? null;
  // Both fingerprints are advisory reads, but they are the SAME function on both sides
  // (`shared/prose_fingerprint.chapter_source_fingerprint`), and the drift gate compares exactly these
  // two (`workers/packet/amendment.py:414-416`) — so a mismatch predicts the refusal.
  const authoredAgainst = packet?.source_fingerprint ?? null;
  const chapterNow = eligibility?.source_fingerprint ?? null;
  const driftLikely =
    proposed && !!authoredAgainst && !!chapterNow && authoredAgainst !== chapterNow;

  return (
    <UiPanel
      eyebrow="Amendment · chapter contract"
      title={
        proposed
          ? "Proposed amendment under review"
          : approved
            ? "Amendment approved — lineage"
            : "Amend this chapter's contract"
      }
      pad="15px 17px"
      style="border-left:3px solid var(--warn)"
    >
      <div style={css("display:flex;flex-direction:column;gap:14px")} data-testid="amendment-panel">
        {proposed && (
          <div
            role="alert"
            data-testid="amendment-authority-banner"
            style={css(
              "display:flex;flex-direction:column;gap:7px;border:1px solid color-mix(in srgb,var(--warn) 45%,var(--line));background:color-mix(in srgb,var(--warn) 9%,var(--bg2));border-radius:9px;padding:12px 14px",
            )}
          >
            <strong style={css("font-size:13.5px;color:var(--warn);font-weight:600")}>
              You are looking at a proposed amendment under review — not this chapter&apos;s live
              contract.
            </strong>
            <Line>
              The chapter is still governed by its predecessor, packet {shortId(predecessorId)}.
              Drafting, scene-packet derivation and every gate answer to that packet until this
              amendment is approved.
            </Line>
            <Line>
              This proposal is what you see because GET /packet resolves by recency with no status
              filter. GET /packet/authority is the governing contract
              {authority ? ` (${shortId(authority.id)})` : ""}.
            </Line>
            <Line>
              Approving it will supersede that predecessor and mark the scene contracts derived from
              it stale; those scenes must be re-derived before they can be drafted.
            </Line>
            <Mono>
              Honest limit: no endpoint returns &quot;the active authority&quot;. The predecessor
              above is the id this proposal carries (supersedes_packet_id) — not a separate read of
              the live contract.
            </Mono>
            {!predecessorId && (
              <Mono tone="--bad">
                This proposal names no predecessor (supersedes_packet_id is null), so approving it
                will refuse with amendment_predecessor_missing and change nothing.
              </Mono>
            )}
          </div>
        )}

        {/* --- predecessor / lineage ------------------------------------------------------------- */}
        {(proposed || approved) && (
          <Section label="Predecessor">
            <KV
              k="Supersedes packet"
              v={predecessorId ? shortId(predecessorId) : "none named"}
              title={predecessorId ?? undefined}
            />
            {approved && (
              <>
                <KV
                  k="Superseded at"
                  v={packet?.superseded_at ?? scope?.superseded_at ?? "not recorded"}
                />
                <KV
                  k="Approved at · source"
                  v={`${packet?.approved_at ?? "not recorded"} · ${packet?.approval_source ?? "not recorded"}`}
                />
              </>
            )}
            {packet?.superseded_by_packet_id && (
              <KV
                k="This packet was itself superseded by"
                v={shortId(packet.superseded_by_packet_id)}
                title={packet.superseded_by_packet_id}
              />
            )}
            {packet?.origin_adoption_id && (
              <KV k="Origin adoption" v={shortId(packet.origin_adoption_id)} />
            )}
          </Section>
        )}

        {/* --- evidence identity ----------------------------------------------------------------- */}
        {(proposed || approved) && (
          <Section label="Evidence change">
            <KV
              k="Prose fingerprint this packet was authored against"
              v={shortFingerprint(authoredAgainst)}
              title={authoredAgainst ?? undefined}
            />
            <KV
              k="Chapter's prose fingerprint now (preflight)"
              v={shortFingerprint(chapterNow)}
              title={chapterNow ?? undefined}
            />
            <KV
              k="Evidence-manifest fingerprint"
              v={shortFingerprint(packet?.evidence_manifest_fingerprint)}
              title={packet?.evidence_manifest_fingerprint ?? undefined}
            />
            <Mono>
              These are hashes, not a diff. They answer only &quot;same or different&quot; — this
              panel cannot compute what changed inside the evidence set from them, and does not
              pretend to.
            </Mono>
            {driftLikely && (
              <Mono tone="--bad">
                The two prose fingerprints differ, so approving will very likely refuse with
                amendment_source_drifted and change nothing. Both reads are advisory; the gate that
                decides runs under the chapter lock.
              </Mono>
            )}
          </Section>
        )}

        {/* --- affected scenes ------------------------------------------------------------------- */}
        {(eligibility || approved) && (
          <Section label="Affected scenes">
            {eligibility && (
              <IdList
                label={`Imported scenes with no seed · ${eligibility.unseeded_scene_ids.length}`}
                caption="why amendment is permitted at all"
                ids={eligibility.unseeded_scene_ids}
                empty="none — no scene is missing a seed"
                tone="--warn"
              />
            )}
            {eligibility && eligibility.seeded_scene_ids.length > 0 && (
              <IdList
                label={`Already seeded · ${eligibility.seeded_scene_ids.length}`}
                caption="covered by the approved contract"
                ids={eligibility.seeded_scene_ids}
                empty="none"
                tone="--good"
              />
            )}
            {approved && (
              <IdList
                label={`Scene contracts staled by this amendment · ${staled.length}`}
                caption="what the supersession invalidated — re-derive these before drafting"
                ids={staled}
                empty="none recorded (amendment_scope carries no staled ids)"
                tone="--bad"
              />
            )}
          </Section>
        )}

        {/* --- eligibility verdict --------------------------------------------------------------- */}
        {eligibility && (
          <Section label="Eligibility">
            <div
              style={css(
                "display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px",
              )}
            >
              <Chip
                label={eligible ? "eligible" : openIsThisPacket ? "under review" : "not eligible"}
                tone={eligible ? "good" : openIsThisPacket ? "info" : "warn"}
                size="sm"
              />
              <span
                data-testid="amendment-reason-token"
                style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}
              >
                {eligibility.reason}
              </span>
            </div>
            {eligible ? (
              <Line>
                This chapter has imported prose its approved contract never seeded, which is the one
                state amendment exists for. Starting one buys a single author pass; approving the
                result is a separate, second decision.
              </Line>
            ) : (
              <>
                {openIsThisPacket && (
                  <Line>
                    Expected, not a problem: the amendment already open is the proposal shown above.
                    Review it and either approve it or discard it — a chapter may only have one
                    amendment in flight.
                  </Line>
                )}
                {eligibility.message && (
                  <Line data-testid="amendment-refusal-message">{eligibility.message}</Line>
                )}
                <GateDisclosure
                  lead={eligibility.message}
                  rows={eligibilityGateRows(eligibility)}
                  testId="amendment-gate-disclosure"
                />
              </>
            )}
            <Mono>
              Advisory only: the approve transition recomputes this verdict under the chapter
              workflow lock, so an eligible answer here informs this screen — it never authorizes
              the change.
            </Mono>
          </Section>
        )}

        {/* --- consequences --------------------------------------------------------------------- */}
        {(proposed || approved) && (
          <Section label={approved ? "What this amendment did" : "What approving does"}>
            <Bullets
              items={[
                approved
                  ? `Superseded packet ${shortId(predecessorId)} — it is no longer the chapter's authority.`
                  : `Supersedes packet ${shortId(predecessorId)} — it stops being the chapter's authority and this packet takes over.`,
                approved
                  ? `Marked ${staled.length} scene contract(s) stale, listed above.`
                  : "Marks every live scene contract derived from that predecessor stale — approving records exactly which ones in amendment_scope.staled_scene_packet_ids.",
                "Those scene contracts must be re-derived and re-approved before those scenes can be drafted; staling preserves their review history rather than deleting them.",
                approved
                  ? "Ran as one chapter-locked transaction: eligibility, the prose fingerprint and the predecessor's authority were all re-checked before anything was written."
                  : "Runs as one chapter-locked transaction: eligibility, the prose fingerprint and the predecessor's authority are re-checked, and any drift refuses with nothing written.",
              ]}
            />
          </Section>
        )}

        {/* --- recovery ------------------------------------------------------------------------- */}
        {recovery && (
          <div
            role="alert"
            data-testid="amendment-recovery"
            style={css(
              "display:flex;flex-direction:column;gap:6px;border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--bg2));border-radius:9px;padding:12px 14px",
            )}
          >
            <strong style={css("font-size:13px;color:var(--bad);font-weight:600")}>
              {recovery.title}
            </strong>
            {recovery.reason && (
              <span
                data-testid="amendment-recovery-reason"
                style={css("font-family:var(--mono);font-size:11px;color:var(--dim)")}
              >
                {recovery.reason}
              </span>
            )}
            {recovery.lines.map((l, i) => (
              <Line key={i}>{l}</Line>
            ))}
          </div>
        )}

        {notice && (
          <div
            role="status"
            data-testid="amendment-notice"
            style={css(
              "border:1px solid color-mix(in srgb,var(--info) 40%,var(--line));background:color-mix(in srgb,var(--info) 8%,var(--bg2));border-radius:9px;padding:11px 13px",
            )}
          >
            <Line>{notice}</Line>
          </div>
        )}

        {/* --- actions -------------------------------------------------------------------------- */}
        <div style={css("display:flex;align-items:center;gap:10px;flex-wrap:wrap")}>
          {onStart && (
            <Button
              disabled={!eligible || busy != null}
              title={
                eligible
                  ? "Author a replacement contract for this chapter (one model pass; approving it is a separate action)"
                  : (eligibility?.message ??
                    "Amendment eligibility has not been read for this chapter")
              }
              onClick={onStart}
            >
              {busy === "start" ? "Starting…" : "Start amendment"}
            </Button>
          )}
          {onApprove && proposed && (
            <Button
              variant="primary"
              style="background:var(--warn);border-color:transparent"
              disabled={busy != null}
              title="Approve this amendment: supersedes the predecessor and stales its scene contracts in one transaction"
              onClick={onApprove}
            >
              {busy === "approve" ? "Approving…" : "Approve amendment"}
            </Button>
          )}
          {onRefresh && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy != null}
              title="Re-read this chapter's packet and amendment eligibility"
              onClick={onRefresh}
            >
              Refresh
            </Button>
          )}
        </div>
      </div>
    </UiPanel>
  );
}

// --- small presentational helpers (local, matching the Packets screen's idiom) ---------------------

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section style={css("display:flex;flex-direction:column;gap:5px")}>
      <div
        style={css(
          "font-family:var(--mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim)",
        )}
      >
        {label}
      </div>
      {children}
    </section>
  );
}

function Line({ children, ...rest }: { children: ReactNode; "data-testid"?: string }) {
  return (
    <p style={css("margin:0;font-size:12.5px;color:var(--ink);line-height:1.5")} {...rest}>
      {children}
    </p>
  );
}

function Mono({ children, tone = "--dim" }: { children: ReactNode; tone?: string }) {
  return (
    <span style={css(`font-family:var(--mono);font-size:11px;color:var(${tone});line-height:1.5`)}>
      {children}
    </span>
  );
}

function KV({ k, v, title }: { k: string; v: string; title?: string }) {
  return (
    <div style={css("display:flex;gap:8px;align-items:baseline;flex-wrap:wrap")}>
      <span style={css("font-size:12.5px;color:var(--dim)")}>{k}</span>
      <span
        title={title}
        style={css(
          "font-family:var(--mono);font-size:11.5px;color:var(--ink);word-break:break-all",
        )}
      >
        {v}
      </span>
    </div>
  );
}

function IdList({
  label,
  caption,
  ids,
  empty,
  tone,
}: {
  label: string;
  caption: string;
  ids: string[];
  empty: string;
  tone: string;
}) {
  return (
    <div style={css("margin-bottom:6px")}>
      <div style={css("font-size:12.5px;color:var(--ink)")}>
        {label} <span style={css("color:var(--dim)")}>— {caption}</span>
      </div>
      {ids.length === 0 ? (
        <Mono>{empty}</Mono>
      ) : (
        <ul
          style={css(
            "margin:4px 0 0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:5px",
          )}
        >
          {ids.map((id) => (
            <li
              key={id}
              title={id}
              style={css(
                `font-family:var(--mono);font-size:10.5px;color:var(--ink);background:color-mix(in srgb,var(${tone}) 10%,var(--bg3));border:1px solid color-mix(in srgb,var(${tone}) 30%,var(--line));border-radius:6px;padding:2px 7px`,
              )}
            >
              {shortId(id)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul style={css("margin:0;padding-left:18px;display:flex;flex-direction:column;gap:4px")}>
      {items.map((it, i) => (
        <li key={i} style={css("font-size:12.5px;color:var(--ink);line-height:1.5")}>
          {it}
        </li>
      ))}
    </ul>
  );
}
