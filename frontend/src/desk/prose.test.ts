import { describe, expect, it } from "vitest";
import { parseBlocks, parseInline, parseInterfaceSpec } from "./prose";

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
