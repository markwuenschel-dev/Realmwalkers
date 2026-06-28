import { describe, expect, it } from "vitest";
import { formatInterfaceHeader, readableText, resolveSurface } from "./litrpgSurfaces";

describe("readableText", () => {
  it("returns white on dark fills", () => {
    expect(readableText("0B0F14")).toBe("FFFFFF");
    expect(readableText("1F2937")).toBe("FFFFFF");
  });

  it("returns dark on light fills", () => {
    expect(readableText("FFFFFF")).toBe("111827");
    expect(readableText("F8FAFC")).toBe("111827");
  });
});

describe("formatInterfaceHeader", () => {
  it("formats creature scan with domain", () => {
    expect(
      formatInterfaceHeader({
        role: "insight",
        creature: "archdemon",
        domain: "death",
      }),
    ).toBe("[ INSIGHT ] CREATURE SCAN · ARCHDEMON · DEATH");
  });

  it("formats nhal as warning creature scan", () => {
    expect(formatInterfaceHeader({ role: "warning", creature: "nhal" })).toBe(
      "[ WARNING ] CREATURE SCAN · N'HAL",
    );
  });

  it("formats progression with domain", () => {
    expect(formatInterfaceHeader({ role: "progression", domain: "fire" })).toBe(
      "[ PROGRESSION ] PROGRESSION · FIRE",
    );
  });

  it("formats domain-only healing", () => {
    expect(formatInterfaceHeader({ role: "healing", domain: "life" })).toBe("[ HEALING ] LIFE");
  });
});

describe("resolveSurface", () => {
  it("returns distinct accents for different specs", () => {
    const archdemon = resolveSurface({
      role: "insight",
      creature: "archdemon",
      domain: "death",
      intensity: "strong",
    });
    const archangel = resolveSurface({
      role: "healing",
      creature: "archangel",
      domain: "light",
      intensity: "standard",
    });
    const progression = resolveSurface({
      role: "progression",
      domain: "fire",
      intensity: "standard",
    });
    const damage = resolveSurface({
      role: "damage",
      domain: "death",
      intensity: "strong",
    });

    expect(archdemon.accent).not.toBe(archangel.accent);
    expect(progression.accent).not.toBe(damage.accent);
    expect(archdemon.leftBorderSize).toBe(24);
    expect(archangel.leftBorderSize).toBe(16);
  });

  it("apex uses dark header and pale body", () => {
    const apex = resolveSurface({
      role: "insight",
      creature: "archdemon",
      domain: "death",
      intensity: "apex",
    });
    expect(apex.headerFill).not.toBe(apex.fill);
    expect(apex.headerText).toBe("FFFFFF");
    expect(apex.leftBorderSize).toBe(32);
    expect(readableText(apex.fill)).toBe("111827");
  });

  it("returns distinct surfaces for crafting and xp", () => {
    const crafting = resolveSurface({ role: "crafting" });
    const xp = resolveSurface({ role: "xp" });
    expect(crafting.accent).not.toBe(xp.accent);
  });
});
