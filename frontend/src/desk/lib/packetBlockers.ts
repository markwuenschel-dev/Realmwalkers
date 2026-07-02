import type { PacketBody, PacketOut } from "../api/types";

export interface PacketBlockedGuidance {
  title: string;
  reason: string | null;
  detail: string;
  actions: string[];
}

const DEFAULT_DETAIL =
  "The packet failed closed, so no prose may be drafted from it until the blocking gate is resolved.";

const SOURCE_LABEL: Record<string, string> = {
  author: "Packet Author",
  qa: "Packet QA",
  validation: "deterministic validation",
  input: "chapter input",
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
