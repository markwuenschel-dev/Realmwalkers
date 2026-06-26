import { describe, expect, it } from "vitest";
import { lineDiff } from "./diff";

describe("lineDiff", () => {
  it("returns all 'same' ops for identical strings", () => {
    const ops = lineDiff("a\nb\nc", "a\nb\nc");
    expect(ops).toEqual([
      { type: "same", text: "a" },
      { type: "same", text: "b" },
      { type: "same", text: "c" },
    ]);
  });

  it("returns all del then add for completely different strings", () => {
    const ops = lineDiff("x\ny", "p\nq");
    const dels = ops.filter((o) => o.type === "del").map((o) => o.text);
    const adds = ops.filter((o) => o.type === "add").map((o) => o.text);
    expect(dels).toEqual(["x", "y"]);
    expect(adds).toEqual(["p", "q"]);
  });

  it("detects a single added line", () => {
    const ops = lineDiff("a\nb", "a\nnew\nb");
    expect(ops).toContainEqual({ type: "add", text: "new" });
    expect(ops.filter((o) => o.type === "del")).toHaveLength(0);
  });

  it("detects a single deleted line", () => {
    const ops = lineDiff("a\nremoved\nb", "a\nb");
    expect(ops).toContainEqual({ type: "del", text: "removed" });
    expect(ops.filter((o) => o.type === "add")).toHaveLength(0);
  });

  it("detects a changed line as del + add", () => {
    const ops = lineDiff("a\nold\nb", "a\nnew\nb");
    const types = ops.map((o) => o.type);
    expect(types).toContain("del");
    expect(types).toContain("add");
    expect(ops.find((o) => o.type === "del")?.text).toBe("old");
    expect(ops.find((o) => o.type === "add")?.text).toBe("new");
  });

  it("handles empty strings (single empty line each)", () => {
    const ops = lineDiff("", "");
    expect(ops).toEqual([{ type: "same", text: "" }]);
  });

  it("handles addition from empty to non-empty", () => {
    const ops = lineDiff("", "hello");
    expect(ops).toContainEqual({ type: "del", text: "" });
    expect(ops).toContainEqual({ type: "add", text: "hello" });
  });

  it("preserves order: unchanged lines appear between changes", () => {
    const ops = lineDiff("keep\nchange\nkeep", "keep\nnew\nkeep");
    expect(ops[0]).toEqual({ type: "same", text: "keep" });
    expect(ops[ops.length - 1]).toEqual({ type: "same", text: "keep" });
  });
});
