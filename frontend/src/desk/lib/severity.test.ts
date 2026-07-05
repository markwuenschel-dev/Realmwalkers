import { describe, expect, it } from "vitest";
import { severityChipTone, severityVar } from "./severity";

describe("unified severity map", () => {
  it("maps the unified vocabulary", () => {
    expect(severityChipTone("block")).toBe("bad");
    expect(severityChipTone("repair")).toBe("warn");
    expect(severityChipTone("warn")).toBe("warn");
    expect(severityChipTone("info")).toBe("info");

    expect(severityVar("block")).toBe("--bad");
    expect(severityVar("repair")).toBe("--warn");
    expect(severityVar("warn")).toBe("--warn");
    expect(severityVar("info")).toBe("--dim");
  });

  it('renders legacy "hard" (pre-unification spelling of block) identically to block', () => {
    expect(severityChipTone("hard")).toBe(severityChipTone("block"));
    expect(severityVar("hard")).toBe(severityVar("block"));
  });

  it("falls back per call site for unknown severities", () => {
    expect(severityChipTone("mystery")).toBe("info");
    expect(severityVar("mystery")).toBe("--dim");
    expect(severityVar("mystery", "--warn")).toBe("--warn");
  });
});
