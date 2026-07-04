import type { PacketBody, PacketOut, PacketWarnings, QaIssue } from "../api/types";

export interface PacketBlockedGuidance {
  title: string;
  reason: string | null;
  detail: string;
  actions: string[];
}

// --- machine-readable QA findings (repair severity tier) -------------------------------------------
// Deterministic violations (`qa_warnings.violations`) and LLM QA issues (`qa_warnings.issues`) both
// carry {kind, field, detail, severity, blocks_*}. Severity policy: `block` = true blocker (drafting,
// review, and export all stop; deterministic checks only), `repair` = fixable (drafting/approval
// proceed; final export waits), `warn`/`info` = advisory. Old rows may predate the persisted
// `blocks_*` booleans, so normalization derives them from severity as a fallback.

export type PacketViolationSeverity = "info" | "warn" | "repair" | "block";

export interface PacketViolation {
  kind: string;
  field: string | null;
  detail: string;
  severity: PacketViolationSeverity;
  blocks_drafting: boolean;
  blocks_human_review: boolean;
  blocks_final_export: boolean;
}

const KNOWN_SEVERITIES: ReadonlySet<string> = new Set(["info", "warn", "repair", "block"]);

/** Gate facts derived from severity — the fallback for rows without persisted `blocks_*` booleans. */
function gatesFromSeverity(severity: PacketViolationSeverity) {
  const blocks = severity === "block";
  return {
    blocks_drafting: blocks,
    blocks_human_review: blocks,
    blocks_final_export: blocks || severity === "repair",
  };
}

/** Normalize one raw violation/issue to the guaranteed machine-readable shape. Persisted `blocks_*`
 *  booleans win; missing ones derive from severity; unknown severity degrades to `warn`. */
export function normalizePacketViolation(raw: QaIssue | null | undefined): PacketViolation {
  const rawSeverity = String(raw?.severity ?? "")
    .trim()
    .toLowerCase();
  const severity = (
    KNOWN_SEVERITIES.has(rawSeverity) ? rawSeverity : "warn"
  ) as PacketViolationSeverity;
  const fallback = gatesFromSeverity(severity);
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    kind: typeof raw?.kind === "string" && raw.kind ? raw.kind : "issue",
    field: typeof raw?.field === "string" && raw.field ? raw.field : null,
    detail: typeof raw?.detail === "string" ? raw.detail : "",
    severity,
    blocks_drafting:
      typeof r.blocks_drafting === "boolean" ? r.blocks_drafting : fallback.blocks_drafting,
    blocks_human_review:
      typeof r.blocks_human_review === "boolean"
        ? r.blocks_human_review
        : fallback.blocks_human_review,
    blocks_final_export:
      typeof r.blocks_final_export === "boolean"
        ? r.blocks_final_export
        : fallback.blocks_final_export,
  };
}

/** Every deterministic violation + LLM QA issue on a packet's qa_warnings, normalized. */
export function packetQaFindings(warnings: PacketWarnings | null | undefined): PacketViolation[] {
  return [...(warnings?.violations ?? []), ...(warnings?.issues ?? [])].map(
    normalizePacketViolation,
  );
}

/** True blockers: findings that stop drafting (packet status will be `blocked`). */
export function packetDraftBlockers(
  warnings: PacketWarnings | null | undefined,
): PacketViolation[] {
  return packetQaFindings(warnings).filter((v) => v.blocks_drafting);
}

/** Outstanding repair tasks: fixable findings that gate final export but never drafting/approval. */
export function packetRepairTasks(warnings: PacketWarnings | null | undefined): PacketViolation[] {
  return packetQaFindings(warnings).filter((v) => v.blocks_final_export && !v.blocks_drafting);
}

// Surface projection AUDIT events: non-blocking records of what the SurfaceContractBuilder did
// (replaced/omitted a forbidden term with its safe label). They are receipts of safe automatic
// work, not defects — the UI collapses them and must never present them as validation failures.
const SURFACE_AUDIT_KINDS: ReadonlySet<string> = new Set([
  "surface_term_replaced",
  "surface_term_omitted",
]);

export function isSurfaceAuditEvent(v: PacketViolation): boolean {
  return SURFACE_AUDIT_KINDS.has(v.kind) && !v.blocks_drafting && !v.blocks_final_export;
}

