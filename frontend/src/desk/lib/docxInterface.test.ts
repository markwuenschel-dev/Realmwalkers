// LitRPG interface-markup render tests. Like docxXml.test.ts these are STRUCTURAL, not a byte-for-byte
// snapshot: pack the Document, unzip `word/document.xml`, and assert the text and palette a reader would
// actually see. Every panel below is a copy-paste example from lib/interface-markup.md — if that
// reference and this file disagree, one of them is lying to the author.
//
// buildDocDoc(title, content) is the narrowest seam that runs the whole path (parseBlocks → renderBlocks
// → docx), so no manuscript/spine scaffolding is needed to exercise a single block.

import { Packer } from "docx";
import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { buildDocDoc } from "./docx";
import { resolveSurface, tableSurface } from "./litrpgSurfaces";

async function xmlOf(content: string): Promise<string> {
  const buf = await Packer.toBuffer(buildDocDoc("Interface markup", content));
  const zip = await JSZip.loadAsync(buf);
  const entry = zip.file("word/document.xml");
  expect(entry, "word/document.xml should exist in the DOCX package").toBeTruthy();
  return entry!.async("string");
}

/** True when `hex` is used as a run color or cell shading anywhere in the document. */
function hasColor(xml: string, hex: string): boolean {
  return new RegExp(`"${hex}"`, "i").test(xml);
}

const fence = (directive: string, lines: string[] = []): string =>
  ["```", directive, ...lines, "```"].join("\n");

const FIRE = resolveSurface({ domain: "fire" });
const WATER = resolveSurface({ domain: "water" });

const GAIN = "1A9D3F";
const LOSS = "B4231F";

describe("level-up banner (role=levelup)", () => {
  it("renders the band identity, from → to, announcement prose, and the vitals grid", async () => {
    const xml = await xmlOf(
      fence('@interface role=levelup name="Kaelen Voss" from=6 to=7', [
        "You have reached **Level 7**. Choose wisely.",
        "- Health: 72 -> 84",
        "- Mana: 37 -> 45",
      ]),
    );

    expect(xml).toContain("Level Up");
    expect(xml).toContain("Kaelen Voss");
    expect(xml).toContain("6 → ");
    expect(xml).toContain("Vitals restored"); // grid caption (the "&" is XML-escaped)
    expect(hasColor(xml, "1C1608")).toBe(true); // GOLD.band

    // announcement prose survives the delta split, inline bold intact
    expect(xml).toContain("You have reached ");
    expect(xml).toContain("Level 7");

    // delta lines become grid cells (label upper-cased), not body prose
    expect(xml).toContain("HEALTH");
    expect(xml).toContain("MANA");
    expect(xml).toContain("→ 84");
  });

  it("colors a gain green and a loss red from the sign of the delta", async () => {
    const gainOnly = await xmlOf(
      fence("@interface role=levelup from=6 to=7", ["- Health: 72 -> 84"]),
    );
    expect(hasColor(gainOnly, GAIN)).toBe(true);
    expect(hasColor(gainOnly, LOSS)).toBe(false);

    const withLoss = await xmlOf(
      fence("@interface role=levelup from=6 to=7", ["- Health: 72 -> 84", "- Mana: 45 -> 37"]),
    );
    expect(hasColor(withLoss, GAIN)).toBe(true);
    expect(hasColor(withLoss, LOSS)).toBe(true);
  });

  it("accepts the unicode arrow as a delta separator", async () => {
    const xml = await xmlOf(fence("@interface role=levelup", ["- Mana: 37 → 45"]));
    expect(xml).toContain("MANA"); // upper-cased grid label ⇒ parsed as a delta, not prose
    expect(hasColor(xml, GAIN)).toBe(true);
  });

  it("degrades gracefully with no name, no levels, and no deltas", async () => {
    const xml = await xmlOf(fence("@interface role=levelup", ["The threshold gives way."]));
    expect(xml).toContain("Level Up");
    expect(xml).toContain("The threshold gives way.");
    expect(xml).not.toContain("Vitals restored"); // no grid caption without deltas
    expect(hasColor(xml, GAIN)).toBe(false);
  });
});

describe("skill learned (role=skill)", () => {
  const learned = fence(
    '@interface role=skill domain=fire skill="Emberlash" tier=I rank="Novice" via="200 successful casts under strain"',
    ["Repetition has taught your hand what instruction never could."],
  );

  it("renders the domain-coded band, the rank · tier tag, the name, and the via footnote", async () => {
    const xml = await xmlOf(learned);
    expect(xml).toContain("Skill Learned  ·  Fire");
    expect(xml).toContain("Novice · I");
    expect(xml).toContain("Emberlash");
    expect(xml).toContain("Repetition has taught your hand");
    expect(xml).toContain("Learned through use — 200 successful casts under strain.");
    expect(hasColor(xml, FIRE.headerFill)).toBe(true);
  });

  it("omits the footnote when via= is absent, and stays domain-coded", async () => {
    const xml = await xmlOf(
      fence('@interface role=skill domain=water skill="Tidecall"', ["The water answers."]),
    );
    expect(xml).toContain("Skill Learned  ·  Water");
    expect(xml).not.toContain("Learned through use");
    expect(hasColor(xml, WATER.headerFill)).toBe(true);
  });

  it("is domain-driven — two domains produce different bands", async () => {
    const fire = await xmlOf(fence("@interface role=skill domain=fire"));
    expect(hasColor(fire, FIRE.headerFill)).toBe(true);
    expect(hasColor(fire, WATER.headerFill)).toBe(false);
  });
});

