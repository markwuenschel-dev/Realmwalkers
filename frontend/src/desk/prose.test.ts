import { describe, expect, it } from "vitest";
import { parseBlocks, parseInline, parseInterfaceSpec, timeMarker } from "./prose";

describe("parseInterfaceSpec", () => {
  it("parses typed key=value pairs", () => {
    expect(
      parseInterfaceSpec("role=insight creature=archdemon domain=death intensity=strong"),
    ).toEqual({
      role: "insight",
      creature: "archdemon",
      domain: "death",
      intensity: "strong",
    });
  });

  it("preserves skill and tier as strings", () => {
    expect(parseInterfaceSpec("role=combat skill=Rift Slash tier=legendary")).toEqual({
      role: "combat",
      skill: "Rift",
      tier: "legendary",
    });
  });

  it("ignores unknown enum values", () => {
    expect(parseInterfaceSpec("role=notarole domain=notadomain")).toEqual({});
  });

  it("ignores unknown keys", () => {
    expect(parseInterfaceSpec("role=insight foo=bar")).toEqual({ role: "insight" });
  });
});

describe("parseBlocks @interface", () => {
  const ifaceProse = `\`\`\`text
@interface role=insight creature=archdemon domain=death intensity=strong
Name: ????
Level: ????

Threat model: Failed.
\`\`\``;

  it("detects @interface inside a fenced code block", () => {
    const blocks = parseBlocks(ifaceProse);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      kind: "interface",
      spec: {
        role: "insight",
        creature: "archdemon",
        domain: "death",
        intensity: "strong",
      },
      lines: ["Name: ????", "Level: ????", "", "Threat model: Failed."],
    });
  });

  it("keeps ordinary fenced code as code blocks", () => {
    const blocks = parseBlocks("```js\nconst x = 1;\n```");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ kind: "code", lang: "js", lines: ["const x = 1;"] });
  });

  it("still parses GFM pipe tables", () => {
    const blocks = parseBlocks("| A | B |\n|---|---|\n| 1 | 2 |");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      kind: "table",
      head: ["A", "B"],
      rows: [["1", "2"]],
    });
  });
});

describe("parseInline", () => {
  it("renders asterisk dialogue as emphasis", () => {
    expect(parseInline("*where are you*")).toEqual([{ t: "em", s: "where are you" }]);
    expect(parseInline("She said *we're loading in* quietly.")).toEqual([
      { t: "text", s: "She said " },
      { t: "em", s: "we're loading in" },
      { t: "text", s: " quietly." },
    ]);
  });

  it("renders bold and code", () => {
    expect(parseInline("**bold** and `code`")).toEqual([
      { t: "strong", s: "bold" },
      { t: "text", s: " and " },
      { t: "code", s: "code" },
    ]);
  });
});

describe("timeMarker", () => {
  it("detects day counters, with or without a time-of-day suffix", () => {
    expect(timeMarker("Day 2")).toBe("Day 2");
    expect(timeMarker("Day 47")).toBe("Day 47");
    expect(timeMarker("Day 3 — Morning")).toBe("Day 3 — Morning");
    expect(timeMarker("Day 12 - Dusk")).toBe("Day 12 - Dusk");
  });

  it("detects calendar dates and in-world 'Nth of X' forms", () => {
    expect(timeMarker("March 3rd")).toBe("March 3rd");
    expect(timeMarker("March 3rd, 1998")).toBe("March 3rd, 1998");
    expect(timeMarker("Monday")).toBe("Monday");
    expect(timeMarker("Monday — Dusk")).toBe("Monday — Dusk");
    expect(timeMarker("the 4th of Emberfall")).toBe("the 4th of Emberfall");
  });

  it("honours the explicit @day / @date / @time escape hatch", () => {
    expect(timeMarker("@day 3")).toBe("Day 3");
    expect(timeMarker("@day 3 — Morning")).toBe("Day 3 — Morning");
    expect(timeMarker("@date The Long Dark of Second Winter")).toBe(
      "The Long Dark of Second Winter",
    );
    expect(timeMarker("@time Just before dawn")).toBe("Just before dawn");
    expect(timeMarker("@day")).toBeNull(); // bare tag with no body is not a marker
  });

  it("never diverts ordinary prose that merely starts date-like", () => {
    expect(timeMarker("Day 3, and then everything changed forever.")).toBeNull(); // comma keeps prose
    expect(timeMarker("Day 3 was the longest of my life.")).toBeNull();
    expect(timeMarker("March forward, soldier.")).toBeNull();
    expect(timeMarker("May I have a word with you?")).toBeNull();
    expect(timeMarker("A full paragraph of prose about Monday morning traffic jams.")).toBeNull();
  });
});

describe("parseBlocks — day/date markers", () => {
  it("lifts a standalone marker into its own time block", () => {
    const blocks = parseBlocks("Day 3\n\nI woke on the cold stone floor.");
    expect(blocks[0]).toEqual({ kind: "time", label: "Day 3" });
    expect(blocks[1].kind).toBe("p");
  });

  it("leaves a date-like sentence as an ordinary paragraph", () => {
    const blocks = parseBlocks("Day 3 was the longest of my life.");
    expect(blocks[0].kind).toBe("p");
  });
});
