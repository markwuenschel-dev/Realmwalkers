import { describe, expect, it } from "vitest";

import { parseBlocks } from "../prose";
import { beautify } from "./beautify";

describe("beautify — prose re-flow", () => {
  it("re-flows a hard-wrapped paragraph into one line", () => {
    const wrapped = "It was a dark and stormy\nnight, and the rain\nfell in torrents.";
    expect(beautify(wrapped)).toBe("It was a dark and stormy night, and the rain fell in torrents.");
  });

  it("leaves blank-line-separated paragraphs as separate paragraphs (agent-prose no-op)", () => {
    const two = "First paragraph here.\n\nSecond paragraph here.";
    expect(beautify(two)).toBe("First paragraph here.\n\nSecond paragraph here.");
  });

  it("normalizes CRLF and collapses interior whitespace", () => {
    expect(beautify("a\r\nb")).toBe("a b");
    expect(beautify("two  spaces   here.")).toBe("two spaces here.");
  });
});

describe("beautify — typeset punctuation", () => {
  it("curls quotes, em-dashes, and ellipses", () => {
    expect(beautify('He said "hi" and don\'t stop.')).toBe("He said “hi” and don’t stop.");
    expect(beautify("wait--stop")).toBe("wait—stop");
    expect(beautify("well...")).toBe("well…");
  });

  it("strips markdown escape backslashes", () => {
    // An escaped hyphen becomes a literal hyphen; only `--`/`---` become em dashes.
    expect(beautify("pass\\! and R\\&D and a \\- dash")).toBe("pass! and R&D and a - dash");
  });
});

describe("beautify — structural pass-through (negative fixtures)", () => {
  it("passes a box-drawing stat window through byte-for-byte", () => {
    const box = "┌─ STATUS ─┐\n│ HP 100   │\n└──────────┘";
    expect(beautify(box)).toBe(box);
  });

  it("passes an @interface fenced block through verbatim, including internal blank lines", () => {
    const iface = "```@interface role=combat\nDamage: 42\n\nCrit: yes\n```";
    expect(beautify(iface)).toBe(iface);
  });

  it("leaves tables, headings, lists, and rules untouched", () => {
    expect(beautify("| A | B |\n| - | - |\n| 1 | 2 |")).toBe("| A | B |\n| - | - |\n| 1 | 2 |");
    expect(beautify("# Title")).toBe("# Title");
    expect(beautify("- one\n- two\n- three")).toBe("- one\n- two\n- three");
    expect(beautify("---")).toBe("---");
  });

  it("does not curl quotes inside an inline `code` span", () => {
    const out = beautify('run `x = "y"` now');
    expect(out).toContain('`x = "y"`'); // straight quotes preserved inside code
    expect(out).not.toContain("“"); // nothing curled outside either (no other quotes)
  });
});

describe("beautify → parseBlocks (the paragraph structure the export consumes)", () => {
  const paras = (t: string) => parseBlocks(beautify(t)).filter((b) => b.kind === "p");

  it("collapses a hard-wrapped paragraph into ONE paragraph block", () => {
    expect(paras("It was a dark and stormy\nnight, and the rain\nfell in torrents.")).toHaveLength(1);
  });

  it("keeps two blank-line-separated paragraphs as TWO paragraph blocks", () => {
    expect(paras("First paragraph here.\n\nSecond paragraph here.")).toHaveLength(2);
  });

  it("keeps a stat window a stat block, never paragraphs", () => {
    const blocks = parseBlocks(beautify("┌─ X ─┐\n│ HP  │\n└─────┘"));
    expect(blocks.some((b) => b.kind === "stat")).toBe(true);
    expect(blocks.some((b) => b.kind === "p")).toBe(false);
  });
});

describe("beautify — mixed manuscript document", () => {
  it("re-flows prose but preserves an interspersed stat window", () => {
    const doc =
      "The alert flashed across\nthe screen.\n\n" +
      "┌─ LEVEL UP ─┐\n│ +1 STR    │\n└───────────┘\n\n" +
      "He grinned and\nkept walking.";
    expect(beautify(doc)).toBe(
      "The alert flashed across the screen.\n\n" +
        "┌─ LEVEL UP ─┐\n│ +1 STR    │\n└───────────┘\n\n" +
        "He grinned and kept walking.",
    );
  });
});