describe("character sheet (@style role=sheet + pipe table)", () => {
  const SHEET = [
    '@style role=sheet name="Kaelen Voss" age=24 level=7',
    "| Race: Human | Discipline: Pyromancer | Language: Common |",
    "|---|---|---|",
    "| # STATS | | |",
    "| ~fire Fire +40% | ~light Light +18% | ~air Air +9% |",
  ].join("\n");

  const PLAIN_TABLE = [
    "| Race: Human | Discipline: Pyromancer | Language: Common |",
    "|---|---|---|",
    "| # STATS | | |",
    "| ~fire Fire +40% | ~light Light +18% | ~air Air +9% |",
  ].join("\n");

  it("renders the identity band, section band, domain pips, and small-caps labels", async () => {
    const xml = await xmlOf(SHEET);

    // identity band from the directive attributes
    expect(xml).toContain("Kaelen Voss");
    expect(xml).toContain("NAME ");
    expect(xml).toContain("AGE ");
    expect(xml).toContain("LEVEL ");
    expect(hasColor(xml, "E5B52A")).toBe(true);

    // `| # STATS |` → a gold section band spanning the row
    expect(xml).toContain("STATS");
    expect(hasColor(xml, "F3D98A")).toBe(true);

    // `~domain ` → a colored pip glyph in that domain's accent
    expect(xml).toContain("■");
    expect(hasColor(xml, FIRE.accent)).toBe(true);
    expect(hasColor(xml, resolveSurface({ domain: "light" }).accent)).toBe(true);
    expect(hasColor(xml, resolveSurface({ domain: "air" }).accent)).toBe(true);

    // `Label: value` → small-caps label + serif value
    expect(xml).toContain("RACE ");
    expect(xml).toContain("Human");
  });

  it("routes role=sheet away from the plain-table treatment", async () => {
    const sheet = await xmlOf(SHEET);
    const plain = await xmlOf(PLAIN_TABLE);
    // the same table WITHOUT the directive keeps the neutral header band …
    expect(hasColor(plain, tableSurface().headerFill)).toBe(true);
    expect(plain).not.toContain("■");
    // … while the sheet replaces it entirely
    expect(hasColor(sheet, tableSurface().headerFill)).toBe(false);
  });

  it("drops the identity band when no name/age/level is given", async () => {
    const xml = await xmlOf(
      ["@style role=sheet", "| A | B |", "|---|---|", "| 1 | 2 |"].join("\n"),
    );
    expect(hasColor(xml, "E5B52A")).toBe(false); // no identity band
    expect(xml).toContain("A"); // table still renders
    expect(hasColor(xml, tableSurface().headerFill)).toBe(false); // still the sheet layout
  });
});

describe("@style color-coding a table or stat window", () => {
  const TABLE = "| Cost | Range | Burn |\n|---|---|---|\n| 24 vigor | 8 m | +2/s |";
  const WINDOW = "┌─────────────┐\n│ MANA  45/60 │\n└─────────────┘";

  it("bands and tints a pipe table from an @style domain directive", async () => {
    const styled = await xmlOf(`@style domain=fire skill="Emberlash" tier=III\n${TABLE}`);
    const plain = await xmlOf(TABLE);

    expect(styled).toContain("Emberlash  ·  fire  ·  Tier III");
    expect(styled).toContain("Stats"); // right-hand category tag
    expect(hasColor(styled, FIRE.headerFill)).toBe(true);

    expect(plain).not.toContain("Stats");
    expect(hasColor(plain, FIRE.headerFill)).toBe(false);
    expect(hasColor(plain, tableSurface().headerFill)).toBe(true);
  });

  it("tints a box-drawing stat window, leaving an unstyled one neutral", async () => {
    const styled = await xmlOf(`@style domain=water\n${WINDOW}`);
    const plain = await xmlOf(WINDOW);

    expect(styled).toContain("MANA  45/60"); // the window art survives either way
    expect(plain).toContain("MANA  45/60");
    expect(hasColor(styled, WATER.fill)).toBe(true);
    expect(hasColor(plain, WATER.fill)).toBe(false);
    expect(hasColor(plain, "FAFAFA")).toBe(true); // neutral stat fill
  });

  it("does not leak a style onto the block after the one it styled", async () => {
    const xml = await xmlOf(`@style domain=fire\n${TABLE}\n\n${TABLE}`);
    // the fire band appears once — the second table renders neutral
    expect(xml.match(/Emberlash/g)).toBeNull();
    expect(hasColor(xml, tableSurface().headerFill)).toBe(true);
  });
});

describe("the original three panels still render", () => {
  it("creature scan → bestiary card", async () => {
    const xml = await xmlOf(
      fence(
        '@interface creature=monster domain=blood intensity=strong skill="Gravemaw Broodmother"',
        ["A bloated arachnid queen whose egg-sacs glow with stolen soul-light."],
      ),
    );
    expect(xml).toContain("Gravemaw Broodmother");
    expect(xml).toContain("Bestiary");
    expect(xml).toContain("KIND  ");
    expect(xml).toContain("Severe"); // threat derived from intensity=strong
    expect(xml).toContain("A bloated arachnid queen");
  });

  it("magic block → domain band", async () => {
    const xml = await xmlOf(
      fence('@interface domain=fire skill="Emberlash" tier=III', ["A whip of living flame."]),
    );
    expect(xml).toContain("Emberlash  ·  fire  ·  Tier III");
    expect(hasColor(xml, FIRE.headerFill)).toBe(true);
  });

  it("role-only → centered system message", async () => {
    const xml = await xmlOf(
      fence("@interface role=system", ["You have entered the **Sunken Vault of Aszhar**."]),
    );
    expect(xml).toContain("SYSTEM");
    expect(xml).toContain("You have entered the ");
    expect(xml).toContain("Sunken Vault of Aszhar");
  });
});
