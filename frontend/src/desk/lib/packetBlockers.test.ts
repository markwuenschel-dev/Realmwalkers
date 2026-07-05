import { describe, expect, it } from "vitest";
import {
  isNoApprovedPacketError,
  normalizePacketViolation,
  packetBlockedGuidance,
  packetDraftBlockers,
  packetQaFindings,
  packetRepairTasks,
} from "./packetBlockers";
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
  approval_state: "blocked",
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

describe("normalizePacketViolation", () => {
  it("keeps persisted blocks_* booleans when present (new rows)", () => {
    const v = normalizePacketViolation({
      kind: "word_budget_override",
      field: "word_budget",
      detail: "the model must not override the planner's budget",
      severity: "repair",
      blocks_drafting: false,
      blocks_human_review: false,
      blocks_final_export: true,
    });
    expect(v).toMatchObject({
      kind: "word_budget_override",
      field: "word_budget",
      severity: "repair",
      blocks_drafting: false,
      blocks_human_review: false,
      blocks_final_export: true,
    });
  });

  it("derives block gates from severity for old rows without blocks_* booleans", () => {
    const v = normalizePacketViolation({
      kind: "invalid_body",
      field: null,
      detail: "chapter packet body is not a JSON object",
      severity: "block",
    });
    expect(v.blocks_drafting).toBe(true);
    expect(v.blocks_human_review).toBe(true);
    expect(v.blocks_final_export).toBe(true);
  });

  it("derives repair gates (export only) from severity for old rows", () => {
    const v = normalizePacketViolation({
      kind: "roster_double_bucketed",
      detail: "Mara is present and absent",
      severity: "repair",
    });
    expect(v.blocks_drafting).toBe(false);
    expect(v.blocks_human_review).toBe(false);
    expect(v.blocks_final_export).toBe(true);
  });

  it("treats warn/info and unknown severities as advisory (blocks nothing)", () => {
    for (const severity of ["warn", "info", "banana", undefined]) {
      const v = normalizePacketViolation({ kind: "x", detail: "d", severity });
      expect(v.blocks_drafting).toBe(false);
      expect(v.blocks_final_export).toBe(false);
    }
    expect(normalizePacketViolation({ kind: "x", detail: "d", severity: "banana" }).severity).toBe(
      "warn",
    );
    expect(normalizePacketViolation(null).kind).toBe("issue");
  });

  it('normalizes legacy "hard" to block — never a downgraded advisory', () => {
    // Regression: "hard" was missing from the known-severity set, so a legacy blocker without
    // persisted blocks_* booleans degraded to a non-blocking warn.
    const bare = normalizePacketViolation({ kind: "invalid_body", detail: "d", severity: "hard" });
    expect(bare.severity).toBe("block");
    expect(bare.blocks_drafting).toBe(true);
    expect(bare.blocks_human_review).toBe(true);
    expect(bare.blocks_final_export).toBe(true);

    // Persisted gate facts still win over the severity-derived fallback.
    const persisted = normalizePacketViolation({
      kind: "x",
      detail: "d",
      severity: "hard",
      blocks_drafting: false,
      blocks_human_review: false,
      blocks_final_export: true,
    });
    expect(persisted.severity).toBe("block");
    expect(persisted.blocks_drafting).toBe(false);
  });
});

describe("packet finding partitions", () => {
  const warnings = {
    violations: [
      { kind: "invalid_body", detail: "unusable body", severity: "block" },
      { kind: "roster_double_bucketed", detail: "present and absent", severity: "repair" },
    ],
    issues: [
      { kind: "tone_drift", detail: "voice risk", severity: "warn" },
      { kind: "leaked_reveal", detail: "reader learns too early", severity: "repair" },
    ],
  };

  it("merges violations + issues into one normalized list", () => {
    expect(packetQaFindings(warnings)).toHaveLength(4);
    expect(packetQaFindings(null)).toEqual([]);
  });

  it("counts a legacy hard violation as a draft blocker", () => {
    const legacy = {
      violations: [{ kind: "legacy_row", detail: "old snapshot", severity: "hard" }],
    };
    expect(packetDraftBlockers(legacy).map((v) => v.kind)).toEqual(["legacy_row"]);
  });

  it("splits true blockers from repair tasks", () => {
    expect(packetDraftBlockers(warnings).map((v) => v.kind)).toEqual(["invalid_body"]);
    expect(packetRepairTasks(warnings).map((v) => v.kind)).toEqual([
      "roster_double_bucketed",
      "leaked_reveal",
    ]);
  });
});

describe("isNoApprovedPacketError", () => {
  it("matches the ApiError shape via parsed data.detail", () => {
    const e = Object.assign(new Error("409 Conflict"), {
      status: 409,
      data: { detail: "no approved chapter packet for this chapter" },
    });
    expect(isNoApprovedPacketError(e)).toBe(true);
  });

  it("matches on the message when no structured detail rode along", () => {
    expect(
      isNoApprovedPacketError(
        new Error('409 Conflict — {"detail":"no approved chapter packet for this chapter"}'),
      ),
    ).toBe(true);
  });

  it("rejects unrelated errors and non-objects", () => {
    expect(isNoApprovedPacketError(new Error("500 Internal Server Error"))).toBe(false);
    expect(isNoApprovedPacketError("no approved chapter packet")).toBe(false);
    expect(isNoApprovedPacketError(null)).toBe(false);
  });
});