/** Non-blocking surface projection audit events (safe replacements/omissions applied). */
export function packetSurfaceAudit(warnings: PacketWarnings | null | undefined): PacketViolation[] {
  return packetQaFindings(warnings).filter(isSurfaceAuditEvent);
}

/** Advisory findings that are neither gates nor surface-audit receipts (plain warn/info). */
export function packetAdvisories(warnings: PacketWarnings | null | undefined): PacketViolation[] {
  return packetQaFindings(warnings).filter(
    (v) => !v.blocks_drafting && !v.blocks_final_export && !isSurfaceAuditEvent(v),
  );
}

/** One-line summary for a collapsed surface-audit group, e.g.
 *  "Surface projection audit: 15 safe replacements applied. Drafting not blocked." */
export function surfaceAuditSummary(events: PacketViolation[]): string {
  const replaced = events.filter((v) => v.kind === "surface_term_replaced").length;
  const omitted = events.length - replaced;
  const parts: string[] = [];
  if (replaced > 0) parts.push(`${replaced} safe replacement${replaced === 1 ? "" : "s"} applied`);
  if (omitted > 0) parts.push(`${omitted} term${omitted === 1 ? "" : "s"} safely omitted`);
  return `Surface projection audit: ${parts.join(" · ") || "no changes"}. Drafting not blocked.`;
}

/** Duck-typed match for the Production-run precondition refusal ("no approved chapter packet for
 *  this chapter"): checks the ApiError's parsed `data.detail` first, then the message string, never
 *  exception identity — so it recognizes the failure across the BFF proxy and in tests alike. */
export function isNoApprovedPacketError(e: unknown): boolean {
  if (!e || typeof e !== "object") return false;
  const data = (e as { data?: unknown }).data;
  const detail =
    data && typeof data === "object" ? (data as { detail?: unknown }).detail : undefined;
  const text =
    typeof detail === "string" ? detail : e instanceof Error && e.message ? e.message : "";
  return /no approved chapter packet/i.test(text);
}

const DEFAULT_DETAIL =
  "The packet failed closed, so no prose may be drafted from it until the blocking gate is resolved.";

const SOURCE_LABEL: Record<string, string> = {
  author: "Packet Author",
  qa: "Packet QA",
  validation: "deterministic validation",
  input: "chapter input",
  rate_limit: "provider rate limit (transient)",
};

function packetBody(packet: PacketOut): PacketBody {
  return packet.body ?? {};
}

function defaultActions(
  source: string | null | undefined,
  kind: string | null | undefined,
): string[] {
  if (source === "author" && kind === "timeout") {
    return [
      "Reduce or split the chapter outline/context, then re-propose.",
      "Choose a faster packet author model in Settings, then re-propose.",
      "Increase DOMINION_PACKET_TIME_BUDGET_S and restart the API, then re-propose.",
    ];
  }
  if (source === "author") {
    return [
      "Tighten or reduce the chapter outline/context, then re-propose.",
      "Check packet-author telemetry/logs if the provider returned an error.",
    ];
  }
  if (source === "validation") {
    return ["Fix the roster fields or forbidden names shown below, then re-propose."];
  }
  if (source === "input" || kind === "no_outline") {
    return ["Add a chapter outline, then re-propose."];
  }
  if (source === "qa") {
    return [
      "Review the QA issue details, adjust the chapter outline/canon inputs, then re-propose.",
    ];
  }
  return ["Re-propose after changing the chapter outline or packet inputs."];
}

export function packetBlockedGuidance(packet: PacketOut): PacketBlockedGuidance {
  const body = packetBody(packet);
  const source = packet.blocker_source ?? packet.qa_warnings?.blocker_source ?? null;
  const kind = packet.blocker_kind ?? packet.qa_warnings?.blocker_kind ?? null;
  const reason =
    packet.blocked_reason ?? packet.qa_warnings?.blocked_reason ?? body.blocked_reason ?? null;
  const actions =
    packet.recovery_actions && packet.recovery_actions.length > 0
      ? packet.recovery_actions
      : packet.qa_warnings?.recovery_actions && packet.qa_warnings.recovery_actions.length > 0
        ? packet.qa_warnings.recovery_actions
        : defaultActions(source, kind);
  const label = source ? (SOURCE_LABEL[source] ?? source) : null;

  return {
    title: label ? `Blocked by ${label}` : "Blocked",
    reason,
    detail: DEFAULT_DETAIL,
    actions,
  };
}
