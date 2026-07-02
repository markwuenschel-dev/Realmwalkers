import { describe, expect, it } from "vitest";
import { packetBlockedGuidance } from "./packetBlockers";
import type { PacketOut } from "../api/types";

const basePacket = {
  id: "packet-1",
  book_id: "book-1",
  chapter_id: "chapter-1",
  status: "blocked",
  confidence: "red",
  qa_verdict: "block_drafting",
  body: {},
  qa_warnings: null,
  open_questions: { items: [] },
  created_at: "2026-01-01T00:00:00Z",
  can_approve: false,
  approval_blockers: [],
} satisfies PacketOut;

describe("packetBlockedGuidance", () => {
  it("renders actionable guidance for author timeouts", () => {
    const guidance = packetBlockedGuidance({
      ...basePacket,
      blocked_reason:
        "Packet Author timed out after 180s while authoring the chapter packet. Re-propose will likely time out again unless the input/model/budget changes.",
      blocker_source: "author",
      blocker_kind: "timeout",
      recovery_actions: [
        "Reduce or split the chapter outline/context, then re-propose.",
        "Choose a faster packet author model in Settings, then re-propose.",
        "Increase DOMINION_PACKET_TIME_BUDGET_S and restart the API, then re-propose.",
      ],
    });

    expect(guidance.reason).toContain("timed out after 180s");
    expect(guidance.title).toBe("Blocked by Packet Author");
    expect(guidance.actions).toEqual(
      expect.arrayContaining([
        expect.stringContaining("faster packet author model"),
        expect.stringContaining("DOMINION_PACKET_TIME_BUDGET_S"),
      ]),
    );
    expect(guidance.detail).not.toContain("edit the chapter outline and try again");
  });

  it("keeps validation guidance distinct from author failures", () => {
    const guidance = packetBlockedGuidance({
      ...basePacket,
      blocked_reason: "deterministic validation failed: Mara is present and absent",
      blocker_source: "validation",
      blocker_kind: "contract_validation",
    });

    expect(guidance.title).toBe("Blocked by deterministic validation");
    expect(guidance.actions.join(" ")).toContain("Fix the roster fields");
  });
});
