import { describe, expect, it } from "vitest";
import { parseBlocks, parseInline, parseInterfaceSpec, timeMarker, type ProseBlock } from "./prose";

/** Narrow a parsed block, failing loudly (never silently passing) when the parser produced another kind. */
function blockOf<K extends ProseBlock["kind"]>(
  blocks: ProseBlock[],
  i: number,
  kind: K,
): Extract<ProseBlock, { kind: K }> {
  const b = blocks[i];
  if (!b || b.kind !== kind) {
    throw new Error(`block ${i} is ${b?.kind ?? "missing"}, expected ${kind}`);
  }
  return b as Extract<ProseBlock, { kind: K }>;
}

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

// Quoted attribute values are what let a directive carry a display name ("Umbral Step") or a whole
// footnote sentence; the bare form has to keep working beside them in the same directive.
describe("parseInterfaceSpec — quoted values", () => {
  it("keeps a double-quoted value whole, spaces and all", () => {
    expect(parseInterfaceSpec('role=skill skill="Umbral Step" rank="Novice"')).toEqual({
      role: "skill",
      skill: "Umbral Step",
      rank: "Novice",
    });
  });

  it("parses the level-up and character-sheet identity attributes", () => {
    expect(parseInterfaceSpec('role=levelup name="Kaelen Voss" from=6 to=7')).toEqual({
      role: "levelup",
      name: "Kaelen Voss",
      from: "6",
      to: "7",
    });
    expect(parseInterfaceSpec('role=sheet name="Kaelen Voss" age=24 level=7')).toEqual({
      role: "sheet",
      name: "Kaelen Voss",
      age: "24",
      level: "7",
    });
  });

  it("mixes bare and quoted values without either swallowing the other", () => {
    expect(
      parseInterfaceSpec(
        'role=skill domain=fire skill="Emberlash" tier=I rank=Novice via="200 successful casts under strain"',
      ),
    ).toEqual({
      role: "skill",
      domain: "fire",
      skill: "Emberlash",
      tier: "I",
      rank: "Novice",
      via: "200 successful casts under strain",
    });
  });

  it("accepts the three new roles and still rejects a near-miss", () => {
    expect(parseInterfaceSpec("role=levelup").role).toBe("levelup");
    expect(parseInterfaceSpec("role=skill").role).toBe("skill");
    expect(parseInterfaceSpec("role=sheet").role).toBe("sheet");
    expect(parseInterfaceSpec("role=levelups").role).toBeUndefined();
  });

  it("keeps an empty quoted value as an empty string", () => {
    expect(parseInterfaceSpec('via="" name=""')).toEqual({ via: "", name: "" });
  });

  it("degrades an unbalanced quote to the bare token instead of eating the rest of the line", () => {
    const spec = parseInterfaceSpec('skill="Ember tier=III');
    expect(spec.skill).toBe('"Ember');
    expect(spec.tier).toBe("III");
  });

  it("tolerates ragged whitespace between attributes", () => {
    expect(parseInterfaceSpec('  role=levelup   name="Kaelen Voss"\tto=7  ')).toEqual({
      role: "levelup",
      name: "Kaelen Voss",
      to: "7",
    });
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

// `@style` is a standalone line that color-codes the ONE block below it. Its binding rules are the part
// an author feels first: bind to the right block, survive a blank line, and never leak into the next one.
describe("parseBlocks @style", () => {
  const TABLE = "| Cost | Range |\n|---|---|\n| 24 vigor | 8 m |";
  const WINDOW = "┌─────────┐\n│ MANA 45 │\n└─────────┘";

  it("binds a directive to the pipe table immediately below it", () => {
    const blocks = parseBlocks(`@style domain=fire skill="Emberlash" tier=III\n${TABLE}`);
    expect(blocks).toHaveLength(1);
    expect(blockOf(blocks, 0, "table").spec).toEqual({
      domain: "fire",
      skill: "Emberlash",
      tier: "III",
    });
  });

  it("binds a directive to a box-drawing stat window", () => {
    const blocks = parseBlocks(`@style domain=water\n${WINDOW}`);
    expect(blocks).toHaveLength(1);
    const stat = blockOf(blocks, 0, "stat");
    expect(stat.spec).toEqual({ domain: "water" });
    expect(stat.lines).toHaveLength(3);
  });

  it("carries the character-sheet identity attributes onto the table", () => {
    const blocks = parseBlocks(
      '@style role=sheet name="Kaelen Voss" age=24 level=7\n| Race: Human |\n|---|\n| Prestige: 2 |',
    );
    expect(blockOf(blocks, 0, "table").spec).toEqual({
      role: "sheet",
      name: "Kaelen Voss",
      age: "24",
      level: "7",
    });
  });

  it("survives blank lines between the directive and the block it styles", () => {
    const blocks = parseBlocks(`@style domain=fire\n\n\n${TABLE}`);
    expect(blockOf(blocks, 0, "table").spec).toMatchObject({ domain: "fire" });
  });

  it("is consumed by exactly one block — the next table is unstyled", () => {
    const blocks = parseBlocks(`@style domain=fire\n${TABLE}\n\n${TABLE}`);
    expect(blocks).toHaveLength(2);
    expect(blockOf(blocks, 0, "table").spec).toMatchObject({ domain: "fire" });
    expect(blockOf(blocks, 1, "table").spec).toBeUndefined();
  });

  it("drops a pending style when a paragraph intervenes", () => {
    const blocks = parseBlocks(
      `@style domain=fire\nHe closed his hand around the ember.\n${TABLE}`,
    );
    expect(blocks).toHaveLength(2);
    expect(blocks[0].kind).toBe("p");
    expect(blockOf(blocks, 1, "table").spec).toBeUndefined();
  });

  it("lets a second directive replace an unconsumed first one", () => {
    const blocks = parseBlocks(`@style domain=fire\n@style domain=water\n${TABLE}`);
    expect(blocks).toHaveLength(1);
    expect(blockOf(blocks, 0, "table").spec).toEqual({ domain: "water" });
  });

  it("never reads a @style line inside a fenced block as a directive", () => {
    const blocks = parseBlocks("```\n@style domain=fire\n| A |\n```");
    expect(blocks).toHaveLength(1);
    expect(blockOf(blocks, 0, "code").lines).toEqual(["@style domain=fire", "| A |"]);
  });

  it("leaves a bare @style (no attributes) as prose, and the table below it unstyled", () => {
    const blocks = parseBlocks(`@style\n${TABLE}`);
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({ kind: "p", text: "@style" });
    expect(blockOf(blocks, 1, "table").spec).toBeUndefined();
  });

  it("leaves an unstyled table and stat window with no spec at all", () => {
    expect(blockOf(parseBlocks(TABLE), 0, "table").spec).toBeUndefined();
    expect(blockOf(parseBlocks(WINDOW), 0, "stat").spec).toBeUndefined();
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
