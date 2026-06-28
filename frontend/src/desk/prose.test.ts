import { describe, expect, it } from "vitest";
import { parseBlocks, parseInterfaceAttrs } from "./prose";

describe("parseInterfaceAttrs", () => {
  it("parses space-separated key=value pairs", () => {
    expect(parseInterfaceAttrs("role=insight creature=archdemon domain=death intensity=strong")).toEqual({
      role: "insight",
      creature: "archdemon",
      domain: "death",
      intensity: "strong",
    });
  });
});

describe("parseBlocks @interface", () => {
  const ifaceProse = `\`\`\`text
@interface role=insight creature=archdemon domain=death intensity=strong
Name: ????
Level: ????
Race: Archdemon
\`\`\``;

  it("detects @interface inside a fenced code block", () => {
    const blocks = parseBlocks(ifaceProse);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      kind: "interface",
      attrs: {
        role: "insight",
        creature: "archdemon",
        domain: "death",
        intensity: "strong",
      },
      lines: ["Name: ????", "Level: ????", "Race: Archdemon"],
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
